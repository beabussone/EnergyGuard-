#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "=== Avvio EnergyGuard ==="
cd "$REPO_DIR"

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
  # prova gnome-terminal, poi xterm se presente
  local cmd="$1"
  if command -v gnome-terminal >/dev/null 2>&1; then
    gnome-terminal -- bash -lc "cd '$REPO_DIR'; $cmd; exec bash"
  elif command -v xterm >/dev/null 2>&1; then
    xterm -e "bash -lc \"cd '$REPO_DIR'; $cmd; exec bash\""
  else
    # Fallback: in background nella shell corrente
    echo "[Warn] Nessun terminale grafico trovato. Eseguo in background: $cmd"
    bash -lc "cd '$REPO_DIR'; $cmd" &
  fi
}

launch_in_terminal() {
  local cmd="$1"
  if [[ "$OSTYPE" == "darwin"* ]]; then
    open_new_terminal_mac "$cmd"
  else
    open_new_terminal_linux "$cmd"
  fi
}

# -------------------------------
# 1) Docker Compose
# -------------------------------
echo "[Stack] Avvio Docker (build+up)…"
docker compose up -d --build

# -------------------------------
# 2) Readiness servizi base
#    (Kafka su 9094, Coordinator su 8000)
# -------------------------------
wait_for_tcp localhost 9094 60 || true
wait_for_http "http://localhost:8000/health" 60 || true

# -------------------------------
# 3) Avvio processi app in nuove finestre
# -------------------------------
echo "[Sim] Sensor Simulator…"
launch_in_terminal "python3 src/sensor_simulator.py"

echo "[Ingest] Ingestion Service…"
launch_in_terminal "python3 src/ingestion_service.py --kafka-bootstrap localhost:9094 --coordinator-url http://localhost:8000"

echo "[Anomaly] Anomaly Detector…"
launch_in_terminal "python3 src/anomaly_detector.py --kafka-bootstrap localhost:9094 --coordinator-url http://localhost:8000"

# -------------------------------
# 4) Apri webapp quando pronta
# -------------------------------
# Se la webapp è dockerizzata su 3000, aspetta e poi apri
if wait_for_tcp localhost 3000 60; then
  echo "[Web] Apro http://localhost:3000"
  if command -v open >/dev/null 2>&1; then
    open "http://localhost:3000"   # macOS
  elif command -v xdg-open >/dev/null 2>&1; then
    xdg-open "http://localhost:3000"  # Linux
  else
    echo "Apri manualmente: http://localhost:3000"
  fi
else
  echo "[Web] La webapp non risponde su :3000 (controlla i log del container webapp)."
fi

echo "=== EnergyGuard avviato. Puoi chiudere questa finestra. ==="