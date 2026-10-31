# AGENTS.md — Kipu Pay · Anonimización del corpus de soporte

> Reto: "Vende los datos sin vender a la gente". La fuente de verdad son `ENUNCIADO.md`, `api/fuentes.openapi.yaml`, `api/participante.openapi.yaml`, `validar.mjs` y `sample/`. Si este archivo los contradice, ganan ellos: corrige este archivo en el mismo commit. El diseño detallado está en `DESIGN.md`.

## 0. Antes de escribir código
1. Lee `ENUNCIADO.md`, `api/fuentes.openapi.yaml`, `api/participante.openapi.yaml` y `validar.mjs`.
2. Baja `sample.zip` y compara `sample/` con `sample/expected_output/`, además de inspeccionar `tests_public.json`. Es la referencia exacta de normalización (cómo salen `author`, `thread_id`, el `body` de PDF, HTML y MD, `metadata` y `columns`) y del estilo de los seudónimos. 10 puntos de utilidad miden el parecido con la salida ideal.
3. Corre `node validar.mjs sample/expected_output --public sample`. Tiene que decir `OK: la salida pasa el validador`.
4. **Nunca dispares `VALIDAR ENTRYPOINT`** en la plataforma de Frontier: solo hay 3 corridas oficiales y las activa únicamente un humano siguiendo §9.

## 1. Qué construimos y entregables obligatorios
Un servicio HTTP en una URL pública bajo raíz `/v1`, sin autenticación y todo en UTF-8, junto con los entregables del repositorio:

| Endpoint | Qué hace | Límite |
|---|---|---|
| `GET /v1/health` | `{status: "ok", team, version, models}`. Responde siempre; nunca bloquearlo durante `/v1/run` | Inmediato |
| `POST /v1/run` | Recibe `{sources_url, token}`, lanza la regeneración completa en un **proceso aparte** (`multiprocessing` / `subprocess`) y devuelve `202` con `{job_id}` de inmediato | 2 h |
| `GET /v1/run/{job_id}` | `{status, started_at, finished_at, records: {chats, docs, tables}, cost: {tokens_in, tokens_out, seconds}, error}` | |
| `GET /v1/output/{archivo}` | Sirve `chats.jsonl`, `docs.jsonl`, `tables.jsonl` o `entities.json` de la última corrida terminada (`404` si no hay) | |
| `POST /v1/anonymize` | Recibe `{source, records}` (hasta 50 registros en el esquema normalizado) y devuelve `{records, entities}` | 50 registros, 60 s (corte interno a 45 s) |
| `GET /v1/entities` | Devuelve `{"entities": [...]}` de la última corrida terminada | |

- **Puntaje:** **Fuga 45 · Utilidad 35 · Consistencia 20**. Una solución sólida por reglas, sin modelos, saca ~88. El núcleo determinista va primero; el LLM es para los últimos puntos.
- **Archivos obligatorios en la raíz:** `run.sh` (ejecutable que levanta el servicio), `DESIGN.md`, `DECISIONS.md` (qué consideramos PII, qué conservamos a propósito y por qué, y costo en tokens y segundos), `README.md` y `failure_analysis.md`.

## 2. Stack y comandos
- Python 3.11+, FastAPI, uvicorn, httpx (async), pydantic v2, unidecode, pyahocorasick, rapidfuzz, Faker (`es_CO`), pypdf, beautifulsoup4, lxml, pytest.
- Node 20+ en la misma imagen para correr `node validar.mjs` como compuerta antes de dar una corrida por terminada (`done`).
- **Prohibido:** cualquier servicio o librería de anonimización de PII como producto terminado (AWS Comprehend PII, Presidio como servicio o librería, Private AI, scrubadub, variantes PII de GLiNER y similares). El NER genérico y Faker sí están permitidos. Un test verifica que `requirements.txt` no incluya paquetes prohibidos.

