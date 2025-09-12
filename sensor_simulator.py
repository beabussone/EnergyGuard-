#!/usr/bin/env python3
import argparse, json, random, time, threading
from datetime import datetime, timezone
from confluent_kafka import Producer
from pydantic import BaseModel, Field

class Reading(BaseModel):
    sensor_id: str
    site_id: str
    timestamp: str = Field(..., description="ISO8601 UTC")
    power_w: float
    kwh: float

def iso_utc(): 
    return datetime.now(timezone.utc).isoformat()

def build_reading(sensor_id, site_id, power_w, kwh_total):
    return Reading(
        sensor_id=sensor_id,
        site_id=site_id,
        timestamp=iso_utc(),
        power_w=round(power_w, 3),
        kwh=round(kwh_total, 6),
    ).model_dump()

def delivery_report(err, msg):
    if err:
        print(f"[producer] delivery error: {err}")

def run_sensor(idx, args, stop_evt: threading.Event):
    r = random.Random((args.seed or 0) + idx)
    sensor_id = f"{args.sensor_prefix}-{idx:04d}"
    site_id   = f"{args.site_prefix}-{idx % max(1, args.sites):03d}"
    topic     = args.topic

    # Producer dedicato per thread
    p = Producer({
        "bootstrap.servers": args.kafka_bootstrap,
        "compression.type": "lz4",
        "linger.ms": 0,            # pubblichiamo subito (cadenza 60s)
        "enable.idempotence": True # sicurezza in caso di retry
    })

    base_power = args.power_mean + r.uniform(-args.power_jitter, args.power_jitter)
    kwh_total  = 0.0
    sent = 0
    next_t = time.time()

    try:
        while not stop_evt.is_set():
            power = max(0.0, base_power + r.gauss(0, args.power_std))
            if r.random() < args.spike_prob:
                power *= args.spike_mult

            kwh_total += (args.interval / 3600.0) * power
            payload = build_reading(sensor_id, site_id, power, kwh_total)

            p.produce(topic, key=sensor_id, value=json.dumps(payload), callback=delivery_report)
            p.poll(0)

            if args.verbose:
                print(f"[{sensor_id}] -> {topic} site={site_id} "
                      f"power={payload['power_w']} kwh={payload['kwh']} ts={payload['timestamp']}")

            sent += 1
            if args.count and sent >= args.count:
                break

            next_t += args.interval
            time.sleep(max(0.0, next_t - time.time()))
    finally:
        p.flush()

def main():
    ap = argparse.ArgumentParser(description="EnergyGuard Sensor Simulator (Kafka, threaded)")
    ap.add_argument("--kafka-bootstrap", default="localhost:9092")
    ap.add_argument("--topic", default="energy.readings")
    ap.add_argument("--sensors", type=int, default=5)
    ap.add_argument("--sites", type=int, default=2)
    ap.add_argument("--sensor-prefix", default="SENS")
    ap.add_argument("--site-prefix", default="SITE")

    ap.add_argument("--interval", type=float, default=60.0, help="secondi tra due letture (default 60)")
    ap.add_argument("--count", type=int, default=0, help="se >0, invia N letture per sensore e termina")
    ap.add_argument("--verbose", "-v", action="store_true")

    ap.add_argument("--power-mean", type=float, default=800.0)
    ap.add_argument("--power-std", type=float, default=50.0)
    ap.add_argument("--power-jitter", type=float, default=100.0)
    ap.add_argument("--spike-prob", type=float, default=0.02)
    ap.add_argument("--spike-mult", type=float, default=1.8)

    ap.add_argument("--seed", type=int, default=None)
    args = ap.parse_args()

    stop_evt = threading.Event()
    threads = []
    try:
        for i in range(args.sensors):
            t = threading.Thread(target=run_sensor, args=(i, args, stop_evt), daemon=True)
            t.start()
            threads.append(t)

        if args.count == 0:
            # run infinito finché non fai Ctrl+C
            while True:
                time.sleep(1.0)
        else:
            for t in threads:
                t.join()
    except KeyboardInterrupt:
        stop_evt.set()
        for t in threads:
            t.join()

if __name__ == "__main__":
    main()