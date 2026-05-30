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
import unicodedata
from pathlib import Path
from typing import Optional

from .models import Developer, Development

# Sinónimos/abreviaturas de ubicación → nombre normalizado (sin acentos) que aparece en
# los datos. El comprador escribe "cdmx"/"df" y el inventario dice "Ciudad de México".
# Inocuo sobre los datos: ningún valor real es exactamente "cdmx"/"df"/"qro".
_LOC_ALIASES: dict[str, str] = {
    "cdmx": "ciudad de mexico",
    "df": "ciudad de mexico",
    "d f": "ciudad de mexico",
    "mexico df": "ciudad de mexico",
    "mexico city": "ciudad de mexico",
    "qro": "queretaro",
}


def _norm(s: Optional[str]) -> str:
    """Normaliza para comparar: minúsculas, SIN acentos, espacios colapsados, y expande
    alias de ubicación. Así "Querétaro"=="queretaro" y "CDMX"=="Ciudad de México"."""
    base = (s or "").strip().lower()
    base = unicodedata.normalize("NFD", base)
    base = "".join(c for c in base if unicodedata.category(c) != "Mn")
    base = " ".join(base.split())
    return _LOC_ALIASES.get(base, base)


# 32 estados de México (normalizados). El scrapeado trae ruido fuera de MX (Texas,
# Maldonado…) que NO debe ofrecérsele a un comprador mexicano → se filtra al cargar.
_MX_STATES = {
    _norm(s) for s in (
        "Aguascalientes", "Baja California", "Baja California Sur", "Campeche", "Chiapas",
        "Chihuahua", "Coahuila", "Colima", "Ciudad de México", "Durango", "Guanajuato",
        "Guerrero", "Hidalgo", "Jalisco", "México", "Estado de México", "Michoacán",
        "Morelos", "Nayarit", "Nuevo León", "Oaxaca", "Puebla", "Querétaro", "Quintana Roo",
        "San Luis Potosí", "Sinaloa", "Sonora", "Tabasco", "Tamaulipas", "Tlaxcala",
        "Veracruz", "Yucatán", "Zacatecas",
    )
}


def _is_mx_or_unknown(state: Optional[str]) -> bool:
    """True si el estado es de México o desconocido (None). False si es claramente extranjero."""
    if not state:
        return True  # sin estado: no lo descartamos (puede ser MX sin parsear)
    return _norm(state) in _MX_STATES


