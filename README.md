# EnergyGuard  
## Sistema distribuito per il monitoraggio e l'allerta precoce di consumi energetici anomali

EnergyGuard è una piattaforma didattica sviluppata per il corso di **Architetture dei Sistemi Distribuiti** dell'Università Campus Bio-Medico di Roma. Il sistema simula la telemetria energetica di un edificio, la elabora tramite una pipeline Kafka → servizi Python e conserva gli stati su un **Key-Value Store distribuito** con consistent hashing, replica e cache LRU. Un servizio di *anomaly detection* produce allarmi che vengono pubblicati in tempo reale via **Server-Sent Events** e visualizzati da una webapp.

---

## Indice
- [Panoramica](#panoramica)
- [Architettura](#architettura)
- [Componenti principali](#componenti-principali)
- [Flusso dei dati](#flusso-dei-dati)
- [Avvio rapido (Docker Compose)](#avvio-rapido-docker-compose)
- [Esecuzione manuale dei servizi Python](#esecuzione-manuale-dei-servizi-python)
- [Interfacce e API](#interfacce-e-api)
- [Configurazione](#configurazione)
- [Struttura della repository](#struttura-della-repository)
- [Troubleshooting & suggerimenti](#troubleshooting--suggerimenti)

---

## Panoramica
- **Obiettivo**: monitorare i consumi dei tre piani di un edificio (simulati) e generare allerte quando vengono superate soglie configurabili.
- **Tecnologie chiave**: Kafka (messaggistica), FastAPI (microservizi), SQLite (persistenza shard), LRU cache in memoria, SSE (notifiche realtime), React/Vite (dashboard).
- **Pattern**: publish/subscribe per l’ingestione, consistent hashing per la distribuzione delle chiavi, replica configurabile, API REST uniformi.
- **Output**: dati consultabili quasi real-time via API, notifiche di anomalia persistite nel KVS e diffuse alla dashboard.
- **Console admin**: la dashboard permette ora di ritoccare le soglie piano per piano, oltre ai valori di fallback.

---

## Architettura

```text
          +-------------------+
          |   Sensor Simulator|
          | (?kWh, kW, A, V)  |
          +---------+---------+
                    |
                    v
           =====================
           |      Kafka        |
           | Topic: readings   |
           =====================
                    |
                    v
           +-------------------+
           | Ingestion Service |
           | (Kafka consumer)  |
           +---------+---------+
                     |
         Consistent-Hash HTTP API
                     |
           +-------------------+
           |   Coordinator     |
           |  (FastAPI proxy)  |
           +---------+---------+
                     |
        ---------------------------------
        |               |               |
        v               v               v
   +---------+     +---------+     +---------+
   |  KVS 1  |     |  KVS 2  |     |  KVS 3  |
   | FastAPI |     | FastAPI |     | FastAPI |
   | SQLite  |     | SQLite  |     | SQLite  |
   +----+----+     +----+----+     +----+----+
        |               |               |
        +--------+------+---------------+
                 |
                 v
         +--------------------+        +------------------------------+
         | Anomaly Detector   |<------>| Historical Stats Cache & DB |
         | + SSE Broadcast    |        | (historical_stats.py + SQLite)|
         +---------+----------+        +---------------+--------------+
                   |                                   ^
                   |                                   |
                   |                    +--------------+----------------+
                   |                    | populate_historical_db.py     |
                   |                    | (dataset sintetico offline)   |
                   |                    +-------------------------------+
                   v
           +-------+--------+
           |   Web Dashboard |
           | (React + SSE)   |
           +-----------------+
```

---

## Componenti principali

### Sensor Simulator (`src/sensor_simulator.py`)
- Producer Kafka che genera ogni `--interval-seconds` (default 30 s) misurazioni realistiche per i piani 1, 2, 3.
- Può iniettare anomalie verso l’alto (`--anomaly-rate`, default 15%).
- Payload JSON inviato al topic `energyguard.readings`:
  ```json
  {
    "piano": 1,
    "sensore1_kwh": 1000.315,
    "sensore2_kw": 6.2,
    "sensore3_corrente": 16.8,
    "sensore3_tensione": 231.5,
    "timestamp": "2024-05-19T11:03:12.123456+00:00"
  }
  ```

### Kafka
Kafka è una piattaforma di streaming distribuito basata su un commit log append-only: i producer scrivono messaggi ordinati su *topic* partizionati e i consumer leggono gli eventi mantenendo il proprio offset. Ogni messaggio resta disponibile per il periodo di retention, così da permettere riletture e rielaborazioni asincrone.

Caratteristiche salienti:
- **Durabilità**: i messaggi vengono replicati su più broker (in cluster) e conservati su disco; gli offset sono gestiti dai consumer group.
- **Scalabilità**: aumentando il numero di broker o di partizioni, il throughput cresce linearmente e diversi consumer possono lavorare in parallelo.
- **Garantisce ordering per partizione**: tutti i messaggi con la stessa chiave finiscono nella medesima partizione e vengono letti in ordine.

Nel nostro progetto il servizio `kafka` del `docker-compose` avvia un broker Bitnami configurato per accettare connessioni dall'host (`localhost:9094`). Il topic `energyguard.readings` viene creato automaticamente, è attualmente composto da una sola partizione (sufficiente per il carico simulato) ma può essere espanso per aumentare concorrenza e resilienza aggiungendo broker e partizioni nel file di compose.

### Ingestion Service (`src/ingestion_service.py`)
- Consumer Kafka (group `energyguard-ingestor-kvs`).
- Per ogni messaggio:
  - Scrive la misura storica con chiave `energy:p{piano}:{timestamp}`.
  - Aggiorna il documento `energy:p{piano}:latest`.
  - Mantiene un indice compatto `energy:p{piano}:index` con gli ultimi N timestamp (`--index-size`, default 500).
- CLI principale:
  ```bash
  python src/ingestion_service.py --kafka-bootstrap localhost:9094 --coordinator-url http://localhost:8000
  ```

### KVS distribuito
- **Coordinator (`src/coordinator.py`)**
  - *Ruolo*: servizio FastAPI centrale che mantiene il consistent hash ring, calcola le repliche e fa da proxy HTTP verso gli shard per tutte le operazioni `PUT/GET/DELETE /key/{chiave}`.
  - *Chi lo contatta*: ingestion service, anomaly detector, webapp (sezione admin) e qualunque client esterno usano le sue API REST e l'endpoint SSE per leggere dati e ricevere alert.
  - *A chi parla*: per ogni richiesta inoltra chiamate HTTP asincrone agli shard registrati, emette eventi SSE verso i subscriber e salva le soglie su file locale (`THRESHOLDS_PATH`).
  - *Funzioni aggiuntive*: espone la gestione dinamica dello sharding (`/sharding/*`), permette di impostare il fattore di replica (`REPLICATION_FACTOR`/`REPLICATION_RATIO`), protegge gli endpoint admin con bearer token e accetta broadcast autenticati degli alert (`/internal/broadcast`).
- **Shard KVS (`src/kvs_limited_cache.py`)**
  - Microservizi FastAPI con storage SQLite (`/data/shard.db`) + cache LRU (`MAX_CACHE_ITEMS`).
  - Espongono `/key/{chiave}` per lettura/scrittura, `/stats` e `/health`.
  - TTL opzionale per chiavi; la cache mantiene calde le chiavi più consultate.

### populate_historical_db (`src/populate_historical_db.py`)
- Script CLI che popola il database storico SQLite con 60 giorni di medie semiorarie sintetiche per ogni piano.
- Genera profili differenti usando rumore gaussiano e pattern dipendenti da giorno/ora cosi da fornire baseline realistiche agli outlier.
- Variabili ambiente come `HIST_BASE_KW_FLOOR*` o `HIST_KW_NOISE_STD` consentono di regolare il dataset prima di lanciare il detector.

### historical_stats (`src/historical_stats.py`)
- Modulo di supporto condiviso da popolazione storica e detector per persistere e rileggere le statistiche.
- Espone helper (`weekday_index`, `halfhour_index`, `ensure_db`, `insert_bucket_average`) e la cache `StatsCache` con media/deviazione e conteggi per metrica.
- Il detector ricarica periodicamente questi valori per calcolare gli z-score alla base degli alert `pattern_outlier_*`.

### Anomaly Detector (`src/anomaly_detector.py`)
- Polling sul coordinator (`/key/energy:p{n}:latest`) con frequenza configurabile (`--poll-secs`).
- Confronta le misure con le soglie per piano (default + override) recuperate da `/admin/thresholds` e genera eventi `kw_over`, `amp_over`, `volt_over`, `dkwh_over` insieme alle anomalie `pattern_outlier_*`.
- Analizza gli andamenti storici per giorno della settimana e slot da 30 minuti segnalando gli scostamenti con eventi `pattern_outlier_*`.
- Salva gli alert nel KVS (`alerts:{piano}:{epoch}`) e li inoltra a `/internal/broadcast` per l'SSE.
- Parametri come `INTERNAL_BROADCAST_TOKEN`, `EDGE_COOLDOWN_SECS`, `DETECTOR_POLL_SECS` sono impostabili da environment.

### Web Dashboard (`webapp/`)
- Applicazione React/Vite che mostra letture correnti, storico notifiche e consente di aggiornare le soglie (default e per piano).
- Consuma:
  - `GET /notifications`, `POST /notifications/mark_read`, `GET /notifications/unread_count`
  - `GET/PUT /admin/thresholds`
  - `GET /key/energy:p{n}:latest`
  - SSE `GET /anomalies/stream`
- La sezione "Dashboard Live" consente di scegliere quante delle letture più recenti visualizzare (1, 3, 5 o 10 campioni); ogni card per piano evidenzia automaticamente in rosso i valori oltre soglia.
- Per ricostruire l'elenco delle ultime letture la webapp interroga l'indice `energy:p{n}:index` (generato dall'ingestor), che contiene un array ordinato degli ultimi timestamp disponibili per ogni piano, e scarica i relativi documenti storici dal KVS.
- Richiede autenticazione bearer tramite il **Login Service** (`webapp/login_service`), che valida credenziali statiche e restituisce `{"token": ADMIN_TOKEN}`.

#### Login amministratore
La webapp mostra un form di autenticazione: le credenziali (default `admin` / `admin`) vengono inviate al servizio FastAPI `login`, che verifica i valori rispetto alle variabili `ADMIN_USER` e `ADMIN_PASS`. Se la verifica va a buon fine il servizio risponde con un JSON `{"token": ADMIN_TOKEN}`.

Il client salva il token (ad esempio in `localStorage`) e lo allega come header `Authorization: Bearer <token>` in tutte le chiamate verso il coordinator. Se il token non coincide con `ADMIN_TOKEN`, gli endpoint protetti rispondono con `401/403`. Per cambiare credenziali o token è sufficiente aggiornare le variabili d'ambiente del login service e del coordinator mantenendole coerenti.

---

## Flusso dei dati
1. Il simulatore pubblica le misure su Kafka (`energyguard.readings`).
2. L’ingestor consuma le misure e le scrive nel KVS distribuito tramite il coordinator.
3. Gli shard salvano i dati su SQLite e li mantengono in cache in RAM.
4. L’anomaly detector legge i valori più recenti, confronta con le soglie e genera alert quando necessario.
5. Gli alert vengono salvati nel KVS e trasmessi via SSE, rendendoli disponibili alla webapp e ad altri client.

---

## Avvio rapido (Docker Compose)
Prerequisiti: Docker Engine + Docker Compose plugin attivi.

```bash
# dalla root del progetto
docker compose up -d
```

Servizi principali (rete `energyguard`):
- `kafka` → broker su `localhost:9094`.
- `coordinator` → API su `http://localhost:8000`.
- `kv1`, `kv2`, `kv3` → shard con database persistenti in `./data/kv{n}`.
- `ingestion` → consumer Kafka che popola il KVS tramite il coordinator.
- `anomaly_detector` → analizza le ultime letture e invia notifiche SSE.
- `webapp` → dashboard su `http://localhost:3000`.
- `login` → servizio auth su `http://localhost:7001`.
- Job `register` registra automaticamente i tre shard nel ring.

Comandi utili:
```bash
docker compose logs -f coordinator
curl http://localhost:8000/health
curl http://localhost:8000/sharding/info
```

Per fermare: `docker compose down` (aggiungi `-v` per eliminare i volumi).

---

## Esecuzione manuale dei servizi Python
Prerequisiti: Python 3.11+, Kafka attivo (puoi usare solo il container `kafka` del compose).

> Nota: con `docker compose up` gli ingressi `ingestion` e `anomaly_detector` sono già in esecuzione; i passaggi seguenti servono per un avvio manuale durante lo sviluppo locale.

1. Installare le dipendenze:
   ```bash
   pip install -r requirements.txt
   ```
2. Avviare gli shard KVS (ripeti per ogni porta):
   ```bash
   SQLITE_PATH=./data/kv1/shard.db uvicorn src.kvs_limited_cache:app --host 0.0.0.0 --port 8101 --reload
   ```
   Assicurati che la cartella `./data/kv{n}` esista e sia scrivibile.
3. Avviare il coordinator:
   ```bash
   uvicorn src.coordinator:app --host 0.0.0.0 --port 8000
   ```
4. Registrare gli shard nel ring (ripeti per ogni nodo):
   ```bash
   curl -X POST http://localhost:8000/sharding/add-node -H "Content-Type: application/json" -d '{"node": "http://localhost:8101"}'
   ```
5. Avviare l’ingestor:
   ```bash
   python src/ingestion_service.py --kafka-bootstrap localhost:9094 --coordinator-url http://localhost:8000
   ```
6. Avviare il simulatore:
   ```bash
   python src/sensor_simulator.py --kafka-bootstrap localhost:9094
   ```
7. Avviare l’anomaly detector:
   ```bash
   python src/anomaly_detector.py --coordinator-url http://localhost:8000
   ```
8. (Opzionale) Avviare la webapp:
   ```bash
   cd webapp
   npm install
   npm run dev
   ```

---

## Interfacce e API

Questa sezione raccoglie le interfacce pubbliche esposte dai servizi principali di EnergyGuard. Gli endpoint REST appartengono quasi interamente al coordinator, che funge da gateway verso il KVS distribuito e verso le funzionalità amministrative. Gli shard e gli altri servizi espongono API più limitate, utili per health check e diagnostica. Ulteriori convenzioni (come i prefissi delle chiavi) ti aiutano a navigare i dati salvati nel KVS.

### Endpoint Coordinator
- `GET /health` – stato servizio.
- `GET /sharding/info` – nodi attivi, replica, virtual nodes.
- `POST /sharding/add-node` – registra uno shard (`{"node": "http://kv1:8100"}`).
- `POST /sharding/remove-node` – rimuove uno shard.
- `POST /sharding/reconfigure` – aggiorna `replication_factor` (body `{"replication_factor": 2}`).
- `PUT /key/{chiave}` – scrive una chiave (body `{"value": {...}, "ttl": opzionale}`).
- `GET /key/{chiave}` – legge da una delle repliche (404 se assente).
- `DELETE /key/{chiave}` – cancella su tutte le repliche.
- `GET /admin/thresholds` – restituisce un payload `{"default": {...}, "per_floor": {"1": {...}}}` (soglie di base + override per piano).
- `PUT /admin/thresholds` – aggiorna default e override (richiede header `Authorization: Bearer <ADMIN_TOKEN>` e accetta la stessa struttura).
- `POST /internal/broadcast` – ingestione alert (richiede `_token` se `INTERNAL_BROADCAST_TOKEN` è impostato).
- `GET /anomalies/stream` – SSE realtime (eventi `anomaly`).
- `GET /notifications` – storico notifiche recenti (`limit`, `since_ts`).
- `GET /notifications/unread_count` – contatore non letti.
- `POST /notifications/mark_read` – marca come lette (`{"ids": [...]}`).

### Endpoint KVS shard
- `GET /health` – verifica DB SQLite.
- `GET /stats` – statistiche (chiavi in DB, cache attiva).
- `PUT/GET/DELETE /key/{chiave}` – operazioni dirette sullo shard (usate via coordinator).

### Convenzioni chiavi applicative
- `energy:p{n}:{timestamp}` – misura storica per piano `n`.
- `energy:p{n}:latest` – ultima misura disponibile per il piano.
- `energy:p{n}:index` – lista ordinata degli ultimi timestamp.
- `alerts:{piano}:{epoch}` – alert generati dal detector.
- Chiavi personalizzate supportano TTL opzionale (passando `ttl` nel body del `PUT`).

### Notifiche & streaming
- Con `curl`:
  ```bash
  curl -N http://localhost:8000/anomalies/stream
  ```
- Con JavaScript:
  ```js
  const es = new EventSource("http://localhost:8000/anomalies/stream");
  es.addEventListener("anomaly", (event) => {
    const payload = JSON.parse(event.data);
    console.log(payload);
  });
  ```

#### Perché SSE invece di WebSocket?
Gli **SSE (Server-Sent Events)** forniscono un canale HTTP one-way in cui il server invia messaggi testuali ai client registrati. Rispetto alle **WebSocket**:
- non richiedono handshake aggiuntivi: usano una normale richiesta HTTP mantenuta aperta e sono supportati nativamente da browser (`EventSource`).
- includono il retry automatico del browser in caso di disconnessione, senza dover scrivere codice di riconnessione personalizzato.
- sono ideali per flussi broadcast dove il client non deve inviare messaggi al server, come la coda di alert generata dal detector.

Le WebSocket sono bidirezionali e più adatte a scenari di chat o controllo interattivo; nel nostro caso l'unica esigenza è consegnare notifiche in tempo reale verso più client, quindi SSE offre una soluzione più semplice da gestire lato Coordinator e frontend.

---

## Configurazione

Le tabelle seguenti elencano le principali variabili d'ambiente e flag che controllano il comportamento di ciascun servizio. Puoi impostarle nei file `docker-compose.yml`, negli script di avvio oppure direttamente dal terminale prima di lanciare i processi (`export VAR=valore`). I parametri CLI elencati accanto ai servizi Python sovrascrivono le impostazioni di default quando il servizio viene avviato manualmente.

### Coordinator (`src/coordinator.py`)
- `PORT` (default 8000) – porta HTTP.
- `REPLICATION_FACTOR` – numero repliche (override diretto).
- `REPLICATION_RATIO` – alternativa per calcolare `rf` in funzione dei nodi.
- `VIRTUAL_NODES` – virtual nodes per l’hash ring.
- `BOOTSTRAP_NODES` – lista di shard da registrare all’avvio.
- `ADMIN_TOKEN` – bearer richiesto per endpoint amministrativi.
- `THRESHOLDS_PATH` – percorso file JSON persistente per le soglie.
- `INTERNAL_BROADCAST_TOKEN` – token condiviso con detector/webapp per `POST /internal/broadcast`.
- `NOTIFY_BUFFER_SIZE` – numero massimo di notifiche mantenute in memoria.
- `NOTIFY_SUPPRESS_SECS` – finestra anti-duplicato per alert identici.

### Shard KVS (`src/kvs_limited_cache.py`)
- `PORT` (default 8100) – porta HTTP.
- `SQLITE_PATH` – percorso DB SQLite (es. `/data/shard.db`).
- `MAX_CACHE_ITEMS` – dimensione massima cache LRU (0 per disabilitarla).

### Sensor Simulator
- `--kafka-bootstrap` – host:porta broker (default `localhost:9094`).
- `--topic` – topic target.
- `--interval-seconds` – intervallo di pubblicazione.
- `--anomaly-rate` – probabilità di iniettare uno spike per piano.

### Ingestion Service
- `--kafka-bootstrap`, `--topic`, `--group-id`.
- `--coordinator-url` – endpoint REST del coordinator.
- `--index-size` – lunghezza max lista timestamp per piano.

### Anomaly Detector
- `--coordinator-url`, `--floors`, `--poll-secs`, `--dry-run`.
- Env: `INTERNAL_BROADCAST_TOKEN`, `DETECTOR_POLL_SECS`, `EDGE_COOLDOWN_SECS`.

### Login Service (`webapp/login_service`)
- `PORT` – porta esposta (default 7000/7001 in host).
- `ADMIN_TOKEN` – token restituito al login, deve combaciare con quello del coordinator.
- `ADMIN_USER` / `ADMIN_PASS` – credenziali richieste dalla webapp.

### Webapp (`webapp/`)
- Variabili Vite: `VITE_COORDINATOR_URL`, `VITE_LOGIN_URL`.
- Usa axios + EventSource; assicurarsi che il coordinator esponga CORS per gli host di sviluppo.

---

## Struttura della repository
- `src/` – servizi Python (simulatore, ingestion, coordinator, anomaly detector, shard KVS).
- `webapp/` – dashboard React + login service FastAPI.
- `data/` – directory montate nei container per la persistenza SQLite.
- `docker-compose.yml` – definizione completa dei servizi.
- `Dockerfile.coordinator` / `Dockerfile.kvstore` / `Dockerfile.ingestion` / `Dockerfile.anomaly` – immagini custom per i microservizi Python.
- `launch_os.sh`, `launch.bat` – script di avvio rapidi (facoltativi).

---

## Troubleshooting & suggerimenti
- **Kafka non raggiungibile**: controlla `docker compose logs kafka` e che il client usi `localhost:9094`.
- **Coordinator 503 “Nessun nodo KVS registrato”**: assicurati che gli shard siano in esecuzione e registrati (`/sharding/add-node`).
- **SSE 403**: verifica `INTERNAL_BROADCAST_TOKEN` coerente tra coordinator, anomaly detector e webapp.
- **Alert duplicati**: aumenta `NOTIFY_SUPPRESS_SECS` o il cooldown del detector.
- **Reset dati**: arresta i servizi e cancella le cartelle `data/kv*` (perdi lo storico).
- **Sviluppo locale**: usa `uvicorn --reload` e `npm run dev` per hot-reload; ricordati di impostare correttamente le variabili `.env` per CORS e token.

---

Felici esperimenti con EnergyGuard!
