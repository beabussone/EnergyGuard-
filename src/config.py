"""Configurazione di default per i servizi locali EnergyGuard."""

from pydantic import BaseModel

class Settings(BaseModel):
    """Imposta endpoint di Kafka e Redis usati dagli script di servizio."""

    kafka_bootstrap: str = "localhost:9094"
    kafka_topic: str = "energyguard.readings"
    kafka_group_id: str = "energyguard-consumer"

    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_db: int = 0

    # intervallo di pubblicazione (secondi)
    publish_interval_seconds: int = 30

settings = Settings()
