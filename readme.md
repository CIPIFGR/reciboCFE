# API de lectura de recibos CFE

Recibe un recibo de luz de CFE en PDF y devuelve sus datos en JSON: titular y domicilio,
datos del servicio, lecturas del medidor, consumo del periodo y desglose del importe.

Funciona con recibos **con capa de texto** (los que se descargan del portal de CFE) y con
recibos **escaneados o fotografiados**, que se resuelven por OCR.

---

## Requisitos

- El entorno virtual del proyecto, en `librerias/`.
- **LM Studio** corriendo en local, solo si vas a usar `/pdf/extraer-ocr-ia`.
  Los otros dos endpoints no lo necesitan.

## Instalación y arranque

```powershell
librerias\Scripts\activate
librerias\Scripts\python.exe -m pip install -r requirements.txt
uvicorn main:app --reload
```

- API: <http://127.0.0.1:8000>
- Documentación interactiva (Swagger): <http://127.0.0.1:8000/schema/swagger>

Para comprobar que todo responde: `GET /pdf/salud`.

## Endpoints

| Método | Ruta                       | Para qué sirve                                          |
|--------|----------------------------|---------------------------------------------------------|
| POST   | `/pdf/extraer-texto`       | Recibo PDF con capa de texto                            |
| POST   | `/pdf/extraer-ocr-pytorch` | Recibo escaneado, OCR local (no necesita LM Studio)     |
| POST   | `/pdf/extraer-ocr-ia`      | Recibo escaneado, modelo de visión                      |
| GET    | `/pdf/salud`               | Estado de LM Studio y dispositivo del OCR               |

### Cuál usar

| Endpoint                   | Tiempo | Aciertos contra la lectura por texto                        | LM Studio |
|----------------------------|--------|-------------------------------------------------------------|-----------|
| `/pdf/extraer-texto`       | ~0.1 s | 29/29 (es la referencia)                                    | No        |
| `/pdf/extraer-ocr-pytorch` | ~11 s  | 27/29 — falla `CUENTA` y `TARIFA` (confunde `O`/`0`, `I`/`1`) | No        |
| `/pdf/extraer-ocr-ia`      | ~12 s  | 26/29 — acierta `CUENTA` y `TARIFA`; falla la línea del código de barras | Sí |

Empieza siempre por `/pdf/extraer-texto`: si el PDF no trae capa de texto responde **400**
y ahí eliges entre los dos endpoints de escaneo.

Para recibos escaneados, **`/pdf/extraer-ocr-ia` es la opción recomendada**: tarda
prácticamente lo mismo que el OCR local y lee bien el número de cuenta y la tarifa, que es
justo donde el OCR se equivoca. Usa `/pdf/extraer-ocr-pytorch` cuando no quieras depender
de LM Studio, o cuando necesites la línea del código de barras, que esa sí la lee bien.

(Los dos endpoints de escaneo dan el mismo resultado en los 26 campos restantes, incluidos
lecturas, consumo, importes y las casillas medida/estimada.)

## Cómo mandar el PDF

Los tres endpoints de extracción aceptan el archivo de tres maneras, en este orden de
prioridad:

1. **Archivo subido** — `multipart/form-data`, campo `archivo`.
2. **Parámetro `ruta`** — ruta del PDF relativa a la raíz del proyecto.
3. **Sin nada** — usa el PDF de ejemplo correspondiente de `configuracionPDF/`.

| Parámetro | Dónde        | Por omisión | Descripción                                       |
|-----------|--------------|-------------|---------------------------------------------------|
| `archivo` | multipart    | —           | El PDF a leer                                     |
| `ruta`    | query string | —           | PDF ya existente en el servidor                   |
| `pagina`  | query string | `0`         | Página a procesar (la 0 es la primera)            |
| `guardar` | query string | `true`      | Guardar el resultado en la base (ver más abajo)   |

### Ejemplos

```bash
# Subir un recibo con capa de texto
curl -X POST -F "archivo=@mi_recibo.pdf" http://127.0.0.1:8000/pdf/extraer-texto

# Subir un recibo escaneado (OCR local, rápido)
curl -X POST -F "archivo=@mi_recibo_escaneado.pdf" http://127.0.0.1:8000/pdf/extraer-ocr-pytorch

# Recibo escaneado con el modelo de visión (tarda varios minutos)
curl -X POST -F "archivo=@mi_recibo_escaneado.pdf" http://127.0.0.1:8000/pdf/extraer-ocr-ia

# Un PDF que ya está en el servidor, segunda página
curl -X POST "http://127.0.0.1:8000/pdf/extraer-texto?ruta=configuracionPDF/recibo_CFE.pdf&pagina=1"

# Sin argumentos: procesa el recibo de ejemplo
curl -X POST http://127.0.0.1:8000/pdf/extraer-texto

# Extraer sin escribir en la base de datos
curl -X POST "http://127.0.0.1:8000/pdf/extraer-texto?guardar=false"
```

