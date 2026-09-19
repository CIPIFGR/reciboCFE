"""Carga de configuración desde .env (única fuente de configuración del proyecto)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_PDF_DIR = BASE_DIR / "configuracionPDF"
ESTRUCTURA_JSON = CONFIG_PDF_DIR / "estructuraPDF.json"

load_dotenv(BASE_DIR / ".env")


@dataclass(frozen=True)
class Settings:
    """Valores del .env con defaults seguros."""

    db_connection: str = os.getenv("DB_CONNECTION", "sqlite")
    db_database: str = os.getenv("DB_DATABASE", "Base Datos/database.sqlite")
    llm_base_url: str = os.getenv("LLM_BASE_URL", "http://localhost:1234/v1")
    llm_model: str = os.getenv("LLM_MODEL", "qwen2.5-coder-14b-instruct")
    llm_vision_model: str = os.getenv("LLM_VISION_MODEL", "qwen/qwen3.8-27b")
    llm_timeout: float = float(os.getenv("LLM_TIMEOUT", "900"))
    # Segunda pasada: un modelo mas grande relee solo los campos dudosos del primero.
    # Vacio = sin segunda pasada.
    llm_revision_model: str = os.getenv("LLM_REVISION_MODEL", "")
    # Campos que el modelo de vision lee mal de forma sistematica y siempre se releen,
    # separados por ';'. Nombre tal como en estructuraPDF.json ('calle', 'TOTAL A PAGAR:')
    # o con punto para los anidados ('lectura_actual.valor').
    llm_revision_campos: tuple[str, ...] = tuple(
        c.strip() for c in os.getenv("LLM_REVISION_CAMPOS", "").split(";") if c.strip()
    )

    @property
    def db_path(self) -> Path:
        ruta = Path(self.db_database)
        return ruta if ruta.is_absolute() else BASE_DIR / ruta


settings = Settings()
