"""Lee configuracionPDF/estructuraPDF.json y de ahi deriva la forma del JSON de salida.

El JSON de configuracion es la unica fuente de verdad: si se agrega un campo ahi,
tanto el extractor de texto como el de OCR/IA lo incluyen sin tocar el codigo.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.config import ESTRUCTURA_JSON


class EstructuraPDF:
    """Envoltura tipada sobre estructuraPDF.json."""

    def __init__(self, ruta: Path = ESTRUCTURA_JSON) -> None:
        self.ruta = Path(ruta)
        with self.ruta.open(encoding="utf-8") as fh:
            self.data: dict[str, Any] = json.load(fh)

    # --- secciones del archivo de configuracion -------------------------------
    @property
    def archivo_origen(self) -> str:
        origenes = self.data.get("archivo_origen") or ["DESCONOCIDO"]
        return origenes[0]

    @property
    def _extraer(self) -> dict[str, Any]:
        return self.data.get("extraer", {})

    @property
    def buscar_seccion(self) -> dict[str, list[str]]:
        return self._extraer.get("buscar_seccion", {})

    @property
    def columnas_servicio(self) -> list[str]:
        return list(self._extraer.get("columnas_servicio", []))

    @property
    def columnas_consumo(self) -> list[Any]:
        return list(self._extraer.get("columnas_consumo", []))

    @property
    def importe(self) -> list[str]:
        return list(self._extraer.get("importe", []))

    # --- utilidades -----------------------------------------------------------
    @staticmethod
    def nombre_campo(campo: Any) -> str:
        """Un campo puede ser 'TARIFA' o {'lectura_actual': [...]}; regresa su nombre."""
        return next(iter(campo)) if isinstance(campo, dict) else campo

    @property
    def campos_consumo_simples(self) -> list[str]:
        return [c for c in self.columnas_consumo if not isinstance(c, dict)]

    @property
    def campos_consumo_anidados(self) -> dict[str, list[str]]:
        return {k: list(v) for c in self.columnas_consumo if isinstance(c, dict) for k, v in c.items()}

    @property
    def etiquetas_planas(self) -> list[str]:
        """Todas las etiquetas que en el recibo aparecen como 'ETIQUETA: valor'."""
        return self.columnas_servicio + self.campos_consumo_simples

    def plantilla(self) -> dict[str, Any]:
        """Esqueleto del JSON de salida con todos los campos en None."""
        seccion = {
            grupo: {campo: None for campo in campos}
            for grupo, campos in self.buscar_seccion.items()
        }
        consumo: dict[str, Any] = {campo: None for campo in self.campos_consumo_simples}
        for nombre, subcampos in self.campos_consumo_anidados.items():
            consumo[nombre] = {sub: None for sub in subcampos}
        return {
            "archivo_origen": self.archivo_origen,
            "buscar_seccion": seccion,
            "columnas_servicio": {campo: None for campo in self.columnas_servicio},
            "columnas_consumo": consumo,
            "importe": {campo: None for campo in self.importe},
        }

    def json_schema(self) -> dict[str, Any]:
        """JSON Schema equivalente, para la salida estructurada del modelo de vision."""

        def objeto(campos: list[str], tipos: str = "string") -> dict[str, Any]:
            return {
                "type": "object",
                "properties": {c: {"type": [tipos, "number", "boolean", "null"]} for c in campos},
                "required": list(campos),
                "additionalProperties": False,
            }

        consumo = objeto(self.campos_consumo_simples)
        for nombre, subcampos in self.campos_consumo_anidados.items():
            consumo["properties"][nombre] = objeto(subcampos)
            consumo["required"].append(nombre)

        return {
            "type": "object",
            "properties": {
                "archivo_origen": {"type": "string"},
                "buscar_seccion": {
                    "type": "object",
                    "properties": {g: objeto(c) for g, c in self.buscar_seccion.items()},
                    "required": list(self.buscar_seccion),
                    "additionalProperties": False,
                },
                "columnas_servicio": objeto(self.columnas_servicio),
                "columnas_consumo": consumo,
                "importe": objeto(self.importe),
            },
            "required": [
                "archivo_origen",
                "buscar_seccion",
                "columnas_servicio",
                "columnas_consumo",
                "importe",
            ],
            "additionalProperties": False,
        }


estructura_pdf = EstructuraPDF()
