"""Coordinatore centrale del cluster EnergyGuard.

Questo servizio FastAPI gestisce l'anello dei nodi KVS, le API 
chiave-valore con replica, la configurazione delle soglie e la 
distribuzione delle notifiche di anomalia.
"""

import os
import json
import asyncio
import logging
import hashlib
import bisect
import threading
import time
import math
from typing import List, Optional, Dict, Any
from datetime import datetime
import httpx
from fastapi import FastAPI, HTTPException, Path, Depends, Request, Body
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from starlette.responses import StreamingResponse  # per SSE senza dipendenze extra

# -----------------------------------------------------------------------------
# Logging
# -----------------------------------------------------------------------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("coordinator")

# -----------------------------------------------------------------------------
# Config / Env
# -----------------------------------------------------------------------------
VIRTUAL_NODES = int(os.getenv("VIRTUAL_NODES", "150"))

_REPLICATION_FACTOR = os.getenv("REPLICATION_FACTOR")
_REPLICATION_RATIO = os.getenv("REPLICATION_RATIO")  # es. "0.66"

BOOTSTRAP_NODES = os.getenv("BOOTSTRAP_NODES", "")

# Admin / soglie
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "eg-admin-token")
THR_PATH = os.getenv("THRESHOLDS_PATH", "/data/thresholds.json")

# Notifiche
NOTIFY_BUFFER_SIZE = int(os.getenv("NOTIFY_BUFFER_SIZE", "200"))
INTERNAL_BROADCAST_TOKEN = os.getenv("INTERNAL_BROADCAST_TOKEN", "")
SUPPRESS_SECS = float(os.getenv("NOTIFY_SUPPRESS_SECS", "20"))  # blocca duplicati entro N secondi


