import json
import random
import time
from datetime import datetime, timezone
from confluent_kafka import Producer
from config import settings

random.seed(42)

producer = Producer({
    "bootstrap.servers": settings.kafka_bootstrap,
})

# Valori di base per simulare andamenti realistici
cumulative_kwh = {1: 1000.0, 2: 800.0, 3: 600.0}


def generate_reading(floor: int):
    global cumulative_kwh
    # incremento cumulativo (kWh) ~ consumo nell'intervallo
    increment = max(0.01, random.gauss(0.05, 0.02))
    cumulative_kwh[floor] += increment

    # potenza istantanea (kW) coerente con incremento (approssimazione)
    kw = max(0.0, random.gauss(5.0 + floor, 1.5))

    # corrente/tensione (A/V) con piccole variazioni
    corrente = max(0.0, random.gauss(15.0 + floor, 2.0))
    tensione = max(0.0, random.gauss(230.0, 3.0))

    return {
        "piano": floor,
        "sensore1_kwh": round(cumulative_kwh[floor], 3),
        "sensore2_kw": round(kw, 3),
        "sensore3_corrente": round(corrente, 3),
        "sensore3_tensione": round(tensione, 1),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def main():
    floors = [1, 2, 3]
    interval = settings.publish_interval_seconds
    print(f"Produzione su topic: {settings.kafka_topic} → {settings.kafka_bootstrap} (ogni {interval}s per piano)")
    try:
        while True:
            # Pubblica una lettura per ogni piano
            for f in floors:
                reading = generate_reading(f)
                payload = json.dumps(reading, default=str).encode("utf-8")
                # callback di consegna opzionale per logging basilare
                def _delivery(err, msg):
                    if err is not None:
                        print(f"[PROD][ERR] delivery failed: {err}")
                producer.produce(settings.kafka_topic, value=payload, on_delivery=_delivery)
                # Servi callback e mantieni il buffer scorrevole
                producer.poll(0)
                print("[PROD]", reading)
            producer.flush()
            # attende 30s (configurabile) prima del prossimo giro
            time.sleep(interval)
    except KeyboardInterrupt:
        print("Chiusura producer…")


if __name__ == "__main__":
    main()
