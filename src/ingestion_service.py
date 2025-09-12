import json
import time
from datetime import datetime, timezone
from confluent_kafka import Consumer
import redis
from pydantic import ValidationError
from config import settings
from models import Reading

r = redis.Redis(host=settings.redis_host, port=settings.redis_port, db=settings.redis_db)

consumer = Consumer({
    "bootstrap.servers": settings.kafka_bootstrap,
    "group.id": settings.kafka_group_id,
    "auto.offset.reset": "earliest",
    "enable.auto.commit": True,
})


def to_epoch_seconds(dt: datetime) -> float:
    return dt.timestamp()


def store_reading(reading: Reading):
    ts_iso = reading.timestamp.isoformat()
    key_hash = f"energy:{reading.piano}:{ts_iso}"
    key_index = f"energy:index:{reading.piano}"
    key_latest = f"energy:latest:{reading.piano}"

    # Hash "tabellare"
    mapping = {
        "piano": str(reading.piano),
        "sensore1_kwh": f"{reading.sensore1_kwh}",
        "sensore2_kw": f"{reading.sensore2_kw}",
        "sensore3_corrente": f"{reading.sensore3_corrente}",
        "sensore3_tensione": f"{reading.sensore3_tensione}",
        "timestamp": ts_iso,
    }
    r.hset(key_hash, mapping=mapping)

    # Indice temporale per range query
    score = to_epoch_seconds(reading.timestamp)
    r.zadd(key_index, {key_hash: score})

    # Ultimo valore per piano
    r.hset(key_latest, mapping=mapping)


def main():
    print("Consumer attivo → Kafka:", settings.kafka_bootstrap, "→ Redis:", settings.redis_host)
    consumer.subscribe([settings.kafka_topic])
    try:
        while True:
            msg = consumer.poll(1.0)
            if msg is None:
                continue
            if msg.error():
                # errori non fatali possono arrivare come eventi
                print("[CONS][WARN]", msg.error())
                continue
            try:
                data = json.loads(msg.value().decode("utf-8"))
                # parsing ISO timestamp → datetime
                if isinstance(data.get("timestamp"), str):
                    data["timestamp"] = datetime.fromisoformat(data["timestamp"])
                reading = Reading(**data)
                store_reading(reading)
                print("[CONS] stored:", reading.model_dump())
            except ValidationError as ve:
                print("[WARN] Validation error:", ve)
            except Exception as e:
                print("[ERR]", e)
                time.sleep(0.5)
    except KeyboardInterrupt:
        print("Chiusura consumer…")
    finally:
        consumer.close()


if __name__ == "__main__":
    main()