| Comando | Qué hace |
|---|---|
| `./run.sh` | Levanta el servicio HTTP (entregable obligatorio) |
| `make regen` | Corre el pipeline local contra el API de fuentes (desarrollo) |
| `make test` | Tests públicos, tests de la muestra, golden, canario e invariantes |
| `make validate` | `node validar.mjs output/ --public fuentes/` |
| `make score-sample` | Corre el pipeline sobre `sample/` y mide fuga, parecido y entidades conservadas contra `sample/expected_output` |

Variables de entorno (en `.env`, jamás en git):
- `SOURCES_URL` (o alias `SOURCES_URL`) y `SOURCES_TOKEN`: para desarrollo local. En evaluación llegan en el body de `POST /v1/run`.
- `OPENROUTER_API_KEY`, `PSEUDONYM_SECRET`, `STATE_DIR` (disco persistente, default `state/`), `RUN_MODE` (`dev` o `eval`) y, opcional, `OPENROUTER_EVAL_KEY`.

## 3. Contratos Oficiales

### 3.1 API de Fuentes (`/v1`, cabecera `Authorization: Bearer <token>`, 600 req/min, soporta `ETag`)
- **Chats:** `GET /chats/channels`, `GET /chats/users` (perfiles parciales: los clientes no traen `customer_id` ni celular), `GET /chats/{canal}` (fechas) y `GET /chats/{canal}/{fecha}` (export de Slack: `user`, `text`, `ts`, `client_msg_id`, `files`, `reactions`, `edited`).
- **Documentos:** `GET /docs` (`index.json`) y `GET /docs/{id}` (11 PDF binarios, 6 HTML, 9 Markdown con front matter; 2 están en inglés).
- **Tablas:** `GET /tables` y `GET /tables/{nombre}` (`clientes.csv` en Latin-1 con separador `;`, `transacciones.csv` en UTF-8 con `,`, `pqrs.xml` en UTF-8 y `empleados.xml` en UTF-8).
- **Reto:** `GET /record_index` (8.297 IDs iniciales; crece sin aviso), `GET /tests/public` (5 tests públicos) y `GET /sample.zip`.

### 3.2 Archivos de `output/` (Validados por `node validar.mjs`)
1. **`chats.jsonl`:** `{"id": client_msg_id, "thread_id": "<canal>:<ts_primer_mensaje>", "author": "<nombre seudonimizado, NUNCA uid>", "ts": "...", "text": "...", "attachments": [{"filename": "..."}]}` (conserva el mismo número de adjuntos que la entrada).
2. **`docs.jsonl`:** `{"id": "doc_xx", "format": "pdf|html|md", "filename": "...", "title": "...", "body": "...", "metadata": {<exactamente las mismas claves que la entrada>}}` (`body` en Markdown conservando front matter y comentarios HTML si los había).
3. **`tables.jsonl`:** `{"id": "<tabla>:<llave>", "table": "clientes|transacciones|pqrs|empleados", "columns": {<exactamente las mismas columnas que la entrada, TODOS los valores como string>}}` (`clientes:CL-12538`, `transacciones:TX-000158`, `pqrs:PQR-2026-0451`, `empleados:KP-005`).
4. **`entities.json`:** `{"entities": [...]}` donde cada elemento tiene `entity_id`, `type` (`person` o `organization_or_place`), `originals` y `records`:
   - **Si es persona seudonimizada:** `pseudonyms` conteniendo `name` y **ÚNICAMENTE** los atributos que aparezcan textualmente en sus `records` (`email`, `phone`, `cedula`, `handle`, `address`, `birth_iso`, `plate`, `account`, `passport`). La línea 358 de `validar.mjs` reprueba cualquier seudónimo declarado que no aparezca en alguno de sus `records`.
   - **Si es organización, lugar, marca o figura pública conservada:** `"kept": true` y `"reason": "..."`.

