"""Persistencia en SQLite de los recibos extraidos.

Las columnas NO se escriben a mano: salen de configuracionPDF/estructuraPDF.json, igual
que el JSON de la respuesta. Agregar un campo ahi agrega la columna correspondiente
(`inicializar()` hace el ALTER TABLE cuando la base ya existe).

Tablas (SQLite no tiene esquemas, por eso el prefijo en vez de 'cfe.'):
  cfe_cliente   un renglon por cuenta: nombre y domicilio
  cfe_servicio  un renglon por cuenta: numero de servicio, RMU, tarifa, medidor...
  cfe_consumo   un renglon por cuenta + periodo facturado: lecturas y consumo
  cfe_importe   un renglon por cuenta + periodo facturado: desglose del importe
"""
from __future__ import annotations

import re
import sqlite3
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import Settings, settings
from app.Service.estructura_service import EstructuraPDF, estructura_pdf

# Campos del recibo que se usan para ligar las cuatro tablas.
CAMPO_CUENTA = "CUENTA"
CAMPO_PERIODO = "PERIODO FACTURADO"
SECCION_CLIENTE = "superior izquierda"

TABLA_CLIENTE = "cfe_cliente"
TABLA_SERVICIO = "cfe_servicio"
TABLA_CONSUMO = "cfe_consumo"
TABLA_IMPORTE = "cfe_importe"

# Tipo por tabla cuando el campo no aparece en _TIPOS_EXPLICITOS.
_TIPO_POR_DEFECTO = {
    TABLA_CLIENTE: "TEXT",
    TABLA_SERVICIO: "TEXT",  # identificadores: conservan ceros a la izquierda
    TABLA_CONSUMO: "REAL",
    TABLA_IMPORTE: "REAL",
}
_TIPOS_EXPLICITOS = {
    "LÍMITE DE PAGO": "TEXT",
    "CORTE A PARTIR": "TEXT",
    "PERIODO FACTURADO": "TEXT",
    "medida": "INTEGER",  # casillas del recibo: 1 marcada, 0 vacia
    "estimada": "INTEGER",
}


def columna(*partes: str) -> str:
    """'NO. DE SERVICIO' -> 'no_de_servicio' ; ('lectura_actual','valor kWh') -> 'lectura_actual_valor_kwh'."""
    texto = "_".join(partes)
    sin_acentos = "".join(
        c for c in unicodedata.normalize("NFKD", texto) if not unicodedata.combining(c)
    )
    return re.sub(r"_+", "_", re.sub(r"[^0-9a-zA-Z]+", "_", sin_acentos)).strip("_").lower()


