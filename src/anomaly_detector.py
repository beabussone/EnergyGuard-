#!/usr/bin/env python3
"""EnergyGuard anomaly detector tuned with zero margin."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests

from historical_stats import (
    HIST_DB_PATH,
    ensure_db,
    halfhour_index,
    insert_bucket_average,
    load_stats,
    weekday_index,
)

DEFAULT_COORD_URL = os.getenv("COORDINATOR_URL", "http://localhost:8000")
INTERNAL_BROADCAST_TOKEN = os.getenv("INTERNAL_BROADCAST_TOKEN", "").strip()

K_SIGMA = float(os.getenv("ANOM_K_SIGMA", "1.6"))
MIN_N_FOR_SIGMA = int(os.getenv("ANOM_MIN_N", "18"))
POLL_SECS = float(os.getenv("DETECTOR_POLL_SECS", "0.5"))
EDGE_COOLDOWN_SECS = float(os.getenv("EDGE_COOLDOWN_SECS", "20"))
THRESHOLD_MARGIN = float(os.getenv("ANOM_THRESHOLD_MARGIN", "0.0"))
STATS_REFRESH_SECS = float(os.getenv("STATS_REFRESH_SECS", "900"))

DEFAULT_THRESHOLD_SET: Dict[str, float] = {
    "delta_kwh_max": 0.08,
    "kw_max": 11.0,
    "corrente_max": 15.0,
    "tensione_max": 232.0,
}

WEEKDAY_NAMES = ["Lun", "Mar", "Mer", "Gio", "Ven", "Sab", "Dom"]

last_raw_ts: Dict[int, str] = {}
_last_fire: Dict[tuple[int, str], float] = {}
processed_first: Dict[int, bool] = {}
bucket_acc: Dict[int, Dict[str, Any]] = {}

stats_cache = None
last_stats_load = 0.0


def http_get_json(url: str, timeout: float = 4.0) -> Any:
    """Esegue una GET HTTP e restituisce il JSON decodificato."""

    resp = requests.get(url, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def http_put_json(url: str, payload: Any, timeout: float = 4.0) -> Any:
    """Invia una PUT HTTP con payload JSON e valida la risposta."""

    resp = requests.put(url, json=payload, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def http_post_json(url: str, payload: Any, timeout: float = 4.0) -> Any:
    """Invia una POST HTTP JSON verso il coordinator."""

    resp = requests.post(url, json=payload, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def _coerce_thresholds(data: Optional[dict]) -> Dict[str, float]:
    """Ripulisce valori di soglia accettando stringhe o numeri."""

    out = dict(DEFAULT_THRESHOLD_SET)
    if not isinstance(data, dict):
        return out
    for key in DEFAULT_THRESHOLD_SET:
        value = data.get(key)
        if value is None:
            continue
        try:
            out[key] = float(value)
        except Exception:
            try:
                out[key] = float(str(value))
            except Exception:
                pass
    return out


def _normalize_thresholds_payload(payload: Optional[dict]) -> Dict[str, Any]:
    """Normalizza la struttura delle soglie ricevute dal coordinator."""

    if not isinstance(payload, dict):
        return {"default": dict(DEFAULT_THRESHOLD_SET), "per_floor": {}}

    default = payload.get("default")
    per_floor = payload.get("per_floor") or {}

    if default is None and not per_floor and all(k in DEFAULT_THRESHOLD_SET for k in payload.keys()):
        default = payload

    normalized_pf: Dict[str, Dict[str, float]] = {}
    if isinstance(per_floor, dict):
        for k, v in per_floor.items():
            normalized_pf[str(k)] = _coerce_thresholds(v if isinstance(v, dict) else None)

    return {
        "default": _coerce_thresholds(default if isinstance(default, dict) else None),
        "per_floor": normalized_pf,
    }


def resolve_thresholds_for_floor(cfg: Dict[str, Any], floor: int) -> Dict[str, float]:
    """Estrae le soglie applicabili per il piano richiesto."""

    per_floor = cfg.get("per_floor") or {}
    key = str(floor)
    if key in per_floor:
        return dict(per_floor[key])
    return dict(cfg.get("default", DEFAULT_THRESHOLD_SET))


def get_thresholds(coord_url: str) -> Dict[str, Any]:
    """Scarica le soglie dal coordinator con fallback ai default."""

    try:
        payload = http_get_json(f"{coord_url.rstrip('/')}/admin/thresholds")
        return _normalize_thresholds_payload(payload)
    except Exception as exc:
        print(f"[WARN] soglie non disponibili: {exc}")
        return _normalize_thresholds_payload(None)


def get_latest_floor(coord_url: str, floor: int) -> Optional[dict]:
    """Ottiene l'ultima lettura salvata nel KVS per un piano."""

    try:
        data = http_get_json(f"{coord_url.rstrip('/')}/key/energy:p{floor}:latest")
    except requests.HTTPError as http_err:
        if http_err.response is not None and http_err.response.status_code == 404:
            return None
        raise
    except Exception:
        return None

    if isinstance(data, dict):
        value = data.get("value") if "value" in data and isinstance(data["value"], dict) else data
        return value if isinstance(value, dict) else None
    return None


