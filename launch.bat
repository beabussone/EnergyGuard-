@echo off
setlocal enabledelayedexpansion

echo === Avvio EnergyGuard (Windows) ===

:: Imposta la directory di lavoro come quella del file
cd /d "%~dp0"

:: -------------------------------
:: 1) Verifica/creazione DB storico (no reset)
:: -------------------------------
set "HIST_DB_PATH=%CD%\data\historical_readings.db"
set "HISTORICAL_DB_PATH=%HIST_DB_PATH%"

if not exist "%HIST_DB_PATH%" (
    echo [DB] Database storico non trovato. Generazione iniziale...
    python src\populate_historical_db.py
    if %errorlevel% neq 0 (
        echo [ERRORE] Impossibile generare il database storico.
        exit /b 1
    )
) else (
    echo [DB] Database storico esistente rilevato: "%HIST_DB_PATH%". Nessuna rigenerazione.
)

:: -------------------------------
:: 2) Avvio stack Docker
:: -------------------------------

echo [Stack] Avvio Docker Compose...
docker compose up -d --build
if %errorlevel% neq 0 (
    echo [ERRORE] Fallito l'avvio dei container Docker.
    exit /b 1
)

:: -------------------------------
:: 3) Attesa readiness dei servizi principali
:: -------------------------------

echo [Wait] Attendo che Kafka e Coordinator siano attivi...
powershell -NoProfile -Command ^
  "$timeout = (Get-Date).AddSeconds(60);" ^
  "do {" ^
  "  Start-Sleep -Seconds 2;" ^
  "  $ok1 = Test-NetConnection -ComputerName localhost -Port 9094 -WarningAction SilentlyContinue;" ^
  "  try {" ^
  "    $response = Invoke-WebRequest -Uri http://localhost:8000/health -UseBasicParsing;" ^
  "    $ok2 = $response.StatusCode -eq 200;" ^
  "  } catch {" ^
  "    $ok2 = $false;" ^
  "  }" ^
  "} until (($ok1.TcpTestSucceeded -and $ok2) -or (Get-Date) -gt $timeout);" ^
  "if (-not ($ok1.TcpTestSucceeded -and $ok2)) { Write-Host '[ERRORE] Kafka o Coordinator non raggiungibili entro 60s'; exit 1 } else { Write-Host '[OK] Servizi pronti' }"

if %errorlevel% neq 0 (
    echo [ERRORE] Kafka o Coordinator non pronti.
    exit /b 1
)

:: -------------------------------
:: 4) Verifica DB storico
:: -------------------------------
if exist "%HIST_DB_PATH%" (
    echo [DB] Database storico pronto in "%HIST_DB_PATH%".
) else (
    echo [ERRORE] Database storico mancante. Controlla i log di populate_historical_db.py.
    exit /b 1
)

:: -------------------------------
:: 5) Avvio dei servizi applicativi
:: -------------------------------

echo [Sim] Avvio Sensor Simulator...
start cmd /k "cd src && python sensor_simulator.py"

echo [Ingest] Avvio Ingestion Service...
start cmd /k "cd src && python ingestion_service.py --kafka-bootstrap localhost:9094 --coordinator-url http://localhost:8000"

echo [Anomaly] Avvio Anomaly Detector (con storico attivo)...
start cmd /k "cd src && python anomaly_detector.py --kafka-bootstrap localhost:9094 --coordinator-url http://localhost:8000"

:: -------------------------------
:: 6) Apertura Webapp
:: -------------------------------

echo [Web] Apro http://localhost:3000
start "" "http://localhost:3000"

echo === EnergyGuard avviato con database storico attivo ===
pause