## 4. Estructura del repo
```text
run.sh               # Script de arranque (ejecutable)
Makefile             # regen, test, validate, score-sample
app/
  main.py            # FastAPI /v1: health, run, run/{job_id}, output/{archivo}, anonymize, entities
  jobs.py            # Ejecución de /v1/run en proceso separado + compuerta node validar.mjs antes de 'done'
  config.py          # Modelos (fast/heavy), rutas, variables de entorno y control de presupuesto OpenRouter
  sources.py         # Cliente async con ETag, backoff (600 req/min) y detección de crecimiento ([SOURCES_GROWN])
  parse/
    chats.py         # Export de Slack → registro normalizado (thread_id, author, attachments)
    docs.py          # PDF (texto + metadata), HTML (→ markdown con comentarios y meta), MD (front matter)
    tables.py        # CSV Latin-1/UTF-8 y XML → columns como texto
  normalize.py       # Port exacto de clean(), norm(), digitosCompactos(), digitosEn() y contiene() de validar.mjs + mapa de offsets + emails deletreados
  registry.py        # Padrón canónico desde clientes.csv, empleados.xml, users.json, transacciones.csv y pqrs.xml
  link.py            # Vínculos entre fuentes: radicado PQR, fecha + monto TX, adjunto, correo, cédula, perfil de chat
  detect/
    known.py         # Autómata pyahocorasick con variantes normalizadas y límites de palabra
    patterns.py      # Regex CO: cédulas, celulares, placas, pasaportes, correos, deletreados, <@U…>, HTML comments, PDF meta
    llm.py           # Extractor de spans tipados {surface, type, person_hint, gender} con caché en STATE_DIR/llm_cache/
  resolve.py         # Desambiguación de homónimas (2 clientas + agente=clienta), relación 'reporta a', menciones parciales, apodos y external:
  pseudonym.py       # Faker(es_CO) con HMAC sobre STATE_DIR/entity_ledger.json (personas previas intocables; colisiones solo en nuevas)
  render.py          # Reemplazo con estilo de superficie espejo (MAYÚSCULAS sin tildes, separadores, deletreado)
  quasi.py           # Generalización dual (tabla + texto) SOLO cuando (barrio, ocupación, año) es única (k = 1)
  exporter.py        # Exporta entities.json desde el registro, con pseudonyms podados a lo efectivamente usado
  guard.py           # Barrido final con contiene() y digitosEn() de validar.mjs sobre toda la salida
  pipeline.py        # Orquestador compartido entre /v1/run y /v1/anonymize
  telemetry.py       # Logs estructurados sin valores reales y métricas para DECISIONS.md y failure_analysis.md
tests/               # Solo datos sintéticos, más los tests públicos y los de la muestra
output/              # Generado, gitignored
state/               # STATE_DIR, gitignored: entity_ledger.json, caché cruda, llm_cache/
logs/                # Sin valores reales, gitignored
DESIGN.md · DECISIONS.md · README.md · ENUNCIADO.md · failure_analysis.md
```

