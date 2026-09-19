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

### 2026-09-18 — Cuarta instrucción

12. Con SQLite en la carpeta `Base Datos`, guardar el contenido de lo que se extrae, con
    estas tablas (el usuario las nombró `cfe.servicio`, `cfe.consumo`, `cfe.importe` y
    `cfe.cliente`):
    - **servicio** ← `columnas_servicio`
    - **consumo** ← `columnas_consumo`, incluidas `lectura_actual` y `lectura_anterior`
    - **importe** ← `importe`
    - **cliente** ← `nombre_completo`, `calle` y la cuenta

    Decisiones consultadas y aprobadas por el usuario: prefijo `cfe_` (SQLite no tiene
    esquemas), guardado automático en cada extracción con `?guardar=false` para omitirlo,
    y reprocesar el mismo recibo **actualiza** en vez de duplicar.

### 2026-09-18 — Quinta instrucción

13. El usuario cambió el modelo a **Qwen2.5 VL 7B** para que quepa en su tarjeta gráfica.
    Terminar los procesos anteriores y usar ese modelo para el OCR.
    Hecho: se descargó el 27B, se cargó `qwen/qwen2.5-vl-7b` con offload completo a GPU y
    se apuntó `LLM_VISION_MODEL` del `.env` al modelo nuevo. **489 s → ~12 s.**

### 2026-09-18 — Sexta instrucción

14. Agregar en `readme.md` que con una tarjeta gráfica más potente (RTX 5090 o
    RTX PRO 6000 Blackwell) se podría cerrar la brecha del 93 % de exactitud hacia el
    100 % con modelos locales, ya que el techo actual es por capacidad de hardware.
    Hecho en la sección "Límite de exactitud y hardware", apoyada en lo medido: el
    27B sí leyó la línea del código de barras que el 7B falla, pero tardaba 489 s por no
    caber en 12 GB.

### 2026-09-19 — Séptima instrucción

15. En `extraer-ocr-ia` el 7B obtiene 26/29 campos. Los 3 que no localiza, intentarlos con
    **Qwen3.8 27B**; los que sí encuentra, dejarlos con **Qwen2.5 VL 7B**.
    Hecho: segunda pasada en `pdf_ocr_service.py`. Resultado medido: **26/29 → 28/29**,
    16.7 s → 143.5 s (132 s del 27B). Solo difiere `calle` por un carácter
    (`XOCHIMILCO.CDMX` contra `XOCHIMILCO,CDMX`). Con `?revisar=false` se omite.

    Matiz importante que se le explicó al usuario: los 3 campos **no venían vacíos**, el
    7B los leía mal (código de barras con otros números, una errata en el domicilio y el
    `Total` del desglose en lugar de `TOTAL A PAGAR`). En producción no hay respuesta
    correcta contra la cual comparar, así que "los que no localiza" se detectan por
    reglas verificables (vacío, formato, cuadres) **más** una lista medida de campos que
    el 7B lee mal siempre (`LLM_REVISION_CAMPOS`). Solo el código de barras era
    detectable por regla; los otros dos únicamente por la lista.

## Qué es

API Litestar que extrae datos de recibos de CFE. Tres caminos según el PDF:

- **con capa de texto** → `pdfplumber` ([app/Service/pdf_texto_service.py](app/Service/pdf_texto_service.py)), ~0.1 s
- **escaneado, rápido** → EasyOCR/PyTorch ([app/Service/pdf_ocr_pytorch_service.py](app/Service/pdf_ocr_pytorch_service.py)), ~11 s
- **escaneado, con modelos de visión** → LM Studio ([app/Service/pdf_ocr_service.py](app/Service/pdf_ocr_service.py)): 7B en ~15 s y revisión de campos dudosos con el 27B, ~2.5 min en total

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
  base_datos_service.py       DDL derivado de estructuraPDF.json + upsert en SQLite
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

**`extraer_ocr_ia`** (modelos de visión en LM Studio, dos pasadas):

- render a ~180 dpi, PNG en base64, **una sola vez** para las dos pasadas;
- 1a pasada con `LLM_VISION_MODEL` (7B): el prompt incluye el JSON Schema completo derivado
  de `estructuraPDF.json` y pide salida estructurada;
- `campos_dudosos()` arma `{ruta: motivo}`: `configurado` (`LLM_REVISION_CAMPOS`), `vacio`,
  `formato` (código de barras solo dígitos) y `no cuadra` (lecturas contra `total_periodo`
  con multiplicador; `Energía + IVA 16%` contra `Fac. del Periodo`, ±0.05);
- 2a pasada con `LLM_REVISION_MODEL` (27B) usando `_esquema_parcial()`: el JSON Schema
  recortado a esos campos. Pedir 3 campos en vez del recibo completo bajó el 27B de 489 s
  a 132 s;
- se fusiona solo lo que el 27B sí trajo; si falla (`LMStudioError`) se conserva lo del
  7B y se reporta en `revision.error`. **La revisión nunca empeora la extracción**;
- ambas respuestas pasan por `normalizar()`: misma plantilla, mismos tipos. Una respuesta
  parcial deja en `None` lo que no trae, por eso la fusión ignora los `None`.

Nombres de campo con punto (`NO. DE SERVICIO`, `Fac. del Periodo`): `_resolver()` busca
primero el nombre exacto y solo si no existe interpreta el último punto como
`grupo.subcampo` (`lectura_actual.valor`). No partir por `.` a ciegas.

El 27B se carga solo por JIT de LM Studio al pedirlo (contexto 8192, TTL 1 h) y convive
con el 7B: VRAM ~11.8 GB de 12, casi todo el 27B en CPU. Probado sin caídas.

