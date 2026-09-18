"""Extraccion de recibos CFE escaneados (sin capa de texto).

La pagina se rasteriza con pypdfium2 y se manda al modelo de vision que corre en
LM Studio (LLM_VISION_MODEL del .env). El modelo devuelve el mismo JSON que produce
el extractor de texto, porque el esquema sale de estructuraPDF.json.
"""
from __future__ import annotations

import base64
import io
import json
import re
from pathlib import Path
from typing import Any, BinaryIO

import pypdfium2 as pdfium

from app.Service.estructura_service import EstructuraPDF, estructura_pdf
from app.Service.llm_client import LMStudioClient, LMStudioError, lm_studio_client

ESCALA_RENDER = 2.5  # ~180 dpi sobre una hoja carta: suficiente para el OCR del modelo

_INSTRUCCIONES = """Eres un extractor de datos de recibos de luz de CFE (Mexico).
Analiza la imagen del recibo y devuelve UNICAMENTE un objeto JSON que cumpla el esquema indicado.

Reglas:
- Copia los valores tal como aparecen impresos; no inventes ni completes datos.
- Si un dato no aparece en la imagen, usa null.
- Los montos y lecturas van como numero (sin $ ni comas). Los identificadores
  (numero de servicio, cuenta, tarifa, medidor) van como texto, conservando ceros iniciales.
- 'medida' y 'estimada' son las casillas de la tabla de consumo: true en la casilla
  que tiene la X y false en la otra.
- 'total_periodo' es la columna 'Total periodo' de la tabla de consumo (kWh del periodo).
- 'arriba de código de barras código' es la linea de digitos impresa justo arriba
  del codigo de barras del talon de pago.
- 'nombre_completo' y 'calle' estan en el bloque superior izquierdo del recibo.

Esquema JSON de la respuesta:
"""


class PDFOCRService:
    """Lee un recibo CFE escaneado usando el modelo de vision local."""

    def __init__(
        self,
        estructura: EstructuraPDF = estructura_pdf,
        cliente: LMStudioClient = lm_studio_client,
    ) -> None:
        self.estructura = estructura
        self.cliente = cliente

    async def extraer(self, origen: str | Path | BinaryIO, pagina: int = 0) -> dict[str, Any]:
        imagen_b64 = self.render_pagina(origen, pagina)
        esquema = self.estructura.json_schema()

        contenido = await self.cliente.chat(
            [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": _INSTRUCCIONES + json.dumps(esquema, ensure_ascii=False, indent=2),
                        },
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{imagen_b64}"},
                        },
                    ],
                }
            ],
            modelo=self.cliente.modelo_vision,
            esquema=esquema,
        )
        return self.normalizar(self._parsear_json(contenido))

    # --- pasos individuales ---------------------------------------------------
    @staticmethod
    def render_pagina(origen: str | Path | BinaryIO, pagina: int = 0) -> str:
        """Rasteriza una pagina del PDF y la regresa como PNG en base64."""
        documento = pdfium.PdfDocument(origen)
        try:
            if pagina >= len(documento):
                raise ValueError(f"El PDF solo tiene {len(documento)} pagina(s)")
            imagen = documento[pagina].render(scale=ESCALA_RENDER).to_pil()
        finally:
            documento.close()
        buffer = io.BytesIO()
        imagen.convert("RGB").save(buffer, format="PNG")
        return base64.b64encode(buffer.getvalue()).decode("ascii")

    @staticmethod
    def _parsear_json(contenido: str) -> dict[str, Any]:
        """El modelo a veces envuelve el JSON en ```json ... ```; se rescata el objeto."""
        texto = contenido.strip()
        texto = re.sub(r"^```(?:json)?|```$", "", texto, flags=re.MULTILINE).strip()
        try:
            return json.loads(texto)
        except json.JSONDecodeError:
            pass
        inicio, fin = texto.find("{"), texto.rfind("}")
        if inicio == -1 or fin <= inicio:
            raise LMStudioError(f"El modelo no devolvio JSON: {contenido[:300]}")
        try:
            return json.loads(texto[inicio : fin + 1])
        except json.JSONDecodeError as exc:
            raise LMStudioError(f"JSON invalido del modelo: {contenido[:300]}") from exc

    @staticmethod
    def _limpiar(valor: Any) -> Any:
        """El modelo a veces escribe 'null', 'N/A' o cadenas vacias en vez de null."""
        if isinstance(valor, str):
            texto = valor.strip()
            return None if texto.lower() in ("", "null", "none", "n/a", "no aplica") else texto
        return valor

    def normalizar(self, crudo: dict[str, Any]) -> dict[str, Any]:
        """Vacia la respuesta del modelo en la plantilla: mismas llaves, mismo orden."""
        resultado = self.estructura.plantilla()
        resultado["archivo_origen"] = (
            self._limpiar(crudo.get("archivo_origen")) or self.estructura.archivo_origen
        )

        for grupo, campos in resultado["buscar_seccion"].items():
            origen = (crudo.get("buscar_seccion") or {}).get(grupo) or {}
            for campo in campos:
                campos[campo] = self._limpiar(origen.get(campo))

        for bloque in ("columnas_servicio", "columnas_consumo", "importe"):
            origen = crudo.get(bloque) or {}
            for campo, valor in resultado[bloque].items():
                if isinstance(valor, dict):  # lectura_actual / lectura_anterior
                    anidado = origen.get(campo) or {}
                    for sub in valor:
                        valor[sub] = self._limpiar(anidado.get(sub))
                else:
                    resultado[bloque][campo] = self._limpiar(origen.get(campo))
        return resultado


pdf_ocr_service = PDFOCRService()
