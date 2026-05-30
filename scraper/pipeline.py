"""Orquestación por desarrolladora (pipeline de 2 fases).

Produce un dict con el MISMO shape que data/seed.json para que el agente lo
consuma como drop-in (y luego un loader lo suba a Supabase).
"""
from __future__ import annotations

import time
import uuid

import httpx

from .config import settings
from .discover import discover_development_urls
from .extract import extract_developer, extract_development, llm_discover_links
from .fetch import extract_internal_links, fetch_html, html_to_markdown


def _id() -> str:
    return str(uuid.uuid4())


# Rango plausible de precio de vivienda nueva en MXN. Fuera de esto casi siempre es
# ruido de extracción ($1, "desde $0") o moneda extranjera (p.ej. COP de un sitio
# colombiano que se coló) → lo dejamos en null para no envenenar el filtro por presupuesto.
PRICE_MIN_MXN = 200_000
PRICE_MAX_MXN = 150_000_000


def _sane_price(p: float | None) -> float | None:
    if p is None:
        return None
    return p if PRICE_MIN_MXN <= p <= PRICE_MAX_MXN else None


def _model_dict(m) -> dict:
    return {
        "id": _id(),
        "name": m.name,
        "housing_type": m.housing_type,
        "bedrooms": m.bedrooms,
        "bathrooms": m.bathrooms,
        "parking": m.parking,
        "area_built_m2": m.area_built_m2,
        "area_lot_m2": m.area_lot_m2,
        "price": _sane_price(m.price),
        "currency": "MXN",
        "availability": m.availability,
    }


def _dedupe_developments(devs: list[dict]) -> list[dict]:
    """Un sitio sirve el mismo desarrollo desde varias URLs (i18n, rutas alternas).
    Deduplica por (nombre, estado, municipio) quedándose con el de más modelos.
    """
    best: dict[tuple, dict] = {}
    for d in devs:
        # (nombre, estado): el municipio se extrae inconsistente (None vs "Apodaca")
        # y dentro de una desarrolladora el nombre+estado ya identifica al desarrollo.
        key = (
            (d.get("name") or "").strip().lower(),
            (d.get("state") or "").strip().lower(),
        )
        cur = best.get(key)
        if cur is None or len(d["models"]) > len(cur["models"]):
            best[key] = d
    return list(best.values())


def _development_dict(d, url: str) -> dict:
    models = [_model_dict(m) for m in d.models]
    prices = [m["price"] for m in models if m["price"] is not None]
    price_on_request = d.price_on_request
    if price_on_request is None:
        price_on_request = len(prices) == 0
    return {
        "id": _id(),
        "name": d.name,
        "housing_type": d.housing_type,
        "status": d.status,
        "state": d.state,
        "municipality": d.municipality,
        "neighborhood": d.neighborhood,
        "price_from": min(prices) if prices else None,
        "price_to": max(prices) if prices else None,
        "price_on_request": price_on_request,
        "amenities": d.amenities or None,
        "delivery_date": d.delivery_date,
        "url": url,
        "sales_phone": d.sales_phone,
        "sales_whatsapp": d.sales_whatsapp,
        "sales_email": d.sales_email,
        "models": models,
    }


async def scrape_developer(client: httpx.AsyncClient, website: str) -> dict:
    result: dict = {
        "id": _id(),
        "name": None,
        "website": website,
        "phone": None,
        "email": None,
        "whatsapp": None,
        "profile_url": None,
        "description": None,
        "cities": [],
        "extraction_status": "pending",
        "extraction_error": None,
        "developments": [],
    }
    start = time.monotonic()

    home = await fetch_html(client, website)
    if not home:
        result["extraction_status"] = "failed"
        result["extraction_error"] = "home no accesible"
        return result

    home_md = html_to_markdown(home)

    # Datos corporativos de la desarrolladora
    try:
        dev = await extract_developer(home_md, website)
        if dev:
            for k in ("name", "phone", "email", "whatsapp", "profile_url", "description"):
                result[k] = getattr(dev, k)
    except Exception as e:  # noqa: BLE001
        result["extraction_error"] = f"developer: {e}"

    # Fase A: descubrir páginas de desarrollo (heurística + sitemap)
    urls = await discover_development_urls(client, website, home, settings.per_site_max_pages)
    # Complemento asistido por LLM para sitios con rutas atípicas
    if settings.gemini_api_key:
        try:
            llm_urls = await llm_discover_links(
                home_md, sorted(extract_internal_links(home, website)), website
            )
            urls = list(dict.fromkeys(urls + llm_urls))[: settings.per_site_max_pages]
        except Exception:  # noqa: BLE001
            pass
    if not urls:
        result["extraction_status"] = "skipped"
        result["extraction_error"] = (result["extraction_error"] or "") + " sin páginas de desarrollo"
        return result

    # Fase B: extraer detalle por desarrollo (con tope de tiempo por sitio)
    states: set[str] = set()
    for url in urls:
        if time.monotonic() - start > settings.site_budget:
            done = len(result["developments"])
            result["extraction_error"] = (
                (result["extraction_error"] or "")
                + f" budget {settings.site_budget:.0f}s agotado tras {done}/{len(urls)} páginas"
            )
            break
        html = await fetch_html(client, url)
        if not html:
            continue
        md = html_to_markdown(html)
        try:
            d = await extract_development(md, url)
        except Exception:  # noqa: BLE001
            continue
        if not d or not d.name:
            continue
        result["developments"].append(_development_dict(d, url))
        if d.state:
            states.add(d.state)

    result["developments"] = _dedupe_developments(result["developments"])
    result["cities"] = sorted(states)
    result["extraction_status"] = "done" if result["developments"] else "skipped"
    return result