## 5. Reglas duras (Invariantes)
1. **El LLM detecta, el código reemplaza.** El LLM devuelve JSON `{surface, type, person_hint, gender}` y el código ubica cada span buscando la cadena en el texto original. Nunca se reescribe el texto: la salida ideal reemplaza, no redacta de nuevo.
2. **Seudónimos estables entre corridas.** El jurado compara el `entities.json` de antes y de después de que crezcan las fuentes. Cada `/v1/run` carga primero el ledger (`STATE_DIR/entity_ledger.json`): quien ya tenía seudónimo lo conserva intacto. Las personas nuevas se generan con `HMAC(PSEUDONYM_SECRET, canonical_id)`, y sus colisiones se resuelven con `seed + contador` sin tocar a las existentes. `PSEUDONYM_SECRET` no cambia nunca.
3. **IDs canónicos estables.** Clientes: `CL-…` (`cliente_id`). Empleados: `KP-…`. Usuarios de chat: su `uid`, vinculado a un cliente o empleado. Externos: `external:<nombre normalizado>`. Un homónimo nuevo recibe un ID nuevo y el viejo conserva el suyo. El `entity_id` de `entities.json` se deriva de este ID canónico, nunca de un contador secuencial.
4. **Inyectividad y distancia.** Personas distintas reciben seudónimos distintos y ningún valor falso coincide con uno real del padrón. El seudónimo no comparte ningún nombre ni apellido con el original, y una cédula o celular falsos no contienen la secuencia de dígitos del original.
5. **Misma categoría.** Nombre colombiano del mismo género. Correo por correo (dominios de organización como `kipupay.co` y proveedores públicos se conservan). Cédula, celular, placa, pasaporte y fecha de nacimiento con formato válido. Nunca hash ni `[REDACTED]`.
6. **Se conservan:** organizaciones, lugares, marcas, figuras públicas, montos, fechas de transacción, productos, el bot de soporte y llaves (`CL-…`, `TX-…`, `PQR-…`, `KP-…`). Un apellido que también es lugar sigue siendo apellido cuando nombra a una persona; si una razón social contiene el nombre de un cliente real (*"Odontología María López SAS"*), se seudonimiza solo el nombre de la persona.
7. **Ningún campo pasa sin revisar,** incluidos `filename`, `attachments`, `title`, `metadata` (author, subject, keywords, meta de HTML), comentarios HTML, front matter y claves de objetos.
8. **1 a 1 contra `record_index`.** Cada id aparece exactamente una vez. Ningún registro se vacía, se omite ni se reescribe. `metadata` y `columns` conservan exactamente sus claves, con todos los valores de `columns` como string, y `attachments` conserva su número de elementos. Si un registro falla en una etapa avanzada, sale con anonimización determinista + `guard.py`.
9. **Compuerta antes de marcar `done`:** `guard.py` comprueba que ningún original declarado siga en la salida, que cada seudónimo declarado en `pseudonyms` aparezca en alguno de sus `records`, y ejecuta `node validar.mjs`. Si el validador falla, la corrida nunca se publica como `done`.
10. **Honestidad del diccionario.** `originals` lista todas las formas reales encontradas tal como aparecieron: nombre completo y variantes, mayúsculas sin tildes, correos (también deletreados), celulares con y sin separadores, cédulas, placas, pasaportes y handles.
11. **Los datos reales no salen de `STATE_DIR` y `output/`.** Nunca van en logs, tests, `failure_analysis.md` ni commits. `entities.json` es solo para el jurado.
12. **Disponibilidad.** `/v1/health` responde siempre. `/v1/run` devuelve el `job_id` de inmediato y procesa en un proceso separado. `/v1/anonymize` tiene corte interno a 45 s; si el LLM no llega a tiempo, devuelve el resultado determinista + guard.
13. **Misma lógica en `/v1/run` y `/v1/anonymize`,** sobre el ledger persistido. `/v1/anonymize` funciona aunque no haya corrida previa (en frío), porque `VALIDAR ENTRYPOINT` lo prueba primero con un registro de la muestra.
14. **Nunca dispares `VALIDAR ENTRYPOINT`** ni ninguna acción que gaste una corrida oficial.

## 6. LLM vía OpenRouter
- Se usa solo donde las reglas no alcanzan: terceros en texto libre, apodos, apellidos ambiguos sin marcador y los 2 documentos en inglés.
- Los modelos se definen en `config.py` en dos niveles: `fast` y `heavy`. Al arrancar se valida cada slug contra `GET /api/v1/models`. `/v1/health` los lista en `models`.
- Parámetros: `models` (lista corta de respaldo), `temperature: 0`, salida JSON con esquema y `provider: { data_collection: "deny", require_parameters: true }`.
- Caché por hash de contenido en `STATE_DIR/llm_cache/`.
- Concurrencia con semáforo configurable: empieza en 16 y sube hasta 25 si no aparecen respuestas `429`. Timeout de 15 s por llamada, con un reintento. Reintentos con backoff exponencial ante `429` y `5xx` (también contra el API de fuentes).
- **Protección de presupuesto (reserva de US$ 5 para evaluación):** si existe `OPENROUTER_EVAL_KEY`, se usa cuando `RUN_MODE=eval`. Si solo hay una llave, `config.py` consulta `GET /api/v1/key` (cacheado 60 s); cuando `usage` supera US$ 44, en `RUN_MODE=dev` deja de llamar al LLM y trabaja solo con caché + determinista, y en `RUN_MODE=eval` fuerza el nivel `fast`.
- En cada llamada se registran el modelo que respondió, los tokens y el costo.

