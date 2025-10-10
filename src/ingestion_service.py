import json
import time
import argparse
from datetime import datetime
from typing import List, Dict, Optional, Tuple

import requests
from confluent_kafka import Consumer


def put_json(base_url: str, key: str, value: dict, timeout: float = 3.0) -> None:
    """Inoltra una PUT al coordinator incapsulando il payload nel formato atteso."""

    url = f"{base_url}/key/{key}"
    # prima: r = requests.put(url, json=value, timeout=timeout)
    r = requests.put(url, json={"value": value}, timeout=timeout)  # <-- WRAP
    r.raise_for_status()


def get_json(base_url: str, key: str, timeout: float = 3.0) -> Optional[dict]:
    """Reperisce un documento dal coordinator gestendo il 404 come valore assente."""

    url = f"{base_url}/key/{key}"
    r = requests.get(url, timeout=timeout)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.json()


def upsert_index(base_url: str, piano: int, ts_iso: str, max_len: int = 500) -> None:
    """
    Mantiene un piccolo indice (lista di timestamp) per piano:
    key = energy:{piano}:index, value = {"timestamps": [...]}
    """
    idx_key    = f"energy:p{piano}:index"  # hash-tag {piano}: utile se in futuro userai un vero cluster
    data = get_json(base_url, idx_key) or {"timestamps": []}
    lst: List[str] = data.get("timestamps", [])
    lst.append(ts_iso)
    if len(lst) > max_len:
        lst = lst[-max_len:]
    put_json(base_url, idx_key, {"timestamps": lst})


def handle_reading(base_url: str, sample: dict, index_size: int) -> Tuple[int, str]:
    """Gestisce una singola lettura replicando la logica del main loop."""

    data = dict(sample)
    # timestamp ISO8601 valido
    ts = data.get("timestamp")
    if isinstance(ts, str):
        # validazione rapida (ISO con Z o offset)
        datetime.fromisoformat(ts.replace("Z", "+00:00"))
    else:
        raise ValueError("timestamp mancante o non stringa ISO8601")

    piano = int(data["piano"])
    row_key    = f"energy:p{piano}:{ts}"
    latest_key = f"energy:p{piano}:latest"

    # 1) riga storica
    put_json(base_url, row_key, data)

    # 2) ultimo valore per piano
    put_json(base_url, latest_key, data)

    # 3) indice compatto degli ultimi N timestamp
    upsert_index(base_url, piano, ts, max_len=index_size)
    return piano, ts


def main():
    """Consuma messaggi Kafka e popola il KVS via coordinator con ultimo valore e indice."""

    ap = argparse.ArgumentParser(description="Ingestion Service → KVS via Coordinator (HTTP)")
    ap.add_argument("--kafka-bootstrap", default="localhost:9094")  # Bitnami Kafka è su 9094
    ap.add_argument("--topic", default="energyguard.readings")
    ap.add_argument("--group-id", default="energyguard-ingestor-kvs")
    ap.add_argument("--coordinator-url", default="http://localhost:8000")
    ap.add_argument("--index-size", type=int, default=500)
    args = ap.parse_args()

    consumer = Consumer({
        "bootstrap.servers": args.kafka_bootstrap,
        "group.id": args.group_id,
        "auto.offset.reset": "earliest",
        "enable.auto.commit": True,
    })
    consumer.subscribe([args.topic])

    print(f"[INGEST-KVS] topic={args.topic} kafka={args.kafka_bootstrap} → coordinator={args.coordinator_url}")

    try:
        while True:
            msg = consumer.poll(1.0)
            if msg is None:
                continue
            if msg.error():
                print("[ERR]", msg.error())
                continue

            try:
                data = json.loads(msg.value().decode("utf-8"))
                piano, ts = handle_reading(args.coordinator_url, data, args.index_size)
                print(f"[CONS→KVS] piano={piano} ts={ts} -> row, latest, index")
            except Exception as e:
                print("[ERR] processing:", e)
                time.sleep(0.2)

    except KeyboardInterrupt:
        print("Closing…")
    finally:
        consumer.close()


if __name__ == "__main__":
    main()