class BaseDatosService:
    """Crea las tablas y guarda cada recibo extraido."""

    def __init__(
        self,
        estructura: EstructuraPDF = estructura_pdf,
        config: Settings = settings,
    ) -> None:
        self.estructura = estructura
        self.ruta = config.db_path

    # --- conexion -------------------------------------------------------------
    def conectar(self) -> sqlite3.Connection:
        self.ruta.parent.mkdir(parents=True, exist_ok=True)
        conexion = sqlite3.connect(self.ruta)
        conexion.row_factory = sqlite3.Row
        conexion.execute("PRAGMA foreign_keys = ON")
        return conexion

    # --- definicion de tablas -------------------------------------------------
    def _campos_cliente(self) -> list[tuple[str, str]]:
        campos = self.estructura.buscar_seccion.get(SECCION_CLIENTE, [])
        return [(columna(c), self._tipo(c, TABLA_CLIENTE)) for c in campos]

    def _campos_servicio(self) -> list[tuple[str, str]]:
        # CUENTA no se repite: es la llave primaria de la tabla.
        campos = [c for c in self.estructura.columnas_servicio if c != CAMPO_CUENTA]
        return [(columna(c), self._tipo(c, TABLA_SERVICIO)) for c in campos]

    def _campos_consumo(self) -> list[tuple[str, str]]:
        campos = [
            (columna(c), self._tipo(c, TABLA_CONSUMO))
            for c in self.estructura.campos_consumo_simples
        ]
        for grupo, subcampos in self.estructura.campos_consumo_anidados.items():
            campos += [
                (columna(grupo, sub), self._tipo(sub, TABLA_CONSUMO)) for sub in subcampos
            ]
        return campos

    def _campos_importe(self) -> list[tuple[str, str]]:
        return [(columna(c), self._tipo(c, TABLA_IMPORTE)) for c in self.estructura.importe]

    @staticmethod
    def _tipo(campo: str, tabla: str) -> str:
        return _TIPOS_EXPLICITOS.get(campo, _TIPO_POR_DEFECTO[tabla])

    def tablas(self) -> dict[str, list[str]]:
        """DDL de cada tabla: llaves fijas + columnas derivadas de estructuraPDF.json."""
        def declarar(campos: list[tuple[str, str]]) -> str:
            return ",\n  ".join(f"{nombre} {tipo}" for nombre, tipo in campos)

        periodo = columna(CAMPO_PERIODO)
        return {
            TABLA_CLIENTE: [
                f"""CREATE TABLE IF NOT EXISTS {TABLA_CLIENTE} (
  cuenta TEXT PRIMARY KEY,
  {declarar(self._campos_cliente())},
  actualizado_en TEXT NOT NULL
)"""
            ],
            TABLA_SERVICIO: [
                f"""CREATE TABLE IF NOT EXISTS {TABLA_SERVICIO} (
  cuenta TEXT PRIMARY KEY REFERENCES {TABLA_CLIENTE}(cuenta) ON DELETE CASCADE,
  {declarar(self._campos_servicio())},
  actualizado_en TEXT NOT NULL
)"""
            ],
            TABLA_CONSUMO: [
                f"""CREATE TABLE IF NOT EXISTS {TABLA_CONSUMO} (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  cuenta TEXT NOT NULL REFERENCES {TABLA_CLIENTE}(cuenta) ON DELETE CASCADE,
  {declarar(self._campos_consumo())},
  metodo TEXT,
  archivo TEXT,
  actualizado_en TEXT NOT NULL,
  UNIQUE (cuenta, {periodo})
)""",
                f"CREATE INDEX IF NOT EXISTS ix_{TABLA_CONSUMO}_cuenta ON {TABLA_CONSUMO}(cuenta)",
            ],
            TABLA_IMPORTE: [
                f"""CREATE TABLE IF NOT EXISTS {TABLA_IMPORTE} (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  cuenta TEXT NOT NULL REFERENCES {TABLA_CLIENTE}(cuenta) ON DELETE CASCADE,
  {periodo} TEXT,
  {declarar(self._campos_importe())},
  actualizado_en TEXT NOT NULL,
  UNIQUE (cuenta, {periodo})
)""",
                f"CREATE INDEX IF NOT EXISTS ix_{TABLA_IMPORTE}_cuenta ON {TABLA_IMPORTE}(cuenta)",
            ],
        }

    def inicializar(self) -> None:
        """Crea lo que falte. Si la base ya existe y estructuraPDF.json creció, agrega columnas."""
        with self.conectar() as conexion:
            for sentencias in self.tablas().values():
                for sentencia in sentencias:
                    conexion.execute(sentencia)
            self._agregar_columnas_faltantes(conexion)

    def _agregar_columnas_faltantes(self, conexion: sqlite3.Connection) -> None:
        esperadas = {
            TABLA_CLIENTE: self._campos_cliente(),
            TABLA_SERVICIO: self._campos_servicio(),
            TABLA_CONSUMO: self._campos_consumo(),
            TABLA_IMPORTE: self._campos_importe(),
        }
        for tabla, campos in esperadas.items():
            existentes = {
                fila["name"] for fila in conexion.execute(f"PRAGMA table_info({tabla})")
            }
            for nombre, tipo in campos:
                if nombre not in existentes:
                    conexion.execute(f"ALTER TABLE {tabla} ADD COLUMN {nombre} {tipo}")

    # --- guardado -------------------------------------------------------------
    def guardar(
        self, datos: dict[str, Any], *, metodo: str, archivo: str | None = None
    ) -> dict[str, Any]:
        """Escribe el recibo en las cuatro tablas. Repetir cuenta+periodo actualiza."""
        servicio = datos.get("columnas_servicio") or {}
        cuenta = servicio.get(CAMPO_CUENTA)
        if not cuenta:
            # Sin cuenta no hay con que ligar las tablas (pasa si el OCR no la pudo leer).
            return {"ok": False, "motivo": f"El recibo no trae {CAMPO_CUENTA}; no se guardo"}

        cuenta = str(cuenta)
        consumo = datos.get("columnas_consumo") or {}
        importe = datos.get("importe") or {}
        cliente = (datos.get("buscar_seccion") or {}).get(SECCION_CLIENTE) or {}
        periodo = consumo.get(CAMPO_PERIODO)
        ahora = datetime.now(timezone.utc).isoformat(timespec="seconds")

        valores_cliente = {
            columna(c): cliente.get(c) for c in self.estructura.buscar_seccion.get(SECCION_CLIENTE, [])
        }
        valores_servicio = {
            columna(c): servicio.get(c)
            for c in self.estructura.columnas_servicio
            if c != CAMPO_CUENTA
        }
        valores_consumo: dict[str, Any] = {
            columna(c): consumo.get(c) for c in self.estructura.campos_consumo_simples
        }
        for grupo, subcampos in self.estructura.campos_consumo_anidados.items():
            anidado = consumo.get(grupo) or {}
            for sub in subcampos:
                valores_consumo[columna(grupo, sub)] = _a_sqlite(anidado.get(sub))
        valores_consumo |= {"metodo": metodo, "archivo": archivo}
        valores_importe = {columna(c): importe.get(c) for c in self.estructura.importe}
        valores_importe[columna(CAMPO_PERIODO)] = periodo

        with self.conectar() as conexion:
            parecida = self._cuenta_parecida(conexion, cuenta)
            self._upsert(conexion, TABLA_CLIENTE, {"cuenta": cuenta}, valores_cliente, ahora)
            self._upsert(conexion, TABLA_SERVICIO, {"cuenta": cuenta}, valores_servicio, ahora)
            llave = {"cuenta": cuenta, columna(CAMPO_PERIODO): periodo}
            self._upsert(
                conexion,
                TABLA_CONSUMO,
                llave,
                {k: v for k, v in valores_consumo.items() if k != columna(CAMPO_PERIODO)},
                ahora,
            )
            self._upsert(
                conexion,
                TABLA_IMPORTE,
                llave,
                {k: v for k, v in valores_importe.items() if k != columna(CAMPO_PERIODO)},
                ahora,
            )

        resultado = {
            "ok": True,
            "base": str(self.ruta),
            "cuenta": cuenta,
            "periodo": periodo,
            "tablas": [TABLA_CLIENTE, TABLA_SERVICIO, TABLA_CONSUMO, TABLA_IMPORTE],
        }
        if parecida:
            resultado["aviso"] = (
                f"La cuenta se parece a '{parecida}', ya registrada. El OCR confunde "
                f"O con 0 e I con 1: verifica cual de las dos es la correcta."
            )
        return resultado

    @staticmethod
    def _cuenta_parecida(conexion: sqlite3.Connection, cuenta: str) -> str | None:
        """Detecta una cuenta ya registrada que solo difiera en O/0 e I/1.

        No se corrige el dato (hay cuentas con letra O real): solo se avisa, porque el
        OCR confunde esos caracteres y si no se nota queda el mismo cliente duplicado.
        """
        def normalizar(texto: str) -> str:
            return texto.upper().replace("O", "0").replace("I", "1")

        objetivo = normalizar(cuenta)
        for fila in conexion.execute(f"SELECT cuenta FROM {TABLA_CLIENTE}"):
            registrada = fila["cuenta"]
            if registrada != cuenta and normalizar(registrada) == objetivo:
                return registrada
        return None

    @staticmethod
    def _upsert(
        conexion: sqlite3.Connection,
        tabla: str,
        llave: dict[str, Any],
        valores: dict[str, Any],
        ahora: str,
    ) -> None:
        """INSERT ... ON CONFLICT(llave) DO UPDATE: reprocesar un recibo lo corrige."""
        columnas = {**llave, **valores, "actualizado_en": ahora}
        nombres = ", ".join(columnas)
        marcadores = ", ".join("?" for _ in columnas)
        asignaciones = ", ".join(f"{c}=excluded.{c}" for c in columnas if c not in llave)
        conexion.execute(
            f"INSERT INTO {tabla} ({nombres}) VALUES ({marcadores}) "
            f"ON CONFLICT ({', '.join(llave)}) DO UPDATE SET {asignaciones}",
            list(columnas.values()),
        )


def _a_sqlite(valor: Any) -> Any:
    """SQLite no tiene booleanos: true/false se guardan como 1/0."""
    return int(valor) if isinstance(valor, bool) else valor


base_datos_service = BaseDatosService()
