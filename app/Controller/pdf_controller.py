"""Endpoints de extraccion de recibos CFE."""
from __future__ import annotations

import io
import sqlite3
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Annotated, Any

from anyio import to_thread
from litestar import Controller, get, post
from litestar.datastructures import UploadFile
from litestar.enums import RequestEncodingType
from litestar.exceptions import ClientException, HTTPException, NotFoundException
from litestar.params import Body

from app.config import BASE_DIR, CONFIG_PDF_DIR
from app.Service.base_datos_service import base_datos_service
from app.Service.llm_client import LMStudioError, lm_studio_client
from app.Service.pdf_ocr_pytorch_service import hay_gpu, pdf_ocr_pytorch_service
from app.Service.pdf_ocr_service import pdf_ocr_service
from app.Service.pdf_texto_service import pdf_texto_service

PDF_TEXTO_EJEMPLO = CONFIG_PDF_DIR / "recibo_CFE.pdf"
PDF_IMAGEN_EJEMPLO = CONFIG_PDF_DIR / "recibo_CFE_imagen.pdf"


@dataclass
class FormularioPDF:
    """Cuerpo multipart opcional: si no se sube nada se usa `ruta` o el PDF de ejemplo."""

    archivo: UploadFile | None = None


CuerpoPDF = Annotated[FormularioPDF, Body(media_type=RequestEncodingType.MULTI_PART)]


class PDFController(Controller):
    """Tres lecturas del mismo recibo: capa de texto, OCR local y modelo de vision."""

    path = "/pdf"
    tags = ["PDF"]

    @post("/extraer-texto", summary="Extrae un recibo CFE con capa de texto (pdfplumber)")
    async def extraer_texto(
        self,
        data: CuerpoPDF,
        ruta: str | None = None,
        pagina: int = 0,
        guardar: bool = True,
    ) -> dict[str, Any]:
        origen = await self._origen(data.archivo, ruta, PDF_TEXTO_EJEMPLO)
        try:
            resultado = pdf_texto_service.extraer(origen, pagina=pagina)
        except ValueError as exc:
            raise ClientException(detail=str(exc)) from exc
        return {
            "metodo": "texto",
            "guardado": await self._guardar(resultado, "texto", data.archivo, ruta, guardar),
            "datos": resultado,
        }

    @post("/extraer-ocr-ia", summary="Extrae un recibo CFE escaneado con el modelo de vision")
    async def extraer_ocr_ia(
        self,
        data: CuerpoPDF,
        ruta: str | None = None,
        pagina: int = 0,
        guardar: bool = True,
        revisar: bool = True,
    ) -> dict[str, Any]:
        origen = await self._origen(data.archivo, ruta, PDF_IMAGEN_EJEMPLO)
        try:
            resultado, revision = await pdf_ocr_service.extraer(
                origen, pagina=pagina, revisar=revisar
            )
        except LMStudioError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except ValueError as exc:
            raise ClientException(detail=str(exc)) from exc
        return {
            "metodo": "ocr_ia",
            "modelo": lm_studio_client.modelo_vision,
            "revision": revision,
            "guardado": await self._guardar(resultado, "ocr_ia", data.archivo, ruta, guardar),
            "datos": resultado,
        }

    @post("/extraer-ocr-pytorch", summary="Extrae un recibo CFE escaneado con OCR local (EasyOCR/PyTorch)")
    async def extraer_ocr_pytorch(
        self,
        data: CuerpoPDF,
        ruta: str | None = None,
        pagina: int = 0,
        guardar: bool = True,
    ) -> dict[str, Any]:
        origen = await self._origen(data.archivo, ruta, PDF_IMAGEN_EJEMPLO)
        try:
            # EasyOCR es sincrono y pesado: se manda a un hilo para no bloquear el loop.
            resultado = await to_thread.run_sync(
                pdf_ocr_pytorch_service.extraer, origen, pagina
            )
        except ValueError as exc:
            raise ClientException(detail=str(exc)) from exc
        return {
            "metodo": "ocr_pytorch",
            "dispositivo": "cuda" if hay_gpu() else "cpu",
            "guardado": await self._guardar(resultado, "ocr_pytorch", data.archivo, ruta, guardar),
            "datos": resultado,
        }

    @get("/salud", summary="Verifica el servidor local de LM Studio")
    async def salud(self) -> dict[str, Any]:
        try:
            modelos = await lm_studio_client.modelos()
        except Exception as exc:  # noqa: BLE001 - el detalle se devuelve al cliente
            raise HTTPException(status_code=502, detail=f"LM Studio no responde: {exc}") from exc
        return {
            "lm_studio": lm_studio_client.base_url,
            "modelos": modelos,
            "modelo_vision": lm_studio_client.modelo_vision,
            "modelo_revision": lm_studio_client.modelo_revision or None,
            "ocr_pytorch": {"dispositivo": "cuda" if hay_gpu() else "cpu"},
        }

    # --- helpers --------------------------------------------------------------
    @staticmethod
    async def _guardar(
        datos: dict[str, Any],
        metodo: str,
        archivo: UploadFile | None,
        ruta: str | None,
        guardar: bool,
    ) -> dict[str, Any]:
        """Persiste el recibo en SQLite. Un fallo de base no invalida la extraccion."""
        if not guardar:
            return {"ok": False, "motivo": "Omitido por el parametro guardar=false"}
        nombre = archivo.filename if archivo is not None else (ruta or "ejemplo")
        try:
            return await to_thread.run_sync(
                partial(base_datos_service.guardar, datos, metodo=metodo, archivo=nombre)
            )
        except sqlite3.Error as exc:
            return {"ok": False, "motivo": f"Error de SQLite: {exc}"}

    @staticmethod
    async def _origen(archivo: UploadFile | None, ruta: str | None, ejemplo: Path) -> io.BytesIO:
        """Prioridad: archivo subido > parametro `ruta` > PDF de ejemplo del repo."""
        if archivo is not None:
            contenido = await archivo.read()
            if not contenido:
                raise ClientException(detail="El archivo subido esta vacio")
            return io.BytesIO(contenido)

        destino = ejemplo if ruta is None else BASE_DIR / ruta
        if not destino.exists():
            raise NotFoundException(detail=f"No existe el PDF: {destino}")
        return io.BytesIO(destino.read_bytes())
