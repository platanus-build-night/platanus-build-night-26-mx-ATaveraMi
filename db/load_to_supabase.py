"""Carga un JSON de inventario (data/scraped.json o data/seed.json) a Supabase.

Idempotente por `website`: en cada carga se hace upsert de la desarrolladora y se
REEMPLAZA su inventario (borra sus developments —cascade borra models— y reinserta).
Así, re-correr el scraper y volver a cargar deja la DB consistente sin duplicar.

Uso:
    python -m db.load_to_supabase --init                 # crea tablas (schema.sql) y carga scraped.json
    python -m db.load_to_supabase --input data/seed.json # carga el seed
    python -m db.load_to_supabase                        # carga data/scraped.json
"""
from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path

import psycopg
from dotenv import load_dotenv

load_dotenv()

SCHEMA_PATH = Path(__file__).parent / "schema.sql"

# Enums permitidos por los CHECK del schema. La data del scraper es ruidosa
# (p.ej. availability="preventa"/"vendido", housing_type="mixto" en un modelo),
# así que saneamos a un valor válido o null antes de insertar.
DEV_HOUSING = {"casa", "departamento", "terreno", "mixto"}
DEV_STATUS = {"preventa", "construccion", "entrega_inmediata", "terminado"}
MODEL_HOUSING = {"casa", "departamento", "terreno"}
MODEL_AVAIL = {"disponible", "pocas_unidades", "agotado"}


def _enum(value, allowed: set):
    """Devuelve el valor si está en el enum permitido; si no, null."""
    return value if value in allowed else None


def _conn_kwargs(db_url: str) -> dict:
    """Parsea la connection string manualmente y devuelve kwargs para psycopg.

    Evita el parser de URI estándar: las passwords de Supabase suelen traer
    caracteres especiales (( [ ] @ etc.) que romperían el parseo o exigirían
    URL-encoding. Aquí la password se pasa LITERAL a psycopg.
    """
    url = db_url.split("?", 1)[0]
    # postgresql://USER:PASSWORD@HOST:PORT/DB   (PASSWORD greedy hasta el último @)
    m = re.match(r"^postgres(?:ql)?://([^:/@]+):(.*)@([^:@/]+):(\d+)/([^/?]+)$", url)
    if not m:
        raise SystemExit("No pude parsear SUPABASE_DB_URL. Formato esperado: postgresql://user:pass@host:port/db")
    user, pwd, host, port, db = m.groups()
    return dict(host=host, port=int(port), user=user, password=pwd, dbname=db, sslmode="require")


def _dev_params(dv: dict) -> dict:
    return {
        # name es NOT NULL; los failed/skipped no traen nombre → fallback al website
        "name": dv.get("name") or dv.get("website"),
        "website": dv.get("website"),
        "phone": dv.get("phone"),
        "email": dv.get("email"),
        "whatsapp": dv.get("whatsapp"),
        "profile_url": dv.get("profile_url"),
        "description": dv.get("description"),
        "cities": dv.get("cities") or None,
        "country": dv.get("country") or "MX",
        "source": dv.get("source") or "lista-297",
        "extraction_status": dv.get("extraction_status"),
        "extraction_error": dv.get("extraction_error"),
    }


def _development_params(d: dict, developer_id) -> dict:
    return {
        "developer_id": developer_id,
        "name": d.get("name"),
        "housing_type": _enum(d.get("housing_type"), DEV_HOUSING),
        "status": _enum(d.get("status"), DEV_STATUS),
        "state": d.get("state"),
        "municipality": d.get("municipality"),
        "neighborhood": d.get("neighborhood"),
        "price_from": d.get("price_from"),
        "price_to": d.get("price_to"),
        "price_on_request": d.get("price_on_request", False),
        "amenities": d.get("amenities") or None,
        "delivery_date": d.get("delivery_date"),
        "url": d.get("url"),
        "sales_phone": d.get("sales_phone"),
        "sales_whatsapp": d.get("sales_whatsapp"),
        "sales_email": d.get("sales_email"),
    }


def _model_params(m: dict, development_id) -> dict:
    return {
        "development_id": development_id,
        "name": m.get("name"),
        "housing_type": _enum(m.get("housing_type"), MODEL_HOUSING),
        "bedrooms": m.get("bedrooms"),
        "bathrooms": m.get("bathrooms"),
        "parking": m.get("parking"),
        "area_built_m2": m.get("area_built_m2"),
        "area_lot_m2": m.get("area_lot_m2"),
        "price": m.get("price"),
        "currency": m.get("currency") or "MXN",
        "availability": _enum(m.get("availability"), MODEL_AVAIL),
    }


