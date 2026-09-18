"""Extraccion de un recibo CFE a partir de palabras con coordenadas.

Este modulo no sabe de donde vienen las palabras: las entrega pdfplumber (PDF con
capa de texto) o EasyOCR (PDF escaneado). Lo unico que exige es una lista de
diccionarios {text, x0, x1, top, bottom} **en puntos de la pagina PDF**, para que
los umbrales geometricos valgan igual en los dos caminos.

Estrategia:
  * campos 'ETIQUETA: valor'  -> expresiones regulares sobre el texto por renglones
  * tabla de consumo          -> columnas por coordenada X del encabezado
  * casillas medida/estimada  -> recuadro (o etiqueta mas cercana) que contiene la 'X'
  * importes                  -> tabla 'Desglose del importe a pagar' (derecha)
  * nombre / calle            -> bloque superior izquierdo
  * codigo de barras          -> linea numerica del pie de pagina
"""
from __future__ import annotations

import re
from typing import Any, Sequence

from app.Service.estructura_service import EstructuraPDF, estructura_pdf

# Identificadores: su valor es un solo token y se conserva como texto para no perder
# ceros a la izquierda ('01'). Lo usa tambien el normalizador del modelo de vision.
CAMPOS_IDENTIFICADOR = {
    "NO. DE SERVICIO",
    "RMU",
    "CUENTA",
    "TARIFA",
    "MULTIPLICADOR",
    "NO. MEDIDOR",
    "NO HILOS",
}

# Columnas de la tabla de consumo -> texto del encabezado que las identifica.
_ENCABEZADOS_TABLA = {
    "lectura_actual": "lectura actual",
    "lectura_anterior": "lectura anterior",
    "total_periodo": "total periodo",
    "Precio": "precio",
    "Subtotal": "subtotal",
}

_NUMERO = re.compile(r"^-?\$?\d{1,3}(?:[,\s]\d{3})*(?:\.\d+)?$|^-?\$?\d+(?:\.\d+)?$")

# Membrete de CFE: aparece en el bloque superior izquierdo del escaneo (logo y
# leyenda) y no forma parte del domicilio del titular.
_RUIDO_ENCABEZADO = re.compile(r"^(cfe|comision federal de electricidad.*)$", re.IGNORECASE)

# Traduccion 1:1 (conserva las posiciones de los caracteres) para comparar etiquetas
# sin acentos: el OCR suele leer 'LIMITE' y 'Energia' donde el recibo dice 'LÍMITE'
# y 'Energía'.
_SIN_ACENTOS = str.maketrans("áéíóúüÁÉÍÓÚÜ", "aeiouuAEIOUU")


def sin_acentos(texto: str) -> str:
    return texto.translate(_SIN_ACENTOS)


def a_numero(texto: str | None) -> float | int | None:
    """'5,223' -> 5223 ; '146.64' -> 146.64 ; '$170' -> 170."""
    if texto is None:
        return None
    limpio = texto.replace("$", "").replace(",", "").replace(" ", "").strip()
    try:
        valor = float(limpio)
    except ValueError:
        return None
    return int(valor) if valor.is_integer() and "." not in limpio else valor


def _patron(etiqueta: str) -> str:
    """'NO. DE SERVICIO' -> regex tolerante a espacios variables y sin acentos."""
    return r"\s+".join(re.escape(t) for t in sin_acentos(etiqueta).split())


