# CLAUDE.md

Guía de trabajo del repositorio. `readme.md` es el **manual de uso de la API** para quien
la consume: no se mezcla nada de bitácora ni de implementación ahí, pero **se actualiza
cuando cambien endpoints, parámetros, respuesta o configuración**.

## Instrucciones recibidas

Bitácora de lo que ha pedido el usuario, en orden. **Cada instrucción nueva se agrega aquí
antes de implementarla.**

### 2026-09-17 — Instrucción inicial

1. Guardar todas las instrucciones que se den (al principio en `readme.md`; desde el
   2026-09-18 viven en este archivo).
2. Crear una API con **Litestar**. El entorno (`librerias/`) ya está creado y ya
   están instalados litestar, uvicorn, pdflib y dotenv.
3. La estructura del proyecto ya está creada: carpetas `Controller` y `Service` dentro de `app`.
4. Los PDF **editables** se leen con pdflib; los que son **OCR** se procesan con un
   servicio local de **LM Studio** ya levantado, cuya configuración está en `.env`.
5. Crear también el `claude.md`.
6. En la carpeta `configuracionPDF` viene un archivo JSON con la configuración de lectura
   del OCR y del texto en PDF y dos PDF de ejemplo:
   - `recibo_CFE_imagen.pdf` — es para el OCR.
   - `recibo_CFE.pdf` — es para el texto.
7. En el Controller crear el método **`extraer_texto`** para leer el archivo 2 y regresar
   en un JSON la estructura leída conforme a `estructuraPDF.json`, y otro método
   **`extraer_ocr_ia`** que regrese también en JSON la estructura conforme a `estructuraPDF.json`.
8. Al terminar, avisar para indicar cómo guardar esa información en una base de datos **SQLite**.

### 2026-09-17 — Segunda instrucción

9. 8 minutos para leer el OCR es mucho. Se instaló **PyTorch**: intentar leerlo con esa
   librería y crear otro método en el Controller llamado **`extraer_ocr_pytorch`**,
   a ver si mejoran los tiempos.
10. Con **LM Studio**, ver si se puede utilizar la **tarjeta gráfica** de la computadora.

### 2026-09-18 — Tercera instrucción

11. Pasar el contenido de `readme.md` a `claude.md`, para que `readme.md` quede como el
    archivo final de uso de la API.

> **Pendiente**: persistencia en SQLite (punto 8), a la espera de las indicaciones del usuario.
> `DB_CONNECTION` y `DB_DATABASE` ya están en el `.env` y `app/config.py` expone `settings.db_path`.

## Qué es

API Litestar que extrae datos de recibos de CFE. Tres caminos según el PDF:

- **con capa de texto** → `pdfplumber` ([app/Service/pdf_texto_service.py](app/Service/pdf_texto_service.py)), ~0.1 s
- **escaneado, rápido** → EasyOCR/PyTorch ([app/Service/pdf_ocr_pytorch_service.py](app/Service/pdf_ocr_pytorch_service.py)), ~11 s
- **escaneado, exacto** → modelo de visión en LM Studio ([app/Service/pdf_ocr_service.py](app/Service/pdf_ocr_service.py)), minutos

Los tres devuelven **exactamente el mismo JSON**. Los dos primeros comparten
[app/Service/extractor_layout.py](app/Service/extractor_layout.py): reciben palabras con
coordenadas **en puntos de la página PDF** y de ahí arman la respuesta. Las cajas de OCR
(en píxeles) se dividen entre la escala de render para entrar en ese mismo sistema de
coordenadas; por eso los umbrales geométricos no se duplican por camino.

## Comandos

```powershell
librerias\Scripts\activate                  # entorno virtual del proyecto
uvicorn main:app --reload                   # API en http://127.0.0.1:8000
librerias\Scripts\python.exe -m pip install -r requirements.txt
```

Prueba rápida sin levantar el servidor:

```powershell
librerias\Scripts\python.exe -c "import json; from app.Service.pdf_texto_service import pdf_texto_service as s; print(json.dumps(s.extraer('configuracionPDF/recibo_CFE.pdf'), ensure_ascii=False, indent=1))"
```

En consola usar `PYTHONIOENCODING=utf-8`: el texto del recibo trae acentos y flechas que
revientan con la codificación cp1252 de Windows.

## Reglas del proyecto

