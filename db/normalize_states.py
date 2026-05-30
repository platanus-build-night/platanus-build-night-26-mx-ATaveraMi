"""Normaliza la columna `state` de developments ya cargados en Supabase.

Recorre todos los developments, recalcula el estado con db.normalize y aplica
UPDATE solo donde cambia. Idempotente. Reusa la conexión del loader.

Uso:
    python -m db.normalize_states           # aplica los cambios
    python -m db.normalize_states --dry-run # solo muestra qué cambiaría
"""
from __future__ import annotations

import argparse
import os
from collections import Counter

import psycopg
from dotenv import load_dotenv

from .load_to_supabase import _conn_kwargs
from .normalize import normalize_state

load_dotenv()


def run(db_url: str, dry_run: bool) -> None:
    changes: list[tuple] = []
    with psycopg.connect(**_conn_kwargs(db_url), prepare_threshold=None) as conn:
        with conn.cursor() as cur:
            cur.execute("select id, state, municipality, neighborhood from developments")
            rows = cur.fetchall()
            for did, state, muni, barrio in rows:
                new = normalize_state(state, muni, barrio)
                if new != state:
                    changes.append((did, state, new))

            # Resumen de transformaciones (origen → destino)
            summary = Counter((s, n) for _, s, n in changes)
            print(f"Developments totales: {len(rows)} | a cambiar: {len(changes)}\n")
            for (old, new), cnt in summary.most_common():
                print(f"  {repr(old)} → {repr(new)}   x{cnt}")

            if dry_run:
                print("\n(dry-run: no se aplicó nada)")
                return

            for did, _old, new in changes:
                cur.execute("update developments set state=%s, updated_at=now() where id=%s", (new, did))
            conn.commit()
            print(f"\n✓ Aplicados {len(changes)} updates")


def main() -> None:
    ap = argparse.ArgumentParser(description="Normaliza states de developments en Supabase")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    db_url = os.getenv("SUPABASE_DB_URL")
    if not db_url or "[PASSWORD]" in db_url:
        raise SystemExit("Falta SUPABASE_DB_URL en .env")
    run(db_url, args.dry_run)


if __name__ == "__main__":
    main()
