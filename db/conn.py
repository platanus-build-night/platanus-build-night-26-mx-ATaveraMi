"""Conexión a Supabase (Postgres) vía psycopg.

Lee `SUPABASE_DB_URL` del entorno. La URL del pooler de Supabase trae `?pgbouncer=true`,
que libpq NO entiende → lo quitamos. Además el pooler corre en modo transacción, donde
los prepared statements de psycopg rompen → `prepare_threshold=None` los desactiva.
"""
from __future__ import annotations

import os
import urllib.parse as up

import psycopg
from dotenv import load_dotenv

load_dotenv()


def _clean_url(raw: str) -> str:
    parts = up.urlsplit(raw)
    q = [(k, v) for k, v in up.parse_qsl(parts.query) if k.lower() != "pgbouncer"]
    return up.urlunsplit((parts.scheme, parts.netloc, parts.path, up.urlencode(q), parts.fragment))


def connect(**kwargs) -> psycopg.Connection:
    raw = os.getenv("SUPABASE_DB_URL")
    if not raw:
        raise RuntimeError("Falta SUPABASE_DB_URL en .env")
    opts = {"connect_timeout": 15, "prepare_threshold": None}
    opts.update(kwargs)
    return psycopg.connect(_clean_url(raw), **opts)
