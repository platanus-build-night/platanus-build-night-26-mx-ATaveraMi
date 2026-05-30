# Scraper — Viviendin (pipeline de extracción)

Puebla `developers / developments / models` (contrato en `DATA_MODEL.md`) desde los
sitios propios de las 297 desarrolladoras (`data/desarrolladoras.txt`).

## Decisión: pipeline determinista, NO agente

El problema es **extracción sobre páginas conocidas con control flow fijo**
(home → descubrir desarrollos → detalle → extraer), no razonamiento abierto. Por eso:

- **Gemini 2.5 Flash** se usa como **extractor puro** (structured output con `response_schema`),
  no como agente. Sin loops, sin tools, sin estado.
- El control flow (fetch, descubrir links, paginación, reintentos, paralelismo) es **código**.
- Más barato, más rápido, más exhaustivo y debuggeable que un agente autónomo.
- Para `obtener TODOS los desarrollos`, barrer `sitemap.xml` + todos los `<a>` es **más
  completo** que un agente (que decide cuándo parar y deja cosas fuera).

## Pipeline de 2 fases (validado con Vinte/Javer/Skyhaus)

La home solo **lista** desarrollos; los **modelos y precios** viven en la subpágina de cada uno.

```
Por desarrolladora (website):
 1. DESCUBRIR páginas de desarrollo  [sin LLM, robusto]
    - sitemap.xml (fuente más confiable y barata)
    - + <a> de la home filtrados por heurística de slug (/desarrollo, /proyecto, /casa…)
    - Gemini Flash SOLO para clasificar links ambiguos (opcional)
 2. EXTRAER detalle por página
    - fetch (httpx) → si vacío / JS-rendered → fallback Playwright
    - limpiar HTML → markdown (reduce tokens)
    - Gemini 2.5 Flash + response_schema → {development, models[]}
 3. EXTRAER datos del developer (home + /contacto) → developer + contacto corporativo
 4. CONSOLIDAR: normalizar estado MX y URLs relativas→absolutas, calcular
    price_from/price_to (min/max de modelos), set price_on_request si no hay precios
 5. ESCRIBIR: mismo shape que data/seed.json → luego upsert a Supabase
```

## Reglas de extracción (Gemini Flash)
- **No inventar**: campo desconocido = `null`. Precios numéricos en MXN.
- Si oculta precios → `price = null`, `price_on_request = true`.
- **Terrenos** (`housing_type = "terreno"`): `bedrooms/bathrooms/area_built_m2 = null`,
  lo relevante es `area_lot_m2` + `price`.
- Contacto de ventas a nivel desarrollo (`sales_*`); corporativo a nivel developer.
- Si el sitio es solo corporativo (sin inventario) → `developments: []`,
  `extraction_status = "skipped"` + nota.

## Orquestación
- `asyncio` + semáforo para concurrencia limitada (respetar rate limits / cortesía).
- Reintentos con backoff exponencial; timeout por página.
- **Idempotencia** por `website` (unique). Estados: `pending → done | failed | skipped`,
  con `extraction_error` y `last_crawled_at`.
- Guardar siempre `raw` (salida cruda del LLM) para reprocesar sin re-crawlear.
- Logs por sitio; los `failed`/`needs_review` se reprocesan aparte (long tail).

## Stack
Python (comparte modelos Pydantic con la sesión del agente) · `httpx` · `selectolax`/
BeautifulSoup · `playwright` (fallback JS) · Gemini 2.5 Flash (`response_schema`).

## Alcance para la demo
Procesar un **subconjunto (~15-30 sitios)** priorizando CDMX y Querétaro (para empatar el
seed) y sitios estáticos. El resto, en background. No bloquear la demo por el long tail.

## Uso
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # y pon GEMINI_API_KEY

python -m scraper.run --only vinte.com.mx skyhaus.mx   # sitios específicos
python -m scraper.run --limit 10                        # primeras 10 de la lista
python -m scraper.run                                   # las 297
```
Salida → `data/scraped.json` (mismo shape que `data/seed.json`). Implementado en
`scraper/`: `discover.py` (sitemap+heurística), `extract.py` (Gemini Flash +
descubrimiento asistido), `pipeline.py` (2 fases), `run.py` (CLI asyncio).

> Estado: `fetch`/`discover`/`html→markdown` probados contra sitios reales (Skyhaus,
> Vinte). Falta `GEMINI_API_KEY` para correr la extracción end-to-end.
