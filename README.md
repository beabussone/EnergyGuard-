## EnergyGuard 
# Sistema Distribuito per il monitoraggio e l'allerta precoce di consumi energetici anomali
===========================================

Spazio di lavoro per lo sviluppo del progetto del corso di Architetture dei Sistemi Distribuiti dell'Università Campus Bio-Medico di Roma.

Questo progetto affronta lo sviluppo di un sistema per monitorare i consumi energetici di un edificio(simulati), identificare anomalie e generare allerte precoci
------------------------

## Requisiti e Installazione

Per eseguire l'applicazione, scaricare le corrette dipendenze tramite:

```bash
pip install -r requirements.txt
```
------------------------

## Funzionamento

Nella versione attuale, l'applicativo consente di avviare un publisher che genera ogni 30s misurazioni randomiche prese da vari sensori per ogni piano dell'edificio. Queste misurazioni vengono raccolte da un server che le deposita in un KVS apposito. Seguiranno sviluppi.

------------------------

## Esecuzione

Per eseguire correttamente l'applicazione seguire i seguenti procedimenti:
- Avviare l'applicazione Docker e verificare che stia runnando.
- Porsi nella directory corretta del terminale ed eseguire:
```bash
docker-compose up -d
```
- Eseguire successivamente su due terminali separati:
```bash
python src/sensor_simulator.py
python src/ingestion_service.py
```
