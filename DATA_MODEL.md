# Viviendin — Contrato de datos

Base: **Supabase (Postgres)**. Jerarquía: `developers` → `developments` → `models`.
`leads` referencia el desarrollo (y opcionalmente el modelo) de interés.

> Principio: los 297 sitios son heterogéneos y muchos campos vendrán vacíos.
> **Todo es nullable salvo lo mínimo** (nombre + relación). Guardamos siempre el
> JSON crudo de la extracción (`raw`) para reprocesar sin volver a crawlear.

## Diagrama

```
developers (1) ──< developments (N) ──< models (N)
                        ▲
                        └──< leads (N)   (interés del comprador)
```

---

## 1. `developers` — la desarrolladora (empresa)

Función: identidad + **contacto para el handoff de broker**.

| Campo | Tipo | Nota |
|-------|------|------|
| id | uuid PK | `gen_random_uuid()` |
| name | text | nombre comercial |
| website | text UNIQUE | de `data/desarrolladoras.txt` (clave de dedup) |
| phone | text | contacto |
| email | text | contacto |
| whatsapp | text | contacto |
| profile_url | text | redes / perfil |
| logo_url | text | |
| description | text | "about" |
| cities | text[] | ciudades donde opera (derivable de developments) |
| country | text | default `'MX'` (marcar atípicos: ecohabitat.com.pe) |
| source | text | default `'lista-297'` |
| extraction_status | text | `pending` / `done` / `failed` / `skipped` |
| extraction_error | text | mensaje si falló |
| last_crawled_at | timestamptz | |
| raw | jsonb | salida cruda del LLM |
| created_at / updated_at | timestamptz | |

## 2. `developments` — el desarrollo (proyecto)

Función: **ubicación** + atributos del proyecto. Es lo que se le presenta al comprador.

| Campo | Tipo | Nota |
|-------|------|------|
| id | uuid PK | |
| developer_id | uuid FK → developers | |
| name | text | nombre del desarrollo |
| housing_type | text | `casa` / `departamento` / `terreno` / `mixto` |
| status | text | `preventa` / `construccion` / `entrega_inmediata` / `terminado` |
| state | text | estado (normalizado, p.ej. "Querétaro") |
| municipality | text | municipio / alcaldía |
| neighborhood | text | colonia / zona |
| address | text | dirección si existe |
| lat / lng | numeric | null por ahora; geocoding después |
| price_from | numeric | cache del mínimo de sus modelos (MXN) |
| price_to | numeric | cache del máximo |
| price_on_request | boolean | **true si el sitio oculta precios** (premium). El match degrada con gracia; el lead se captura igual. |
| amenities | text[] | alberca, gym, seguridad, ... |
| delivery_date | text | entrega estimada (texto libre, p.ej. "Dic 2026") |
| description | text | |
| images | jsonb | array de URLs |
| url | text | página de detalle del desarrollo (de la fase A del crawl) |
| sales_phone | text | **contacto de ventas del desarrollo** (suele diferir del corporativo) |
| sales_whatsapp | text | **WhatsApp de ventas del desarrollo** — el que usa el broker para el handoff |
| sales_email | text | correo de ventas del desarrollo |
| raw | jsonb | |
| created_at / updated_at | timestamptz | |

> **Contacto en 2 niveles:** `developers` guarda el contacto corporativo; `developments`
> guarda el contacto de ventas específico del proyecto. Para el handoff de broker se
> prefiere el de `developments` (cae en el desarrollo de interés) y se usa el corporativo
> como respaldo.

## 3. `models` — el modelo / prototipo (unidad vendible)

Función: lo que el comprador **filtra** (precio, recámaras, m²). Tabla propia para poder
hacer `WHERE bedrooms >= 2 AND price <= 2000000` en SQL.

| Campo | Tipo | Nota |
|-------|------|------|
| id | uuid PK | |
| development_id | uuid FK → developments | |
| name | text | "Modelo Toscana" / "Lote 250 m²" |
| housing_type | text | `casa` / `departamento` / `terreno` |
| bedrooms | int | recámaras (null en terrenos) |
| bathrooms | numeric | baños (permite 2.5; null en terrenos) |
| parking | text | cajones (puede ser rango, p.ej. "2 a 3") |
| levels | int | niveles |
| area_built_m2 | numeric | m² construcción (null en terrenos) |
| area_lot_m2 | numeric | m² terreno / **superficie del lote** (clave en terrenos) |
| price | numeric | MXN |
| currency | text | default `'MXN'` |
| availability | text | `disponible` / `pocas_unidades` / `agotado` |
| floorplan_url | text | plano |
| raw | jsonb | |
| created_at / updated_at | timestamptz | |