class InventoryRepo:
    def __init__(self, developers: list[Developer]) -> None:
        # Filtra desarrollos claramente fuera de México (ruido del scrapeado: Texas, etc.).
        for dr in developers:
            dr.developments = [d for d in dr.developments if _is_mx_or_unknown(d.state)]
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
            "developer_website": dr.website if dr else None,
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

    # ---- panorama del inventario (para que el agente decida con datos) ----
    def overview(self) -> dict:
        """Resumen agregado del inventario: estados (con conteo y tipos), tipos disponibles
        y rango de precios. Para que el agente sepa QUÉ hay antes de orientar la conversación.
        """
        states: dict[str, dict] = {}
        htypes: set[str] = set()
        all_prices: list[float] = []
        n_dev = n_model = 0
        for dr in self.developers:
            for dp in dr.developments:
                if not dp.name:
                    continue
                n_dev += 1
                if dp.housing_type:
                    htypes.add(dp.housing_type)
                st = dp.state or "(sin estado)"
                s = states.setdefault(
                    st, {"developments": 0, "housing_types": set(), "price_from": None, "price_to": None}
                )
                s["developments"] += 1
                if dp.housing_type:
                    s["housing_types"].add(dp.housing_type)
                for m in dp.models:
                    n_model += 1
                    if m.housing_type:
                        htypes.add(m.housing_type)
                    if m.price is not None:
                        all_prices.append(m.price)
                        s["price_from"] = m.price if s["price_from"] is None else min(s["price_from"], m.price)
                        s["price_to"] = m.price if s["price_to"] is None else max(s["price_to"], m.price)
        return {
            "total_developers": len(self.developers),
            "total_developments": n_dev,
            "total_models": n_model,
            "housing_types_available": sorted(htypes),
            "price_range_mxn": {
                "min": min(all_prices) if all_prices else None,
                "max": max(all_prices) if all_prices else None,
            },
            "states": sorted(
                (
                    {
                        "state": st,
                        "developments": v["developments"],
                        "housing_types": sorted(v["housing_types"]),
                        "price_from": v["price_from"],
                        "price_to": v["price_to"],
                    }
                    for st, v in states.items()
                ),
                key=lambda x: -x["developments"],
            ),
        }

    # ---- búsqueda por desarrolladora / marca ----
    def find_by_developer(self, query: str, *, limit: int = 6) -> dict:
        """Busca desarrolladoras por nombre (o dominio) y devuelve sus desarrollos.

        Para "¿tienes algo de Atlas/MiRA/Vinte?". Match por substring sin acentos.
        """
        q = _norm(query)
        if not q:
            return {"found": False, "developers": []}
        out: list[dict] = []
        for dr in self.developers:
            if q in _norm(dr.name) or (dr.website and q in _norm(dr.website)):
                devs = [
                    self._summary(dr, dp, dp.models, 0, None)
                    for dp in dr.developments if dp.name
                ]
                if devs or dr.name:
                    out.append({
                        "developer": dr.name,
                        "website": dr.website,
                        "n_developments": len(devs),
                        "developments": devs[:limit],
                    })
        return {"found": bool(out), "developers": out[:limit]}

    # ---- zonas disponibles (anti-alucinación) ----
    def list_areas(
        self, *, housing_type: Optional[str] = None, state: Optional[str] = None
    ) -> dict:
        """Zonas/estados REALES donde hay inventario de un tipo.

        Para responder "¿qué zonas tienes?" o para ofrecer alternativas verdaderas cuando
        una búsqueda salió vacía — en vez de que el LLM invente zonas.
        """
        is_terreno = _norm(housing_type) == "terreno"
        want_state = _norm(state) if state else None

        zones_here: list[str] = []
        seen: set[str] = set()
        states_with_type: set[str] = set()
        for dr in self.developers:
            for dp in dr.developments:
                if not dp.name:
                    continue
                if housing_type and not self._type_match(dp, housing_type, is_terreno):
                    continue
                if dp.state:
                    states_with_type.add(dp.state)
                st = _norm(dp.state)
                in_state = want_state is None or (
                    want_state in st or (st and st in want_state)
                )
                if in_state:
                    label = self._zone(dp)
                    if label not in seen:
                        seen.add(label)
                        zones_here.append(label)
        return {
            "housing_type": housing_type,
            "requested_state": state,
            "total_here": len(zones_here),
            "zones_available_here": zones_here,
            "states_with_this_type": sorted(states_with_type),
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
        max_show: int = 5,
        pool: int = 7,
    ) -> dict:
        """Devuelve {total_matches, should_narrow, results}.

        - `total_matches`: cuántos desarrollos cuadran (en la mejor capa de ubicación).
        - `should_narrow`: True si hay más de `max_show` → el agente debe seguir perfilando.
        - `results`: hasta `pool` candidatos pre-rankeados por fit (el agente curará ≤5).
        """
        is_terreno = _norm(housing_type) == "terreno"
        has_loc = bool(state or municipality or zone)

        # (loc_tier, fit_score, dr, dp, matches) — loc_tier: 3=zona, 2=municipio, 1=estado
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

                fit = self._fit_score(dp, matches, budget_max, bedrooms_min, is_terreno)
                cands.append((loc or 0, fit, dr, dp, matches))

        if not cands:
            # Vacío NUNCA "mudo": el código (que sí conoce el inventario) calcula las
            # alternativas REALES para que el agente no improvise zonas inexistentes.
            return {
                "total_matches": 0,
                "should_narrow": False,
                "results": [],
                "alternativas": self._alternatives(
                    state=state, municipality=municipality, zone=zone,
                    housing_type=housing_type, is_terreno=is_terreno,
                ),
            }

        # Jerarquía: si hay resultados en una capa más específica (p.ej. la zona exacta),
        # NO mezclar las más amplias. Solo se amplía cuando no hay nada más fino.
        best_tier = max(c[0] for c in cands)
        tier = [c for c in cands if c[0] == best_tier]
        # Pre-ranking determinista por fit; desempate por precio ascendente (no orden de carga).
        tier.sort(key=lambda c: (-c[1], self._sort_price(c[3])))

        total = len(tier)
        # Estados presentes en los resultados: si hay >1, la zona es ambigua (p.ej. "Roma"
        # existe en CDMX y en Monterrey) → el agente debe aclarar de qué ciudad.
        # Dedup por forma normalizada para no contar "CDMX" y "Ciudad de México" como dos.
        _seen_states: set[str] = set()
        states_in_results: list[str] = []
        for _, _, _, dp, _ in tier:
            if dp.state and _norm(dp.state) not in _seen_states:
                _seen_states.add(_norm(dp.state))
                states_in_results.append(dp.state)
        return {
            "total_matches": total,
            "should_narrow": total > max_show,
            "states_in_results": states_in_results,
            "results": [
                self._summary(dr, dp, matches, best_tier, zone)
                for _, _, dr, dp, matches in tier[:pool]
            ],
        }

    # ---- alternativas reales cuando una búsqueda sale vacía (anti-improvisación) ----
    def _infer_state(self, municipality: Optional[str], zone: Optional[str]) -> Optional[str]:
        """Deduce el estado a partir del municipio/zona pedidos, mirando el inventario.

        Si la zona pedida NO está en los datos (p.ej. "Coyoacán", que no tenemos), no se
        puede inferir → devuelve None y el agente debe pasar `state` explícito.
        """
        terms = [
            _norm(p)
            for v in (municipality, zone)
            for p in (v or "").split(",")
            if _norm(p)
        ]
        if not terms:
            return None
        for dr in self.developers:
            for dp in dr.developments:
                hay = f"{_norm(dp.neighborhood)} {_norm(dp.municipality)}"
                if any(t in hay or (hay.strip() and t in hay) for t in terms):
                    return _norm(dp.state)
        return None

    def _alternatives(
        self, *, state, municipality, zone, housing_type, is_terreno
    ) -> dict:
        """Qué SÍ hay, dado lo que se pidió (todo verificado contra el inventario):

        - `mismo_estado_otros_tipos`: tipos disponibles en el estado pedido (p.ej. pidió
          "casa en CDMX" → "en CDMX hay departamento"). Lo más cercano al intent.
        - `mismo_tipo_otros_estados`: dónde SÍ hay ese tipo, marcado como OTRO ESTADO
          (p.ej. "casas en Querétaro") — el agente debe preguntarlo, no proponerlo como cercano.
        """
        requested_state = _norm(state) if state else self._infer_state(municipality, zone)
        alts: dict = {}

        # (1) mismo estado, otros tipos disponibles ahí
        if requested_state:
            types_here: set[str] = set()
            state_label: Optional[str] = None
            for dr in self.developers:
                for dp in dr.developments:
                    if not dp.name:
                        continue
                    st = _norm(dp.state)
                    if requested_state in st or (st and st in requested_state):
                        state_label = dp.state
                        if dp.housing_type:
                            types_here.add(_norm(dp.housing_type))
                        for m in dp.models:
                            if m.housing_type:
                                types_here.add(_norm(m.housing_type))
            if housing_type:
                types_here.discard(_norm(housing_type))  # ese tipo justo no hay
            if types_here:
                alts["mismo_estado_otros_tipos"] = {
                    "estado": state_label,
                    "housing_types": sorted(types_here),
                }

        # (2) mismo tipo, otros estados (cruce de estado: SIEMPRE marcado)
        if housing_type:
            la = self.list_areas(housing_type=housing_type)
            otros = [
                s for s in la["states_with_this_type"] if _norm(s) != requested_state
            ]
            if otros:
                alts["mismo_tipo_otros_estados"] = {
                    "housing_type": housing_type,
                    "estados": otros,
                    "cruza_estado": True,
                }
        return alts

    # ---- helpers ----
    def _loc_score(
        self, dp: Development, state, municipality, zone
    ) -> Optional[int]:
        """Cercanía a lo pedido: 3=zona/colonia, 2=municipio, 1=estado, None=no cuadra.

        Más específico gana. Evita que un match de estado ("Querétaro") arrastre TODO
        el estado cuando el comprador pidió una zona concreta (p.ej. "Zibatá").

        `zone`/`municipality`/`state` aceptan VARIOS términos separados por coma (match si
        cualquiera cuadra). Así el agente puede resolver "el sur de CDMX" a alcaldías reales
        (p.ej. zone="Tlalpan, Coyoacán, Xochimilco") y barrer la franja en una sola búsqueda.
        """
        nb, mun, st = _norm(dp.neighborhood), _norm(dp.municipality), _norm(dp.state)

        def _terms(value) -> list[str]:
            return [t for t in (_norm(p) for p in (value or "").split(",")) if t]

        # El ESTADO es filtro DURO cuando se especifica: si no cuadra, fuera — aunque la
        # zona coincida. Así "Roma CDMX" NUNCA trae la "Roma" de Monterrey.
        if state and not any((s in st or (st and st in s)) for s in _terms(state)):
            return None

        if zone and any(
            (z in nb or (nb and nb in z) or z in mun) for z in _terms(zone)
        ):
            return 3
        if municipality and any(
            (m in mun or (mun and mun in m)) for m in _terms(municipality)
        ):
            return 2
        if state:
            return 1  # el estado ya pasó el filtro duro; sin zona/municipio específico
        if not (zone or municipality or state):
            return 0
        return None

    def _type_match(self, dp: Development, housing_type: str, is_terreno: bool) -> bool:
        ht = _norm(housing_type)
        dht = _norm(dp.housing_type)
        model_types = {_norm(m.housing_type) for m in dp.models if m.housing_type}

        if is_terreno:
            return dht == "terreno" or "terreno" in model_types
        # Requiere EVIDENCIA real del tipo: el tipo del desarrollo, o un modelo de ese tipo.
        # Sin lenidad de tipo: ofrecer un depto/tipo-desconocido como "casa" es engañoso (y el
        # comprador lo nota). La lenidad (no descartar por desconocido) aplica a precio/recámaras,
        # NO al tipo, que es categórico.
        return dht == ht or ht in model_types

    @staticmethod
    def _sort_price(dp: Development) -> float:
        return dp.price_from if dp.price_from is not None else float("inf")

    @staticmethod
    def _fit_score(dp: Development, matches: list, budget_max, bedrooms_min, is_terreno) -> int:
        """Qué tan bien le queda al comprador (mayor = mejor). Para pre-rankear candidatos."""
        priced = [m for m in matches if m.price is not None]
        s = 0
        if budget_max and priced and any(m.price <= budget_max for m in priced):
            s += 4  # tiene algo dentro de presupuesto (lo más importante)
        if not is_terreno and bedrooms_min and any((m.bedrooms or 0) >= bedrooms_min for m in matches):
            s += 2  # cumple recámaras pedidas
        if any((m.availability or "") == "disponible" for m in matches):
            s += 1  # disponibilidad
        if priced:
            s += 1  # tiene precio real (mejor que "a consultar")
        if not dp.price_on_request:
            s += 1
        return s

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
