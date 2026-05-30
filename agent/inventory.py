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
        loc_terms = [_norm(t) for t in (state, municipality, zone) if t]

        scored: list[tuple[int, dict]] = []
        for dr in self.developers:
            for dp in dr.developments:
                if not dp.name:
                    continue
                if loc_terms and not self._loc_match(dp, loc_terms):
                    continue
                if housing_type and not self._type_match(dp, housing_type, is_terreno):
                    continue

                matches = [
                    m for m in dp.models
                    if self._model_ok(m, is_terreno, bedrooms_min, budget_max)
                ]
                # Sin modelos parseados o sin match exacto: igual se ofrece si la
                # ubicación/tipo cuadran (broker → ofrecer y capturar el lead).
                has_models = bool(dp.models)
                if has_models and not matches:
                    continue

                # score: más alto = mejor (modelos con precio dentro de presupuesto primero)
                score = 0
                if matches:
                    score += 2
                if any(m.price is not None for m in matches):
                    score += 1
                if not dp.price_on_request:
                    score += 1
                scored.append((score, self._summary(dr, dp, matches)))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [s for _, s in scored[:limit]]

    # ---- helpers ----
    def _loc_match(self, dp: Development, terms: list[str]) -> bool:
        hay = " ".join(_norm(x) for x in (dp.state, dp.municipality, dp.neighborhood))
        return any(t in hay for t in terms)

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

    def _summary(self, dr: Developer, dp: Development, matches: list) -> dict:
        best = sorted(
            (m for m in matches if m.price is not None), key=lambda m: m.price
        )
        sample = (best or matches or dp.models)[:2]
        return {
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
