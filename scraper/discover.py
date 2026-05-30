"""Descubrimiento de páginas de desarrollo (fase A) — SIN LLM, robusto.

Estrategia: sitemap.xml (lo más confiable) + links de la home filtrados por
heurística de slug. Para `obtener TODOS los desarrollos`, barrer estas fuentes
es más exhaustivo que un agente autónomo.
"""
from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse

import httpx

from .fetch import extract_internal_links, fetch_html

LOC_RE = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>", re.IGNORECASE)

# Palabras en el path que sugieren una página de desarrollo/proyecto.
DEV_KEYWORDS = (
    "desarrollo", "desarrollos", "proyecto", "proyectos", "propiedad", "propiedades",
    "residencial", "torre", "torres", "lote", "lotes", "modelo", "modelos",
    "vivienda", "preventa", "departamento", "departamentos", "depto", "casa", "casas",
    "comunidad", "comunidades", "fraccionamiento", "condominio",
)

# Paths a ignorar (no son desarrollos).
NEGATIVE = (
    "blog", "noticia", "aviso", "privacidad", "contacto", "nosotros", "bolsa",
    "trabajo", "terminos", "cookies", "faq", "login", "registro",
)


def looks_like_development(url: str) -> bool:
    path = urlparse(url).path.lower()
    segs = [s for s in path.split("/") if s]
    if not segs:
        return False
    # Excluir variantes en inglés (i18n): /en/... o slug "en-..."
    if "en" in segs or segs[-1].startswith("en-"):
        return False
    if any(n in path for n in NEGATIVE):
        return False
    return any(k in path for k in DEV_KEYWORDS)


async def _from_sitemap(client: httpx.AsyncClient, base_url: str) -> set[str]:
    candidates: set[str] = set()
    seen: set[str] = set()
    for path in ("/sitemap.xml", "/sitemap_index.xml", "/sitemap-index.xml"):
        xml = await fetch_html(client, urljoin(base_url, path))
        if not xml:
            continue
        for loc in LOC_RE.findall(xml):
            if loc.endswith(".xml") and loc not in seen:
                seen.add(loc)
                child = await fetch_html(client, loc)
                if child:
                    candidates.update(LOC_RE.findall(child))
            else:
                candidates.add(loc)
    return {u for u in candidates if looks_like_development(u)}


async def discover_development_urls(
    client: httpx.AsyncClient, base_url: str, home_html: str | None, max_pages: int
) -> list[str]:
    urls: set[str] = set()
    try:
        urls |= await _from_sitemap(client, base_url)
    except Exception:
        pass
    if home_html:
        urls |= {u for u in extract_internal_links(home_html, base_url) if looks_like_development(u)}
    # Quitar la home misma y normalizar trailing slash
    base = base_url.rstrip("/")
    urls = {u.rstrip("/") for u in urls if u.rstrip("/") != base}
    return sorted(urls)[:max_pages]