**Precisión medida** con `qwen/qwen2.5-vl-7b` solo (`?revisar=false`): 26 de 29 campos. Acierta `CUENTA` y `TARIFA`
(donde falla EasyOCR), pero no lee bien la línea de dígitos del código de barras —son muy
chicos— y deja alguna errata en el domicilio. `TOTAL A PAGAR:` lo da como 170.1 (el total
con centavos, que es el renglón `Total` del desglose) mientras el extractor de texto lee
el 170 del recuadro `TOTAL A PAGAR`, que es el campo pedido: el 7B confunde renglones.
Se agregó al prompt una instrucción específica y **no lo corrigió**; lo corrige la
revisión con el 27B. Subir `ESCALA_RENDER` a 3.5 acerca el código de barras pero no
lo corrige y cuesta 3 s más, por eso se quedó en 2.5.

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

## Base de datos

`app/Service/base_datos_service.py`. `main.py` llama a `inicializar()` en `on_startup`.

- **Las columnas salen de `estructuraPDF.json`**, igual que el JSON de la respuesta: la
  función `columna()` convierte la etiqueta del recibo en nombre SQL
  (`LÍMITE DE PAGO` → `limite_de_pago`, `('lectura_actual','valor kWh')` →
  `lectura_actual_valor_kwh`). Agregar un campo al JSON agrega la columna;
  `inicializar()` hace `ALTER TABLE ADD COLUMN` si la base ya existía.
- **Llaves**: `CAMPO_CUENTA` (`CUENTA`) liga las cuatro tablas y `CAMPO_PERIODO`
  (`PERIODO FACTURADO`) distingue recibos. Son constantes del módulo, no listas de campos:
  elegir la llave es decisión de diseño, no configuración.
- **Tipos**: por tabla (`_TIPO_POR_DEFECTO`), con excepciones explícitas en
  `_TIPOS_EXPLICITOS`. `cfe_servicio` y `cfe_cliente` van en TEXT para conservar los ceros
  a la izquierda; `medida`/`estimada` en INTEGER porque SQLite no tiene booleanos.
- **Upsert** con `ON CONFLICT ... DO UPDATE`: reprocesar un recibo lo corrige.
- **Aviso de cuenta parecida**: `_cuenta_parecida()` detecta una cuenta ya registrada que
  solo difiera en `O`/`0` o `I`/`1` y lo reporta en `guardado.aviso`. **No corrige el
  dato**: hay cuentas con letra `O` real. Sin este aviso, el mismo cliente terminaba
  duplicado al guardar la salida del OCR (se comprobó: `32DN7ODO11002050` contra
  `32DN70D011002050`).
- Un fallo de SQLite **no invalida la extracción**: el controlador lo devuelve en
  `guardado.ok = false` con el motivo.

## LM Studio y GPU

`GET /pdf/salud` lista los modelos cargados y el dispositivo del OCR. LM Studio solo acepta
`response_format.type` igual a `json_schema` o `text`: si el modelo rechaza el esquema
estricto, el cliente reintenta en texto plano (el prompt ya pide JSON) y conserva el error
original en el mensaje, que es lo que permite diagnosticar.

La máquina tiene una **RTX 5070 Ti Laptop de 12 GB** (driver 610.62).

**Configuración actual (la buena):** `qwen/qwen2.5-vl-7b` cargado **entero en la GPU**.

```powershell
lms load qwen/qwen2.5-vl-7b --gpu max -c 32768 --parallel 1 --ttl 3600 -y
# 6.04 GB de modelo, 8.8 GB de VRAM ocupada de 12  ->  ~12 s por página
```

**Lección aprendida, no repetir:** antes se usaba `qwen/qwen3.8-27b` (17.74 GB, ~20 GiB
con offload completo). No cabía en 12 GB, parte de las capas iban a CPU y tardaba **489 s
por página**. Se intentó arreglar por configuración —bajar el contexto a 16384 con
`--parallel 1`, y después fijar el offload a mano— y todo salió peor: peticiones con
imagen respondiendo 400, generaciones de más de 10 minutos sin terminar y, con
`--gpu 0.235`, el motor muriéndose a media generación (`{"error":"terminated"}` a los
466 s, seguido de `No engine protocol runtime is registered`). **Cuando el modelo no cabe,
el arreglo es cambiar de modelo, no ajustar la carga.** Medición de VRAM por offload del
27B, por si sirve de referencia: sin modelo 371 MiB · `--gpu 0.15` 7081 · `0.2` 8199 ·
`0.235` 9367 · `0.3` 10705 · auto 11759.

`LLM_TIMEOUT=900` quedó de la época del 27B; con el modelo actual sobra y se puede bajar.

El modelo de visión devuelve todo como texto (`"$170.10"`, `"5,223"`). `normalizar()` en
`pdf_ocr_service.py` aplica los tipos del proyecto para que su salida sea intercambiable
con la de los otros dos métodos y entre bien en SQLite: `buscar_seccion` siempre texto
(si no, el código de barras se convertiría en un número enorme), identificadores como
texto y primer token, consumo e importe a número cuando el texto lo es.

**PyTorch está instalado en build `+cpu`**, así que EasyOCR corre en CPU (aun así, 11 s).
Para GPU hay que reinstalar con ruedas `cu128` o superiores (la RTX 5070 Ti es Blackwell):
`pip install --force-reinstall torch torchvision --index-url https://download.pytorch.org/whl/cu128`.
No hay que tocar el código: `obtener_lector()` ya pasa `gpu=torch.cuda.is_available()`.

Comandos útiles:

```powershell
lms ps                                                            # contexto, paralelismo, tamaño
lms load <modelo> --gpu max -c <contexto> --estimate-only -y      # estimar VRAM sin cargar
nvidia-smi --query-gpu=memory.used,memory.total --format=csv      # VRAM real en uso
```
