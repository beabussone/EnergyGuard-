#!/usr/bin/env python3
"""EnergyGuard sensor simulator with diversified anomaly mix."""

import argparse
import json
import math
import os
import random
import time
from datetime import datetime, timezone
from typing import Dict

from confluent_kafka import Producer

NOISE_KW_STD = float(os.getenv("SIM_NOISE_KW_STD", "1.4"))
NOISE_A_STD = float(os.getenv("SIM_NOISE_A_STD", "0.45"))
NOISE_V_STD = float(os.getenv("SIM_NOISE_V_STD", "4.5"))
BASELINE_JITTER_STD = float(os.getenv("SIM_BASELINE_JITTER_STD", "0.35"))
AMP_PER_KW = float(os.getenv("SIM_AMP_PER_KW", "2.8"))
BASE_VOLTAGE = float(os.getenv("SIM_BASE_VOLT", "227.0"))
AMP_SPIKE_COUPLING = float(os.getenv("SIM_AMP_SPIKE_COUPLING", "0.05"))

OUTLIER_PROB = float(os.getenv("SIM_OUTLIER_PROB", "0.065"))
OUTLIER_KW_FACTOR = float(os.getenv("SIM_OUTLIER_KW_FACTOR", "3.0"))
OUTLIER_A_FACTOR = float(os.getenv("SIM_OUTLIER_A_FACTOR", "1.8"))
OUTLIER_V_BUMP = float(os.getenv("SIM_OUTLIER_V_BUMP", "35.0"))
OUTLIER_SEQUENCE = [x.strip() for x in os.getenv("SIM_OUTLIER_SEQUENCE", "kw,kw,a,v,kw,dkwh,v,a").split(",") if x.strip()]
if not OUTLIER_SEQUENCE:
    OUTLIER_SEQUENCE = ["kw", "a", "v", "dkwh"]

SHIFT_PROB = float(os.getenv("SIM_SHIFT_PROB", "0.07"))
SHIFT_FACTOR = float(os.getenv("SIM_SHIFT_FACTOR", "0.55"))
SHIFT_DURATION_MIN = int(os.getenv("SIM_SHIFT_DURATION_MIN", "160"))
SHIFT_DURATION_MAX = int(os.getenv("SIM_SHIFT_DURATION_MAX", "340"))

DRIFT_STEP_KW = float(os.getenv("SIM_DRIFT_STEP_KW", "0.16"))
DRIFT_STEP_V = float(os.getenv("SIM_DRIFT_STEP_V", "0.7"))

BASE_KW = {
    1: float(os.getenv("SIM_BASE_KW_FLOOR1", "6.0")),
    2: float(os.getenv("SIM_BASE_KW_FLOOR2", "6.6")),
    3: float(os.getenv("SIM_BASE_KW_FLOOR3", "7.2")),
}

KWH_SPIKE_PROB = float(os.getenv("SIM_KWH_SPIKE_PROB", "0.18"))
KWH_SPIKE_MULT = float(os.getenv("SIM_KWH_SPIKE_MULT", "2.8"))

SIM_TOPIC = os.getenv("SIM_TOPIC", "energyguard.readings")
SIM_KAFKA_BOOTSTRAP = os.getenv("SIM_KAFKA_BOOTSTRAP", "localhost:9094")
SIM_INTERVAL_SECONDS = int(os.getenv("SIM_INTERVAL_SECONDS", "5"))
SIM_SEED = os.getenv("SIM_SEED", "42")

try:
    random.seed(int(SIM_SEED))
except Exception:
    random.seed(42)

FLOORS = [1, 2, 3]

def build_producer(bootstrap: str) -> Producer:
    """Crea un producer Kafka con bootstrap fornito per il simulator."""
    return Producer({"bootstrap.servers": bootstrap})


def diurnal_profile(hour_frac: float) -> float:
    """Curva periodica che simula la domanda energetica nelle 24 ore."""
    return 1.0 + 0.75 * math.sin((hour_frac - 7) * math.pi / 12)


