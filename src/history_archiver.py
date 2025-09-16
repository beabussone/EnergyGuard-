# src/historical_archiver.py
import os, json, argparse, sqlite3
from datetime import datetime
from confluent_kafka import Consumer
from pydantic import BaseModel, Field, ConfigDict, ValidationError
from typing import Literal

# --- Modello lettura (coerente con il producer) ---
class Reading(BaseModel):
    model_config = ConfigDict(extra="forbid")
    piano: Literal[1, 2, 3]
    sensore1_kwh: float = Field(gt=0)
    sensore2_kw: float = Field(ge=0)
    sensore3_corrente: float = Field(ge=0)
    sensore3_tensione: float = Field(ge=0)
    timestamp: datetime

CREATE_SQL = """
CREATE TABLE IF NOT EXISTS readings (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  piano INTEGER NOT NULL,
  sensore1_kwh REAL NOT NULL,
  sensore2_kw REAL NOT NULL,
  sensore3_corrente REAL NOT NULL,
  sensore3_tensione REAL NOT NULL,
  timestamp TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_readings_ts ON readings(timestamp);
CREATE INDEX IF NOT EXISTS idx_readings_piano_ts ON readings(piano, timestamp);
"""

INSERT_SQL = """
INSERT INTO readings (piano, sensore1_kwh, sensore2_kw, sensore3_corrente, sensore3_tensione, timestamp)
VALUES (?, ?, ?, ?, ?, ?);
"""

def open_db(path: str) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    conn = sqlite3.connect(path, isolation_level=None)  # autocommit
    conn.execute("PRAGMA journal_mode=WAL;")  # migliore durabilità/letture concorrenti
    conn.executescript(CREATE_SQL)
    return conn

def main():
    ap = argparse.ArgumentParser(description="EnergyGuard historical archiver (Kafka → cumulative SQLite)")
    ap.add_argument("--kafka-bootstrap", default="localhost:9094")
    ap.add_argument("--topic", default="energyguard.readings")
    ap.add_argument("--group-id", default="energyguard-archiver")
    ap.add_argument("--db-path", default="data/energyguard_history.db")
    args = ap.parse_args()

    conn = open_db(args.db_path)

    consumer = Consumer({
        "bootstrap.servers": args.kafka_bootstrap,
        "group.id": args.group_id,
        "auto.offset.reset": "earliest",
    })
    consumer.subscribe([args.topic])

    print(f"[ARCHIVER] topic={args.topic} kafka={args.kafka_bootstrap} db={args.db_path}")

    try:
        while True:
            msg = consumer.poll(1.0)
            if msg is None:
                continue
            if msg.error():
                print("[ERR]", msg.error()); continue

            try:
                data = json.loads(msg.value().decode("utf-8"))
                if isinstance(data.get("timestamp"), str):
                    data["timestamp"] = datetime.fromisoformat(data["timestamp"])
                r = Reading(**data)

                conn.execute(
                    INSERT_SQL,
                    (r.piano, float(r.sensore1_kwh), float(r.sensore2_kw),
                     float(r.sensore3_corrente), float(r.sensore3_tensione),
                     r.timestamp.isoformat())
                )
            except ValidationError as ve:
                print("[WARN] validation:", ve)
            except Exception as e:
                print("[ERR] processing:", e)

    except KeyboardInterrupt:
        print("Closing…")
    finally:
        consumer.close()
        try: conn.close()
        except: pass

if __name__ == "__main__":
    main()