def check_anomalies(sample: dict, thresholds: Dict[str, float]) -> List[dict]:
    """Costruisce eventi soglia superata confrontando il sample con i limiti."""

    events: List[dict] = []
    if not isinstance(sample, dict):
        return events

    def push(metric_key: str, tipo: str, value: Any, thr_key: str, desc_fmt: str) -> None:
        if value is None:
            return
        try:
            val = float(value)
        except Exception:
            return
        thr_raw = thresholds.get(thr_key, DEFAULT_THRESHOLD_SET.get(thr_key))
        try:
            thr_val = float(thr_raw)
        except Exception:
            try:
                thr_val = float(DEFAULT_THRESHOLD_SET[thr_key])
            except Exception:
                return
        thr_eff = thr_val * (1.0 + THRESHOLD_MARGIN)
        if val <= thr_eff:
            return
        events.append({
            "tipo": tipo,
            "metrica": metric_key,
            "valore": val,
            "threshold": thr_eff,
            "threshold_base": thr_val,
            "descrizione": desc_fmt.format(threshold=thr_eff, base=thr_val),
        })

    push("kw", "kw_over", sample.get("sensore2_kw"), "kw_max", "kW oltre soglia (>{threshold:.2f})")
    push("corrente", "amp_over", sample.get("sensore3_corrente"), "corrente_max", "Corrente oltre soglia (>{threshold:.2f})")
    push("tensione", "volt_over", sample.get("sensore3_tensione"), "tensione_max", "Tensione oltre soglia (>{threshold:.2f})")

    dkwh = sample.get("delta_kwh") or sample.get("delta_kwh_30s")
    push("delta_kwh", "dkwh_over", dkwh, "delta_kwh_max", "ΔkWh oltre soglia (>{threshold:.2f})")

    return events


def _parse_iso(ts_iso: str) -> datetime:
    """Converte un timestamp ISO8601 in datetime UTC."""

    return datetime.fromisoformat(ts_iso.replace("Z", "+00:00")).astimezone(timezone.utc)


def _slot_label(wd: int, hh: int) -> str:
    """Genera una etichetta leggibile per fascia oraria e giorno."""

    hour = hh // 2
    minute = 30 if hh % 2 else 0
    weekday = WEEKDAY_NAMES[wd % 7]
    return f"{weekday} {hour:02d}:{minute:02d}"