## Respuesta

```json
{
  "metodo": "texto",
  "guardado": {
    "ok": true,
    "base": "Base Datos/database.sqlite",
    "cuenta": "32DN70D011002050",
    "periodo": "23 JUN 26-24 AGO 26",
    "tablas": ["cfe_cliente", "cfe_servicio", "cfe_consumo", "cfe_importe"]
  },
  "datos": {
    "archivo_origen": "CFE",
    "buscar_seccion": {
      "superior izquierda": {
        "nombre_completo": "FIGUEROA ROSAS LUCIO GUILLERMO",
        "calle": "CALLE MONTE BELLO LT66 MZ13, MONTE CARMELO Y CARR SAN PABLO, SAN LUCAS XOCHIMANCAC.P.16300, XOCHIMILCO,CDMX"
      },
      "inferior al centro": {
        "arriba de código de barras código": "01 142861200719 260907 000000170 5"
      }
    },
    "columnas_servicio": {
      "NO. DE SERVICIO": "142861200719",
      "RMU": "16300",
      "CUENTA": "32DN70D011002050",
      "TARIFA": "01",
      "MULTIPLICADOR": "1",
      "NO. MEDIDOR": "414JVA",
      "NO HILOS": "1"
    },
    "columnas_consumo": {
      "total_periodo": 130,
      "LÍMITE DE PAGO": "07 SEP 2026",
      "CORTE A PARTIR": "08 SEP 2026",
      "PERIODO FACTURADO": "23 JUN 26-24 AGO 26",
      "TOTAL A PAGAR:": 170,
      "Subtotal": 146.64,
      "Precio": 1.128,
      "lectura_actual": { "medida": true, "estimada": false, "valor": 5223 },
      "lectura_anterior": { "medida": true, "estimada": false, "valor kWh": 5093 }
    },
    "importe": {
      "Energía": 146.64,
      "IVA 16%": 23.46,
      "Fac. del Periodo": 170.1,
      "Total": 170.1,
      "Apoyo Gubernamental": 273.82
    }
  }
}
```

Puntos a tener en cuenta al consumir la respuesta:

- **`datos` siempre trae las mismas llaves**, sin importar el endpoint que la haya
  producido. Un campo que no se pudo leer viene en `null`, nunca se omite.
- **`metodo`** indica de dónde salieron los datos: `texto`, `ocr_pytorch` u `ocr_ia`.
  `ocr_pytorch` agrega `dispositivo` (`cpu` o `cuda`) y `ocr_ia` agrega `modelo`.
- **`guardado`** dice qué pasó con la base de datos (ver la sección siguiente). Si falla
  el guardado, la extracción igual se devuelve: `guardado.ok` viene en `false` con el motivo.
- **Los identificadores llegan como texto** (`"01"`, `"142861200719"`), para no perder los
  ceros a la izquierda. Los montos y las lecturas llegan como número (`146.64`, `5223`).
- **`medida` y `estimada`** son booleanos: reflejan cuál de las dos casillas del recibo
  viene marcada con X.
- La forma del JSON sale de `configuracionPDF/estructuraPDF.json`. Si ahí se agrega un
  campo, aparece en la respuesta de los tres endpoints.

## Base de datos

Cada extracción se guarda en SQLite, en el archivo que indique `DB_DATABASE` del `.env`
(por omisión `Base Datos/database.sqlite`). Las tablas se crean solas al arrancar la API.
SQLite no maneja esquemas, así que en lugar de `cfe.servicio` las tablas llevan prefijo:

| Tabla          | Un renglón por…       | Contenido                                                    |
|----------------|-----------------------|--------------------------------------------------------------|
| `cfe_cliente`  | cuenta                | `nombre_completo`, `calle`                                   |
| `cfe_servicio` | cuenta                | no. de servicio, RMU, tarifa, multiplicador, medidor, hilos  |
| `cfe_consumo`  | cuenta + periodo      | lecturas, consumo, fechas, subtotal, precio, total a pagar   |
| `cfe_importe`  | cuenta + periodo      | energía, IVA, fac. del periodo, total, apoyo gubernamental   |

