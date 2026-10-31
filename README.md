# Vende los datos sin vender a la gente

Paquete de arranque del reto de anonimización. El enunciado completo está en `ENUNCIADO.md`.

| Archivo                         | Qué es                                                                                                 |
| ------------------------------- | ------------------------------------------------------------------------------------------------------ |
| `ENUNCIADO.md`                  | la historia, las fuentes, el formato de `output/`, el API que expone tu servicio y cómo se puntúa      |
| `api/fuentes.openapi.yaml`      | el contrato del API de fuentes, de donde bajas los chats, documentos y tablas                          |
| `api/participante.openapi.yaml` | el contrato que tiene que cumplir tu servicio: `/health`, `/run`, `/output`, `/anonymize`, `/entities` |
| `validar.mjs`                   | el validador de `output/`, el mismo que corre el servicio de evaluación                                |

## Primeros pasos

1. En `/evento/reto` de Frontier copia la URL del API de fuentes y tu token.
2. Baja la muestra: `curl -H "Authorization: Bearer $TOKEN" $FUENTES/sample.zip -o sample.zip` y descomprímela en `sample/`.
3. Corre el validador sobre la salida ideal de la muestra con Node 20 o más nuevo:
   `node validar.mjs sample/expected_output --public sample`. Tiene que decir `OK: la salida pasa el validador`.
4. Baja `GET /record_index` a `fuentes/record_index.json` y valida tu salida con
   `node validar.mjs output/ --public fuentes/`.
5. Despliega tu servicio en una URL pública. Cuando quieras una evaluación, pega la URL en `/evento/reto` y dale a
   VALIDAR ENTRYPOINT: si responde `/health` y `/anonymize`, arranca una de tus tres corridas.

---

## Solución Implementada (`abh_cdmx`)

### Arranque rápido
```bash
# Levantar el servicio HTTP en el puerto 9000 (o $PORT)
./run.sh

# Regenerar output/ contra el API de fuentes y ejecutar el validador oficial
make regen

# Validar output/ con validar.mjs
make validate

# Evaluar el pipeline sobre sample/input y validar contra sample/
make score-sample

# Ejecutar la suite completa de pruebas automatizadas (pytest)
make test
```

### Entregables y Documentación
- [`run.sh`](run.sh): Script ejecutable que inicia el servicio FastAPI bajo `/v1` (`/v1/health`, `/v1/run`, `/v1/run/{job_id}`, `/v1/output/{archivo}`, `/v1/anonymize`, `/v1/entities`).
- [`DESIGN.md`](DESIGN.md): Arquitectura de 10 etapas, resolución de entidades cruzada, desambiguación contextual de homónimos y generalización dual de cuasi-identificadores ($k=1$).
- [`DECISIONS.md`](DECISIONS.md): Inventario de qué consideramos PII, qué conservamos a propósito (`kept: true`) y por qué, junto con métricas reales de latencia y costo.
- [`failure_analysis.md`](failure_analysis.md): Registro estructurado de casos borde diagnosticados y mitigados (sin PII real).