## 7. Telemetría
```text
[STEP] stage={fetch|parse|registry|link|detect|resolve|render|quasi|guard|validate|write} record={id} mode={DET|LLM} model={slug|NONE} latency_ms={n} tokens={n} cost_usd={x} guard={PASS|INTERCEPT} status={PASS|RETRY|DEGRADED|FAIL}
```
- Cada evento se escribe en `logs/execution_trace.jsonl` (jamás con valores de PII real).
- `GET /v1/run/{job_id}` expone el conteo de registros, tokens, segundos y costo; `DECISIONS.md` toma de ahí las métricas finales.
- Cada fallo o ambigüedad no resuelta agrega una entrada a `failure_analysis.md` con caso (ID de registro, sin PII), timestamp, componente, causa raíz y mitigación.

## 8. Tests y definición de terminado
- Los 5 tests de `tests_public.json` y los 13 de `sample/` como tests automáticos.
- Un test golden por cada trampa del enunciado (`DESIGN.md` §6), con datos sintéticos.
- `node validar.mjs` sobre `sample/expected_output` y sobre nuestra salida `output/`.
- Test canario (`test_canary_obfuscation`): PII sintética en todas sus formas (confusables, invisibles, separadores, palabras pegadas) debe dar cero apariciones.
- Test de estabilidad: correr dos veces y luego simular crecimiento agregando registros; los seudónimos existentes deben quedar 100 % idénticos.
- `test_pseudonym_category`: mismo género, formato válido y ningún nombre, apellido o secuencia de dígitos compartidos con el original.
- `test_structure`: las uniones entre tablas (`CL-…`, `TX-…`, `PQR-…`, `KP-…`) y el grafo de participantes de cada hilo quedan intactos.
- Los casos T1 a T7 y sus umbrales están en `DESIGN.md` §9 y coinciden con `design.html`.

Una tarea está terminada cuando `make test` pasa, `make validate` dice OK y no hay valores reales en `logs/`.

## 9. Corridas oficiales
- Hay tres corridas oficiales: la primera mide el 60 % de los tests ocultos, la segunda el 75 % y la tercera el 100 %.
- Las fuentes crecen sin aviso. La última corrida antes del crecimiento vale el 30 % del total y la última después, el 70 %. **Sin una corrida antes del crecimiento, ese 30 % queda en cero.**
- Estrategia: lanzar la **Corrida 1** en cuanto el pipeline determinista pase el validador y los tests públicos/muestra (meta $\ge 88$), sin esperar al LLM ni a una hora fija. Reservar las **Corridas 2 y 3** para después del crecimiento de las fuentes.
- Lista de verificación antes de cada corrida:
  1. `PSEUDONYM_SECRET` y `STATE_DIR/entity_ledger.json` son exactamente los mismos de la corrida anterior.
  2. `make validate` dice OK y `make test` pasa completo.
  3. `/v1/health` y `/v1/anonymize` responden desde la URL pública en menos del SLA.
  4. El servicio corre en hosting público con disco persistente (`STATE_DIR`), sin suspensión por inactividad y sin depender de un computador local ni de un túnel efímero (debe permanecer activo durante todo el evento y el debrief).

## 10. Crecimiento de las fuentes
1. Un chequeo periódico compara el tamaño de `record_index` (y su `ETag`) con la última corrida. Si cambió, emite `[SOURCES_GROWN]` y registra en `logs/incidents_log.json` los conteos por fuente (nunca valores reales).
2. La siguiente corrida regenera toda la salida completa desde el API de fuentes (registros viejos y nuevos).
3. Las personas previas conservan exactamente su seudónimo desde `STATE_DIR/entity_ledger.json`. Las nuevas reciben seudónimos nuevos y únicos.
4. Una persona previa que aparece con un identificador nuevo se vincula a su entidad existente por sus identificadores conocidos. Una persona nueva que se llama igual que una previa recibe un ID y seudónimo distintos.