Todas se ligan por `cuenta`; `cfe_consumo` y `cfe_importe` además por `periodo_facturado`.
`cfe_consumo` guarda también con qué `metodo` y de qué `archivo` salió cada renglón.

**Volver a procesar el mismo recibo actualiza el registro en vez de duplicarlo** (la llave
es cuenta + periodo facturado). Eso permite corregir: si primero lo pasaste por OCR y
luego por el modelo de visión, el segundo resultado pisa al primero.

Las casillas `medida` y `estimada` se guardan como `1` y `0`, que es como SQLite maneja
los booleanos. Los nombres de columna son la versión sin acentos ni espacios del campo
(`LÍMITE DE PAGO` → `limite_de_pago`, `lectura_actual.valor` → `lectura_actual_valor`).

Ejemplo de consulta:

```sql
SELECT cl.nombre_completo, s.no_de_servicio, s.tarifa,
       co.periodo_facturado, co.total_periodo, i.total
FROM cfe_cliente cl
JOIN cfe_servicio s  ON s.cuenta = cl.cuenta
JOIN cfe_consumo  co ON co.cuenta = cl.cuenta
JOIN cfe_importe  i  ON i.cuenta = cl.cuenta AND i.periodo_facturado = co.periodo_facturado;
```

### Cuándo no se guarda

- Con `?guardar=false`.
- Si el recibo no trae `CUENTA`, porque es el campo que liga las cuatro tablas. La
  respuesta lo dice en `guardado.motivo` y la extracción se devuelve igual.

### Aviso de cuenta parecida

Si llega una cuenta que solo difiere de una ya registrada en `O`/`0` o `I`/`1`, la
respuesta incluye `guardado.aviso`. Es el caso típico de `/pdf/extraer-ocr-pytorch`: el
recibo se guarda tal como se leyó —no se corrige solo, porque hay cuentas con letra `O`
real— pero queda señalado para que revises cuál de las dos es la buena.

```json
"guardado": {
  "ok": true,
  "cuenta": "32DN7ODO11002050",
  "aviso": "La cuenta se parece a '32DN70D011002050', ya registrada. El OCR confunde O con 0 e I con 1: verifica cual de las dos es la correcta."
}
```

## Errores

| Código | Cuándo                                                                       |
|--------|------------------------------------------------------------------------------|
| 400    | El PDF no tiene capa de texto (en `/pdf/extraer-texto`), o el archivo va vacío |
| 400    | La página pedida no existe                                                    |
| 404    | La `ruta` indicada no existe en el servidor                                   |
| 502    | LM Studio no responde o rechazó la petición (endpoints que lo usan)           |

El detalle viene en el campo `detail`:

```json
{
  "status_code": 400,
  "detail": "El PDF no tiene capa de texto; usa /pdf/extraer-ocr-pytorch o /pdf/extraer-ocr-ia"
}
```

## Configuración (`.env`)

```
DB_CONNECTION=sqlite
DB_DATABASE=Base Datos/database.sqlite

LLM_BASE_URL=http://localhost:1234/v1
LLM_MODEL=qwen2.5-coder-14b-instruct
LLM_VISION_MODEL=qwen/qwen3.8-27b
LLM_TIMEOUT=900
```

| Variable            | Para qué                                                          |
|---------------------|-------------------------------------------------------------------|
| `LLM_BASE_URL`      | Servidor de LM Studio (API compatible con OpenAI)                 |
| `LLM_VISION_MODEL`  | Modelo que lee la imagen en `/pdf/extraer-ocr-ia`                 |
| `LLM_TIMEOUT`       | Segundos de espera; el modelo actual tarda minutos por página     |
| `DB_DATABASE`       | Archivo SQLite donde se guardan los recibos                       |
| `DB_CONNECTION`     | Motor de base de datos; hoy solo se usa SQLite                    |

## Qué hay dentro

```
main.py               arranque de la API
app/Controller/       endpoints
app/Service/          lectura de PDF, OCR, cliente de LM Studio y guardado en SQLite
configuracionPDF/     estructuraPDF.json (define los campos) y dos recibos de ejemplo
Base Datos/           database.sqlite
```

Los detalles de implementación y las decisiones de diseño están en [CLAUDE.md](CLAUDE.md).
