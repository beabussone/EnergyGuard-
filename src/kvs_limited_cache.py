"""KVS FastAPI con cache LRU e persistenza SQLite per EnergyGuard."""

import os
import json
import time
import sqlite3
from typing import Optional, Tuple
from collections import OrderedDict

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

# ------------------------------------------------------------
# Config da env
# ------------------------------------------------------------
SQLITE_PATH = os.getenv("SQLITE_PATH", "/data/shard.db")
MAX_CACHE_ITEMS = int(os.getenv("MAX_CACHE_ITEMS", "20000"))  # 0 = disabilita LRU
DEFAULT_TTL = None  # puoi mettere un numero di secondi per TTL di default oppure lasciare None

# ------------------------------------------------------------
# Modello richieste/risposte
# ------------------------------------------------------------
class ValueIn(BaseModel):
    value: dict
    ttl: Optional[int] = Field(None, ge=1, description="TTL in secondi (opzionale)")

class ValueOut(BaseModel):
    value: dict

# ------------------------------------------------------------
# Cache LRU con TTL
# ------------------------------------------------------------
class LRUCacheTTL:
    """Implementa una piccola cache LRU con supporto TTL opzionale."""

    def __init__(self, capacity: int):
        self.capacity = max(0, capacity)
        self.data: OrderedDict[str, Tuple[float, Optional[float], dict]] = OrderedDict()
        # mappa: key -> (last_access_ts, expire_ts, value)

    def get(self, key: str):
        """Restituisce un valore valido dalla cache aggiornando l'ordine LRU."""

        if self.capacity == 0:
            return None
        item = self.data.get(key)
        if not item:
            return None
        last_access, expire, value = item
        # scadenza
        if expire and time.time() > expire:
            # rimuovi scaduto
            self.data.pop(key, None)
            return None
        # aggiorna LRU
        self.data.move_to_end(key, last=True)
        self.data[key] = (time.time(), expire, value)
        return value

    def put(self, key: str, value: dict, ttl: Optional[int]):
        """Inserisce o aggiorna una voce nella cache rispettando la capacita."""

        if self.capacity == 0:
            return
        expire_ts = time.time() + ttl if ttl else None
        self.data[key] = (time.time(), expire_ts, value)
        self.data.move_to_end(key, last=True)
        # trim se oltre capacità
        if len(self.data) > self.capacity:
            self.data.popitem(last=False)

    def delete(self, key: str):
        """Rimuove una voce dalla cache se presente."""

        self.data.pop(key, None)

cache = LRUCacheTTL(MAX_CACHE_ITEMS)

# ------------------------------------------------------------
# Persistenza SQLite
# ------------------------------------------------------------
def db_connect():
    """Inizializza il database SQLite e garantisce la presenza della tabella."""

    os.makedirs(os.path.dirname(SQLITE_PATH), exist_ok=True)
    conn = sqlite3.connect(SQLITE_PATH, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS kv (
            k TEXT PRIMARY KEY,
            v TEXT NOT NULL,
            expire REAL
        );
    """)
    conn.commit()
    return conn

conn = db_connect()

def db_get(key: str) -> Optional[Tuple[dict, Optional[float]]]:
    """Legge la chiave dal database verificando eventuale scadenza."""

    cur = conn.execute("SELECT v, expire FROM kv WHERE k = ?", (key,))
    row = cur.fetchone()
    if not row:
        return None
    v_str, expire = row
    if expire is not None and time.time() > float(expire):
        # scaduto: elimina
        conn.execute("DELETE FROM kv WHERE k = ?", (key,))
        conn.commit()
        return None
    try:
        return json.loads(v_str), (float(expire) if expire is not None else None)
    except Exception:
        return None

def db_put(key: str, value: dict, ttl: Optional[int]):
    """Salva o aggiorna il valore JSON nel database con TTL opzionale."""

    expire = time.time() + ttl if ttl else None
    v_str = json.dumps(value, separators=(",", ":"), ensure_ascii=False)
    conn.execute(
        "INSERT INTO kv(k, v, expire) VALUES(?,?,?) "
        "ON CONFLICT(k) DO UPDATE SET v=excluded.v, expire=excluded.expire",
        (key, v_str, expire),
    )
    conn.commit()

def db_delete(key: str):
    """Elimina la chiave dal database persistente."""

    conn.execute("DELETE FROM kv WHERE k = ?", (key,))
    conn.commit()

# ------------------------------------------------------------
# FastAPI app
# ------------------------------------------------------------
app = FastAPI(title="EnergyGuard KVS Node", version="1.0.0")

@app.get("/health")
def health():
    """Verifica che il database sia raggiungibile esponendo stato ok/errore."""

    # ping minimale e verifica accesso al DB
    try:
        conn.execute("SELECT 1;")
        return {"status": "ok"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"db error: {e}")

@app.get("/stats")
def stats():
    """Espone statistiche basilari di cache e archivio persistente."""

    cur = conn.execute("SELECT COUNT(*) FROM kv;")
    (count,) = cur.fetchone()
    return {
        "db_path": SQLITE_PATH,
        "keys_in_db": int(count),
        "cache_enabled": MAX_CACHE_ITEMS > 0,
        "cache_items": len(cache.data) if MAX_CACHE_ITEMS > 0 else 0,
        "max_cache_items": MAX_CACHE_ITEMS,
    }

@app.put("/key/{key:path}")
def put_key(key: str, body: ValueIn):
    """Persistenza del valore e aggiornamento della cache per una chiave."""

    # salva su DB
    ttl = body.ttl if body.ttl is not None else DEFAULT_TTL
    db_put(key, body.value, ttl)
    # aggiorna cache
    cache.put(key, body.value, ttl)
    return {"status": "ok"}

@app.get("/key/{key:path}", response_model=ValueOut)
def get_key(key: str):
    """Recupera la chiave sfruttando prima la cache poi il database."""

    # prima cache
    v = cache.get(key)
    if v is not None:
        return ValueOut(value=v)
    # poi DB
    row = db_get(key)
    if not row:
        raise HTTPException(status_code=404, detail="Not found")
    value, expire = row
    # rimetti in cache rispettando TTL residuo
    ttl = None
    if expire:
        rem = int(max(0, expire - time.time()))
        ttl = rem if rem > 0 else None
    cache.put(key, value, ttl)
    return ValueOut(value=value)

@app.delete("/key/{key:path}")
def delete_key(key: str):
    """Cancella la chiave sia dal database che dalla cache."""

    db_delete(key)
    cache.delete(key)
    return {"status": "ok"}