1. **`configuracionPDF/estructuraPDF.json` manda.** Define qué campos se extraen y la forma
   del JSON de salida. No se escriben listas de campos en el código: se leen de ahí con
   `EstructuraPDF` ([app/Service/estructura_service.py](app/Service/estructura_service.py)).
   Agregar un campo al JSON debe bastar para que aparezca en los tres métodos.
   - `plantilla()` → esqueleto de la respuesta (mismas llaves, mismo orden).
   - `json_schema()` → el esquema que se le pasa al modelo de visión.
2. **Código y comentarios en español**, igual que los nombres de endpoints y campos.
3. **Configuración solo desde `.env`**, vía `app/config.py`. Nada de URLs, modelos ni rutas
   de base de datos escritos en el código.
4. **Controller delgado**: valida entrada, traduce errores a HTTP y delega. La lógica de
   extracción vive en `app/Service/`.
5. Identificadores (servicio, cuenta, tarifa, medidor) se devuelven como **texto** para
   conservar ceros a la izquierda; montos y lecturas como **número**.

## Arquitectura

```
main.py                       Litestar app + OpenAPI en /schema
app/config.py                 Settings desde .env (BASE_DIR, CONFIG_PDF_DIR, ESTRUCTURA_JSON)
app/Controller/
  pdf_controller.py           PDFController: extraer_texto, extraer_ocr_pytorch,
                              extraer_ocr_ia, salud
app/Service/
  estructura_service.py       estructuraPDF.json -> plantilla + JSON Schema
  extractor_layout.py         palabras con coordenadas -> JSON (regex por etiqueta,
                              columnas por X, casillas, importes)
  pdf_texto_service.py        pdfplumber -> extractor_layout
  pdf_ocr_pytorch_service.py  pypdfium2 -> EasyOCR -> palabras en puntos -> extractor_layout
  pdf_ocr_service.py          pypdfium2 -> PNG base64 -> modelo de visión -> normalizar()
  llm_client.py               LM Studio (API estilo OpenAI); LMStudioError
```

Entrada de los endpoints, por prioridad: archivo multipart (`archivo`) → parámetro `ruta`
→ PDF de ejemplo de `configuracionPDF/`. En Litestar, el archivo opcional se recibe con un
dataclass (`FormularioPDF`); anotar `UploadFile | None` directo falla cuando no se manda cuerpo.

## Cómo lee cada método

**`extraer_texto`** (pdfplumber):

- campos `ETIQUETA: valor` por expresión regular, cortando donde empieza la siguiente etiqueta;
- tabla de consumo por coordenada X del encabezado (`Lectura actual`, `Total periodo`, `Precio`, `Subtotal`);
- casillas `Medida`/`Estimada`: cuál de los cuatro recuadros vectoriales contiene la `X`;
- importes: renglón del bloque "Desglose del importe a pagar" (derecha, `x0 >= 290`);
- nombre y calle: bloque superior izquierdo, filtrando la columna derecha antes de armar renglones;
- código de barras: línea de dígitos del talón de pago.

Sin capa de texto responde 400 remitiendo a los endpoints de escaneo.

**`extraer_ocr_pytorch`** (EasyOCR sobre PyTorch):

- render a escala 4.0 (~290 dpi) y OCR en español;
- se descartan cajas con confianza menor a `CONFIANZA_MINIMA` (0.30): logos, sellos, marcas de agua;
- cada caja se reparte en palabras interpolando el ancho por número de caracteres y los
  píxeles se dividen entre la escala para volver a puntos;
- de ahí en adelante es el mismo `ExtractorLayout`. Sin recuadros vectoriales, la `X` se
  adjudica a la etiqueta `Medida`/`Estimada` más cercana.

Los modelos de EasyOCR se cargan una sola vez (~6 s en la primera llamada) y quedan en memoria.

**Precisión medida** contra `recibo_CFE_imagen.pdf`: 27 de 29 campos idénticos a la lectura
por texto. Falla `CUENTA` y `TARIFA` (ver abajo).

**`extraer_ocr_ia`** (modelo de visión en LM Studio):

- render a ~180 dpi, PNG en base64;
- el prompt incluye el JSON Schema derivado de `estructuraPDF.json` y pide salida estructurada;
- la respuesta se vacía en la misma plantilla, ignorando llaves extra y normalizando
  `"null"`, `"N/A"` y cadenas vacías a `null`.

Verificado contra `recibo_CFE_imagen.pdf`: coincide campo por campo con la extracción por texto.

