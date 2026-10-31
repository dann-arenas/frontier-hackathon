# FORMAT.md

## 1. Esquemas de Request y Response de los Endpoints

### /v1/health
**Request:** `GET /v1/health`

**Response:**
```json
{
  "status": "ok",
  "team": "[nombre_equipo]",
  "version": "1.0.0",
  "models": [
    "model1",
    "model2"
  ]
}
```

### /v1/run
**Request:** `POST /v1/run`
```json
{
  "sources_url": "[fuentes_url]",
  "token": "[token]"
}
```
**Response:**
```json
{
  "job_id": "[job_id]"
}
```

### /v1/run/{job_id}
**Request:** `GET /v1/run/{job_id}`

**Response:**
```json
{
  "status": "[queued|running|done|failed]",
  "started_at": "[timestamp]",
  "finished_at": "[timestamp]",
  "records": {
    "chats": n,
    "docs": n,
    "tables": n
  },
  "cost": {
    "tokens_in": n,
    "tokens_out": n,
    "seconds": n
  },
  "error": "[error_message]"
}
```

### /v1/output/{archivo}
**Request:** `GET /v1/output/{archivo}`

**Response:**
```json
{...
}
```

### /v1/anonymize
**Request:** `POST /v1/anonymize`
```json
{
  "source": "[chats|docs|tables]",
  "records": [
    {...},
    {...}
  ]
}
```
**Response:**
```json
{
  "records": [
    {...},
    {...}
  ],
  "entities": [
    {...},
    {...}
  ]
}
```

### /v1/entities
**Request:** `GET /v1/entities`

**Response:**
```json
{
  "entities": [
    {...},
    {...}
  ]
}
```

## 2. Transformación de las Fuentes

### Campos de Transformación
- **author:** Seudónimo generado en el formato correspondiente.
- **thread_id:** Formato `<canal>:<ts_primer_mensaje>`.
- **attachments:** Lista de objetos con `filename` que debe mantenerse igual.
- **body (PDF/HTML/MD):** Transformar a Markdown, conservando comentarios HTML si los hay.
- **metadata:** Debe conservar exactamente las mismas claves desde la entrada.
- **columns:** Todas las columnas se convierten a texto.

### Ejemplo de Transformación
- Entrada:
```json
{
  "user": "U0V6WYVYG",
  "text": "Hola, soy Camila Rodríguez",
  "attachments": [{"filename": "foto.jpg"}],
  "ts": "1785769807.000100"
}
```
- Salida:
```json
{
  "author": "[nombre_seudonimizado]",
  "text": "Hola, soy [nombre_seudonimizado]",
  "attachments": [{"filename": "foto.jpg"}],
  "ts": "1785769807.000100"
}
```

## 3. Estructura de entities.json

- **type:** `person` o `organization_or_place`.
- **originals:** Lista con las formas reales encontradas.
- **pseudonyms:** Objeto con los seudónimos generados.
- **kept:** Indica si la entidad fue conservada.

Ejemplo:
```json
{
  "entities": [
    {
      "entity_id": "p_001",
      "type": "person",
      "originals": ["Camila Rodríguez"],
      "pseudonyms": {"name": "[seudonimo]"},
      "kept": false
    }
  ]
}
```

## 4. Cuasi-identificadores

- **barrio, ocupación, año:** Generalizados a categorías o expresiones que no identifiquen a una única persona.

## 5. Conteos y Estructura del Corpus

### Columnas de los Archivos
- clientes.csv: `cliente_id`, `nombre`, `correo`, `celular`, `barrio`, `ocupacion`, `fecha_nacimiento`.
- transacciones.csv: `transaccion_id`, `cliente_id`, `monto`, `fecha`.
- pqrs.xml: `pqr_id`, `cliente_id`, `fecha`, `motivo`.
- empleados.xml: `empleado_id`, `nombre`, `cargo`, `reporta_a`.

### Relaciones
- La llave de `clientes.csv` es `cliente_id`.
- La relación "reporta a" se expresa en `empleados.xml` mediante `reporta_a`.
- `barrio`, `ocupación`, y `año` deben ser examinados para evitar cuasi-identificadores.

### Estructura de `users.json` y `docs/index.json`
1. `users.json`: tiene `uid`, `nombre`, `correo`.
2. `docs/index.json`: contiene `id`, `filename`, `format`.

## 6. Conteo de Nombres Repetidos
- Nombres completos repetidos en `clientes.csv`: [conteo].
- Nombres completos repetidos entre `empleados.xml` y `clientes.csv`: [conteo].
