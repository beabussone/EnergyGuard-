#!/usr/bin/env python3
"""
Anomaly detector — consuma il topic Kafka e genera alert
SOLO per supero soglie superiori su: ΔkWh, kW, Corrente, Volt.
Salva gli alert via Coordinator (chiavi: alerts:{piano}:{timestamp}, latest, index).
"""
import os
import json
import time
import requests
import argparse
from datetime import datetime, timezone
from confluent_kafka import Consumer

def now_iso():
    return datetime.now(timezone.utc).isoformat()

def load_thresholds(coord_url: str, cache, ttl=30):
    t = time.time()
    if cache.get("_ts", 0) + ttl > t:
        return cache["val"]
    try:
        r = requests.get(f"{coord_url}/admin/thresholds", timeout=5)
        r.raise_for_status()
        cache["val"] = r.json()
        cache["_ts"] = t
        return cache["val"]
    except Exception:
        # fallback defaults (upper bounds only)
        return cache.get("val", {
            "delta_kwh_max": 0.20,
            "kw_max": 12.0,
            "corrente_max": 25.0,
            "tensione_max": 240.0,
        })

def post_alert(coord_url: str, key: str, value: dict):
    try:
        # salva l'evento
        requests.put(f"{coord_url}/key/{key}", json={"value": value}, timeout=5)
        # latest per piano
        latest_key = f"alerts:{value.get('piano','?')}:latest"
        requests.put(f"{coord_url}/key/{latest_key}", json={"value": value}, timeout=5)
        # index (append timestamp)
        index_key = f"alerts:{value.get('piano','?')}:index"
        r = requests.get(f"{coord_url}/key/{index_key}", timeout=3)
        if r.status_code == 200:
            try:
                arr = r.json().get("value", {}).get("timestamps", [])
            except Exception:
                arr = []
        else:
            arr = []
        arr.append(value.get("timestamp", now_iso()))
        requests.put(f"{coord_url}/key/{index_key}", json={"value": {"timestamps": arr[-200:]}}, timeout=5)
    except Exception as e:
        print("[ALERT-ERR] save failed:", e)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kafka-bootstrap", default=os.getenv("KAFKA_BOOTSTRAP", "localhost:9094"))
    ap.add_argument("--topic", default=os.getenv("KAFKA_TOPIC", "energyguard.readings"))
    ap.add_argument("--group-id", default="energyguard-anomaly-detector")
    ap.add_argument("--coordinator-url", default=os.getenv("COORD_URL", "http://localhost:8000"))
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args()

    consumer = Consumer({
        "bootstrap.servers": args.kafka_bootstrap,
        "group.id": args.group_id,
        "auto.offset.reset": "earliest",
        "enable.auto.commit": True,
    })
    consumer.subscribe([args.topic])

    print(f"[ANOM] topic={args.topic} kafka={args.kafka_bootstrap} → thresholds@{args.coordinator_url}")

    last_kwh = {}  # per calcolo ΔkWh per piano
    thr_cache = {}

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
            except Exception:
                continue

            piano = data.get("piano")
            ts = data.get("timestamp", now_iso())
            kwh = float(data.get("sensore1_kwh", 0.0))
            kw  = float(data.get("sensore2_kw", 0.0))
            amp = float(data.get("sensore3_corrente", 0.0))
            volt= float(data.get("sensore3_tensione", 0.0))

            thr = load_thresholds(args.coordinator_url, thr_cache, ttl=30)

            alerts = []

            # ΔkWh (upper bound only)
            if piano in last_kwh:
                delta = max(0.0, kwh - last_kwh[piano])
                if args.debug:
                    print(f"[DBG] p{piano} ΔkWh={delta:.3f} thr={thr.get('delta_kwh_max')}")
                if delta > float(thr["delta_kwh_max"]):
                    alerts.append({
                        "tipo": "delta_kwh_exceed",
                        "descrizione": f"ΔkWh={delta:.3f} > {thr['delta_kwh_max']}",
                        "valore": delta
                    })
            last_kwh[piano] = kwh

            # kW (upper)
            if args.debug:
                print(f"[DBG] p{piano} kW={kw:.3f} thr={thr.get('kw_max')}")
            if kw > float(thr["kw_max"]):
                alerts.append({
                    "tipo": "kw_exceed",
                    "descrizione": f"kW={kw:.3f} > {thr['kw_max']}",
                    "valore": kw
                })

            # Corrente A (upper)
            if args.debug:
                print(f"[DBG] p{piano} A={amp:.3f} thr={thr.get('corrente_max')}")
            if amp > float(thr["corrente_max"]):
                alerts.append({
                    "tipo": "amp_exceed",
                    "descrizione": f"A={amp:.3f} > {thr['corrente_max']}",
                    "valore": amp
                })

            # Volt (upper) – sovratensione
            if args.debug:
                print(f"[DBG] p{piano} V={volt:.1f} thr_max={thr.get('tensione_max')}")
            if volt > float(thr["tensione_max"]):
                alerts.append({
                    "tipo": "volt_exceed",
                    "descrizione": f"V={volt:.1f} > {thr['tensione_max']}",
                    "valore": volt
                })

            if alerts:
                alert_doc = {
                    "piano": piano,
                    "timestamp": ts,
                    "eventi": alerts,
                    "raw": data
                }
                key = f"alerts:{piano}:{ts}"
                print("[ALERT]", json.dumps(alert_doc, ensure_ascii=False))
                post_alert(args.coordinator_url, key, alert_doc)

    except KeyboardInterrupt:
        print("Chiusura anomaly detector…")
    finally:
        consumer.close()

if __name__ == "__main__":
    main()
