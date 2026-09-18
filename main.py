"""Punto de entrada de la API: uvicorn main:app --reload"""
from __future__ import annotations

from litestar import Litestar, get
from litestar.openapi import OpenAPIConfig

from app.Controller.pdf_controller import PDFController


@get("/", summary="Ping de la API")
async def raiz() -> dict[str, str]:
    return {"api": "reciboCFE", "docs": "/schema/swagger"}


app = Litestar(
    route_handlers=[raiz, PDFController],
    request_max_body_size=500 * 1024 * 1024,
    openapi_config=OpenAPIConfig(title="API recibos CFE", version="0.1.0", path="/schema"),
)