## 4. `leads` — el comprador calificado

Función: salida del agente. Se registra aquí **y** se notifica al número interno.

| Campo | Tipo | Nota |
|-------|------|------|
| id | uuid PK | |
| wa_user | text | teléfono de WhatsApp del comprador |
| name | text | |
| budget | numeric | presupuesto (MXN) |
| search_state | text | estado/ciudad buscada |
| search_zone | text | zona buscada |
| housing_type | text | |
| bedrooms | int | |
| credit | text | `infonavit` / `bancario` / `contado` / `cofinavit` / `na` |
| horizon | text | `ya` / `3-6m` / `explorando` |
| development_id | uuid FK → developments | desarrollo de interés |
| model_id | uuid FK → models | opcional |
| visit_date | text | fecha tentativa de visita |
| visit_time | text | hora tentativa |
| status | text | `nuevo` / `notificado` / `contactado` / `agendado` / `descartado` |
| notes | text | |
| created_at | timestamptz | |

---

## Pipeline de extracción — **2 fases** (validado con sitios reales)

Probado contra Vinte, Javer y Skyhaus (2026-05-29). Hallazgo: **la home NO trae
modelos ni precios**; solo lista desarrollos con sus URLs. El detalle vive en la
subpágina de cada desarrollo. Por eso el crawl es de dos fases:

- **Fase A — Listado (home):** descubre desarrollos + su `url` de detalle + ubicación.
  Devuelve `developments[]` con `models: []`.
- **Fase B — Detalle (por desarrollo):** visita cada `url` y extrae `models[]` con
  precios, m², recámaras, amenidades completas y **contacto de ventas del desarrollo**.

> **Hallazgo de precios:** ~mitad de los sitios ocultan precios (premium, p.ej. Skyhaus
> → "reserva con $50,000", resto tras formulario); otros los publican completos (Vinte
> → 6 modelos $1.04M–$1.93M). Cuando no hay precio: `price = null`, `price_on_request = true`.
> El agente igual ofrece el desarrollo por zona/recámaras y **captura el lead** (esto
> refuerza el modelo de broker). Normalizar URLs relativas (`/puebla/x`) a absolutas.

## Contrato de extracción (lo que el agente de background devuelve por sitio)

Un solo objeto por desarrolladora; el pipeline lo desnormaliza a las 3 tablas.
Campos desconocidos → `null` (nunca inventar). Precios siempre numéricos en MXN.

```json
{
  "developer": {
    "name": "string",
    "phone": "string|null",
    "email": "string|null",
    "whatsapp": "string|null",
    "profile_url": "string|null",
    "description": "string|null"
  },
  "developments": [
    {
      "name": "string",
      "housing_type": "casa|departamento|terreno|mixto|null",
      "status": "preventa|construccion|entrega_inmediata|terminado|null",
      "state": "string|null",
      "municipality": "string|null",
      "neighborhood": "string|null",
      "address": "string|null",
      "amenities": ["string"],
      "delivery_date": "string|null",
      "url": "string|null",
      "price_on_request": "boolean",
      "sales_phone": "string|null",
      "sales_whatsapp": "string|null",
      "sales_email": "string|null",
      "models": [
        {
          "name": "string|null",
          "housing_type": "casa|departamento|terreno|null",
          "bedrooms": "int|null",
          "bathrooms": "number|null",
          "parking": "string|null",
          "area_built_m2": "number|null",
          "area_lot_m2": "number|null",
          "price": "number|null",
          "availability": "disponible|pocas_unidades|agotado|null"
        }
      ]
    }
  ],
  "extraction_confidence": "high|medium|low"
}
```

### Reglas de la extracción
- **No inventar.** Si el sitio no dice el precio, `price = null` y `price_on_request = true`.
- **Terrenos/lotes:** `housing_type = 'terreno'`; `bedrooms`/`bathrooms`/`area_built_m2`
  van en `null` y lo relevante es `area_lot_m2` (superficie del lote) + `price`.
- Normalizar `state` a nombre oficial del estado de MX; URLs relativas → absolutas.
- `price_from` / `price_to` del desarrollo se calculan del min/max de sus modelos.
- Si el sitio no tiene desarrollos parseables (es solo corporativo), `developments: []`
  y `extraction_status = 'skipped'` con nota.
- Guardar siempre el `raw` para reprocesar.

## Pendientes de diseño (a confirmar)
- ¿Geocoding (lat/lng) ahora o post-piloto? → propuesta: **post-piloto**, el match
  por estado/municipio/zona en texto basta para la demo.
- ¿Catálogo controlado de `amenities` o texto libre? → propuesta: **texto libre** ahora,
  normalizar después.
