import os
import asyncio
import logging
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Path
from pydantic import BaseModel, Field
import httpx
import hashlib
import bisect

# -----------------------------------------------------------------------------
# Logging
# -----------------------------------------------------------------------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("coordinator")

# -----------------------------------------------------------------------------
# Config / Env
# -----------------------------------------------------------------------------
VIRTUAL_NODES = int(os.getenv("VIRTUAL_NODES", "150"))

# Preferito: numero assoluto di repliche
_REPLICATION_FACTOR = os.getenv("REPLICATION_FACTOR")
# Opzionale: frazione 0<r<=1, convertita in count rispetto ai nodi presenti
_REPLICATION_RATIO = os.getenv("REPLICATION_RATIO")  # es. "0.66"

# Nodi da registrare all'avvio (separati da virgola)
BOOTSTRAP_NODES = os.getenv("BOOTSTRAP_NODES", "")

# -----------------------------------------------------------------------------
# Stato runtime
# -----------------------------------------------------------------------------
KVS_NODES: List[str] = []   # es. ["http://kv1:8100", "http://kv2:8100", "http://kv3:8100"]

# -----------------------------------------------------------------------------
# Consistent Hash Ring
# -----------------------------------------------------------------------------
def _h(s: str) -> int:
    # MD5 → int (stabile e veloce per consistent hashing)
    return int(hashlib.md5(s.encode("utf-8")).hexdigest(), 16)

class HashRing:
    def __init__(self, replicas: int = 150) -> None:
        self.replicas = replicas
        self.ring: List[tuple[int, str]] = []  # (hash, node_url)
        self.keys: List[int] = []

    def rebuild(self, nodes: List[str]) -> None:
        ring: List[tuple[int, str]] = []
        for n in nodes:
            for i in range(self.replicas):
                ring.append((_h(f"{n}#{i}"), n))
        ring.sort(key=lambda x: x[0])
        self.ring = ring
        self.keys = [k for (k, _) in ring]

    def add_node(self, node: str) -> None:
        # ricostruzione semplice: per pochi nodi è più che sufficiente
        if node not in KVS_NODES:
            KVS_NODES.append(node)
        self.rebuild(KVS_NODES)

    def remove_node(self, node: str) -> None:
        if node in KVS_NODES:
            KVS_NODES.remove(node)
        self.rebuild(KVS_NODES)

    def get_nodes(self, key: str, rf: int = 1) -> List[str]:
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

# -----------------------------------------------------------------------------
# RF helper
# -----------------------------------------------------------------------------
def compute_rf(total_nodes: int) -> int:
    """Calcola l'RF effettivo come COUNT (>=1). Preferisce REPLICATION_FACTOR (int)."""
    # 1) count assoluto se disponibile
    if _REPLICATION_FACTOR is not None:
        try:
            rf = int(_REPLICATION_FACTOR)
        except ValueError:
            rf = 1
    else:
        # 2) ratio → count
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

# -----------------------------------------------------------------------------
# FastAPI models
# -----------------------------------------------------------------------------
class NodeIn(BaseModel):
    node: str = Field(..., description="Base URL del nodo KVS, es. http://kv1:8100")

class RFIn(BaseModel):
    replication_factor: int = Field(..., ge=1, description="Numero repliche (count)")

class ValueIn(BaseModel):
    value: dict
    ttl: Optional[int] = Field(None, ge=1, description="TTL in secondi (opzionale)")

# -----------------------------------------------------------------------------
# App
# -----------------------------------------------------------------------------
app = FastAPI(title="EnergyGuard Coordinator", version="1.0.0")

@app.on_event("startup")
async def startup_event():
    # bootstrap nodi da env all'avvio
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

# -----------------------------------------------------------------------------
# Sharding mgmt
# -----------------------------------------------------------------------------
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
    # considera ok se almeno 1 scrittura va a buon fine
    successes = [r for r in results if not isinstance(r, Exception)]
    if not successes:
        logger.error(f"PUT {key} fallito su tutti i target: {results}")
        raise HTTPException(status_code=502, detail="Scrittura fallita su tutti i nodi")
    return {"status": "ok", "replicas_ok": len(successes), "targets": targets}

@app.get("/key/{key:path}")
async def kv_get(key: str = Path(..., description="Chiave KVS")):
    if not KVS_NODES:
        raise HTTPException(status_code=503, detail="Nessun nodo KVS registrato")
    rf = compute_rf(len(KVS_NODES))
    targets = hash_ring.get_nodes(key, rf=rf)
    if not targets:
        raise HTTPException(status_code=503, detail="Ring vuoto")

    async with httpx.AsyncClient() as client:
        # prova in ordine: appena uno risponde 200, ritorna
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