def check_pattern_outliers(sample: dict, floor: int) -> List[dict]:
    """Usa le statistiche storiche per rilevare outlier sui pattern."""

    global stats_cache
    if not isinstance(sample, dict) or stats_cache is None:
        return []

    ts_iso = sample.get("timestamp")
    if not ts_iso:
        return []

    try:
        dt = _parse_iso(ts_iso)
    except Exception:
        return []

    wd = weekday_index(dt)
    hh = halfhour_index(dt)

    events: List[dict] = []

    def maybe(metric_key: str, label: str, raw_value: Any) -> None:
        if raw_value is None:
            return
        try:
            x = float(raw_value)
        except Exception:
            return
        ms = stats_cache.get(floor, wd, hh, metric_key)
        if not ms or ms.n < MIN_N_FOR_SIGMA or ms.std in (None, 0.0):
            return
        z = abs((x - ms.mean) / ms.std)
        if z < K_SIGMA:
            return
        thr_val = ms.mean + K_SIGMA * ms.std
        if x <= thr_val:
            return

        events.append({
            "tipo": label,
            "metrica": metric_key,
            "valore": x,
            "zscore": round(z, 2),
            "mu": round(ms.mean, 3),
            "sigma": round(ms.std, 3),
            "threshold": round(thr_val, 3),
            "threshold_base": round(ms.mean, 3),
            "fascia_weekday": wd,
            "fascia_slot": hh,
            "fascia_label": _slot_label(wd, hh),
            "descrizione": f"pattern outlier su {metric_key} (z={z:.2f} ≥ {K_SIGMA}, μ={ms.mean:.2f}, σ={ms.std:.2f})",
        })

    maybe("kw", "pattern_outlier_kw", sample.get("sensore2_kw"))
    maybe("corrente", "pattern_outlier_amp", sample.get("sensore3_corrente"))
    maybe("tensione", "pattern_outlier_volt", sample.get("sensore3_tensione"))

    return events


def _accumulate_and_maybe_flush(sample: dict, floor: int, conn: sqlite3.Connection) -> None:
    """Aggrega letture nel bucket corrente e salva medie quando cambia."""

    ts_iso = sample.get("timestamp")
    if not ts_iso:
        return
    try:
        dt = _parse_iso(ts_iso)
    except Exception:
        return

    wd = weekday_index(dt)
    hh = halfhour_index(dt)
    key = (wd, hh)

    state = bucket_acc.get(floor)
    if state is None:
        state = {
            "key": key,
            "count": 0,
            "sum": {"kw": 0.0, "corrente": 0.0, "tensione": 0.0, "dkwh": 0.0},
            "last_dt": dt,
        }
        bucket_acc[floor] = state

    if state["count"] > 0 and state["key"] != key:
        n = max(1, state["count"])
        sums = state["sum"]
        avg_kw = sums["kw"] / n
        avg_a = sums["corrente"] / n
        avg_v = sums["tensione"] / n
        avg_dk = (sums["dkwh"] / n) if sums["dkwh"] > 0 else None

        insert_bucket_average(
            conn,
            dt_utc=state["last_dt"],
            floor=floor,
            kw=avg_kw,
            corrente=avg_a,
            tensione=avg_v,
            dkwh=avg_dk,
        )

        state["key"] = key
        state["count"] = 0
        state["sum"] = {"kw": 0.0, "corrente": 0.0, "tensione": 0.0, "dkwh": 0.0}

    def add(metric: str, value: Any) -> None:
        if value is None:
            return
        try:
            state["sum"][metric] += float(value)
        except Exception:
            pass

    add("kw", sample.get("sensore2_kw"))
    add("corrente", sample.get("sensore3_corrente"))
    add("tensione", sample.get("sensore3_tensione"))
    add("dkwh", sample.get("delta_kwh") or sample.get("delta_kwh_30s"))

    state["count"] += 1
    state["last_dt"] = dt


def post_alert(coord_url: str, key: str, doc: dict) -> None:
    """Persistenza dell'alert nel KVS tramite coordinator."""

    http_put_json(f"{coord_url.rstrip('/')}/key/{key}", {"value": doc})


def broadcast_notify(coord_url: str, doc: dict) -> None:
    """Invia l'alert al canale interno di notifica SSE."""

    body = dict(doc)
    body.setdefault("eventi", doc.get("eventi", []))
    if INTERNAL_BROADCAST_TOKEN:
        body["_token"] = INTERNAL_BROADCAST_TOKEN
    http_post_json(f"{coord_url.rstrip('/')}/internal/broadcast", body)


