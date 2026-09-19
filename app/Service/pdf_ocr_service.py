"""Extraccion de recibos CFE escaneados (sin capa de texto) con modelos de vision.

Dos pasadas:
  1. El modelo de vision chico (LLM_VISION_MODEL, cabe entero en la GPU) lee todo el
     recibo en segundos.
  2. Los campos dudosos se le vuelven a pedir -solo esos- al modelo grande
     (LLM_REVISION_MODEL). Lo que el chico ya leyo bien se queda como esta.

Un campo es dudoso si:
  * esta en LLM_REVISION_CAMPOS: el modelo chico lo lee mal de forma sistematica
    (medido; en produccion no hay respuesta correcta contra la cual comparar);
  * vino vacio;
  * no cumple su formato (la linea del codigo de barras solo lleva digitos);
  * no cuadra con otros campos (lecturas contra consumo, energia + IVA contra facturado).

La pagina se rasteriza una sola vez y la misma imagen sirve para las dos pasadas.
"""
from __future__ import annotations

import base64
import io
import json
import re
import time
from pathlib import Path
from typing import Any, BinaryIO, Iterable

import pypdfium2 as pdfium

from app.config import Settings, settings
from app.Service.estructura_service import EstructuraPDF, estructura_pdf
from app.Service.extractor_layout import CAMPOS_IDENTIFICADOR, a_numero
from app.Service.llm_client import LMStudioClient, LMStudioError, lm_studio_client

ESCALA_RENDER = 2.5  # ~180 dpi sobre una hoja carta: suficiente para el OCR del modelo

Ruta = tuple[str, ...]

# Reglas de negocio del recibo CFE que se pueden verificar sin conocer la respuesta.
CAMPO_CODIGO_BARRAS = "arriba de código de barras código"
CAMPO_TOTAL_PERIODO = "total_periodo"
CAMPO_MULTIPLICADOR = "MULTIPLICADOR"
IMPORTE_SUMANDOS = ("Energía", "IVA 16%")
IMPORTE_FACTURADO = "Fac. del Periodo"
TOLERANCIA_PESOS = 0.05  # redondeo de centavos al sumar energia + IVA
CASILLAS = ("medida", "estimada")

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
- 'TOTAL A PAGAR:' es el monto del recuadro grande 'TOTAL A PAGAR', arriba a la derecha
  del nombre del titular, tal como aparece ahi. No lo confundas con el renglon 'Total'
  del desglose del importe.
- 'arriba de código de barras código': hasta abajo al centro, en el talon de pago, hay un
  codigo de barras; justo ARRIBA de las barras va impresa una linea larga de digitos
  repartidos en varios grupos separados por espacios. Copiala completa y tal cual,
  respetando los espacios. No la confundas con el numero de servicio ni con la cuenta.
- 'nombre_completo' y 'calle' estan en el bloque superior izquierdo del recibo:
  el nombre es el primer renglon y 'calle' es TODO el domicilio que sigue debajo
  (calle, colonia, codigo postal, alcaldia y estado) en una sola cadena separada por comas.
