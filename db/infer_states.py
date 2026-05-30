"""Infiere el `state` de developments sin estado a partir de su desarrolladora.

Criterio CONSERVADOR (alta confianza, sin LLM):
  1. Si los OTROS developments del mismo developer apuntan todos a UN solo estado MX
     → ese estado.
  2. Si no, y `developer.cities` normaliza a UN solo estado MX → ese estado.
  3. Si hay ambigüedad (varios estados) o el candidato no es MX → se deja sin tocar.

Uso:
    python -m db.infer_states --dry-run   # muestra qué inferiría
    python -m db.infer_states             # aplica
"""
from __future__ import annotations

import argparse
import os
from collections import Counter, defaultdict

import psycopg
from dotenv import load_dotenv

from .load_to_supabase import _conn_kwargs
from .normalize import OFFICIAL, normalize_state

load_dotenv()

OFFICIAL_SET = set(OFFICIAL)


def _mx_state(value) -> str | None:
    """Normaliza un valor (estado o ciudad) y lo devuelve solo si es estado MX oficial."""
    n = normalize_state(value)
    return n if n in OFFICIAL_SET else None


def run(db_url: str, dry_run: bool) -> None:
    with psycopg.connect(**_conn_kwargs(db_url), prepare_threshold=None) as conn:
        with conn.cursor() as cur:
            cur.execute("select id, cities from developers")
            dev_cities = {r[0]: (r[1] or []) for r in cur.fetchall()}

            cur.execute("select id, developer_id, state from developments")
            rows = cur.fetchall()

            # Estados MX confirmados por developer (de sus developments con estado)
            dev_states: dict = defaultdict(set)
            for _did, devid, state in rows:
                if state in OFFICIAL_SET:
                    dev_states[devid].add(state)

            def candidate(devid) -> str | None:
                st = dev_states.get(devid, set())
                if len(st) == 1:
                    return next(iter(st))
                if len(st) > 1:
                    return None  # developer multi-estado → ambiguo
                # sin developments con estado: probar cities
                norm = {s for s in (_mx_state(c) for c in dev_cities.get(devid, [])) if s}
                return next(iter(norm)) if len(norm) == 1 else None

            changes = []
            for did, devid, state in rows:
                if state is None:
                    cand = candidate(devid)
                    if cand:
                        changes.append((did, cand))

            print(f"Sin estado: {sum(1 for _,_,s in rows if s is None)} | inferibles: {len(changes)}\n")
            for est, cnt in Counter(c for _, c in changes).most_common():
                print(f"  → {est}: {cnt}")

            if dry_run:
                print("\n(dry-run: no se aplicó nada)")
                return
            for did, cand in changes:
                cur.execute("update developments set state=%s, updated_at=now() where id=%s", (cand, did))
            conn.commit()
            print(f"\n✓ Aplicados {len(changes)} updates")


def main() -> None:
    ap = argparse.ArgumentParser(description="Infiere states desde la desarrolladora")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    db_url = os.getenv("SUPABASE_DB_URL")
    if not db_url or "[PASSWORD]" in db_url:
        raise SystemExit("Falta SUPABASE_DB_URL en .env")
    run(db_url, args.dry_run)


if __name__ == "__main__":
    main()