class ExtractorLayout:
    """Arma el JSON de estructuraPDF.json a partir de palabras posicionadas."""

    def __init__(self, estructura: EstructuraPDF = estructura_pdf) -> None:
        self.estructura = estructura

    def extraer(
        self,
        palabras: list[dict],
        *,
        ancho: float,
        alto: float,
        rects: Sequence[dict] = (),
        texto: str | None = None,
    ) -> dict[str, Any]:
        resultado = self.estructura.plantilla()
        if texto is None:
            texto = self.texto_de(palabras)

        etiquetados = self._campos_etiquetados(texto)
        for campo in self.estructura.columnas_servicio:
            resultado["columnas_servicio"][campo] = etiquetados.get(campo)
        for campo in self.estructura.campos_consumo_simples:
            resultado["columnas_consumo"][campo] = etiquetados.get(campo)

        columnas = self._tabla_consumo(palabras)
        for campo in ("total_periodo", "Precio", "Subtotal"):
            if resultado["columnas_consumo"].get(campo) is None:
                resultado["columnas_consumo"][campo] = columnas.get(campo)

        casillas = self._casillas_lectura(palabras, rects)
        for nombre, subcampos in self.estructura.campos_consumo_anidados.items():
            marcas = casillas.get(nombre, {})
            for sub in subcampos:
                if sub in ("medida", "estimada"):
                    resultado["columnas_consumo"][nombre][sub] = marcas.get(sub)
                else:  # 'valor' / 'valor kWh'
                    resultado["columnas_consumo"][nombre][sub] = columnas.get(nombre)

        importes = self._importes(palabras)
        for campo in self.estructura.importe:
            resultado["importe"][campo] = importes.get(campo)

        seccion = resultado["buscar_seccion"]
        if "superior izquierda" in seccion:
            sup = self._superior_izquierda(palabras, ancho, alto)
            for campo in seccion["superior izquierda"]:
                seccion["superior izquierda"][campo] = sup.get(campo)
        if "inferior al centro" in seccion:
            codigo = self._codigo_barras(palabras, alto)
            for campo in seccion["inferior al centro"]:
                seccion["inferior al centro"][campo] = codigo
        return resultado

    # --- helpers -------------------------------------------------------------
    @staticmethod
    def _lineas(palabras: list[dict], tolerancia: float = 3.0) -> list[list[dict]]:
        """Agrupa palabras en renglones por su coordenada vertical."""
        lineas: list[list[dict]] = []
        for palabra in sorted(palabras, key=lambda w: (w["top"], w["x0"])):
            for linea in lineas:
                if abs(linea[0]["top"] - palabra["top"]) <= tolerancia:
                    linea.append(palabra)
                    break
            else:
                lineas.append([palabra])
        return [sorted(linea, key=lambda w: w["x0"]) for linea in lineas]

    @classmethod
    def texto_de(cls, palabras: list[dict]) -> str:
        """Reconstruye el texto por renglones (lo que pdfplumber daria con extract_text)."""
        return "\n".join(
            " ".join(w["text"] for w in linea) for linea in cls._lineas(palabras)
        )

    def _campos_etiquetados(self, texto: str) -> dict[str, Any]:
        """Busca 'ETIQUETA: valor', cortando donde empieza la siguiente etiqueta."""
        etiquetas = [e.rstrip(":") for e in self.estructura.etiquetas_planas]
        cortes = re.compile("|".join(_patron(e) + r"\s*:" for e in etiquetas))
        renglones = texto.splitlines()
        valores: dict[str, Any] = {}

        for etiqueta in etiquetas:
            patron = re.compile(_patron(etiqueta) + r"\s*:\s*")
            for i, renglon in enumerate(renglones):
                # Se busca sobre el renglon sin acentos, pero el valor se corta del
                # original: la traduccion es 1:1 y las posiciones coinciden.
                encontrado = patron.search(sin_acentos(renglon))
                if not encontrado:
                    continue
                resto = renglon[encontrado.end():]
                siguiente = cortes.search(sin_acentos(resto))
                if siguiente:
                    resto = resto[: siguiente.start()]
                resto = resto.strip(" .;-")
                if not resto:  # el valor esta en el renglon de abajo (TOTAL A PAGAR:)
                    resto = next((r.strip() for r in renglones[i + 1:] if r.strip()), "")
                identificador = etiqueta in CAMPOS_IDENTIFICADOR
                if identificador and resto.split():
                    resto = resto.split()[0]
                clave = next(
                    (e for e in self.estructura.etiquetas_planas if e.rstrip(":") == etiqueta),
                    etiqueta,
                )
                # Los identificadores (servicio, cuenta, tarifa '01') se quedan como texto
                # para no perder ceros a la izquierda al guardarlos en la base.
                valores[clave] = (
                    resto if identificador or not _NUMERO.match(resto) else a_numero(resto)
                )
                break
        return valores

    def _tabla_consumo(self, palabras: list[dict]) -> dict[str, Any]:
        """Asigna los numeros de la tabla de consumo a su columna por cercania en X."""
        # El encabezado es el renglon que concentra los nombres de columna; no se
        # ancla a 'Concepto' porque esa palabra va en una linea base ligeramente distinta.
        claves = {frase.split()[0] for frase in _ENCABEZADOS_TABLA.values()}
        encabezado = max(
            self._lineas(palabras),
            key=lambda linea: len({w["text"].lower().strip("()") for w in linea} & claves),
            default=[],
        )
        if len({w["text"].lower().strip("()") for w in encabezado} & claves) < 3:
            return {}

        centros = self._centros_columnas(encabezado)
        if not centros:
            return {}

        # Segundo renglon del encabezado ('Medida X Estimada ... periodo (MXN)'):
        # solo el que va pegado al encabezado, no el de la tabla del mercado electrico.
        base = max(w["bottom"] for w in encabezado)
        subencabezados = [
            w
            for w in palabras
            if w["text"].lower().strip("()") in ("medida", "estimada", "periodo", "mxn")
            and base - 4 <= w["top"] <= base + 12
        ]
        inicio = max((w["bottom"] for w in subencabezados), default=base)

        valores: dict[str, Any] = {}
        for linea in self._lineas(palabras):
            if not (inicio <= linea[0]["top"] <= inicio + 40):
                continue
            for palabra in linea:
                if not _NUMERO.match(palabra["text"]):
                    continue
                centro = (palabra["x0"] + palabra["x1"]) / 2
                campo = min(centros, key=lambda c: abs(centros[c] - centro))
                if abs(centros[campo] - centro) <= 45 and campo not in valores:
                    valores[campo] = a_numero(palabra["text"])
        return valores

    @staticmethod
    def _centros_columnas(encabezado: list[dict]) -> dict[str, float]:
        """Centro X de cada columna a partir del renglon 'Concepto ... Subtotal'.

        Busca la frase completa ('Lectura actual'); si el encabezado esta apilado
        en dos renglones ('Total' / 'periodo') se conforma con el primer token.
        """

        def norma(palabra: dict) -> str:
            return palabra["text"].lower().strip("()")

        def centro(seq: list[dict]) -> float:
            return sum((w["x0"] + w["x1"]) / 2 for w in seq) / len(seq)

        usados: set[int] = set()
        centros: dict[str, float] = {}
        for campo, frase in _ENCABEZADOS_TABLA.items():
            tokens = frase.split()
            posicion = next(
                (
                    i
                    for i in range(len(encabezado))
                    if i not in usados
                    and all(
                        i + j < len(encabezado) and norma(encabezado[i + j]) == t
                        for j, t in enumerate(tokens)
                    )
                ),
                None,
            )
            if posicion is not None:
                usados.update(range(posicion, posicion + len(tokens)))
                centros[campo] = centro(encabezado[posicion : posicion + len(tokens)])
                continue
            posicion = next(
                (
                    i
                    for i in range(len(encabezado))
                    if i not in usados and norma(encabezado[i]) == tokens[0]
                ),
                None,
            )
            if posicion is not None:
                usados.add(posicion)
                centros[campo] = centro([encabezado[posicion]])
        return centros

    def _casillas_lectura(
        self, palabras: list[dict], rects: Sequence[dict] = ()
    ) -> dict[str, dict[str, bool]]:
        """Casillas 'Medida'/'Estimada': se marca la que contiene la X.

        Con el PDF editable se usan los 4 recuadros vectoriales; con OCR no hay
        recuadros, asi que la X se adjudica a la etiqueta mas cercana.
        """
        etiquetas = sorted(
            (w for w in palabras if w["text"].lower() in ("medida", "estimada")),
            key=lambda w: w["x0"],
        )
        if len(etiquetas) < 4:
            return {}
        banda_top = min(w["top"] for w in etiquetas) - 4
        banda_bottom = max(w["bottom"] for w in etiquetas) + 4
        equis = [
            w
            for w in palabras
            if w["text"].strip().upper() == "X" and banda_top <= w["top"] <= banda_bottom
        ]
        recuadros = sorted(
            (
                r
                for r in rects
                if banda_top <= r["top"] <= banda_bottom and r["x1"] - r["x0"] < 20
            ),
            key=lambda r: r["x0"],
        )

        if len(recuadros) >= 4:
            marcadas = [
                any(r["x0"] - 1 <= x["x0"] and x["x1"] <= r["x1"] + 1 for x in equis)
                for r in recuadros[:4]
            ]
        else:
            def centro(caja: dict) -> float:
                return (caja["x0"] + caja["x1"]) / 2

            indices = range(4)
            adjudicadas = {
                min(indices, key=lambda i: abs(centro(etiquetas[i]) - centro(x)))
                for x in equis
            }
            marcadas = [i in adjudicadas for i in indices]

        nombres = list(self.estructura.campos_consumo_anidados) or [
            "lectura_actual",
            "lectura_anterior",
        ]
        return {
            nombre: {"medida": marcadas[i * 2], "estimada": marcadas[i * 2 + 1]}
            for i, nombre in enumerate(nombres[:2])
        }

    def _importes(self, palabras: list[dict]) -> dict[str, Any]:
        """Desglose del importe: concepto a la izquierda, monto al final del renglon."""
        lineas = self._lineas(palabras)
        valores: dict[str, Any] = {}
        for etiqueta in self.estructura.importe:
            patron = re.compile("^" + _patron(etiqueta) + r"(?!\w)", re.IGNORECASE)
            candidatos: list[tuple[float, Any]] = []
            for linea in lineas:
                for corte, palabra in enumerate(linea):
                    resto = sin_acentos(" ".join(w["text"] for w in linea[corte:]))
                    if not patron.match(resto):
                        continue
                    numeros = [w for w in linea[corte:] if _NUMERO.match(w["text"])]
                    if numeros:
                        candidatos.append((palabra["x0"], a_numero(numeros[-1]["text"])))
                    break
            if not candidatos:
                continue
            # El desglose vive a la derecha (x0 >= 290): asi se resuelve 'Energia' duplicada.
            derecha = [c for c in candidatos if c[0] >= 290]
            valores[etiqueta] = (derecha or candidatos)[0][1]
        return valores

    def _superior_izquierda(self, palabras: list[dict], ancho: float, alto: float) -> dict[str, str]:
        """Nombre del titular y domicilio: bloque izquierdo antes del primer campo 'X:'."""
        limite_x = ancho * 0.45
        # Se filtra por columna ANTES de armar renglones: el bloque de la derecha
        # ('TOTAL A PAGAR:') comparte altura con el nombre y contaminaria la linea.
        izquierda = [w for w in palabras if w["x1"] < limite_x and w["top"] < alto * 0.30]
        textos: list[str] = []
        for linea in self._lineas(izquierda):
            texto = " ".join(w["text"] for w in linea).strip()
            if ":" in texto and texto.split(":")[0].isupper():
                break  # empiezan los campos NO. DE SERVICIO / RMU / ...
            if _RUIDO_ENCABEZADO.match(sin_acentos(texto).rstrip("?¿.,")):
                continue  # logo y leyenda de CFE que el OCR captura arriba del nombre
            if texto:
                textos.append(texto)
        if not textos:
            return {}
        return {"nombre_completo": textos[0], "calle": ", ".join(textos[1:])}

    def _codigo_barras(self, palabras: list[dict], alto: float) -> str | None:
        """Linea numerica impresa arriba del codigo de barras del talon de pago."""
        candidatos: list[str] = []
        for linea in self._lineas(palabras):
            if linea[0]["top"] < alto * 0.75:
                continue
            texto = " ".join(w["text"] for w in linea).strip()
            if re.fullmatch(r"[\d\s]{15,}", texto):
                candidatos.append(re.sub(r"\s+", " ", texto))
        return max(candidatos, key=len) if candidatos else None


extractor_layout = ExtractorLayout()