"""

_SOLO_ESTOS = """
IMPORTANTE: devuelve solo los campos del esquema siguiente; son los que hay que leer con
especial cuidado.
"""


class PDFOCRService:
    """Lee un recibo CFE escaneado con el modelo de vision y relee los campos dudosos."""

    def __init__(
        self,
        estructura: EstructuraPDF = estructura_pdf,
        cliente: LMStudioClient = lm_studio_client,
        config: Settings = settings,
    ) -> None:
        self.estructura = estructura
        self.cliente = cliente
        self.campos_revision = config.llm_revision_campos

    # --- API publica -----------------------------------------------------------
    async def extraer(
        self, origen: str | Path | BinaryIO, pagina: int = 0, revisar: bool = True
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        """Regresa (datos, revision). `revision` es None si no hubo segunda pasada."""
        imagen_b64 = self.render_pagina(origen, pagina)
        esquema = self.estructura.json_schema()
        resultado = await self._consultar(imagen_b64, self.cliente.modelo_vision, esquema)

        if not (revisar and self.cliente.modelo_revision):
            return resultado, None
        dudosos = self.campos_dudosos(resultado)
        if not dudosos:
            return resultado, None
        return resultado, await self._revisar(imagen_b64, resultado, dudosos)

    def campos_dudosos(self, datos: dict[str, Any]) -> dict[Ruta, str]:
        """{ruta: motivo} de los campos que conviene releer con el modelo grande."""
        dudosos: dict[Ruta, str] = {}

        for nombre in self.campos_revision:
            for ruta in self._resolver(nombre):
                dudosos.setdefault(ruta, "configurado")

        for ruta in self._rutas_hoja(self.estructura.plantilla()):
            if ruta[0] != "archivo_origen" and _obtener(datos, ruta) is None:
                dudosos.setdefault(ruta, "vacio")

        for ruta in self._resolver(CAMPO_CODIGO_BARRAS):
            valor = _obtener(datos, ruta)
            if valor is not None and not re.fullmatch(r"[\d ]{15,}", str(valor)):
                dudosos.setdefault(ruta, "formato")

        for ruta in self._lecturas_que_no_cuadran(datos):
            dudosos.setdefault(ruta, "no cuadra")
        for ruta in self._importes_que_no_cuadran(datos):
            dudosos.setdefault(ruta, "no cuadra")
        return dudosos

    # --- consultas al modelo --------------------------------------------------
    async def _consultar(
        self, imagen_b64: str, modelo: str, esquema: dict[str, Any], extra: str = ""
    ) -> dict[str, Any]:
        contenido = await self.cliente.chat(
            [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": _INSTRUCCIONES
                            + extra
                            + "\nEsquema JSON de la respuesta:\n"
                            + json.dumps(esquema, ensure_ascii=False, indent=2),
                        },
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{imagen_b64}"},
                        },
                    ],
                }
            ],
            modelo=modelo,
            esquema=esquema,
        )
        return self.normalizar(self._parsear_json(contenido))

    async def _revisar(
        self, imagen_b64: str, resultado: dict[str, Any], dudosos: dict[Ruta, str]
    ) -> dict[str, Any]:
        """Segunda pasada: solo los campos dudosos, con el modelo grande.

        Si el modelo grande falla o no encuentra un campo, se conserva lo del chico:
        la revision nunca deja la extraccion peor de lo que estaba.
        """
        modelo = self.cliente.modelo_revision
        inicio = time.perf_counter()
        informe: dict[str, Any] = {"modelo": modelo, "campos": []}
        try:
            releido = await self._consultar(
                imagen_b64,
                modelo,
                _esquema_parcial(self.estructura.json_schema(), dudosos),
                _SOLO_ESTOS,
            )
        except LMStudioError as exc:
            informe["error"] = str(exc)
            releido = None
        informe["segundos"] = round(time.perf_counter() - inicio, 1)

        for ruta, motivo in dudosos.items():
            antes = _obtener(resultado, ruta)
            despues = _obtener(releido, ruta) if releido is not None else None
            if despues is not None:
                _asignar(resultado, ruta, despues)
            informe["campos"].append(
                {
                    "campo": ".".join(ruta[1:]),
                    "motivo": motivo,
                    "modelo_vision": antes,
                    "modelo_revision": despues,
                    "cambio": despues is not None and despues != antes,
                }
            )
        return informe

    # --- reglas verificables ----------------------------------------------------
    def _lecturas_que_no_cuadran(self, datos: dict[str, Any]) -> list[Ruta]:
        """(lectura actual - lectura anterior) x multiplicador debe dar el total del periodo."""
        grupos = list(self.estructura.campos_consumo_anidados.items())
        if len(grupos) < 2 or CAMPO_TOTAL_PERIODO not in self.estructura.campos_consumo_simples:
            return []
        (actual, sub_actual), (anterior, sub_anterior) = grupos[:2]
        ruta_actual = ("columnas_consumo", actual, _subcampo_valor(sub_actual))
        ruta_anterior = ("columnas_consumo", anterior, _subcampo_valor(sub_anterior))
        ruta_total = ("columnas_consumo", CAMPO_TOTAL_PERIODO)
        valores = [_obtener(datos, r) for r in (ruta_actual, ruta_anterior, ruta_total)]
        if not all(isinstance(v, (int, float)) for v in valores):
            return []  # si falta alguno ya quedo marcado como vacio
        multiplicador = a_numero(str(datos["columnas_servicio"].get(CAMPO_MULTIPLICADOR) or 1)) or 1
        lectura_actual, lectura_anterior, total = valores
        if abs((lectura_actual - lectura_anterior) * multiplicador - total) < 1:
            return []
        return [ruta_actual, ruta_anterior, ruta_total]

    def _importes_que_no_cuadran(self, datos: dict[str, Any]) -> list[Ruta]:
        """Energia + IVA debe dar lo facturado en el periodo."""
        nombres = (*IMPORTE_SUMANDOS, IMPORTE_FACTURADO)
        if not all(n in self.estructura.importe for n in nombres):
            return []
        rutas = [("importe", n) for n in nombres]
        valores = [_obtener(datos, r) for r in rutas]
        if not all(isinstance(v, (int, float)) for v in valores):
            return []
        *sumandos, facturado = valores
        return [] if abs(sum(sumandos) - facturado) <= TOLERANCIA_PESOS else rutas

    # --- rutas dentro de la plantilla -------------------------------------------
    @classmethod
    def _rutas_hoja(cls, nodo: dict[str, Any], prefijo: Ruta = ()) -> Iterable[Ruta]:
        for llave, valor in nodo.items():
            if isinstance(valor, dict):
                yield from cls._rutas_hoja(valor, prefijo + (llave,))
            else:
                yield prefijo + (llave,)

    def _resolver(self, nombre: str) -> list[Ruta]:
        """'calle' o 'lectura_actual.valor' -> ruta completa dentro de la respuesta.

        Primero se busca el nombre exacto, porque hay campos que llevan punto
        ('NO. DE SERVICIO', 'Fac. del Periodo'); solo si no existe se interpreta el
        ultimo punto como separador grupo.subcampo.
        """
        rutas = list(self._rutas_hoja(self.estructura.plantilla()))
        exactas = [r for r in rutas if r[-1] == nombre]
        if exactas:
            return exactas
        grupo, _, subcampo = nombre.rpartition(".")
        return [r for r in rutas if len(r) >= 2 and r[-2:] == (grupo, subcampo)]

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

    @staticmethod
    def _identificador(valor: Any) -> Any:
        """'16300 86-12-29 FIRL-480208' -> '16300'; siempre texto, nunca numero."""
        if valor is None:
            return None
        texto = str(valor).strip()
        return texto.split()[0] if texto.split() else None

    @staticmethod
    def _numerico(valor: Any) -> Any:
        """El modelo devuelve '$170.10' o '5,223'; aqui pasan a numero como en los otros metodos.

        Si el texto no es numerico (fechas, periodos) se queda tal cual.
        """
        if not isinstance(valor, str):
            return valor
        numero = a_numero(valor)
        return valor if numero is None else numero

    def normalizar(self, crudo: dict[str, Any]) -> dict[str, Any]:
        """Vacia la respuesta del modelo en la plantilla y le aplica los tipos del proyecto.

        El modelo entrega todo como texto ('$170.10', '5,223'); aqui se convierte para que
        la salida sea intercambiable con la de los otros dos metodos y entre bien en SQLite:
          * buscar_seccion  -> siempre texto (el codigo de barras no debe volverse numero)
          * identificadores -> texto y primer token ('01' conserva el cero)
          * consumo/importe -> numero cuando el texto lo es; fechas y periodos se quedan
        Una respuesta parcial (la de la segunda pasada) deja en None lo que no trae.
        """
        resultado = self.estructura.plantilla()
        # Constante que identifica al documento: se toma del archivo de configuracion,
        # no de lo que el modelo quiera contestar ('Recibo de luz CFE').
        resultado["archivo_origen"] = self.estructura.archivo_origen

        for grupo, campos in resultado["buscar_seccion"].items():
            origen = (crudo.get("buscar_seccion") or {}).get(grupo) or {}
            for campo in campos:
                valor = self._limpiar(origen.get(campo))
                campos[campo] = None if valor is None else str(valor)

        origen = crudo.get("columnas_servicio") or {}
        for campo in resultado["columnas_servicio"]:
            valor = self._limpiar(origen.get(campo))
            resultado["columnas_servicio"][campo] = (
                self._identificador(valor) if campo in CAMPOS_IDENTIFICADOR else valor
            )

        for bloque in ("columnas_consumo", "importe"):
            origen = crudo.get(bloque) or {}
            for campo, valor in resultado[bloque].items():
                if isinstance(valor, dict):  # lectura_actual / lectura_anterior
                    anidado = origen.get(campo) or {}
                    for sub in valor:
                        limpio = self._limpiar(anidado.get(sub))
                        valor[sub] = limpio if sub in CASILLAS else self._numerico(limpio)
                else:
                    resultado[bloque][campo] = self._numerico(self._limpiar(origen.get(campo)))
        return resultado


# --- utilidades sobre rutas ------------------------------------------------------
def _obtener(datos: dict[str, Any] | None, ruta: Ruta) -> Any:
    nodo: Any = datos
    for llave in ruta:
        if not isinstance(nodo, dict):
            return None
        nodo = nodo.get(llave)
    return nodo


def _asignar(datos: dict[str, Any], ruta: Ruta, valor: Any) -> None:
    nodo = datos
    for llave in ruta[:-1]:
        nodo = nodo[llave]
    nodo[ruta[-1]] = valor


def _subcampo_valor(subcampos: list[str]) -> str:
    """De ['medida', 'estimada', 'valor kWh'] regresa 'valor kWh'."""
    return next(s for s in subcampos if s not in CASILLAS)


def _esquema_parcial(esquema: dict[str, Any], rutas: Iterable[Ruta]) -> dict[str, Any]:
    """Recorta el JSON Schema completo a las ramas que llevan a los campos pedidos."""
    rutas = list(rutas)
    propiedades: dict[str, Any] = {}
    for nombre, subesquema in esquema.get("properties", {}).items():
        hijas = [r[1:] for r in rutas if r and r[0] == nombre]
        if not hijas:
            continue
        propiedades[nombre] = (
            subesquema if any(not h for h in hijas) else _esquema_parcial(subesquema, hijas)
        )
    return {**esquema, "properties": propiedades, "required": list(propiedades)}


pdf_ocr_service = PDFOCRService()
