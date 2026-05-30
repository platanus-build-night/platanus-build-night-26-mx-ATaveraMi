-- Viviendin — esquema de datos (Supabase / Postgres)
-- Fuente de verdad: DATA_MODEL.md. Jerarquía: developers -> developments -> models; leads referencia el desarrollo (y opcionalmente el modelo) de interés.
--
-- Principio: los 297 sitios son heterogéneos; casi todo es nullable salvo lo mínimo
-- (nombre + relación). Se guarda el JSON crudo de la extracción (raw) para reprocesar.
--
-- Quién escribe qué:
--   scraper  -> developers, developments, models
--   agente   -> leads
--
-- Aplicar:  psql "$DATABASE_URL" -f db/schema.sql   (o pegar en el SQL Editor de Supabase)

create extension if not exists pgcrypto;  -- gen_random_uuid()

-- updated_at automático
create or replace function set_updated_at()
returns trigger language plpgsql as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

-- ───────────────────────────────────────────────────────────────────────────
-- 1. developers — la desarrolladora (empresa). Identidad + contacto corporativo.
-- ───────────────────────────────────────────────────────────────────────────
create table if not exists developers (
  id                uuid primary key default gen_random_uuid(),
  name              text not null,
  website           text unique,                 -- de data/desarrolladoras.txt (clave de dedup)
  phone             text,
  email             text,
  whatsapp          text,
  profile_url       text,
  logo_url          text,
  description       text,
  cities            text[],                       -- ciudades donde opera (derivable de developments)
  country           text default 'MX',            -- marcar atípicos (ecohabitat.com.pe)
  source            text default 'lista-297',
  extraction_status text default 'pending'        -- pending / done / failed / skipped
                    check (extraction_status in ('pending','done','failed','skipped')),
  extraction_error  text,
  last_crawled_at   timestamptz,
  raw               jsonb,                         -- salida cruda del LLM
  created_at        timestamptz not null default now(),
  updated_at        timestamptz not null default now()
);

-- ───────────────────────────────────────────────────────────────────────────
-- 2. developments — el desarrollo (proyecto). Ubicación + contacto de ventas del proyecto.
-- ───────────────────────────────────────────────────────────────────────────
create table if not exists developments (
  id               uuid primary key default gen_random_uuid(),
  developer_id     uuid not null references developers(id) on delete cascade,
  name             text not null,
  housing_type     text check (housing_type in ('casa','departamento','terreno','mixto')),
  status           text check (status in ('preventa','construccion','entrega_inmediata','terminado')),
  state            text,                           -- estado normalizado (p.ej. "Querétaro")
  municipality     text,
  neighborhood     text,
  address          text,
  lat              numeric,
  lng              numeric,
  price_from       numeric,                        -- cache del min de sus modelos (MXN)
  price_to         numeric,                        -- cache del max
  price_on_request boolean not null default false, -- true si el sitio oculta precios (premium)
  amenities        text[],
  delivery_date    text,                           -- texto libre (p.ej. "Dic 2026")
  description      text,
  images           jsonb,                          -- array de URLs
  url              text,                           -- página de detalle del desarrollo
  -- contacto de ventas específico del proyecto (preferido para el handoff de broker;
  -- si vacío, cae al contacto corporativo del developer)
  sales_phone      text,
  sales_whatsapp   text,
  sales_email      text,
  raw              jsonb,
  created_at       timestamptz not null default now(),
  updated_at       timestamptz not null default now()
);

-- ───────────────────────────────────────────────────────────────────────────
-- 3. models — el modelo / prototipo (unidad vendible). Lo que el comprador filtra.
-- ───────────────────────────────────────────────────────────────────────────
create table if not exists models (
  id             uuid primary key default gen_random_uuid(),
  development_id uuid not null references developments(id) on delete cascade,
  name           text,                             -- "Modelo Toscana" / "Lote 250 m²"
  housing_type   text check (housing_type in ('casa','departamento','terreno')),
  bedrooms       int,                              -- null en terrenos
  bathrooms      numeric,                          -- permite 2.5; null en terrenos
  parking        text,                             -- cajones; puede ser rango ("2 a 3")
  levels         int,
  area_built_m2  numeric,                          -- null en terrenos
  area_lot_m2    numeric,                          -- superficie del lote (clave en terrenos)
  price          numeric,                          -- MXN
  currency       text default 'MXN',
  availability   text check (availability in ('disponible','pocas_unidades','agotado')),
  floorplan_url  text,
  raw            jsonb,
  created_at     timestamptz not null default now(),
  updated_at     timestamptz not null default now()
);

-- ───────────────────────────────────────────────────────────────────────────
-- 4. leads — el comprador calificado. Salida del agente (también se notifica al número interno).
-- ───────────────────────────────────────────────────────────────────────────
create table if not exists leads (
  id             uuid primary key default gen_random_uuid(),
  wa_user        text not null,                    -- teléfono de WhatsApp del comprador
  name           text,
  budget         numeric,                          -- presupuesto (MXN)
  search_state   text,
  search_zone    text,
  housing_type   text,
  bedrooms       int,
  credit         text check (credit in ('infonavit','bancario','contado','cofinavit','na')),
  horizon        text check (horizon in ('ya','3-6m','explorando')),
  development_id uuid references developments(id) on delete set null,  -- desarrollo de interés
  model_id       uuid references models(id) on delete set null,        -- opcional
  visit_date     text,
  visit_time     text,
  status         text not null default 'nuevo'
                 check (status in ('nuevo','notificado','contactado','agendado','descartado')),
  notes          text,
  created_at     timestamptz not null default now()
);

-- ── triggers updated_at ──────────────────────────────────────────────────────
drop trigger if exists trg_developers_updated   on developers;
drop trigger if exists trg_developments_updated on developments;
drop trigger if exists trg_models_updated        on models;
create trigger trg_developers_updated   before update on developers   for each row execute function set_updated_at();
create trigger trg_developments_updated before update on developments for each row execute function set_updated_at();
create trigger trg_models_updated        before update on models        for each row execute function set_updated_at();

-- ── índices para search_inventory (filtro del agente) ────────────────────────
create index if not exists idx_developments_developer on developments(developer_id);
create index if not exists idx_developments_location  on developments(state, municipality);
create index if not exists idx_developments_htype     on developments(housing_type);
create index if not exists idx_models_development     on models(development_id);
create index if not exists idx_models_filter          on models(bedrooms, price);
create index if not exists idx_leads_wa_user          on leads(wa_user);