## Detalles del recibo CFE que ya costaron trabajo

- Las palabras del encabezado de la tabla de consumo **no comparten línea base**
  (`Concepto` va ~3 pt más abajo): el renglón de encabezado se detecta por cuántos nombres
  de columna contiene, no buscando la palabra "Concepto".
- `Total periodo` viene apilado en dos renglones; si no se encuentra la frase completa se
  usa el primer token para fijar el centro de la columna.
- El segundo renglón del encabezado se acota a ±12 pt del primero: si no, `(MXN)` de la
  tabla del Mercado Eléctrico Mayorista desplaza la lectura a la tabla equivocada.
- `Medida`/`Estimada` son cuatro rectángulos del PDF; el marcado es el que contiene la `X`.
- "Energía" aparece dos veces en la página; el desglose del importe es el de la derecha (`x0 >= 290`).
- El nombre del titular y `TOTAL A PAGAR:` están a la misma altura en columnas distintas:
  hay que filtrar por columna **antes** de agrupar palabras en renglones.
- `TOTAL A PAGAR:` trae el valor en el renglón siguiente; el extractor lo contempla.
- El OCR pierde acentos (`LIMITE`, `Energia`): las etiquetas se comparan sin acentos con
  una traducción **1:1** (`str.maketrans`), para que las posiciones del renglón original
  sigan sirviendo al cortar el valor. Sin esto, `LÍMITE DE PAGO` sale `null` y `Energía`
  toma el importe de la tabla del Mercado Eléctrico.
- El OCR captura el logo `CFE` y la leyenda del membrete arriba del nombre del titular;
  se descartan con `_RUIDO_ENCABEZADO` en `_superior_izquierda`.
- EasyOCR entrega segmentos, no palabras: se reparten interpolando el ancho de la caja por
  número de caracteres, que basta para asignar columnas.
- OCR y alfanuméricos: `CUENTA` y `TARIFA` salen con `O`/`0` e `I`/`1` confundidos
  (`32DN7ODO11002050`, `OI`). **No corregir a ciegas** (hay cuentas con letra `O` real);
  si se necesita exactitud en esos campos, usar `extraer_ocr_ia`, que sí los acierta.

## LM Studio y GPU

`GET /pdf/salud` lista los modelos cargados y el dispositivo del OCR. LM Studio solo acepta
`response_format.type` igual a `json_schema` o `text`: si el modelo rechaza el esquema
estricto, el cliente reintenta en texto plano (el prompt ya pide JSON) y conserva el error
original en el mensaje, que es lo que permite diagnosticar.

La máquina tiene una **RTX 5070 Ti Laptop de 12 GB** (driver 610.62):

- LM Studio **ya corre en CUDA** (backend `llama.cpp ... nvidia-cuda12`), pero
  `qwen/qwen3.8-27b` pesa 17.74 GB y con offload completo pide ~20 GiB: no cabe en 12 GB y
  parte de las capas quedan en CPU. De ahí los minutos por página y `LLM_TIMEOUT=900`.
- **No repetir el intento de bajar contexto a 16384 con `--parallel 1`**: sube la VRAM
  usada de 9.0 a 11.3 GB de 12, Windows empieza a paginar memoria de video contra la RAM
  y queda más lento — peticiones con imagen respondiendo 400 y generaciones de más de
  10 minutos sin terminar. La configuración que funciona es la original:
  `lms load qwen/qwen3.8-27b -c 98304 --parallel 4 --ttl 3600 -y`.
- La única salida real es un **modelo de visión que quepa entero en 12 GB** (7B–12B
  cuantizado, ~6–8 GB). Implica descargarlo: decisión del usuario.
- **PyTorch está instalado en build `+cpu`**, así que EasyOCR corre en CPU (aun así, 11 s).
  Para GPU hay que reinstalar con ruedas `cu128` o superiores (la RTX 5070 Ti es Blackwell):
  `pip install --force-reinstall torch torchvision --index-url https://download.pytorch.org/whl/cu128`.
  No hay que tocar el código: `obtener_lector()` ya pasa `gpu=torch.cuda.is_available()`.

Comandos útiles para experimentar:

```powershell
lms ps                                                            # contexto, paralelismo, tamaño
lms load <modelo> -c <contexto> --parallel 1 --estimate-only -y   # estimar sin cargar
nvidia-smi --query-gpu=memory.used,memory.total --format=csv      # VRAM real en uso
```
