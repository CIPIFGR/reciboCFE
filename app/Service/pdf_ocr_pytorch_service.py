"""Extraccion de recibos CFE escaneados con OCR local (EasyOCR sobre PyTorch).

Es el camino rapido para PDF sin capa de texto: en vez de pedirle la lectura a un
modelo de lenguaje, se reconocen las palabras con OCR y se reutiliza el mismo
ExtractorLayout que usa el PDF editable. Para que los umbrales geometricos sirvan
igual, las cajas en pixeles se convierten a puntos de la pagina PDF.

Usa GPU automaticamente si el PyTorch instalado trae CUDA (hoy la build es +cpu).
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, BinaryIO

import numpy as np
import pypdfium2 as pdfium
import torch

from app.Service.extractor_layout import ExtractorLayout, extractor_layout

# ~290 dpi. Con 2.5 el OCR tarda 3 s menos pero confunde mas digitos con letras.
ESCALA_RENDER = 4.0
CONFIANZA_MINIMA = 0.30  # descarta basura del OCR (logos, sellos, marcas de agua)
IDIOMAS = ["es"]

_lector = None
_candado = threading.Lock()


def hay_gpu() -> bool:
    """True si el PyTorch instalado puede usar CUDA (la build +cpu nunca puede)."""
    return torch.cuda.is_available()


def obtener_lector():
    """EasyOCR tarda ~6 s en cargar sus modelos: se construye una sola vez."""
    global _lector
    if _lector is None:
        with _candado:
            if _lector is None:
                import easyocr  # import diferido: arrastra torch y tarda en cargar

                _lector = easyocr.Reader(IDIOMAS, gpu=hay_gpu(), verbose=False)
    return _lector


class PDFOCRPytorchService:
    """Lee un recibo CFE escaneado con EasyOCR y lo devuelve como estructuraPDF.json."""

    def __init__(self, extractor: ExtractorLayout = extractor_layout) -> None:
        self.extractor = extractor

    def extraer(self, origen: str | Path | BinaryIO, pagina: int = 0) -> dict[str, Any]:
        imagen, ancho, alto = self.render_pagina(origen, pagina)
        cajas = obtener_lector().readtext(np.array(imagen), detail=1, paragraph=False)
        escala = imagen.width / ancho  # pixeles por punto
        palabras = self.palabras_de_cajas(cajas, escala)
        if not palabras:
            raise ValueError("El OCR no reconocio texto en la pagina")
        return self.extractor.extraer(palabras, ancho=ancho, alto=alto)

    # --- pasos individuales ---------------------------------------------------
    @staticmethod
    def render_pagina(origen: str | Path | BinaryIO, pagina: int = 0):
        """Rasteriza la pagina y regresa (imagen PIL, ancho_pt, alto_pt)."""
        documento = pdfium.PdfDocument(origen)
        try:
            if pagina >= len(documento):
                raise ValueError(f"El PDF solo tiene {len(documento)} pagina(s)")
            hoja = documento[pagina]
            ancho, alto = hoja.get_size()
            imagen = hoja.render(scale=ESCALA_RENDER).to_pil().convert("RGB")
        finally:
            documento.close()
        return imagen, ancho, alto

    @staticmethod
    def palabras_de_cajas(cajas: list, escala: float) -> list[dict]:
        """Convierte las cajas de EasyOCR en palabras sueltas con coordenadas en puntos.

        EasyOCR devuelve segmentos completos ('NO. DE SERVICIO:142861200719'); se
        reparten en palabras interpolando el ancho de la caja por numero de caracteres,
        que es lo que necesita el extractor para asignar columnas.
        """
        palabras: list[dict] = []
        for caja, texto, confianza in cajas:
            texto = texto.strip()
            if not texto or confianza < CONFIANZA_MINIMA:
                continue
            xs = [punto[0] for punto in caja]
            ys = [punto[1] for punto in caja]
            x0, x1 = min(xs) / escala, max(xs) / escala
            top, bottom = min(ys) / escala, max(ys) / escala
            ancho_caja = x1 - x0
            largo = len(texto)
            posicion = 0
            for token in texto.split(" "):
                if token:
                    palabras.append(
                        {
                            "text": token,
                            "x0": x0 + ancho_caja * posicion / largo,
                            "x1": x0 + ancho_caja * (posicion + len(token)) / largo,
                            "top": top,
                            "bottom": bottom,
                        }
                    )
                posicion += len(token) + 1
        return palabras


pdf_ocr_pytorch_service = PDFOCRPytorchService()
