"""Script per popolare il DB storico con dati sintetici."""

# populate_historical_db.py
from datetime import datetime, timedelta, timezone
import random
import math
import os
from historical_stats import ensure_db, insert_bucket_average

HIST_DB_PATH = os.getenv("HISTORICAL_DB_PATH", "/data/historical_readings.db")
FLOORS = [1, 2, 3]
HIST_BASE_KW = {
    1: float(os.getenv("HIST_BASE_KW_FLOOR1", "5.8")),
    2: float(os.getenv("HIST_BASE_KW_FLOOR2", "6.3")),
    3: float(os.getenv("HIST_BASE_KW_FLOOR3", "6.9")),
}
HIST_AMP_PER_KW = float(os.getenv("HIST_AMP_PER_KW", "2.2"))
HIST_BASE_VOLT = float(os.getenv("HIST_BASE_VOLT", "226.0"))
HIST_KW_NOISE = float(os.getenv("HIST_KW_NOISE_STD", "0.6"))
HIST_AMP_NOISE = float(os.getenv("HIST_AMP_NOISE_STD", "0.7"))
HIST_V_NOISE = float(os.getenv("HIST_V_NOISE_STD", "3.5"))

DAYS = 130
SEED = 42

random.seed(SEED)

def baseline_kw(hour, weekday, floor):
    """Riproduce il carico medio giornaliero per piano con variazioni settimanali."""

    base = HIST_BASE_KW[floor]
    day_factor = 1.0 + 0.55 * math.sin((hour - 6.5) * math.pi / 12)
    week_adj = 0.92 if weekday >= 5 else 1.05
    return max(0.4, base * day_factor * week_adj)

def run():
    """Genera serie storica sintetica e scrive le medie nel database SQLite."""

    conn = ensure_db(HIST_DB_PATH)
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=DAYS)
    t = start.replace(minute=0, second=0, microsecond=0)
    if t.minute >= 30:
        t = t.replace(minute=30)
    else:
        t = t.replace(minute=0)

    total = 0
    while t < now:
        for f in FLOORS:
            for minute in (0, 30):
                tt = t.replace(minute=minute)
                hour = tt.hour + (0.5 if minute == 30 else 0.0)
                kw = baseline_kw(hour, tt.weekday(), f) + random.gauss(0, HIST_KW_NOISE)
                kw = max(0.0, kw)
                corrente = kw * HIST_AMP_PER_KW + random.gauss(0, HIST_AMP_NOISE)
                corrente = max(0.0, corrente)
                tensione = HIST_BASE_VOLT + random.gauss(0, HIST_V_NOISE)
                dkwh = kw * 0.5

                insert_bucket_average(
                    conn,
                    dt_utc=tt,
                    floor=f,
                    kw=kw,
                    corrente=corrente,
                    tensione=tensione,
                    dkwh=dkwh,
                )
                total += 1
        t += timedelta(hours=1)
    print(f"Inserted {total} historical rows in {HIST_DB_PATH}")

if __name__ == "__main__":
    run()
