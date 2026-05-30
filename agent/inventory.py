"""InventoryRepo — capa de datos del agente (lee inventario, filtra a nivel modelo).

Detrás de una interfaz simple para poder cambiar de seed.json/scraped.json a
Supabase sin tocar el agente. Carga el JSON anidado (developer→developments→models,
misma forma que data/seed.json) y resuelve búsquedas y contacto de handoff.

Reglas de match (degradan con gracia — el objetivo es capturar el lead, no un
filtro perfecto):
- Ubicación: substring case-insensitive sobre estado/municipio/colonia.
- Presupuesto: un modelo SIN precio (price_on_request) NO se descarta; se ofrece igual.
- Recámaras: un modelo sin recámaras conocidas NO se descarta.
- Terreno: se ignoran recámaras; filtra por precio (y superficie del lote).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from .models import Developer, Development


def _norm(s: Optional[str]) -> str:
    return (s or "").strip().lower()


class InventoryRepo:
    def __init__(self, developers: list[Developer]) -> None:
        self.developers = developers
        # índices por id para lookups O(1)
        self._dev_by_id: dict[str, Development] = {}
        self._developer_by_dev_id: dict[str, Developer] = {}
        for dr in developers:
            for dp in dr.developments:
                if dp.id:
                    self._dev_by_id[dp.id] = dp
                    self._developer_by_dev_id[dp.id] = dr

    # ---- carga ----
    @classmethod
    def from_paths(cls, paths: list[str | Path]) -> "InventoryRepo":
        """Carga y combina uno o más archivos con la forma {_meta, developers:[...]}."""
        developers: list[Developer] = []
        for p in paths:
            path = Path(p)
            if not path.exists():
                continue
            data = json.loads(path.read_text(encoding="utf-8"))
            for raw in data.get("developers", []):
                if not raw.get("name") and not raw.get("developments"):
                    continue  # registros failed/skipped sin contenido útil
                developers.append(Developer.model_validate(raw))
        return cls(developers)

    # ---- contacto para el handoff de broker ----
    def sales_contact(self, development_id: str) -> Optional[str]:
        """Contacto de ventas del desarrollo; si vacío, cae al corporativo del developer."""
        dp = self._dev_by_id.get(development_id)
        dr = self._developer_by_dev_id.get(development_id)
        for c in (dp.sales_whatsapp, dp.sales_phone, dp.sales_email) if dp else ():
            if c:
                return c
        for c in (dr.whatsapp, dr.phone, dr.email) if dr else ():
            if c:
                return c
        return None

    def developer_of(self, development_id: str) -> Optional[Developer]:
        return self._developer_by_dev_id.get(development_id)

    # ---- detalle ----
    def get_development(self, development_id: str) -> Optional[dict]:
        dp = self._dev_by_id.get(development_id)
        if not dp:
            return None
        dr = self._developer_by_dev_id.get(development_id)
        return {
            "development_id": dp.id,
            "name": dp.name,
            "developer": dr.name if dr else None,
            "housing_type": dp.housing_type,
            "status": dp.status,
            "zone": self._zone(dp),
            "price_desde": self._price_label(dp),
            "amenities": dp.amenities or [],
            "delivery_date": dp.delivery_date,
            "url": dp.url,
            "sales_contact": self.sales_contact(dp.id) if dp.id else None,
            "models": [
                {
                    "model_id": m.id,
                    "name": m.name,
                    "housing_type": m.housing_type,
                    "bedrooms": m.bedrooms,
                    "bathrooms": m.bathrooms,
                    "area_built_m2": m.area_built_m2,
                    "area_lot_m2": m.area_lot_m2,
                    "price": m.price,
                    "availability": m.availability,
                }
                for m in dp.models
            ],
        }

    # ---- búsqueda ----
    def search(
        self,
        *,
        state: Optional[str] = None,
        municipality: Optional[str] = None,
        zone: Optional[str] = None,
        housing_type: Optional[str] = None,
        bedrooms_min: Optional[int] = None,
        budget_max: Optional[float] = None,
        limit: int = 3,
    ) -> list[dict]:
        is_terreno = _norm(housing_type) == "terreno"
        has_loc = bool(state or municipality or zone)

        # (loc_tier, content_score, dr, dp, matches) — loc_tier: 3=zona, 2=municipio, 1=estado
        cands: list[tuple[int, int, Developer, Development, list]] = []
        for dr in self.developers:
            for dp in dr.developments:
                if not dp.name:
                    continue
                loc = self._loc_score(dp, state, municipality, zone)
                if has_loc and loc is None:
                    continue  # ni siquiera cae en el estado pedido
                if housing_type and not self._type_match(dp, housing_type, is_terreno):
                    continue

                matches = [
                    m for m in dp.models
                    if self._model_ok(m, is_terreno, bedrooms_min, budget_max)
                ]
                # Sin modelos parseados: igual se ofrece (broker → ofrecer y capturar el lead).
                if dp.models and not matches:
                    continue

                content = 0
                if matches:
                    content += 2
                if any(m.price is not None for m in matches):
                    content += 1
                if not dp.price_on_request:
                    content += 1
                cands.append((loc or 0, content, dr, dp, matches))

        if not cands:
            return []

        # Jerarquía: si hay resultados en una tier más específica (p.ej. la zona exacta),
        # NO mezclar las más amplias. Solo se amplía cuando no hay nada más fino.
        best_tier = max(c[0] for c in cands)
        tier = sorted(
            (c for c in cands if c[0] == best_tier), key=lambda c: c[1], reverse=True
        )
        return [
            self._summary(dr, dp, matches, best_tier, zone)
            for _, _, dr, dp, matches in tier[:limit]
        ]

    # ---- helpers ----
    def _loc_score(
        self, dp: Development, state, municipality, zone
    ) -> Optional[int]:
        """Cercanía a lo pedido: 3=zona/colonia, 2=municipio, 1=estado, None=no cuadra.

        Más específico gana. Evita que un match de estado ("Querétaro") arrastre TODO
        el estado cuando el comprador pidió una zona concreta (p.ej. "Zibatá").
        """
        nb, mun, st = _norm(dp.neighborhood), _norm(dp.municipality), _norm(dp.state)
        if zone:
            z = _norm(zone)
            if z and (z in nb or (nb and nb in z) or z in mun):
                return 3
        if municipality:
            m = _norm(municipality)
            if m and (m in mun or (mun and mun in m)):
                return 2
        if state:
            s = _norm(state)
            if s and (s in st or (st and st in s)):
                return 1
        if not (zone or municipality or state):
            return 0
        return None

    def _type_match(self, dp: Development, housing_type: str, is_terreno: bool) -> bool:
        ht = _norm(housing_type)
        dht = _norm(dp.housing_type)
        if dht in ("", "mixto"):
            return True  # mixto/desconocido: no excluir
        if is_terreno:
            return dht == "terreno"
        # casa/departamento: aceptar si coincide o si el desarrollo tiene modelos del tipo
        if dht == ht:
            return True
        return any(_norm(m.housing_type) == ht for m in dp.models)

    @staticmethod
    def _model_ok(m, is_terreno: bool, bedrooms_min, budget_max) -> bool:
        if budget_max is not None and m.price is not None and m.price > budget_max:
            return False  # precio conocido y fuera de presupuesto → fuera
        if not is_terreno and bedrooms_min is not None and m.bedrooms is not None:
            if m.bedrooms < bedrooms_min:
                return False
        return True  # precio/recámaras desconocidos → NO se descarta

    def _zone(self, dp: Development) -> str:
        parts = [p for p in (dp.neighborhood, dp.municipality, dp.state) if p]
        return ", ".join(parts) or "ubicación por confirmar"

    def _price_label(self, dp: Development) -> str:
        if dp.price_from:
            return f"${dp.price_from:,.0f} MXN"
        return "precio a consultar"

    @staticmethod
    def _location_match(tier: int, requested_zone) -> str:
        """Cómo de bien cuadra con lo pedido (el agente lo usa para ser honesto)."""
        if requested_zone and tier < 3:
            return "ampliado"  # NO había en la zona pedida; se amplió la búsqueda
        return {3: "zona_exacta", 2: "mismo_municipio", 1: "mismo_estado"}.get(tier, "general")

    def _summary(
        self, dr: Developer, dp: Development, matches: list, tier: int, requested_zone
    ) -> dict:
        best = sorted(
            (m for m in matches if m.price is not None), key=lambda m: m.price
        )
        sample = (best or matches or dp.models)[:2]
        return {
            "location_match": self._location_match(tier, requested_zone),
            "development_id": dp.id,
            "name": dp.name,
            "developer": dr.name,
            "zone": self._zone(dp),
            "housing_type": dp.housing_type,
            "price_desde": self._price_label(dp),
            "price_on_request": dp.price_on_request,
            "status": dp.status,
            "highlight": (dp.amenities or [None])[0],
            "modelos": [
                {
                    "model_id": m.id,
                    "name": m.name,
                    "bedrooms": m.bedrooms,
                    "area_built_m2": m.area_built_m2,
                    "area_lot_m2": m.area_lot_m2,
                    "price": m.price,
                }
                for m in sample
            ],
        }
