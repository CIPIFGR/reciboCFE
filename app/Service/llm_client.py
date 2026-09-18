"""Cliente minimo para el servidor local de LM Studio (API compatible con OpenAI)."""
from __future__ import annotations

import json
from typing import Any

import httpx

from app.config import Settings, settings


class LMStudioError(RuntimeError):
    """Falla al hablar con el servidor local de LM Studio."""


class LMStudioClient:
    def __init__(self, config: Settings = settings) -> None:
        self.base_url = config.llm_base_url.rstrip("/")
        self.timeout = config.llm_timeout
        self.modelo_texto = config.llm_model
        self.modelo_vision = config.llm_vision_model

    async def modelos(self) -> list[str]:
        async with httpx.AsyncClient(timeout=30) as cliente:
            respuesta = await cliente.get(f"{self.base_url}/models")
            respuesta.raise_for_status()
            return [m["id"] for m in respuesta.json().get("data", [])]

    async def chat(
        self,
        mensajes: list[dict[str, Any]],
        *,
        modelo: str | None = None,
        esquema: dict[str, Any] | None = None,
        temperatura: float = 0.0,
    ) -> str:
        """Manda un chat completion. Con `esquema` pide salida estructurada JSON."""
        cuerpo: dict[str, Any] = {
            "model": modelo or self.modelo_texto,
            "messages": mensajes,
            "temperature": temperatura,
            "stream": False,
        }
        if esquema is not None:
            cuerpo["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "recibo_cfe", "strict": True, "schema": esquema},
            }

        async with httpx.AsyncClient(timeout=self.timeout) as cliente:
            respuesta = await cliente.post(f"{self.base_url}/chat/completions", json=cuerpo)
            fallo_inicial = ""
            if respuesta.status_code == 400 and esquema is not None:
                # Si el modelo rechaza el json_schema estricto se reintenta en texto plano
                # (LM Studio solo admite 'json_schema' o 'text'); el prompt ya pide JSON.
                fallo_inicial = f" | con json_schema: {respuesta.text[:300]}"
                cuerpo["response_format"] = {"type": "text"}
                respuesta = await cliente.post(f"{self.base_url}/chat/completions", json=cuerpo)
            if respuesta.status_code >= 400:
                raise LMStudioError(
                    f"LM Studio respondio {respuesta.status_code}: "
                    f"{respuesta.text[:500]}{fallo_inicial}"
                )
            datos = respuesta.json()

        try:
            return datos["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError) as exc:  # pragma: no cover
            raise LMStudioError(f"Respuesta inesperada de LM Studio: {json.dumps(datos)[:500]}") from exc


lm_studio_client = LMStudioClient()
