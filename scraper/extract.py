"""Extracción con Gemini 2.5 Flash como extractor puro (structured output).

El LLM NO maneja control flow ni tools: recibe el markdown de una página y
devuelve un objeto Pydantic validado. Todo lo demás es código.
"""
from __future__ import annotations

import json

from google import genai
from google.genai import types

from .config import settings
from .models import DiscoveredLinks, ExtractedDeveloper, ExtractedDevelopment

_client: genai.Client | None = None


def client() -> genai.Client:
    global _client
    if _client is None:
        _client = genai.Client(api_key=settings.gemini_api_key)
    return _client


_DEV_PROMPT = """Eres un extractor de datos inmobiliarios de México. A partir del contenido \
de la página de UN desarrollo de vivienda nueva, extrae la información estructurada.

Reglas estrictas:
- NO inventes. Si un dato no aparece, déjalo en null.
- Precios en pesos mexicanos (MXN), SOLO el número (sin "$", sin comas, sin "MXN").
- Si los precios NO están visibles (ej. "solicita información", "precio a consultar"),
  pon price=null en los modelos y price_on_request=true.
- Terrenos/lotes: housing_type="terreno"; bedrooms/bathrooms/area_built_m2=null;
  usa area_lot_m2 (superficie del lote).
- housing_type del desarrollo: casa | departamento | terreno | mixto.
- status: preventa | construccion | entrega_inmediata | terminado.
- amenities: lista corta de strings (ej. "Alberca", "Gym", "Seguridad 24h").
- sales_phone / sales_whatsapp / sales_email: contacto de ventas de ESTE desarrollo.
- models: cada prototipo/tipo de unidad con sus recámaras, baños, m² y precio.

URL: {url}
Contenido (markdown):
---
{content}
---"""

_DEVELOPER_PROMPT = """Eres un extractor de datos. Del contenido de la home de una \
DESARROLLADORA inmobiliaria mexicana, extrae sus datos corporativos.
- NO inventes; dato ausente = null.
- name: nombre comercial de la desarrolladora.
- phone/whatsapp/email: contacto corporativo si aparece.
- description: una frase de qué hacen.

URL: {url}
Contenido (markdown):
---
{content}
---"""


async def _generate(prompt: str, schema):
    resp = await client().aio.models.generate_content(
        model=settings.gemini_model,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=schema,
            temperature=0,
        ),
    )
    if getattr(resp, "parsed", None) is not None:
        return resp.parsed
    # Fallback: parsear el texto crudo
    try:
        return schema.model_validate(json.loads(resp.text))
    except Exception:
        return None


async def extract_development(content: str, url: str) -> ExtractedDevelopment | None:
    return await _generate(_DEV_PROMPT.format(content=content, url=url), ExtractedDevelopment)


async def extract_developer(content: str, url: str) -> ExtractedDeveloper | None:
    return await _generate(_DEVELOPER_PROMPT.format(content=content, url=url), ExtractedDeveloper)


_DISCOVER_PROMPT = """Te doy la home (markdown) de una desarrolladora inmobiliaria \
mexicana y una lista de URLs internas. Devuelve SOLO las URLs que son la página de UN \
desarrollo/proyecto/comunidad/fraccionamiento concreto (la que tendría su nombre, ubicación,
modelos y/o precios).
EXCLUYE: home, listados generales (ej. "/desarrollos" sin slug), blog, noticias, contacto,
nosotros, aviso de privacidad, bolsa de trabajo, términos.

Home (markdown):
---
{home}
---
URLs internas candidatas:
{links}"""


async def llm_discover_links(home_md: str, links: list[str], base_url: str) -> list[str]:
    """Fase A asistida por LLM: identifica páginas de desarrollo entre los links.

    Complementa la heurística de slug para sitios con rutas atípicas
    (ej. Vinte usa /comunidades/{estado}/{slug} sin keywords obvias).
    """
    from urllib.parse import urlparse

    if not links:
        return []
    listing = "\n".join(links[:150])
    res = await _generate(
        _DISCOVER_PROMPT.format(home=home_md[:8000], links=listing), DiscoveredLinks
    )
    if not res:
        return []
    host = urlparse(base_url).netloc
    return [u for u in res.development_urls if urlparse(u).netloc == host]
