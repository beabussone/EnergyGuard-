# EnergyGuard  
## Sistema Distribuito per il monitoraggio e l'allerta precoce di consumi energetici anomali

Spazio di lavoro per lo sviluppo del progetto del corso di **Architetture dei Sistemi Distribuiti** dell'Università Campus Bio-Medico di Roma.

Questo progetto affronta lo sviluppo di un sistema per monitorare i consumi energetici di un edificio (simulati), identificare anomalie e generare allerte precoci.

---

## Obiettivo

Costruire un'applicazione capace di leggere le misurazioni di sensori (simulati in questo caso) sui consumi energetici di un edificio e poter evidenziare casi di consumi anomali potenzialmente pericolosi.

---

## Requisiti e Installazione

Per eseguire l'applicazione, scaricare le corrette dipendenze tramite:

```bash
pip install -r requirements.txt
```

---

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
```
---

## Componenti

### Docker
Docker viene utilizzato per eseguire in container i servizi esterni di cui l’applicazione ha bisogno (Kafka e Redis), senza doverli installare manualmente sul sistema.  
Con **Docker Compose** è possibile avviare l’intero stack con un solo comando (`docker compose up -d`), garantendo:
- **Portabilità**: l’app gira in modo identico su qualunque macchina con Docker installato.
- **Isolamento**: i servizi vivono nei propri container senza interferire con l’ambiente locale.
- **Semplicità di setup**: niente configurazioni complicate, tutto pronto con il file `docker-compose.yml`.

---

### Kafka
Kafka rappresenta il cuore della comunicazione dell’applicazione, implementando il modello **publish/subscribe**.  
- Il **simulatore di sensori** (producer) pubblica letture su un *topic* (`energyguard.readings`).  
- I **consumer** (ingestor, archiver, anomaly detector) si iscrivono al topic e ricevono i dati.  

Caratteristiche principali:
- **Buffer e persistenza temporanea**: i messaggi rimangono disponibili per un certo tempo (retention).
- **Scalabilità**: più consumer possono leggere in parallelo senza interferenze.
- **Affidabilità**: ogni consumer mantiene il proprio offset e può riprendere da dove era rimasto.

---

### Redis
Redis viene utilizzato come **Key-Value Store in RAM** per la gestione dei dati in tempo reale.  
Il consumer *ingestor* salva in Redis:
- l’ultima lettura disponibile per ciascun piano;
- uno storico indicizzato temporalmente tramite Sorted Set.  

Vantaggi:
- **Velocità**: accesso sub-ms ai dati.
- **Analisi real-time**: query rapide per pattern di consumo e rilevamento anomalie.
- **Supporto a stream di eventi**: Redis può memorizzare e distribuire alert o notifiche.

---

### SQLite
SQLite viene utilizzato dall’*archiver* per archiviare lo **storico cumulativo** delle letture.  
Tutte le misurazioni sono salvate in un file `.db`, facilmente interrogabile tramite SQL, che consente:
- **Analisi retrospettive** sui consumi.
- **Reportistica** su intervalli temporali.
- **Portabilità** del database (un unico file spostabile su altre macchine).