def weekday_adjust(weekday: int) -> float:
    """Riduce o amplifica i consumi in base al giorno della settimana."""
    return 0.88 if weekday >= 5 else 1.02


def baseline_kw_for_floor(floor: int, dt: datetime) -> float:
    """Combina profilo giornaliero e weekday per ottenere il carico base per piano."""
    hour_frac = dt.hour + dt.minute / 60.0
    return BASE_KW[floor] * diurnal_profile(hour_frac) * weekday_adjust(dt.weekday())


def init_state() -> Dict[int, dict]:
    """Inizializza il dizionario con drift, spike e accumuli per ogni piano."""
    return {
        f: {
            "drift_kw": 0.0,
            "drift_v": 0.0,
            "shift_mul": 1.0,
            "shift_until": 0.0,
            "tick": 0,
            "kwh": 900.0 if f == 1 else (730.0 if f == 2 else 560.0),
            "last_kwh": None,
            "last_spike_kind": None,
            "spike_idx": 0,
        }
        for f in FLOORS
    }


def apply_noise_and_regimes(floor: int, dt: datetime, state: dict):
    """Applica rumore, drift e anomalie restituendo le grandezze simulate."""
    s = state[floor]
    tick = s["tick"]

    kw = baseline_kw_for_floor(floor, dt)
    kw = max(0.0, kw + random.gauss(0, NOISE_KW_STD) + random.gauss(0, BASELINE_JITTER_STD))
    volt = max(0.0, BASE_VOLTAGE + random.gauss(0, NOISE_V_STD))

    if tick % 8 == 0:
        s["drift_kw"] += random.choice([-1, 1]) * DRIFT_STEP_KW
    if tick % 16 == 0:
        s["drift_v"] += random.choice([-1, 1]) * DRIFT_STEP_V

    kw = max(0.0, kw + s["drift_kw"])
    volt = max(0.0, volt + s["drift_v"])

    now_ts = time.time()
    if s["shift_until"] < now_ts and random.random() < SHIFT_PROB:
        s["shift_mul"] = 1.0 + SHIFT_FACTOR
        s["shift_until"] = now_ts + random.randint(SHIFT_DURATION_MIN, SHIFT_DURATION_MAX)

    if now_ts < s["shift_until"]:
        kw *= s["shift_mul"]

    amp_base = max(0.0, kw * AMP_PER_KW + random.gauss(0, NOISE_A_STD))
    amp = amp_base

    spike_label = None
    forced_kwh_multiplier = None

    if random.random() < OUTLIER_PROB:
        seq = OUTLIER_SEQUENCE or ["kw", "a", "v", "dkwh"]
        idx = s.get("spike_idx", 0) % len(seq)
        which = seq[idx]
        s["spike_idx"] = (idx + 1) % len(seq)

        last_kind = s.get("last_spike_kind")
        if len(seq) > 1 and which == last_kind:
            which = seq[s["spike_idx"]]
            s["spike_idx"] = (s["spike_idx"] + 1) % len(seq)
        s["last_spike_kind"] = which

        if which == "kw":
            kw *= random.uniform(OUTLIER_KW_FACTOR * 0.9, OUTLIER_KW_FACTOR * 1.1)
            amp = max(0.0, amp_base * (AMP_SPIKE_COUPLING + 0.2) + random.gauss(0, NOISE_A_STD * 0.4))
            volt += OUTLIER_V_BUMP * 0.3
            spike_label = f"kw*x{OUTLIER_KW_FACTOR:.1f}"
        elif which == "a":
            amp = max(0.0, amp_base * OUTLIER_A_FACTOR + random.gauss(0, NOISE_A_STD * 0.5))
            volt += OUTLIER_V_BUMP * 0.25
            kw *= 1.1
            spike_label = f"a*x{OUTLIER_A_FACTOR:.1f}"
        elif which == "dkwh":
            forced_kwh_multiplier = random.uniform(1.8, KWH_SPIKE_MULT)
            kw *= 1.05
            amp = max(0.0, amp_base * (AMP_SPIKE_COUPLING + 0.25) + random.gauss(0, NOISE_A_STD * 0.35))
            volt += OUTLIER_V_BUMP * 0.18
            spike_label = "dkwh"
        else:  # voltage
            volt += OUTLIER_V_BUMP
            kw *= 1.12
            amp = max(0.0, amp_base * (AMP_SPIKE_COUPLING + 0.28) + random.gauss(0, NOISE_A_STD * 0.45))
            spike_label = f"v+{OUTLIER_V_BUMP:.0f}"

    kw = max(0.0, kw)
    amp = max(0.0, amp)
    volt = max(0.0, volt)
    if kw > 0:
        amp_limit = kw * AMP_PER_KW * 1.4 + 4.5
        if amp > amp_limit:
            amp = amp_limit

    return kw, amp, volt, spike_label, forced_kwh_multiplier


