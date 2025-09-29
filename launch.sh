#!/bin/bash
set -e

echo "=== Avvio EnergyGuard ==="

# 1) Docker Compose (Kafka, KVS, Coordinator, Webapp)
echo "[Stack] Avvio Docker..."
docker compose up -d --build

# 2) Attesa per readiness dei container
echo "[Stack] Attendo 10 secondi per readiness..."
sleep 10

# 3) Avvio Sensor Simulator in background
echo "[Sim] Avvio Sensor Simulator..."
python src/sensor_simulator.py &

# 4) Avvio Ingestion Service in background
echo "[Ingest] Avvio Ingestion Service..."
python src/ingestion_service.py \
  --kafka-bootstrap localhost:9094 \
  --coordinator-url http://localhost:8000 &

# 5) Avvia Anomaly Detector
echo "[4/4] Avvio Anomaly Detector..."
gnome-terminal -- bash -c "python3 src/anomaly_detector.py --kafka-bootstrap localhost:9094 --coordinator-url http://localhost:8000; exec bash"


# 6) Apri webapp (se disponibile open/xdg-open)
echo "[Web] Apro http://localhost:3000"
if command -v open &> /dev/null; then
  open http://localhost:3000       # macOS
elif command -v xdg-open &> /dev/null; then
  xdg-open http://localhost:3000   # Linux
else
  echo "Apri manualmente: http://localhost:3000"
fi

# Mantieni script attivo per non far chiudere i processi
wait
