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

Nella versione attuale, l'applicativo consente di avviare un publisher che genera ogni 30s misurazioni randomiche prese da vari sensori per ognuno dei tre piani dell'edificio che stiamo simulando. Queste misurazioni vengono raccolte dall'*ingestor* che si occupa di salvarli in un nuovo kvs con consistent hashing e sharding.

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
python src/ingestion_service.py --kafka-bootstrap localhost:9094 --coordinator-url http://localhost:8000
```
------------------------

## Caratteristiche dell’applicazione

L’applicazione **EnergyGuard** è stata progettata per simulare, raccogliere e analizzare i consumi energetici di un edificio. Le letture dei sensori vengono generate artificialmente tramite un **simulatore** che produce valori realistici di kWh, kW, corrente e tensione per tre piani dell’edificio. Queste letture sono pubblicate in tempo reale su un **topic Kafka** (pattern *publish/subscribe*), che funge da canale di comunicazione affidabile e scalabile: i **producer** inviano i dati sul topic, mentre diversi **consumer** li leggono indipendentemente per scopi diversi. In particolare, un consumer (*ingestor*) legge i dati sul topic e si occupa di salvarli in un KVS dedicato, dove sono disponibili quasi in tempo reale per consultazioni rapide e per l’individuazione di pattern di consumo. Kafka garantisce la persistenza temporanea e la distribuzione dei messaggi, mentre Redis, operando in RAM, consente accesso a bassa latenza ai dati più recenti, supportando così sia l’analisi real-time sia l’elaborazione di lungo periodo.

------------------------
### Architettura a blocchi

```text
          +-------------------+
          |   Sensor Simulator|
          | (valori kWh, kW,  |
          | corrente, tensione)|
          +---------+---------+
                    |
                    v
           =====================
           |      Kafka        |
           |   Topic: readings |
           =====================
                    |
                    v
           +-------------------+
           |   Ingestor        |
           | (Consumer Kafka)  |
           +---------+---------+
                     |
                     v
           +-------------------+
           |   Coordinator     |
           | (Consistent Hash) |
           +---------+---------+
                     |
        ---------------------------------
        |               |               |
        v               v               v
   +---------+     +---------+     +---------+
   |  KVS 1  |     |  KVS 2  |     |  KVS 3  |
   | SQLite  |     | SQLite  |     | SQLite  |
   +---------+     +---------+     +---------+
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

### KVS Distribuito (Coordinator + Shards)
Il sistema Key-Value Store è stato implementato ad-hoc per garantire **scalabilità, resilienza e persistenza**.  
È composto da due parti principali:

#### 1. Coordinator
- Espone un’API REST centrale (`/key/{chiave}`, `/health`, `/sharding/info`).  
- Mantiene un **consistent hash ring**, una struttura logica che distribuisce le chiavi tra i nodi in maniera bilanciata.  
- Gestisce la **replica**: ogni chiave viene salvata su più nodi in base al `REPLICATION_FACTOR` (es. 2 → ogni chiave scritta su due nodi distinti).  
- Si occupa di inoltrare le richieste di **PUT/GET/DELETE** al nodo o ai nodi corretti.

#### 2. Nodi KVS (Shard)
- Ogni nodo KVS è un **microservizio FastAPI** eseguito in container Docker separato.  
- Ogni shard espone endpoint REST (`/health`, `/stats`, `/key/{chiave}`) e mantiene i dati localmente.  
- **Persistenza**: i dati vengono salvati in un **database SQLite** locale (`.db`) montato su volume, così che non vadano persi anche se il container viene riavviato.  
- **Cache interna**: ogni nodo utilizza una **LRU Cache** (Least Recently Used) per mantenere in memoria le chiavi più utilizzate e ridurre la latenza.  
- **Indici speciali**: oltre alla lettura puntuale di una chiave, il sistema mantiene chiavi logiche come:
  - `energy:p{n}:latest` → ultimo valore per un piano.
  - `energy:p{n}:index` → lista dei timestamp disponibili per quel piano.

---

### Flusso Operativo del KVS
1. **Ingestione**: l’ingestion service legge i dati da Kafka.  
2. **Routing**: il coordinator calcola l’hash della chiave (es. `energy:p1:2025-09-23T15:28:32Z`) e decide su quale nodo memorizzarla usando il **consistent hash ring**.  
3. **Replica**: se il `REPLICATION_FACTOR=2`, la stessa chiave viene scritta su due nodi diversi.  
4. **Persistenza locale**: ogni nodo salva la chiave sia su SQLite sia nella cache LRU.  
5. **Query**: le richieste di lettura passano dal coordinator, che interroga i nodi corretti. Grazie alla cache, i dati più recenti sono immediatamente disponibili in RAM.  

---

### Vantaggi del KVS distribuito
- **Scalabilità**: aggiungendo nuovi nodi al ring, le chiavi vengono ridistribuite bilanciando il carico.  
- **Resilienza**: grazie alla replica, un nodo che si guasta non comporta perdita dei dati.  
- **Performance**: la cache in RAM accelera l’accesso ai dati più usati.  
- **Persistenza**: i database SQLite montati su volume garantiscono che i dati non vadano persi.  
- **API uniforme**: tutti gli shard rispondono con la stessa interfaccia, rendendo il sistema trasparente al client.

---