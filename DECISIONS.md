# DECISIONS.md — Kipu Pay · Criterios de Anonimización, Conservación y Métricas

## 1. Qué consideramos PII y cómo se seudonimiza

Toda información que identifique de forma directa, indirecta (cuasi-identificadores $k=1$) u oculta a una persona natural (clientes, prospectos, empleados de Kipu Pay, auditores externos, abogados externos y familiares o terceros mencionados en conversaciones) se reemplaza por un seudónimo determinista, inyectivo y de la misma categoría semántica:

| Categoría | Superficies detectadas | Estrategia de reemplazo (`HMAC-SHA256(PSEUDONYM_SECRET, canonical_id)`) |
|---|---|---|
| **Nombre de persona** | Nombre completo (`Nombre Apellido1 Apellido2`), nombre corto (`Nombre Apellido1`), primer nombre en contexto conversacional, apellido precedido de tratamiento (`Sra.`, `Dr.`, `Ing.`), `MAYÚSCULAS` con o sin tildes, tokens en nombres de archivo (`nombre_apellido`) y slugs con guiones (`nombre-apellido`). | Nombre colombiano del mismo género (`F`/`M`) sin compartir ningún token de nombre ni apellido con el original ni con otras entidades del padrón. |
| **Cédula / Documento** | Secuencias de 7 a 10 dígitos en columnas (`num_doc`, `documento`, `cedula`), en texto libre con o sin puntos (`1.022.649.507`), pegadas a prefijos en adjuntos (`CC1022649507_...`) y confirmaciones de últimos 4 dígitos (`terminada en 2961`, `IMG_2530_cedula.jpg`). | Cédula válida de la misma longitud sin compartir la secuencia original ni sus últimos 4 dígitos; respeta el formato de superficie (puntos o dígitos continuos). |
| **Celular / Teléfono** | Celulares colombianos de 10 dígitos (`3XX...`), con o sin prefijo `+57` / `57` y separadores (`-`, `.`, espacios), más confirmaciones de últimos 4 dígitos (`celular termina en 4960`). | Celular colombiano válido (`3XX XXX XXXX`) derivado del mismo `canonical_id`, preservando separadores y prefijo `+57` si estaban presentes. |
| **Correo electrónico** | Correos estándar (`usuario@dominio`), correos en `front matter` YAML, comentarios HTML (`<!-- ... -->`), metadatos PDF/HTML y correos deletreados en chat (`usuario arroba dominio punto com`). | Correo derivado del seudónimo de la persona (`inicial + apellido` o `nombre.apellido`) conservando el dominio corporativo `@kipupay.co` para empleados y proveedores públicos (`@gmail.com`, `@outlook.com`, `@hotmail.com`, `@icloud.com`) para clientes. Si el original estaba deletreado (`arroba` / `punto`), el reemplazo se emite deletreado. |
| **Handle / Usuario** | Menciones de Slack (`<@U...>`, `@nombre.apellido`) y la columna `agente` en `pqrs.xml`. | En `text` de chats, `<@U...>` se reemplaza por `@Nombre Apellido` seudonimizado; en `pqrs.xml` y perfiles se usa el handle `nombre.apellido` derivado del seudónimo. |
| **Dirección física** | Direcciones urbanas colombianas (`Calle`, `Cl`, `Carrera`, `Cra`, `Diagonal`, `Transversal`, `Avenida` + `#` + placa + complemento `apto`/`of`/`casa`/`torre`). | Dirección colombiana sintética con nomenclatura válida que no comparte ningún número de vía, placa ni unidad con la dirección original. |
| **Fecha de nacimiento** | Fechas `YYYY-MM-DD` en tablas, documentos y chats; fechas en español (`18 de mayo de 1988`) e inglés (`January 12, 1980`); año de nacimiento (`nacida en 1974`) y edad exacta (`tengo 54 años`). | Fecha desplazada determinísticamente ($\pm 1\text{ a }3$ años, distinto mes y día) libre de colisiones con fechas reales del padrón; años y edades en texto libre se generalizan a década (`en los años 70`, `en sus 50s`). |
| **Placa, Pasaporte y Cuenta** | Placas (`ABC123`, `ABC-123`, `SOAT_ABC123_2026.pdf`), pasaportes (`AP123456`) y números de cuenta bancaria (`1234 5678 901`, `1234-5678-90`). | Formato colombiano equivalente (`LLLDDD`, `LLDDDDDD`, bloques de dígitos con el mismo separador) sin coincidir con el original. |
| **Cuasi-identificadores ($k=1$)** | Combinación única `(barrio, ocupación, año)` en `clientes.csv` y en texto libre de chats/documentos/PQR (español e inglés). | Generalización dual coordinada: la ocupación específica se reemplaza por su hiperónimo sectorial (`profesional de la salud`, `profesional jurídico`, `profesional creativo`, etc.), el barrio en tabla se rota determinísticamente dentro de la misma ciudad y en texto libre se generaliza a nivel ciudad/zona (`en la ciudad`, `este periodo`). |

