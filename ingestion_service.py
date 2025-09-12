#!/usr/bin/env python3
import argparse, json, os, threading
from datetime import datetime, timezone
from typing import Optional
import redis
import uvicorn
from fastapi import FastAPI
from pydantic import BaseModel, Field, ValidationError
from confluent_kafka import Consumer, KafkaException

class Reading(BaseModel):
    sensor_id: str
    site_id: str
    timestamp: str = Field(..., description="ISO8601 UTC")
    power_w: float
    kwh: float

def iso_utc(): return datetime.now(timezone.utc).isoformat()

class Stats(BaseModel):
    started_at: str
    consumed_messages: int = 0
    last_message_ts: Optional[str] = None
    last_sensor_id: Optional[str] = None

class KVS:
    def __init__(self, host="localhost", port=6379, db=0, prefix="eg"):
        self.r = redis.Redis(host=host, port=port, db=db, decode_responses=True)
        self.prefix = prefix
        self.max_index_len = 100
    def _k(self, *parts): return ":".join([self.prefix, *parts])
    def save_reading(self, reading: dict):
        sid, ts = reading["sensor_id"], reading["timestamp"]
        self.r.set(self._k("readings", sid, ts), json.dumps(reading))
        self.r.lpush(self._k("idx", sid), ts)
        self.r.ltrim(self._k("idx", sid), 0, self.max_index_len - 1)
        self.r.set(self._k("latest", sid), ts)

class IngestionWorker(threading.Thread):
    def __init__(self, bootstrap, topic, group_id, kvs: KVS, stats: Stats):
        super().__init__(daemon=True)
        self.topic, self.kvs, self.stats = topic, kvs, stats
        self.c = Consumer({
            "bootstrap.servers": bootstrap,
            "group.id": group_id,
            "auto.offset.reset": "earliest",
            "enable.auto.commit": True,
        })
        self._stop = threading.Event()
    def run(self):
        self.c.subscribe([self.topic])
        try:
            while not self._stop.is_set():
                msg = self.c.poll(1.0)
                if msg is None: 
                    continue
                if msg.error():
                    raise KafkaException(msg.error())
                try:
                    reading = Reading(**json.loads(msg.value().decode("utf-8")))
                except ValidationError as e:
                    print(f"[ingestion] invalid message: {e}")
                    continue
                self.kvs.save_reading(reading.model_dump())
                self.stats.consumed_messages += 1
                self.stats.last_message_ts = reading.timestamp
                self.stats.last_sensor_id = reading.sensor_id
        finally:
            self.c.close()
    def stop(self): self._stop.set()

def build_app(stats: Stats) -> FastAPI:
    app = FastAPI(title="EnergyGuard Ingestion Service")
    @app.get("/health")
    def health(): return {"status": "ok", "started_at": stats.started_at, "consumed": stats.consumed_messages}
    @app.get("/stats")
    def get_stats(): return stats.model_dump()
    return app

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kafka-bootstrap", default=os.getenv("KAFKA_BOOTSTRAP", "localhost:9092"))
    ap.add_argument("--topic", default=os.getenv("KAFKA_TOPIC", "energy.readings"))
    ap.add_argument("--group-id", default=os.getenv("KAFKA_GROUP", "ingestion-eg"))
    ap.add_argument("--redis-host", default=os.getenv("REDIS_HOST", "localhost"))
    ap.add_argument("--redis-port", type=int, default=int(os.getenv("REDIS_PORT", "6379")))
    ap.add_argument("--redis-db", type=int, default=int(os.getenv("REDIS_DB", "0")))
    ap.add_argument("--kvs-prefix", default=os.getenv("KVS_PREFIX", "eg"))
    ap.add_argument("--api-host", default="127.0.0.1")
    ap.add_argument("--api-port", type=int, default=8000)
    args = ap.parse_args()

    stats = Stats(started_at=iso_utc())
    kvs = KVS(host=args.redis_host, port=args.redis_port, db=args.redis_db, prefix=args.kvs_prefix)
    worker = IngestionWorker(args.kafka_bootstrap, args.topic, args.group_id, kvs, stats)
    worker.start()

    app = build_app(stats)
    try:
        uvicorn.run(app, host=args.api_host, port=args.api_port)
    finally:
        worker.stop()

if __name__ == "__main__":
    main()