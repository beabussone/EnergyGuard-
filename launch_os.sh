#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "=== Avvio EnergyGuard ==="
cd "$REPO_DIR"

HIST_DB_PATH="$REPO_DIR/data/historical_readings.db"
export HISTORICAL_DB_PATH="$HIST_DB_PATH"

# -------------------------------
# Helpers
# -------------------------------
wait_for_tcp() {
  local host="$1" port="$2" timeout="${3:-30}"
  echo "[Wait] $host:$port (<= ${timeout}s)"
  local start=$(date +%s)
  while ! nc -z "$host" "$port" >/dev/null 2>&1; do
    sleep 1
    local now=$(date +%s)
    if (( now - start >= timeout )); then
      echo "[Wait] Timeout su $host:$port"
      return 1
    fi
  done
  echo "[Wait] OK $host:$port"
}

wait_for_http() {
  local url="$1" timeout="${2:-30}"
  echo "[Wait] $url (<= ${timeout}s)"
  local start=$(date +%s)
  while ! curl -fsS "$url" >/dev/null 2>&1; do
    sleep 1
    local now=$(date +%s)
    if (( now - start >= timeout )); then
      echo "[Wait] Timeout su $url"
      return 1
    fi
  done
  echo "[Wait] OK $url"
}

open_new_terminal_mac() {
  local cmd="$1"
  /usr/bin/osascript <<EOF
tell application "Terminal"
  do script "cd '$REPO_DIR'; $cmd"
  activate
end tell
EOF
}

open_new_terminal_linux() {
  local cmd="$1"
  local base_cmd
  base_cmd=$(printf 'cd %q; %s' "$REPO_DIR" "$cmd")
  if command -v gnome-terminal >/dev/null 2>&1; then
    gnome-terminal -- bash -lc "$base_cmd; exec bash"
  elif command -v xterm >/dev/null 2>&1; then
    xterm -e "bash -lc \"$base_cmd; exec bash\""
  else
    echo "[Warn] Nessun terminale grafico trovato. Eseguo in background: $cmd"
    bash -lc "$base_cmd" &
  fi
}

launch_in_terminal() {
  local cmd="$1"
  local env_assign
  env_assign=$(printf 'HISTORICAL_DB_PATH=%q' "$HIST_DB_PATH")
  local full_cmd="$env_assign $cmd"
  if [[ "$OSTYPE" == "darwin"* ]]; then
    open_new_terminal_mac "$full_cmd"
  else
    open_new_terminal_linux "$full_cmd"
  fi
}

# -------------------------------
# 1) Preparazione DB storico (no reset)
# -------------------------------
echo "[DB] Verifico database storico…"
mkdir -p "$(dirname "$HIST_DB_PATH")"

if [[ ! -f "$HIST_DB_PATH" ]]; then
  echo "[DB] Database storico non trovato, generazione in corso..."
  python3 src/populate_historical_db.py || {
    echo "[Errore] Impossibile generare il DB storico"
    exit 1
  }
else
  echo "[DB] Database storico già presente: $HIST_DB_PATH"
fi

if [[ ! -f "$HIST_DB_PATH" ]]; then
  echo "[Errore] Database storico mancante. Controlla i log di populate_historical_db.py."
  exit 1
fi

# -------------------------------
# 2) Docker Compose
# -------------------------------
echo "[Stack] Avvio Docker (build+up)…"
docker compose up -d --build

# -------------------------------
# 3) Readiness servizi base
# -------------------------------
wait_for_tcp localhost 9094 60
wait_for_http "http://localhost:8000/health" 60

# -------------------------------
# 4) Avvio processi applicativi
# -------------------------------
echo "[Sim] Sensor Simulator…"
launch_in_terminal "python3 src/sensor_simulator.py"

echo "[Ingest] Ingestion Service…"
launch_in_terminal "python3 src/ingestion_service.py --kafka-bootstrap localhost:9094 --coordinator-url http://localhost:8000"

echo "[Anomaly] Anomaly Detector (con storico attivo)…"
launch_in_terminal "python3 src/anomaly_detector.py --kafka-bootstrap localhost:9094 --coordinator-url http://localhost:8000"

# -------------------------------
# 5) Webapp
# -------------------------------
if wait_for_tcp localhost 3000 60; then
  echo "[Web] Apro http://localhost:3000"
  if command -v open >/dev/null 2>&1; then
    open "http://localhost:3000"
  elif command -v xdg-open >/dev/null 2>&1; then
    xdg-open "http://localhost:3000"
  else
    echo "Apri manualmente: http://localhost:3000"
  fi
else
  echo "[Web] La webapp non risponde su :3000 (controlla i log del container webapp)."
fi

echo "=== EnergyGuard avviato con database storico attivo ==="
