"""Punto de entrada de la API: uvicorn main:app --reload"""
from __future__ import annotations

from litestar import Litestar, get
from litestar.openapi import OpenAPIConfig

from app.Controller.pdf_controller import PDFController
from app.Service.base_datos_service import base_datos_service


@get("/", summary="Ping de la API")
async def raiz() -> dict[str, str]:
    return {"api": "reciboCFE", "docs": "/schema/swagger"}


def preparar_base_datos(app: Litestar) -> None:
    """Crea las tablas de SQLite si no existen (y agrega columnas nuevas si las hay)."""
    base_datos_service.inicializar()


app = Litestar(
    route_handlers=[raiz, PDFController],
    request_max_body_size=500 * 1024 * 1024,
    openapi_config=OpenAPIConfig(title="API recibos CFE", version="0.1.0", path="/schema"),
    on_startup=[preparar_base_datos],
)

#uvicorn main:app --host 127.0.0.1 --port 8001
