"""Fetch HTTP + limpieza de HTML a markdown (reduce tokens para el LLM)."""
from __future__ import annotations

from urllib.parse import urljoin, urlparse, urlunparse

import httpx
from markdownify import markdownify
from selectolax.parser import HTMLParser

from .config import settings

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; ViviendinBot/0.1; +https://viviendin.mx)",
    "Accept-Language": "es-MX,es;q=0.9",
}


def _url_variants(url: str) -> list[str]:
    """Variantes a probar si el fetch original falla: www↔apex y https→http.

    Muchos de los 297 sitios solo sirven en `www.` o redirigen desde http
    (p.ej. vinte.com.mx: el apex en https rechaza, www.vinte.com.mx responde 200).
    """
    p = urlparse(url)
    host = p.netloc
    alt_host = host[4:] if host.startswith("www.") else "www." + host
    variants = [url, urlunparse(p._replace(netloc=alt_host))]
    if p.scheme == "https":
        variants.append(urlunparse(p._replace(scheme="http")))
        variants.append(urlunparse(p._replace(scheme="http", netloc=alt_host)))
    seen: set[str] = set()
    return [v for v in variants if not (v in seen or seen.add(v))]


async def _get(client: httpx.AsyncClient, url: str, timeout: float) -> str | None:
    # connect corto (host muerto se rinde rápido) + read configurable.
    to = httpx.Timeout(timeout, connect=settings.connect_timeout)
    r = await client.get(url, headers=HEADERS, follow_redirects=True, timeout=to)
    return r.text if r.status_code == 200 and r.text else None


# Errores de conexión = host/esquema equivocado → vale la pena probar variantes (es rápido).
# Un ReadTimeout = host alcanzable pero lento → NO multiplicar el timeout de 20s.
_CONNECT_ERRORS = (httpx.ConnectError, httpx.ConnectTimeout)


async def fetch_html(client: httpx.AsyncClient, url: str) -> str | None:
    """Descarga una URL y devuelve el HTML/XML como texto, o None si falla.

    Fail-fast: solo reintenta variantes www↔apex / https→http si la conexión
    original FALLA (rechazada/DNS) o devuelve no-200 — casos rápidos. Un timeout
    de lectura NO se reintenta (evita pagar 20s × 4 en sitios muertos).

    TODO: fallback a Playwright para sitios JS-rendered (cuando el markdown
    salga casi vacío). Por ahora httpx cubre la mayoría de sitios estáticos.
    """
    variants = _url_variants(url)
    try:
        html = await _get(client, variants[0], settings.request_timeout)
        if html:
            return html
    except _CONNECT_ERRORS:
        pass  # host/esquema equivocado → probar variantes abajo
    except Exception:
        return None  # ReadTimeout u otro: alcanzable pero lento → no multiplicar
    # La original falló por conexión o dio no-200: probar variantes con timeout corto.
    alt_timeout = min(8.0, settings.request_timeout)
    for variant in variants[1:]:
        try:
            html = await _get(client, variant, alt_timeout)
            if html:
                return html
        except Exception:
            continue
    return None


def html_to_markdown(html: str) -> str:
    """Limpia scripts/estilos y convierte el body a markdown recortado."""
    tree = HTMLParser(html)
    for tag in tree.css("script, style, noscript, svg, iframe"):
        tag.decompose()
    body = tree.body
    cleaned = body.html if body else html
    md = markdownify(cleaned or "", strip=["img"])
    md = "\n".join(line.rstrip() for line in md.splitlines() if line.strip())
    return md[: settings.max_markdown_chars]


def extract_internal_links(html: str, base_url: str) -> set[str]:
    """Devuelve los links del mismo dominio (sin fragmentos)."""
    tree = HTMLParser(html)
    base_host = urlparse(base_url).netloc
    links: set[str] = set()
    for a in tree.css("a[href]"):
        href = a.attributes.get("href")
        if not href:
            continue
        u = urljoin(base_url, href).split("#")[0]
        if urlparse(u).netloc == base_host:
            links.add(u)
    return links
