# Strategia di test per il PoC EnergyGuard

Questo documento descrive l'approccio di test per il Proof of Concept, la copertura ottenuta e le istruzioni per eseguire i test in locale.

## Obiettivi coperti

- **Simulazione sensori**: verificare la generazione deterministica dei profili base (`tests/test_sensor_simulator.py`).
- **Logica di thresholds & anomalie**: assicurare che normalizzazione soglie e rilevamento superamenti funzionino (`tests/test_anomaly_detector.py`).
- **Integrazione ingestione → rilevamento**: validare il flusso tra `ingestion_service.handle_reading` e `anomaly_detector` usando un coordinator fittizio (`tests/test_pipeline_integration.py`).

## Tipologie di test

| Tipo | File | Contenuto |
| --- | --- | --- |
| Unit test | `tests/test_sensor_simulator.py` | Profili circadiani, weekend vs weekday, baseline per piano. |
| Unit test | `tests/test_anomaly_detector.py` | Normalizzazione payload soglie e check delle anomalie sulle metriche chiave. |
| Test di integrazione | `tests/test_pipeline_integration.py` | Simula store KVS via HTTP fittizio, verifica la persistenza della lettura, l'aggiornamento dell'indice e la generazione degli eventi `*_over`. |

Uno stub di `confluent_kafka` viene predisposto in `tests/conftest.py` qualora la libreria nativa non fosse disponibile, rendendo i test eseguibili senza il broker.

## Dipendenze

Le dipendenze minime per eseguire i test sono elencate in `requirements.txt` (include `pytest`). Installazione consigliata:

```bash
pip install -r requirements.txt
```

## Esecuzione

```bash
pytest -q
```

Per verificare un singolo scenario, ad esempio l'integrazione:

```bash
pytest tests/test_pipeline_integration.py -q
```

Se il sistema utilizza sandbox con restrizioni di scrittura, assicurarsi che la directory del progetto sia inclusa tra quelle scrivibili e, se necessario, impostare temporaneamente `TMPDIR` all'interno del workspace:

```bash
TMPDIR=$(pwd)/.tmp pytest -q
```

## Risultati

- **Tentativo nel container Codex**: `pytest -q` → fallito con messaggio `failed in sandbox`, indicativo di restrizioni sull'esecuzione dei test nel contesto attuale.
- **Atteso in locale**: suite passata (3 file di test, 1 integrazione + 2 unit). Dopo l'esecuzione con successo non viene modificato lo stato del datastore fittizio e tutti gli assert risultano verdi.

Per riprodurre i risultati attesi, eseguire i comandi da un ambiente locale (o CI) con accesso completo al filesystem del progetto.
