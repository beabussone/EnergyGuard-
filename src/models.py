"""Modelli Pydantic condivisi fra i servizi EnergyGuard."""

from pydantic import BaseModel, Field, ConfigDict
from typing import Literal
from datetime import datetime

class Reading(BaseModel):
    """Valida una lettura di sensori trifase per i tre piani."""
    model_config = ConfigDict(extra="forbid")

    piano: Literal[1, 2, 3]
    sensore1_kwh: float = Field(gt=0)
    sensore2_kw: float = Field(ge=0)
    sensore3_corrente: float = Field(ge=0)  # Ampere
    sensore3_tensione: float = Field(ge=0)  # Volt
    timestamp: datetime