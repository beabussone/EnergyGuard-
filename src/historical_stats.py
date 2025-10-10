"""Utility per aggregare e servire statistiche storiche di EnergyGuard."""

# historical_stats.py
import os
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, Tuple, Optional, Iterable

HIST_DB_PATH = os.getenv("HISTORICAL_DB_PATH", "/data/historical_readings.db")
# fascia da 30 minuti: 0..47
def halfhour_index(dt: datetime) -> int:
    """Restituisce l'indice (0-47) della fascia da 30 minuti del timestamp."""

    h = dt.hour
    return h * 2 + (1 if dt.minute >= 30 else 0)

def weekday_index(dt: datetime) -> int:
    """Restituisce il giorno della settimana (0 lunedi .. 6 domenica)."""

    # Monday=0 ... Sunday=6
    return int(dt.weekday())

def ensure_db(path: str = HIST_DB_PATH):
    """Crea il database SQLite se mancante e ritorna la connessione aperta."""

    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.execute("""
    CREATE TABLE IF NOT EXISTS readings (
        ts_iso TEXT NOT NULL,        -- fine o centro fascia (ISO UTC)
        weekday INTEGER NOT NULL,    -- 0..6
        halfhour INTEGER NOT NULL,   -- 0..47
        floor INTEGER NOT NULL,
        kw REAL,
        corrente REAL,
        tensione REAL,
        dkwh REAL
    );
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_readings_keys ON readings(floor, weekday, halfhour);")
    conn.commit()
    return conn

@dataclass
class MeanStd:
    mean: float
    std: float
    n: int

StatsKey = Tuple[int, int, int]  # (floor, weekday, halfhour)

class StatsCache:
    """
    Cache in-memory di { (floor,weekday,halfhour): {metric: MeanStd} }.
    """
    def __init__(self):
        self.by_key: Dict[StatsKey, Dict[str, MeanStd]] = {}

    def get(self, floor: int, weekday: int, halfhour: int, metric: str) -> Optional[MeanStd]:
        """Recupera la statistica per metrica e fascia se disponibile."""

        return self.by_key.get((floor, weekday, halfhour), {}).get(metric)

def _accumulate(rows: Iterable[Tuple[int,int,int, Optional[float], Optional[float], Optional[float], Optional[float]]]) -> Dict[StatsKey, Dict[str, MeanStd]]:
    """Calcola media e deviazione standard per ogni chiave di bucket."""

    # rows: (floor, weekday, halfhour, kw, corrente, tensione, dkwh)
    acc_sum = defaultdict(lambda: {"kw": 0.0, "corrente": 0.0, "tensione": 0.0, "dkwh": 0.0})
    acc_sum2 = defaultdict(lambda: {"kw": 0.0, "corrente": 0.0, "tensione": 0.0, "dkwh": 0.0})
    acc_n = defaultdict(lambda: {"kw": 0, "corrente": 0, "tensione": 0, "dkwh": 0})

    def upd(key, metric, val):
        if val is None:
            return
        try:
            v = float(val)
        except:
            return
        acc_sum[key][metric] += v
        acc_sum2[key][metric] += v * v
        acc_n[key][metric] += 1

    for floor, wd, hh, kw, a, v, dk in rows:
        k = (int(floor), int(wd), int(hh))
        upd(k, "kw", kw)
        upd(k, "corrente", a)
        upd(k, "tensione", v)
        upd(k, "dkwh", dk)

    out: Dict[StatsKey, Dict[str, MeanStd]] = {}
    for k in acc_sum:
        out[k] = {}
        for m in ("kw", "corrente", "tensione", "dkwh"):
            n = acc_n[k][m]
            if n <= 1:
                # std = 0 per sicurezza se n<=1
                out[k][m] = MeanStd(mean=(acc_sum[k][m] / n if n else 0.0), std=0.0, n=n)
            else:
                s = acc_sum[k][m]
                s2 = acc_sum2[k][m]
                mean = s / n
                var = max(0.0, (s2 / n) - (mean * mean))  # popolazione (OK qui)
                out[k][m] = MeanStd(mean=mean, std=var ** 0.5, n=n)
    return out

def load_stats(path: str = HIST_DB_PATH) -> StatsCache:
    """Carica tutte le statistiche dal database in un oggetto StatsCache."""

    conn = ensure_db(path)
    cur = conn.execute("""
      SELECT floor, weekday, halfhour, kw, corrente, tensione, dkwh
      FROM readings
    """)
    rows = cur.fetchall()
    cache = StatsCache()
    cache.by_key = _accumulate(rows)
    return cache

def insert_bucket_average(conn: sqlite3.Connection, *,
                          dt_utc: datetime, floor: int,
                          kw: Optional[float], corrente: Optional[float],
                          tensione: Optional[float], dkwh: Optional[float]) -> None:
    """Inserisce nel database la media della fascia da 30 minuti."""
    wd = weekday_index(dt_utc)
    hh = halfhour_index(dt_utc)
    conn.execute("""
      INSERT INTO readings(ts_iso, weekday, halfhour, floor, kw, corrente, tensione, dkwh)
      VALUES(?,?,?,?,?,?,?,?)
    """, (dt_utc.replace(tzinfo=timezone.utc).isoformat(), wd, hh, int(floor), kw, corrente, tensione, dkwh))
    conn.commit()