def main():
    """Loop principale: genera letture e le invia su Kafka rispettando gli intervalli."""
    parser = argparse.ArgumentParser(description="EnergyGuard sensor simulator")
    parser.add_argument("--kafka-bootstrap", default=SIM_KAFKA_BOOTSTRAP)
    parser.add_argument("--topic", default=SIM_TOPIC)
    parser.add_argument("--interval-seconds", type=int, default=SIM_INTERVAL_SECONDS)
    args = parser.parse_args()

    producer = build_producer(args.kafka_bootstrap)
    state = init_state()

    print(
        f"[BOOT] Simulator topic={args.topic} kafka={args.kafka_bootstrap} "
        f"dt={args.interval_seconds}s outlier_p={OUTLIER_PROB}"
    )

    try:
        while True:
            dt_utc = datetime.now(timezone.utc)
            for floor in FLOORS:
                s = state[floor]
                s["tick"] += 1

                kw, amp, volt, spike, forced_kwh = apply_noise_and_regimes(floor, dt_utc, state)

                delta_kwh = max(0.0, kw * (args.interval_seconds / 3600.0))
                kwh_spike = False
                spike_mult = 1.0
                if forced_kwh:
                    spike_mult = forced_kwh
                    delta_kwh *= spike_mult
                    kwh_spike = True
                elif delta_kwh > 0.0 and random.random() < KWH_SPIKE_PROB:
                    spike_mult = random.uniform(1.3, KWH_SPIKE_MULT)
                    delta_kwh *= spike_mult
                    kwh_spike = True

                prev_kwh = s.get("kwh")
                s["kwh"] += delta_kwh
                s["last_kwh"] = prev_kwh if prev_kwh is not None else s["kwh"]

                reading = {
                    "piano": floor,
                    "sensore1_kwh": round(s["kwh"], 3),
                    "sensore2_kw": round(kw, 3),
                    "sensore3_corrente": round(amp, 3),
                    "sensore3_tensione": round(volt, 1),
                    "delta_kwh": round(delta_kwh, 4),
                    "timestamp": dt_utc.isoformat(),
                }
                if kwh_spike:
                    reading["delta_kwh_multiplier"] = round(spike_mult, 2)

                labels = []
                if spike and spike != "dkwh":
                    labels.append(spike)
                if kwh_spike:
                    labels.append(f"dkwh*x{spike_mult:.1f}")

                if labels:
                    print(f"[SIM-ANOM] p{floor} {'|'.join(labels)} -> {reading}")
                else:
                    print(
                        f"[PROD] p{floor} {reading['timestamp']} "
                        f"kw={reading['sensore2_kw']} a={reading['sensore3_corrente']} v={reading['sensore3_tensione']}"
                    )

                producer.produce(args.topic, json.dumps(reading).encode("utf-8"))
                producer.poll(0)

            producer.flush()
            time.sleep(args.interval_seconds)

    except KeyboardInterrupt:
        print("[SIM] arresto manuale")
        producer.flush()


if __name__ == "__main__":
    main()
