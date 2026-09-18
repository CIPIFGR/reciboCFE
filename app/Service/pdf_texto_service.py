"""Extraccion de recibos CFE con capa de texto (PDF editable) usando pdfplumber.

Solo se encarga de abrir el PDF y entregar palabras, recuadros y texto; el armado
del JSON lo hace ExtractorLayout, que comparte con el camino de OCR.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, BinaryIO

import pdfplumber

from app.Service.extractor_layout import ExtractorLayout, extractor_layout


class PDFTextoService:
    """Lee un recibo CFE editable y lo devuelve con la forma de estructuraPDF.json."""

    def __init__(self, extractor: ExtractorLayout = extractor_layout) -> None:
        self.extractor = extractor

    def extraer(self, origen: str | Path | BinaryIO, pagina: int = 0) -> dict[str, Any]:
        with pdfplumber.open(origen) as pdf:
            if pagina >= len(pdf.pages):
                raise ValueError(f"El PDF solo tiene {len(pdf.pages)} pagina(s)")
            hoja = pdf.pages[pagina]
            texto = hoja.extract_text() or ""
            if not texto.strip():
                raise ValueError(
                    "El PDF no tiene capa de texto; usa /pdf/extraer-ocr-pytorch o /pdf/extraer-ocr-ia"
                )
            return self.extractor.extraer(
                hoja.extract_words(),
                ancho=hoja.width,
                alto=hoja.height,
                rects=hoja.rects,
                texto=texto,
            )


pdf_texto_service = PDFTextoService()
