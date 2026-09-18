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

| Método | Ruta                       | Para qué sirve                                   |
|--------|----------------------------|--------------------------------------------------|
| POST   | `/pdf/extraer-texto`       | Recibo PDF con capa de texto                     |
| POST   | `/pdf/extraer-ocr-pytorch` | Recibo escaneado, OCR local                      |
| POST   | `/pdf/extraer-ocr-ia`      | Recibo escaneado, modelo de visión (más exacto)  |
| GET    | `/pdf/salud`               | Estado de LM Studio y dispositivo del OCR        |

### Cuál usar

| Endpoint                   | Tiempo por página | Exactitud                        | Necesita LM Studio |
|----------------------------|-------------------|----------------------------------|--------------------|
| `/pdf/extraer-texto`       | ~0.1 s            | Total                            | No                 |
| `/pdf/extraer-ocr-pytorch` | ~11 s             | 27 de 29 campos                  | No                 |
| `/pdf/extraer-ocr-ia`      | varios minutos    | Total                            | Sí                 |

Empieza siempre por `/pdf/extraer-texto`: si el PDF no trae capa de texto responde **400**
y ahí eliges entre los dos endpoints de escaneo.

Diferencia entre los dos de escaneo: el OCR local confunde `O` con `0` e `I` con `1` en
identificadores alfanuméricos (`CUENTA` y `TARIFA`). Si esos dos campos tienen que salir
exactos, usa `/pdf/extraer-ocr-ia`; si necesitas velocidad, usa el de PyTorch y valida
`CUENTA` y `TARIFA` contra otra fuente.

## Cómo mandar el PDF

Los tres endpoints de extracción aceptan el archivo de tres maneras, en este orden de
prioridad:

1. **Archivo subido** — `multipart/form-data`, campo `archivo`.
2. **Parámetro `ruta`** — ruta del PDF relativa a la raíz del proyecto.
3. **Sin nada** — usa el PDF de ejemplo correspondiente de `configuracionPDF/`.

| Parámetro | Dónde        | Por omisión | Descripción                            |
|-----------|--------------|-------------|----------------------------------------|
| `archivo` | multipart    | —           | El PDF a leer                          |
| `ruta`    | query string | —           | PDF ya existente en el servidor        |
| `pagina`  | query string | `0`         | Página a procesar (la 0 es la primera) |

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
```

## Respuesta

```json
{
  "metodo": "texto",
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
- **Los identificadores llegan como texto** (`"01"`, `"142861200719"`), para no perder los
  ceros a la izquierda. Los montos y las lecturas llegan como número (`146.64`, `5223`).
- **`medida` y `estimada`** son booleanos: reflejan cuál de las dos casillas del recibo
  viene marcada con X.
- La forma del JSON sale de `configuracionPDF/estructuraPDF.json`. Si ahí se agrega un
  campo, aparece en la respuesta de los tres endpoints.

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
| `DB_CONNECTION`, `DB_DATABASE` | Base de datos (persistencia aún no implementada)        |

## Qué hay dentro

```
main.py               arranque de la API
app/Controller/       endpoints
app/Service/          lectura de PDF, OCR y cliente de LM Studio
configuracionPDF/     estructuraPDF.json (define los campos) y dos recibos de ejemplo
```

Los detalles de implementación y las decisiones de diseño están en [CLAUDE.md](CLAUDE.md).
