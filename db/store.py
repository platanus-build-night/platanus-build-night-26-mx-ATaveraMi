"""Backend Supabase para el agente: lee el inventario y escribe los leads.

- `load_inventory_repo()` lee developers/developments/models y arma el MISMO
  `InventoryRepo` en memoria (reusa toda la lógica de búsqueda ya probada). Es un
  snapshot al arranque; reiniciar el server lo refresca.
- `SupabaseLeadRepo` hace INSERT en la tabla `leads` (misma interfaz que `LeadRepo`).

Así, cambiar de JSON a Supabase NO toca al agente: solo cambia quién provee el repo.
"""
from __future__ import annotations

import logging

from psycopg.rows import dict_row

from agent.inventory import InventoryRepo
from agent.models import Developer, Lead

from .conn import connect

logger = logging.getLogger("viviendin.db.store")

# CHECKs de la tabla leads: saneamos a valor válido o null para no romper el INSERT.
_CREDIT = {"infonavit", "bancario", "contado", "cofinavit", "na"}
_HORIZON = {"ya", "3-6m", "explorando"}


def load_inventory_repo() -> InventoryRepo:
    """Lee todo el inventario de Supabase y construye el InventoryRepo en memoria."""
    with connect() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "select id::text, name, website, phone, email, whatsapp, description, cities "
            "from developers"
        )
        devs: dict[str, dict] = {}
        order: list[str] = []
        for r in cur.fetchall():
            r["developments"] = []
            devs[r["id"]] = r
            order.append(r["id"])

        cur.execute(
            "select id::text, developer_id::text, name, housing_type, status, state, "
            "municipality, neighborhood, price_from, price_to, price_on_request, amenities, "
            "delivery_date, url, sales_phone, sales_whatsapp, sales_email from developments"
        )
        dmap: dict[str, dict] = {}
        for r in cur.fetchall():
            r["models"] = []
            dmap[r["id"]] = r
            dev = devs.get(r["developer_id"])
            if dev is not None:
                dev["developments"].append(r)

        cur.execute(
            "select id::text, development_id::text, name, housing_type, bedrooms, bathrooms, "
            "parking, area_built_m2, area_lot_m2, price, currency, availability from models"
        )
        for r in cur.fetchall():
            dp = dmap.get(r["development_id"])
            if dp is not None:
                dp["models"].append(r)

    developers = [Developer.model_validate(devs[i]) for i in order]
    logger.info(
        "Inventario desde Supabase: %d desarrolladoras, %d desarrollos",
        len(developers),
        sum(len(d.developments) for d in developers),
    )
    return InventoryRepo(developers)


class SupabaseLeadRepo:
    """Persiste leads en la tabla `leads` de Supabase. Misma interfaz que LeadRepo."""

    _INSERT = """
    insert into leads
      (wa_user, name, budget, search_state, search_zone, housing_type, bedrooms,
       credit, horizon, development_id, model_id, visit_date, visit_time, status, notes)
    values
      (%(wa_user)s, %(name)s, %(budget)s, %(search_state)s, %(search_zone)s, %(housing_type)s,
       %(bedrooms)s, %(credit)s, %(horizon)s, %(development_id)s, %(model_id)s, %(visit_date)s,
       %(visit_time)s, %(status)s, %(notes)s)
    returning id::text
    """

    def save(self, lead: Lead) -> str:
        rec = lead.model_dump()
        # Sanear contra los CHECK de la tabla (el agente puede mandar valores libres).
        rec["credit"] = rec.get("credit") if rec.get("credit") in _CREDIT else None
        rec["horizon"] = rec.get("horizon") if rec.get("horizon") in _HORIZON else None
        with connect() as conn, conn.cursor() as cur:
            cur.execute(self._INSERT, rec)
            lead_id = cur.fetchone()[0]
            conn.commit()
        logger.info("Lead %s guardado en Supabase (wa_user=%s)", lead_id, rec.get("wa_user"))
        return lead_id