def _filter_events_for_floor(floor: int, sample: dict, events: List[dict], now: float) -> List[dict]:
    """Applica debounce e deduplica degli eventi per piano."""

    if floor not in processed_first:
        processed_first[floor] = False

    ts_iso = sample.get("timestamp")
    if ts_iso and processed_first[floor] and last_raw_ts.get(floor) == ts_iso:
        return []

    if ts_iso:
        last_raw_ts[floor] = ts_iso

    if not events:
        return []

    processed_first[floor] = True

    filtered: List[dict] = []
    for ev in events:
        tipo = ev.get("tipo", "anomaly")
        key = (floor, tipo)
        last = _last_fire.get(key, 0.0)
        if (now - last) >= EDGE_COOLDOWN_SECS:
            filtered.append(ev)
            _last_fire[key] = now
    return filtered


def main() -> None:
    """Loop del detector: legge ultimi campioni, calcola anomalie e invia alert."""

    global stats_cache, last_stats_load

    parser = argparse.ArgumentParser(description="EnergyGuard anomaly detector")
    parser.add_argument("--coordinator-url", default=DEFAULT_COORD_URL)
    parser.add_argument("--floors", default="1,2,3")
    parser.add_argument("--poll-secs", type=float, default=POLL_SECS)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--kafka-bootstrap", default="localhost:9094", help="compatibilità")
    args = parser.parse_args()

    coord_url = args.coordinator_url
    floors = [int(x.strip()) for x in args.floors.split(",") if x.strip()]
    poll_secs = max(0.5, float(args.poll_secs))

    print(f"[BOOT] Coordinator URL = {coord_url}")
    print(f"[BOOT] Floors = {floors}, poll = {poll_secs}s")
    print(f"[BOOT] INTERNAL_BROADCAST_TOKEN {'non ' if not INTERNAL_BROADCAST_TOKEN else ''}impostato")

    conn_hist = ensure_db(HIST_DB_PATH)
    stats_cache = load_stats(HIST_DB_PATH)
    last_stats_load = time.time()
    print("[BOOT] Historical stats loaded")

    last_heartbeat = time.time()

    while True:
        thresholds_cfg = get_thresholds(coord_url)
        any_event = False
        now = time.time()

        for floor in floors:
            sample = get_latest_floor(coord_url, floor)
            if not sample:
                continue

            _accumulate_and_maybe_flush(sample, floor, conn_hist)

            floor_thr = resolve_thresholds_for_floor(thresholds_cfg, floor)
            events = check_anomalies(sample, floor_thr)

            if (time.time() - last_stats_load) > STATS_REFRESH_SECS:
                stats_cache = load_stats(HIST_DB_PATH)
                last_stats_load = time.time()
                print("[STATS] historical cache refresh")

            events.extend(check_pattern_outliers(sample, floor))
            filtered = _filter_events_for_floor(floor, sample, events, now)
            if not filtered:
                continue

            any_event = True
            tipi = "-".join(sorted(ev.get("tipo", "anomaly") for ev in filtered))
            ts_iso = sample.get("timestamp") or "no-ts"
            alert_id = f"{floor}:{tipi}:{ts_iso}"

            alert_doc = {
                "alert_id": alert_id,
                "piano": floor,
                "timestamp": time.time(),
                "eventi": filtered,
                "raw": sample,
                "thresholds": floor_thr,
            }

            print("[ALERT]", json.dumps(alert_doc, ensure_ascii=False))

            if args.dry_run:
                continue

            kv_key = f"alerts:{floor}:{int(time.time())}"
            try:
                post_alert(coord_url, kv_key, alert_doc)
            except Exception as exc:
                print(f"[WARN] impossibile salvare alert {alert_id}: {exc}")

            try:
                broadcast_notify(coord_url, alert_doc)
            except Exception as exc:
                print(f"[WARN] impossibile notificare alert {alert_id}: {exc}")

        if not any_event and (time.time() - last_heartbeat) > 10:
            print("[HEARTBEAT] detector running...")
            last_heartbeat = time.time()

        time.sleep(poll_secs)


if __name__ == "__main__":
    main()