# -----------------------------------------------------------------------------
# App (UNICA istanza)
# -----------------------------------------------------------------------------
app = FastAPI(title="EnergyGuard Coordinator", version="1.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000", "http://127.0.0.1:3000",
        "http://localhost:5173", "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# -----------------------------------------------------------------------------
# Modelli
# -----------------------------------------------------------------------------
class NodeIn(BaseModel):
    node: str = Field(..., description="Base URL del nodo KVS, es. http://kv1:8100")

class RFIn(BaseModel):
    replication_factor: int = Field(..., ge=1, description="Numero repliche (count)")

class ValueIn(BaseModel):
    value: dict
    ttl: Optional[int] = Field(None, ge=1, description="TTL in secondi (opzionale)")

DEFAULT_THRESHOLD_SET = {
    "delta_kwh_max": 0.08,
    "kw_max": 11.0,
    "corrente_max": 15.0,
    "tensione_max": 232.0,
}


class Thresholds(BaseModel):
    delta_kwh_max: float = Field(DEFAULT_THRESHOLD_SET["delta_kwh_max"], ge=0)
    kw_max: float       = Field(DEFAULT_THRESHOLD_SET["kw_max"], ge=0)
    corrente_max: float = Field(DEFAULT_THRESHOLD_SET["corrente_max"], ge=0)
    tensione_max: float = Field(DEFAULT_THRESHOLD_SET["tensione_max"], ge=0)


class ThresholdsConfig(BaseModel):
    """Restituisce soglie di default e specifiche per piano."""

    default: Thresholds = Field(default_factory=Thresholds, description="Soglie di fallback")
    per_floor: Dict[int, Thresholds] = Field(default_factory=dict, description="Override per piano")

    def resolve_for_floor(self, floor: int) -> Thresholds:
        """Restituisce la configurazione piu adatta al piano richiesto."""

        if not isinstance(floor, int):
            try:
                floor = int(floor)
            except Exception:
                return self.default
        if floor in self.per_floor:
            return self.per_floor[floor]
        str_key = str(floor)
        if str_key in self.per_floor:
            value = self.per_floor[str_key]
            if isinstance(value, Thresholds):
                return value
        return self.default

# ======== Notifiche / Eventi ========
class Notification(BaseModel):
    id: str
    timestamp: float
    floor: int
    metric: str
    value: float
    threshold: float
    severity: str = Field("warning", pattern="^(info|warning|high|critical)$")
    message: str

class MarkReadIn(BaseModel):
    ids: List[str] = Field(default_factory=list)

# -----------------------------------------------------------------------------
# Soglie: persistenza + auth
# -----------------------------------------------------------------------------
_thr_lock = threading.Lock()

def _load_thresholds() -> ThresholdsConfig:
    """Carica la configurazione delle soglie da disco con fallback sicuro."""

    try:
        if os.path.exists(THR_PATH):
            with open(THR_PATH, "r", encoding="utf-8") as f:
                raw = json.load(f)
            if isinstance(raw, dict):
                if "default" in raw or "per_floor" in raw:
                    per_floor = raw.get("per_floor") or {}
                    if isinstance(per_floor, dict):
                        normalized = {}
                        for k, v in per_floor.items():
                            try:
                                normalized[int(k)] = v
                            except Exception:
                                continue
                        raw["per_floor"] = normalized
                    return ThresholdsConfig(**raw)
                return ThresholdsConfig(default=Thresholds(**raw))
    except Exception as e:
        logger.warning("Failed to load thresholds config: %s", e)
    return ThresholdsConfig()

def _save_thresholds(cfg: ThresholdsConfig):
    """Persistenza su file della configurazione corrente."""

    os.makedirs(os.path.dirname(THR_PATH), exist_ok=True)
    with open(THR_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg.model_dump(mode="json"), f, ensure_ascii=False, indent=2)

THRESHOLDS_CFG = _load_thresholds()

def require_admin(request: Request) -> bool:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    token = auth.split(" ", 1)[1].strip()
    if token != ADMIN_TOKEN:
        raise HTTPException(status_code=403, detail="Invalid token")
    return True

@app.get("/admin/thresholds")
def get_thresholds():
    with _thr_lock:
        return THRESHOLDS_CFG.model_dump(mode="json")

@app.put("/admin/thresholds")
def put_thresholds(payload: ThresholdsConfig, _: bool = Depends(require_admin)):
    with _thr_lock:
        global THRESHOLDS_CFG
        THRESHOLDS_CFG = payload
        _save_thresholds(THRESHOLDS_CFG)
        return {"status": "ok", "thresholds": THRESHOLDS_CFG.model_dump(mode="json")}

# -----------------------------------------------------------------------------
# Stato runtime (KVS ring)
# -----------------------------------------------------------------------------
KVS_NODES: List[str] = []

def _h(s: str) -> int:
    return int(hashlib.md5(s.encode("utf-8")).hexdigest(), 16)

class HashRing:
    """Hash ring con nodi virtuali per lo sharding dei dati."""

    def __init__(self, replicas: int = 150) -> None:
        self.replicas = replicas
        self.ring: List[tuple[int, str]] = []
        self.keys: List[int] = []

    def rebuild(self, nodes: List[str]) -> None:
        """Rigenera la tabella hash dopo variazioni dell'elenco nodi."""

        ring: List[tuple[int, str]] = []
        for n in nodes:
            for i in range(self.replicas):
                ring.append((_h(f"{n}#{i}"), n))
        ring.sort(key=lambda x: x[0])
        self.ring = ring
        self.keys = [k for (k, _) in ring]

    def add_node(self, node: str) -> None:
        """Registra un nuovo nodo e ricostruisce l'anello."""

        if node not in KVS_NODES:
            KVS_NODES.append(node)
        self.rebuild(KVS_NODES)

    def remove_node(self, node: str) -> None:
        """Rimuove un nodo esistente dall'anello."""

        if node in KVS_NODES:
            KVS_NODES.remove(node)
        self.rebuild(KVS_NODES)

    def get_nodes(self, key: str, rf: int = 1) -> List[str]:
        """Determina le repliche responsabili di una chiave."""

        if not self.ring:
            return []
        pos = bisect.bisect(self.keys, _h(key)) % len(self.ring)
        out: List[str] = []
        seen = set()
        while len(out) < rf and len(seen) < len(self.ring):
            node = self.ring[pos][1]
            if node not in seen:
                out.append(node)
                seen.add(node)
            pos = (pos + 1) % len(self.ring)
        return out

hash_ring = HashRing(replicas=VIRTUAL_NODES)

def compute_rf(total_nodes: int) -> int:
    """Determina il replication factor effettivo in base alla configurazione."""

    if _REPLICATION_FACTOR is not None:
        try:
            rf = int(_REPLICATION_FACTOR)
        except ValueError:
            rf = 1
    else:
        try:
            ratio = float(_REPLICATION_RATIO) if _REPLICATION_RATIO is not None else 0.0
        except ValueError:
            ratio = 0.0
        if ratio > 0 and total_nodes > 0:
            rf = max(1, min(total_nodes, int(round(ratio * total_nodes))))
        else:
            rf = 1
    if total_nodes > 0:
        rf = max(1, min(rf, total_nodes))
    else:
        rf = 1
    return rf

@app.on_event("startup")
async def startup_event():
    nodes = [n.strip() for n in BOOTSTRAP_NODES.split(",") if n.strip()]
    if nodes:
        for n in nodes:
            if n not in KVS_NODES:
                KVS_NODES.append(n)
        hash_ring.rebuild(KVS_NODES)
        logger.info(f"Bootstrap nodes: {KVS_NODES}")
    else:
        logger.info("Bootstrap nodes: none")

# -----------------------------------------------------------------------------
# Health & Info
# -----------------------------------------------------------------------------
@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/sharding/info")
def sharding_info():
    total_nodes = len(KVS_NODES)
    rf_count = compute_rf(total_nodes)
    return {
        "total_nodes": total_nodes,
        "replication_factor": rf_count,
        "virtual_nodes_per_node": VIRTUAL_NODES,
        "total_virtual_nodes": VIRTUAL_NODES * max(1, total_nodes),
        "nodes": KVS_NODES,
    }

@app.post("/sharding/add-node")
def add_node(body: NodeIn):
    node = body.node
    if node in KVS_NODES:
        return {"status": "warning", "message": f"Il nodo {node} è già registrato", "nodes": KVS_NODES}
    hash_ring.add_node(node)
    logger.info(f"Aggiunto nodo {node}")
    return {"status": "success", "message": f"Nodo {node} aggiunto", "nodes": KVS_NODES}

@app.post("/sharding/remove-node")
def remove_node(body: NodeIn):
    node = body.node
    if node not in KVS_NODES:
        return {"status": "warning", "message": f"Nodo {node} non presente", "nodes": KVS_NODES}
    hash_ring.remove_node(node)
    logger.info(f"Rimosso nodo {node}")
    return {"status": "success", "message": f"Nodo {node} rimosso", "nodes": KVS_NODES}

@app.post("/sharding/reconfigure")
def reconfigure_rf(body: RFIn):
    global _REPLICATION_FACTOR
    _REPLICATION_FACTOR = str(int(body.replication_factor))
    logger.info(f"Replication factor impostato a {body.replication_factor}")
    return {"status": "ok", "replication_factor": int(_REPLICATION_FACTOR)}

# -----------------------------------------------------------------------------
# Proxy KVS (/key)
# -----------------------------------------------------------------------------
async def _node_put(client: httpx.AsyncClient, node: str, key: str, payload: dict) -> None:
    url = f"{node}/key/{key}"
    r = await client.put(url, json=payload, timeout=5.0)
    r.raise_for_status()

async def _node_get(client: httpx.AsyncClient, node: str, key: str) -> httpx.Response:
    url = f"{node}/key/{key}"
    r = await client.get(url, timeout=5.0)
    return r

async def _node_delete(client: httpx.AsyncClient, node: str, key: str) -> None:
    url = f"{node}/key/{key}"
    r = await client.delete(url, timeout=5.0)
    r.raise_for_status()

@app.put("/key/{key:path}")
async def kv_put(key: str = Path(..., description="Chiave KVS"), body: ValueIn = ...):
    """Propaga la scrittura della chiave su tutte le repliche individuate."""

    if not KVS_NODES:
        raise HTTPException(status_code=503, detail="Nessun nodo KVS registrato")
    rf = compute_rf(len(KVS_NODES))
    targets = hash_ring.get_nodes(key, rf=rf)
    if not targets:
        raise HTTPException(status_code=503, detail="Ring vuoto")

    async with httpx.AsyncClient() as client:
        results = await asyncio.gather(
            *[_node_put(client, n, key, body.model_dump(exclude_none=True)) for n in targets],
            return_exceptions=True
        )
    successes = [r for r in results if not isinstance(r, Exception)]
    if not successes:
        logger.error(f"PUT {key} fallito su tutti i target: {results}")
        raise HTTPException(status_code=502, detail="Scrittura fallita su tutti i nodi")
    return {"status": "ok", "replicas_ok": len(successes), "targets": targets}

@app.get("/key/{key:path}")
async def kv_get(key: str = Path(..., description="Chiave KVS")):
    """Legge sequenzialmente le repliche finche trova un valore valido."""

    if not KVS_NODES:
        raise HTTPException(status_code=503, detail="Nessun nodo KVS registrato")
    rf = compute_rf(len(KVS_NODES))
    targets = hash_ring.get_nodes(key, rf=rf)
    if not targets:
        raise HTTPException(status_code=503, detail="Ring vuoto")

    async with httpx.AsyncClient() as client:
        for n in targets:
            try:
                r = await _node_get(client, n, key)
                if r.status_code == 200:
                    return r.json()
            except Exception:
                continue
    raise HTTPException(status_code=404, detail="Chiave non trovata su nessuna replica")

@app.delete("/key/{key:path}")
async def kv_delete(key: str = Path(..., description="Chiave KVS")):
    """Invia la cancellazione a tutte le repliche calcolate."""

    if not KVS_NODES:
        raise HTTPException(status_code=503, detail="Nessun nodo KVS registrato")
    rf = compute_rf(len(KVS_NODES))
    targets = hash_ring.get_nodes(key, rf=rf)
    if not targets:
        raise HTTPException(status_code=503, detail="Ring vuoto")

    async with httpx.AsyncClient() as client:
        results = await asyncio.gather(
            *[_node_delete(client, n, key) for n in targets],
            return_exceptions=True
        )
    successes = [r for r in results if not isinstance(r, Exception)]
    if not successes:
        raise HTTPException(status_code=502, detail="Cancellazione fallita su tutti i nodi")
    return {"status": "ok", "replicas_ok": len(successes), "targets": targets}

# -----------------------------------------------------------------------------
# ======== NOTIFICHE: storage in-memory + SSE =========
# -----------------------------------------------------------------------------
_notify_lock = asyncio.Lock()
_notifications: List[Dict[str, Any]] = []  # buffer ultimi N
_unread_count: int = 0                     # unread globale per demo mono-utente
# firma -> last_seen_time (serve per non duplicare notifiche troppo ravvicinate)
_last_sig: Dict[str, float] = {}

# Semplice hub per SSE: ogni subscriber ha una Queue
_subscribers: "set[asyncio.Queue]" = set()


def _coerce_epoch(value: Any, *, assume_ms: bool = False) -> Optional[float]:
    """Best-effort conversion of various timestamp representations to epoch seconds."""
    if value is None:
        return None
    if isinstance(value, bool):  # guard: bool is subclass of int
        return None

    if isinstance(value, (int, float)):
        try:
            v = float(value)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(v) or v <= 0:
            return None
        if assume_ms or v >= 1_000_000_000_000:  # heuristically treat large values as milliseconds
            return v / 1000.0
        return v

    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        try:
            # numeric string (seconds or milliseconds)
            return _coerce_epoch(float(s), assume_ms=assume_ms)
        except ValueError:
            pass

        normalized = s.replace("Z", "+00:00").replace(" UTC", "+00:00")
        try:
            dt = datetime.fromisoformat(normalized)
        except ValueError:
            return None
        return dt.timestamp()

    if isinstance(value, datetime):
        try:
            return value.timestamp()
        except (OverflowError, OSError, ValueError):
            return None

    return None

async def _broadcast(anomaly: Dict[str, Any]):
    """Invia l'evento a tutti i subscriber SSE."""
    dead = []
    for q in list(_subscribers):
        try:
            await q.put(anomaly)
        except Exception:
            dead.append(q)
    for q in dead:
        _subscribers.discard(q)

def _normalize_event(payload: Dict[str, Any], single_ev: Dict[str, Any]) -> Dict[str, Any]:
    """Converte il payload grezzo del detector in un evento coerente."""

    floor = int(payload.get("piano", -1))
    raw = payload.get("raw") or {}
    raw_dict = raw if isinstance(raw, dict) else {}
    raw_ts_iso = raw_dict.get("timestamp")

    # Tipo, valore e messaggio
    tipo = single_ev.get("tipo", "anomaly")
    try:
        value = float(single_ev.get("valore", 0.0))
    except Exception:
        value = 0.0
    message = single_ev.get("descrizione", "Anomalia")

    # Mappatura tipo -> metrica
    if "kw" in tipo:
        metric = "kw"
    elif "amp" in tipo or "corr" in tipo:
        metric = "corrente"
    elif "volt" in tipo:
        metric = "tensione"
    else:
        metric = "delta_kwh"

    # Threshold best-effort
    thr = 0.0
    try:
        if "threshold" in single_ev:
            thr = float(single_ev["threshold"])
        elif ">" in message:
            thr = float(message.split(">")[-1].strip())
    except Exception:
        thr = 0.0

    thr_base = None
    try:
        if "threshold_base" in single_ev:
            thr_base = float(single_ev["threshold_base"])
    except Exception:
        thr_base = None
    if thr_base is None and thr > 0:
        thr_base = thr

    # Timestamp: preferisci sempre quello ISO del raw
    ts_epoch = None
    candidates: List[tuple[Any, bool]] = []
    if raw_ts_iso is not None:
        candidates.append((raw_ts_iso, False))

    if "timestamp_ms" in raw_dict:
        candidates.append((raw_dict["timestamp_ms"], True))

    for key in ("timestamp_epoch", "timestamp_unix"):
        if key in raw_dict:
            candidates.append((raw_dict[key], False))

    for key in ("timestamp_ms", "timestamp_epoch", "timestamp_unix", "timestamp"):
        if key in payload:
            candidates.append((payload[key], key.endswith("_ms")))

    for candidate, assume_ms in candidates:
        ts_epoch = _coerce_epoch(candidate, assume_ms=assume_ms)
        if ts_epoch is not None and ts_epoch > 0:
            break

    if ts_epoch is None or ts_epoch <= 0:
        ts_epoch = time.time()

    # ID stabile: se il detector ha già passato un alert_id, usa quello
    if payload.get("alert_id"):
        stable_id = str(payload["alert_id"])
    else:
        stable_id = f"{floor}:{metric}:{tipo}:{raw_ts_iso or int(ts_epoch)}"

    if not message:
        message = f"{metric} oltre soglia ({value:.2f} > {thr:.2f})" if thr else "Anomalia rilevata"

    return {
        "id": stable_id,
        "timestamp": ts_epoch,
        "timestamp_ms": int(ts_epoch * 1000),
        "floor": floor,
        "metric": metric,
        "value": value,
        "threshold": float(thr),
        "threshold_base": float(thr_base) if thr_base is not None else float(thr),
        "severity": "warning",
        "message": message,
    }


@app.post("/internal/broadcast")
async def internal_broadcast(body: Dict[str, Any] = Body(...)):
    if INTERNAL_BROADCAST_TOKEN:
        tok = (body.get("_token") or "")
        if tok != INTERNAL_BROADCAST_TOKEN:
            raise HTTPException(status_code=403, detail="Forbidden")

    eventi = body.get("eventi") or []
    if not isinstance(eventi, list) or not eventi:
        # fallback: tratta il payload intero come un singolo evento generico
        eventi = [{}]

    accepted = 0
    suppressed = 0
    now = time.time()

    for ev in eventi:
        evt = _normalize_event(body, ev)

        # firma anti-duplicato: usiamo l'id stabile
        sig = evt["id"]

        # suppress entro finestra temporale
        last = _last_sig.get(sig, 0.0)
        if (now - last) < SUPPRESS_SECS:
            suppressed += 1
            continue

        _last_sig[sig] = now

        async with _notify_lock:
            _notifications.append(evt)
            if len(_notifications) > NOTIFY_BUFFER_SIZE:
                _notifications[:] = _notifications[-NOTIFY_BUFFER_SIZE:]
            global _unread_count
            _unread_count += 1

        await _broadcast(evt)
        accepted += 1

    return {"status": "ok", "accepted": accepted, "suppressed": suppressed}

@app.get("/anomalies/stream")
async def anomalies_stream():
    """
    SSE: invia ogni evento appena pubblicato.
    """
    q: asyncio.Queue = asyncio.Queue()
    _subscribers.add(q)

    async def gen():
        try:
            # invia un ping iniziale (opzionale)
            yield "event: ping\ndata: {}\n\n"
            while True:
                evt = await q.get()
                yield f"event: anomaly\ndata: {json.dumps(evt, ensure_ascii=False)}\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            _subscribers.discard(q)

    return StreamingResponse(gen(), media_type="text/event-stream")

@app.get("/notifications")
async def notifications(limit: int = 50, since_ts: Optional[float] = None):
    """
    Storico recente (in-memory). 'since_ts' filtra > timestamp.
    """
    out = list(_notifications)
    if since_ts is not None:
        out = [x for x in out if x.get("timestamp", 0) > float(since_ts)]
    out = out[-max(1, min(1000, limit)):]
    return {"items": out, "total": len(out)}

@app.get("/notifications/unread_count")
async def notifications_unread_count():
    return {"unread": _unread_count}

@app.post("/notifications/mark_read")
async def notifications_mark_read(body: MarkReadIn):
    # Demo mono-utente: azzera globale. Se vuoi finezza, scala _unread_count di len(ids) presenti.
    async with _notify_lock:
        global _unread_count
        _unread_count = max(0, _unread_count - max(1, len(body.ids) or 1))
    return {"status": "ok", "unread": _unread_count}
