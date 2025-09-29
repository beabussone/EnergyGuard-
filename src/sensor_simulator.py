#!/usr/bin/env python3
"""
Sensor simulator — genera letture per i 3 piani.
Inietta anomalie SOLO verso l'alto (mai verso il basso) con probabilità configurabile:
- ΔkWh  (incremento extra)
- kW    (picco)
- A     (picco)
- Volt  (sovratensione)
"""
import json
import random
import time
import argparse
from datetime import datetime, timezone
from confluent_kafka import Producer

# seed riproducibile (opzionale)
random.seed(42)

def build_producer(bootstrap: str) -> Producer:
    return Producer({'bootstrap.servers': bootstrap})

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def simulate_base_values(floor: int, cumulative_kwh: dict):
    """
    Simulazione "normale":
    - ΔkWh ~ N(0.05, 0.02)
    - kW   ~ N(5+floor, 1.5)
    - A    ~ N(15+floor, 2.0)
    - V    ~ N(230, 3.0)
    """
    delta = max(0.01, random.gauss(0.05, 0.02))
    cumulative_kwh[floor] += delta

    kw = max(0.0, random.gauss(5.0 + floor, 1.5))
    amp = max(0.0, random.gauss(15.0 + floor, 2.0))
    volt = max(0.0, random.gauss(230.0, 3.0))

    return delta, kw, amp, volt

def maybe_inject_anomaly(floor: int, delta, kw, amp, volt, anomaly_rate: float):
    """
    Con probabilità 'anomaly_rate' applica UNO spike verso l'alto su:
    - ΔkWh: +0.15..+0.25
    - kW:   +4..+7 kW
    - A:    +6..+10 A
    - Volt: +5..+15 V (sovratensione)
    Restituisce (delta, kw, amp, volt, label | None)
    """
    if random.random() > anomaly_rate:
        return delta, kw, amp, volt, None

    kind = random.choice(["dkwh", "kw", "amp", "volt"])
    if kind == "dkwh":
        bump = random.uniform(0.15, 0.25)
        delta += bump
        return delta, kw, amp, volt, f"ΔkWh+{bump:.2f}"
    if kind == "kw":
        bump = random.uniform(4.0, 7.0)
        kw += bump
        return delta, kw, amp, volt, f"kW+{bump:.1f}"
    if kind == "amp":
        bump = random.uniform(6.0, 10.0)
        amp += bump
        return delta, kw, amp, volt, f"A+{bump:.1f}"
    if kind == "volt":
        bump = random.uniform(5.0, 15.0)
        volt += bump
        return delta, kw, amp, volt, f"V+{bump:.1f}"

    return delta, kw, amp, volt, None

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kafka-bootstrap", default="localhost:9094")
    ap.add_argument("--topic", default="energyguard.readings")
    ap.add_argument("--interval-seconds", type=int, default=30)
    ap.add_argument("--anomaly-rate", type=float, default=0.15)
    args = ap.parse_args()

    producer = build_producer(args.kafka_bootstrap)
    floors = [1, 2, 3]
    cumulative_kwh = {1: 1000.0, 2: 800.0, 3: 600.0}

    print(f"Produzione su topic: {args.topic} → {args.kafka_bootstrap} "
          f"(ogni {args.interval_seconds}s/piano, anomaly_rate={args.anomaly_rate:.0%})")

    try:
        while True:
            for f in floors:
                delta, kw, amp, volt = simulate_base_values(f, cumulative_kwh)

                # inietta al massimo UNA anomalia verso l'alto
                delta2, kw2, amp2, volt2, an_label = maybe_inject_anomaly(
                    f, delta, kw, amp, volt, args.anomaly_rate
                )

                # se ΔkWh è stato modificato, aggiorna il cumulativo
                if delta2 != delta:
                    cumulative_kwh[f] += (delta2 - delta)

                reading = {
                    "piano": f,
                    "sensore1_kwh": round(cumulative_kwh[f], 3),
                    "sensore2_kw": round(kw2, 3),
                    "sensore3_corrente": round(amp2, 3),
                    "sensore3_tensione": round(volt2, 1),
                    "timestamp": now_iso(),
                }

                if an_label:
                    print(f"[SIM-ANOM] p{f} {an_label} → {reading}")
                else:
                    print(f"[PROD] {reading}")

                payload = json.dumps(reading).encode("utf-8")
                producer.produce(args.topic, value=payload)
                producer.poll(0)

            producer.flush()
            time.sleep(args.interval_seconds)

    except KeyboardInterrupt:
        print("Chiusura producer…")
        producer.flush()

if __name__ == "__main__":
    main()