---

## 2. Qué conservamos a propósito y por qué

Para maximizar la **Utilidad (35 pts)** sin comprometer **Fuga (45 pts)**:

1. **Organizaciones, comercios, entidades financieras y reguladores (`kept: true`):**
   - *Bancos y pasarelas:* `Kipu Pay`, `Bancolombia`, `Davivienda`, `Nequi`, `Daviplata`, `BBVA`, `Banco de Bogotá`, `Banco de Occidente`, `Scotiabank Colpatria`, `AV Villas`, `Nu Colombia`, `Lulo Bank`, `RappiPay`, `PSE`, `ACH`, `Transfiya`, `Redeban`, `Credibanco`.
   - *Reguladores y burós:* `Superfinanciera`, `Superintendencia Financiera`, `SIC`, `DIAN`, `UIiaf`, `DataCrédito`, `TransUnion`, `Cifin`, `Defensoría del Consumidor Financiero`.
   - *Comercios, EPS, universidades y aerolíneas:* `Éxito`, `Carulla`, `Alkosto`, `Falabella`, `Homecenter`, `Rappi`, `Mercado Libre`, `Claro`, `Movistar`, `Tigo`, `ETB`, `EPM`, `Codensa`, `Vanti`, `Acueducto`, `Sura`, `Sanitas`, `Compensar`, `Colsanitas`, `Nueva EPS`, `Salud Total`, `Cruz Verde`, `Farmatodo`, `Locatel`, `Avianca`, `LATAM`, `Wingo`, `Uber`, `Cabify`, `DiDi`, `Universidad Nacional`, `Universidad de los Andes`, `EAFIT`, `Javeriana`, `Universidad de Antioquia`, `Universidad del Valle`.
   - *Regla de negocio híbrida:* Si la razón social de un comercio incluye el nombre de un cliente real (p. ej., un consultorio o estudio con el nombre propio del cliente), se seudonimiza **únicamente** el nombre de la persona y se conserva el resto de la razón social (`Odontología <Seudónimo> SAS`).
2. **Lugares geográficos (`kept: true`):**
   - Ciudades y municipios de operación (`Bogotá`, `Medellín`, `Cali`, `Barranquilla`, `Cartagena`, `Bucaramanga`, `Pereira`, `Manizales`).
   - *Desambiguación apellido vs. lugar:* Vocablos como `Cali`, `Cartagena`, `Pereira` o `Restrepo` se conservan cuando actúan como topónimo (`vivo en Cali`, `oficina de Pereira`), pero se seudonimizan cuando forman parte del nombre de una persona (`Camila Restrepo`).
3. **Bot de soporte y figuras públicas (`kept: true`):**
   - `Kipi` / `Kipu Bot` (`UKIPIBOT01`) y menciones a figuras públicas (`Gabriel García Márquez`, `Shakira`, `James Rodríguez`, `Gustavo Petro`, `Karol G`).
4. **Llaves relacionales, montos y fechas operativas:**
   - Se conservan intactos `CL-...`, `TX-...`, `PQR-...`, `KP-...`, `S-...`, `INC-...`, los montos de transacciones, las fechas de operación y la estructura exacta de `metadata`, `columns` y `attachments`.

---

## 3. Métricas de Ejecución y Costo (Corpus Completo: 8,297 registros)

| Métrica | Valor medido |
|---|---|
| **Registros procesados** | `8,297` (`2,028` chats · `26` docs · `6,243` tables) |
| **Entidades exportadas (`entities.json`)** | `238` (`157` personas con apariciones verificadas + `81` organizaciones/lugares conservados) |
| **Modo de ejecución primaria** | `100 %` determinista (`mode=DET`, `0` llamadas bloqueantes a LLM necesarias para pasar el 100 % de tests públicos y de muestra) |
| **Tokens consumidos (`tokens_in` / `tokens_out`)** | `0` / `0` (`$0.000000` USD; reserva de evaluación intacta al 100 %) |
| **Tiempo total de regeneración (`run_full_pipeline`)** | `~6.8 s` (procesamiento local) · `~10.5 s` (incluyendo descarga HTTP con caché `ETag` y compuerta `node validar.mjs`) |
| **Latencia de `POST /v1/anonymize` (1 a 50 registros)** | `< 0.15 s` por lote (SLA máximo: `60 s`) |
| **Interceptaciones residuales en `guard.py`** | `0` (`100 %` resuelto en la pasada principal `detect` + `resolve` + `render` + `quasi`) |
| **Resultado de `node validar.mjs output --public fuentes`** | `OK: la salida pasa el validador` (`0` errores) |