_UPSERT_DEVELOPER = """
insert into developers
  (name, website, phone, email, whatsapp, profile_url, description, cities,
   country, source, extraction_status, extraction_error)
values
  (%(name)s, %(website)s, %(phone)s, %(email)s, %(whatsapp)s, %(profile_url)s,
   %(description)s, %(cities)s, %(country)s, %(source)s, %(extraction_status)s, %(extraction_error)s)
on conflict (website) do update set
  name = excluded.name, phone = excluded.phone, email = excluded.email,
  whatsapp = excluded.whatsapp, profile_url = excluded.profile_url,
  description = excluded.description, cities = excluded.cities,
  extraction_status = excluded.extraction_status,
  extraction_error = excluded.extraction_error, updated_at = now()
returning id
"""

_INSERT_DEVELOPMENT = """
insert into developments
  (developer_id, name, housing_type, status, state, municipality, neighborhood,
   price_from, price_to, price_on_request, amenities, delivery_date, url,
   sales_phone, sales_whatsapp, sales_email)
values
  (%(developer_id)s, %(name)s, %(housing_type)s, %(status)s, %(state)s, %(municipality)s,
   %(neighborhood)s, %(price_from)s, %(price_to)s, %(price_on_request)s, %(amenities)s,
   %(delivery_date)s, %(url)s, %(sales_phone)s, %(sales_whatsapp)s, %(sales_email)s)
returning id
"""

_INSERT_MODEL = """
insert into models
  (development_id, name, housing_type, bedrooms, bathrooms, parking,
   area_built_m2, area_lot_m2, price, currency, availability)
values
  (%(development_id)s, %(name)s, %(housing_type)s, %(bedrooms)s, %(bathrooms)s, %(parking)s,
   %(area_built_m2)s, %(area_lot_m2)s, %(price)s, %(currency)s, %(availability)s)
"""


def load(path: str, db_url: str, init: bool, truncate: bool) -> None:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    developers = data["developers"]

    # El Transaction pooler de Supabase (pgbouncer, puerto 6543) no soporta prepared
    # statements; los deshabilitamos. La password se pasa literal vía _conn_kwargs.
    n_dev = n_devel = n_model = 0
    with psycopg.connect(**_conn_kwargs(db_url), prepare_threshold=None) as conn:
        with conn.cursor() as cur:
            if init:
                cur.execute(SCHEMA_PATH.read_text(encoding="utf-8"))
                conn.commit()
                print("✓ schema aplicado (db/schema.sql)")
            if truncate:
                cur.execute("truncate leads, models, developments, developers restart identity cascade")
                conn.commit()
                print("✓ tablas vaciadas (truncate)")

            for dv in developers:
                if not dv.get("website"):
                    continue
                try:
                    cur.execute(_UPSERT_DEVELOPER, _dev_params(dv))
                    developer_id = cur.fetchone()[0]
                    # Reemplazar inventario de esta desarrolladora
                    cur.execute("delete from developments where developer_id = %s", (developer_id,))
                    for d in dv.get("developments", []):
                        cur.execute(_INSERT_DEVELOPMENT, _development_params(d, developer_id))
                        development_id = cur.fetchone()[0]
                        for m in d.get("models", []):
                            cur.execute(_INSERT_MODEL, _model_params(m, development_id))
                            n_model += 1
                        n_devel += 1
                    n_dev += 1
                    conn.commit()
                except Exception as e:  # noqa: BLE001
                    conn.rollback()
                    print(f"  ✗ {dv.get('website')}: {e}")

    print(f"\n✓ Cargado: {n_dev} desarrolladoras, {n_devel} desarrollos, {n_model} modelos")


def main() -> None:
    ap = argparse.ArgumentParser(description="Carga inventario JSON a Supabase")
    ap.add_argument("--input", default="data/scraped.json")
    ap.add_argument("--init", action="store_true", help="crear tablas (schema.sql) antes de cargar")
    ap.add_argument("--truncate", action="store_true", help="vaciar las tablas antes de cargar")
    args = ap.parse_args()

    db_url = os.getenv("SUPABASE_DB_URL")
    if not db_url or "[PASSWORD]" in db_url:
        raise SystemExit("Falta SUPABASE_DB_URL en .env (connection string de Supabase). Ver .env.example")
    if not Path(args.input).exists():
        raise SystemExit(f"No existe {args.input}")

    print(f"Cargando {args.input} → Supabase...")
    load(args.input, db_url, args.init, args.truncate)


if __name__ == "__main__":
    main()
