## EnergyGuard 
# Sistema Distribuito per il monitoraggio e l'allerta precoce di consumi energetici anomali
===========================================

Spazio di lavoro per lo sviluppo del progetto del corso di Architetture dei Sistemi Distribuiti dell'Università Campus Bio-Medico di Roma.

Questo progetto affronta lo sviluppo di un sistema per monitorare i consumi energetici di un edificio(simulati), identificare anomalie e generare allerte precoci
------------------------

## Obiettivo

Costruire un'applicazione capace di leggere le misurazioni di sensori (simulati in questo caso) sui consumi energetici di un edificio e poter evidenziare casi di consumi anomali potenzialmente pericolosi.
------------------------

## Requisiti e Installazione

Per eseguire l'applicazione, scaricare le corrette dipendenze tramite:

```bash
pip install -r requirements.txt
```
------------------------

## Funzionamento

Nella versione attuale, l'applicativo consente di avviare un publisher che genera ogni 30s misurazioni randomiche prese da vari sensori per ognuno dei tre piani dell'edificio che stiamo simulando. Queste misurazioni vengono raccolte tramite Redis che le deposita in un KVS apposito, disponibile in RAM. Oltre alla disponibilità delle risorse in RAM adesso è possibile accedere all'archivio storico delle letture dei sensori, che vengono salvate in un apposito file energyguard_history.db in maniera da poter fare un'analisi sui dati passati delle misurazioni.

------------------------

## Esecuzione

Per eseguire correttamente l'applicazione seguire i seguenti procedimenti:
- Avviare l'applicazione Docker e verificare che stia runnando.
- Porsi nella directory corretta del terminale ed eseguire:
```bash
docker-compose up -d
```
- Eseguire successivamente su terminali separati:
```bash
python src/sensor_simulator.py
python src/ingestion_service.py
python src/history_archiver.py
```
------------------------

## Caratteristiche dell’applicazione

L’applicazione **EnergyGuard** è stata progettata per simulare, raccogliere e analizzare i consumi energetici di un edificio. Le letture dei sensori vengono generate artificialmente tramite un **simulatore** che produce valori realistici di kWh, kW, corrente e tensione per tre piani dell’edificio. Queste letture sono pubblicate in tempo reale su un **topic Kafka** (pattern *publish/subscribe*), che funge da canale di comunicazione affidabile e scalabile: i **producer** inviano i dati sul topic, mentre diversi **consumer** li leggono indipendentemente per scopi diversi. In particolare, un consumer (*ingestor*) archivia i dati su **Redis**, dove sono disponibili quasi in tempo reale per consultazioni rapide e per l’individuazione di pattern di consumo; un altro consumer (*archiver*) salva invece lo storico completo in un database SQLite per analisi e report futuri. Kafka garantisce la persistenza temporanea e la distribuzione dei messaggi, mentre Redis, operando in RAM, consente accesso a bassa latenza ai dati più recenti, supportando così sia l’analisi real-time sia l’elaborazione di lungo periodo.

------------------------
### Architettura a blocchi

```text
          +-------------------+
          |   Sensor Simulator |
          | (valori kWh, kW,   |
          | corrente, tensione)|
          +---------+---------+
                    |
                    v
           =====================
           |      Kafka        |
           |   Topic: readings |
           =====================
             /              \
            v                v
+----------------+   +---------------------+
|   Ingestor     |   |     Archiver        |
| (Consumer)     |   | (Consumer)          |
|  → Redis (RAM) |   |  → SQLite (.db)     |
+----------------+   +---------------------+
        |                     |
        v                     v
  +-------------+      +------------------+
  | Redis KVS   |      | Historical DB    |
  | (real-time) |      | (analisi futura) |
  +-------------+      +------------------+