# API de lectura de recibos CFE

Recibe un recibo de luz de CFE en PDF y devuelve sus datos en JSON: titular y domicilio,
datos del servicio, lecturas del medidor, consumo del periodo y desglose del importe.

Funciona con recibos **con capa de texto** (los que se descargan del portal de CFE) y con
recibos **escaneados o fotografiados**, que se resuelven por OCR.

---

## Requisitos

- El entorno virtual del proyecto, en `librerias/`.
- **LM Studio** corriendo en local con el modelo de visión cargado (`LLM_VISION_MODEL`
  del `.env`), solo si vas a usar `/pdf/extraer-ocr-ia`. El modelo de revisión
  (`LLM_REVISION_MODEL`) solo tiene que estar descargado: LM Studio lo carga al pedirlo.
  Los otros dos endpoints no necesitan LM Studio.

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

| Endpoint                                  | Tiempo    | Aciertos contra la lectura por texto | LM Studio |
|-------------------------------------------|-----------|--------------------------------------|-----------|
| `/pdf/extraer-texto`                      | ~0.1 s    | 29/29 (es la referencia)             | No        |
| `/pdf/extraer-ocr-pytorch`                | ~11 s     | 27/29 — falla `CUENTA` y `TARIFA` (confunde `O`/`0`, `I`/`1`) | No |
| `/pdf/extraer-ocr-ia` (por omisión)       | ~2.5 min  | **28/29** — solo difiere un signo de puntuación en el domicilio | Sí |
| `/pdf/extraer-ocr-ia?revisar=false`       | ~15 s     | 26/29 — falla código de barras, domicilio y total a pagar | Sí |

Empieza siempre por `/pdf/extraer-texto`: si el PDF no trae capa de texto responde **400**
y ahí eliges entre los endpoints de escaneo.

Para recibos escaneados:

- **Cuando importa la exactitud: `/pdf/extraer-ocr-ia`** tal cual. Es la lectura más
  exacta de las tres: el modelo de visión lee el recibo y un modelo más grande relee los
  campos difíciles. Tarda ~2.5 minutos por la segunda pasada.
- **Cuando importa la velocidad: `/pdf/extraer-ocr-ia?revisar=false`** (~15 s). Lee bien
  cuenta y tarifa, pero no la línea del código de barras ni el total a pagar.
- **Sin LM Studio: `/pdf/extraer-ocr-pytorch`** (~11 s). Lee bien la línea del código de
  barras, pero confunde letras con dígitos en la cuenta y la tarifa.

## Revisión con el modelo grande

`/pdf/extraer-ocr-ia` trabaja en dos pasadas:

1. **Modelo de visión** (`LLM_VISION_MODEL`, hoy `qwen/qwen2.5-vl-7b`). Cabe entero en la
   tarjeta gráfica y lee todo el recibo en unos 15 segundos.
2. **Modelo de revisión** (`LLM_REVISION_MODEL`, hoy `qwen/qwen3.8-27b`). Se le piden
   **solo los campos dudosos**; todo lo que el primero ya leyó bien se queda como está.

Un campo se considera dudoso cuando:

| Motivo        | Qué significa                                                                        |
|---------------|--------------------------------------------------------------------------------------|
| `configurado` | Está en `LLM_REVISION_CAMPOS`: el modelo de visión lo lee mal de forma sistemática   |
| `vacio`       | El modelo de visión no lo encontró                                                    |
| `formato`     | No tiene la forma esperada (la línea del código de barras solo lleva dígitos)        |
| `no cuadra`   | No es consistente con otros campos del recibo (ver abajo)                            |

Las reglas de consistencia se verifican con los propios datos del recibo, sin necesidad de
conocer la respuesta correcta:

- **(lectura actual − lectura anterior) × multiplicador = total del periodo.** Si no da,
  se releen las tres cifras.
- **Energía + IVA = facturado en el periodo** (con tolerancia de 5 centavos). Si no da,
  se releen los tres importes.

Por qué hace falta la lista `LLM_REVISION_CAMPOS`: en producción no hay contra qué comparar,
y algunos errores del modelo chico no dejan huella — el campo trae un valor con la forma
correcta, solo que está mal leído. Esos se identificaron midiendo, y se releen siempre:

```
LLM_REVISION_CAMPOS=arriba de código de barras código;calle;TOTAL A PAGAR:
```

Los nombres van tal como en `estructuraPDF.json`, separados por `;`. Para un campo anidado
se usa punto: `lectura_actual.valor`.

**La revisión nunca empeora el resultado**: si el modelo grande falla, no responde o no
encuentra un campo, se conserva lo que había leído el primero. La respuesta trae un
bloque `revision` que dice qué campos se releyeron, por qué, y qué leyó cada modelo:

```json
"revision": {
  "modelo": "qwen/qwen3.8-27b",
  "segundos": 132.2,
  "campos": [
    {
      "campo": "inferior al centro.arriba de código de barras código",
      "motivo": "configurado",
      "modelo_vision": "142861200719 16300 86-12-29 FIRM-480208 001 CFE",
      "modelo_revision": "01 142861200719 260907 000000170 5",
      "cambio": true
    },
    {
      "campo": "TOTAL A PAGAR:",
      "motivo": "configurado",
      "modelo_vision": 170.1,
      "modelo_revision": 170,
      "cambio": true
    }
  ]
}
```

Si no hubo segunda pasada (`?revisar=false`, `LLM_REVISION_MODEL` vacío, o ningún campo
dudoso), `revision` viene en `null`. Si el modelo grande falló, trae un campo `error`.

El modelo de revisión no necesita estar cargado de antemano: LM Studio lo carga solo la
primera vez que se le pide (unos 25 segundos extra en esa primera llamada).

## Límite de exactitud y hardware

Medido sobre el recibo de ejemplo escaneado:

| Configuración                                  | Tiempo    | Exactitud        |
|------------------------------------------------|-----------|------------------|
| Solo el modelo de 7B                           | ~15 s     | 26/29 (90 %)     |
| **7B + revisión con el de 27B (por omisión)**  | ~2.5 min  | **28/29 (97 %)** |
| Solo el modelo de 27B, recibo completo         | 489 s     | —                |

El único campo que todavía difiere es el domicilio, **por un solo carácter**: el modelo
lee `XOCHIMILCO.CDMX` donde el recibo dice `XOCHIMILCO,CDMX`.

La exactitud ya está ahí; **lo que cuesta es el tiempo, y ese tiempo es de hardware**.
El equipo tiene una **RTX 5070 Ti Laptop de 12 GB**. El modelo de 7B (6 GB) cabe entero y
responde en segundos. El de 27B pesa 17.74 GB y necesita ~20 GiB para correr completo en
la tarjeta: no cabe, la mayor parte de sus capas se ejecutan en CPU, y por eso la segunda
pasada tarda 132 segundos aunque solo se le pidan tres campos. Pedirle solo los campos
dudosos en lugar del recibo completo ya la bajó de 489 a 132 segundos; más allá de eso,
el límite es la memoria de la tarjeta.

Hay además un segundo efecto en la misma dirección. La letra chica se resuelve subiendo la
resolución con la que se rasteriza la página, pero cada aumento de resolución multiplica
los tokens de imagen y por lo tanto la memoria de contexto. Hoy se trabaja a ~180 dpi
porque es lo que el presupuesto de VRAM permite.

| Tarjeta                            | VRAM  | Qué permite                                                                 |
|------------------------------------|-------|------------------------------------------------------------------------------|
| RTX 5070 Ti Laptop (actual)        | 12 GB | 7B completo en GPU; el 27B corre casi todo en CPU → revisión de ~2 min        |
| **RTX 5090**                       | 32 GB | 27B–32B completo en GPU: la revisión pasa de minutos a segundos, con más resolución |
| **RTX PRO 6000 Blackwell**         | 96 GB | Modelos de 72B, o el 7B y el 27B cargados a la vez sin competir por memoria  |

Con cualquiera de esas dos tarjetas, el modelo de revisión correría **completo en GPU**:
la misma exactitud del 97 % en segundos en lugar de minutos, y margen para acercarse al
100 % —más resolución, o un modelo grande que lea el recibo entero en una sola pasada— **sin
mandar un solo recibo a un servicio externo**. La ruta es **más VRAM, no más código**.

Una precisión honesta sobre el 100 %: las cifras de este documento se midieron sobre el
recibo de ejemplo, y ningún sistema de lectura puede garantizar exactitud perfecta sobre
cualquier documento escaneado. Lo que sí está demostrado es que los campos que el modelo
chico no lee bien **sí los lee el modelo grande**, y que lo que impide usarlo a velocidad
de producción es la memoria de la tarjeta.

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
| `revisar` | query string | `true`      | Solo en `/pdf/extraer-ocr-ia`: releer los campos dudosos con el modelo grande (ver [Revisión con el modelo grande](#revisión-con-el-modelo-grande)) |

### Ejemplos

```bash
# Subir un recibo con capa de texto
curl -X POST -F "archivo=@mi_recibo.pdf" http://127.0.0.1:8000/pdf/extraer-texto

# Subir un recibo escaneado (OCR local, rápido)
curl -X POST -F "archivo=@mi_recibo_escaneado.pdf" http://127.0.0.1:8000/pdf/extraer-ocr-pytorch

# Recibo escaneado con el modelo de visión (con revisión de los campos dudosos)
curl -X POST -F "archivo=@mi_recibo_escaneado.pdf" http://127.0.0.1:8000/pdf/extraer-ocr-ia

# Lo mismo, rápido y sin revisión
curl -X POST -F "archivo=@mi_recibo_escaneado.pdf" "http://127.0.0.1:8000/pdf/extraer-ocr-ia?revisar=false"

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
  `ocr_pytorch` agrega `dispositivo` (`cpu` o `cuda`); `ocr_ia` agrega `modelo` y
  `revision` (qué campos releyó el modelo grande; ver
  [Revisión con el modelo grande](#revisión-con-el-modelo-grande)).
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
LLM_VISION_MODEL=qwen/qwen2.5-vl-7b
LLM_TIMEOUT=900

LLM_REVISION_MODEL=qwen/qwen3.8-27b
LLM_REVISION_CAMPOS=arriba de código de barras código;calle;TOTAL A PAGAR:
```

| Variable            | Para qué                                                          |
|---------------------|-------------------------------------------------------------------|
| `LLM_BASE_URL`      | Servidor de LM Studio (API compatible con OpenAI)                 |
| `LLM_VISION_MODEL`  | Modelo que lee la imagen en `/pdf/extraer-ocr-ia`                 |
| `LLM_REVISION_MODEL`| Modelo grande que relee los campos dudosos. Vacío = sin revisión  |
| `LLM_REVISION_CAMPOS`| Campos que siempre se releen, separados por `;`                  |
| `LLM_TIMEOUT`       | Segundos de espera por la respuesta del modelo                    |
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
