"""Modelos Pydantic del scraper.

Dos grupos:
- Schemas de EXTRACCIÓN: lo que Gemini Flash devuelve (sin ids ni timestamps).
- El output final se arma como dicts con el MISMO shape que data/seed.json
  (anidado developer -> developments -> models, con ids generados).

Ver el contrato completo en DATA_MODEL.md.
"""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


class ExtractedModel(BaseModel):
    """Un prototipo/unidad vendible dentro de un desarrollo."""
    name: Optional[str] = None
    housing_type: Optional[str] = None          # casa | departamento | terreno
    bedrooms: Optional[int] = None              # null en terrenos
    bathrooms: Optional[float] = None           # permite 2.5; null en terrenos
    parking: Optional[str] = None               # puede ser rango "2 a 3"
    area_built_m2: Optional[float] = None        # null en terrenos
    area_lot_m2: Optional[float] = None          # superficie del lote (clave en terrenos)
    price: Optional[float] = None               # MXN; null si price_on_request
    availability: Optional[str] = None          # disponible | pocas_unidades | agotado


class ExtractedDevelopment(BaseModel):
    """Un desarrollo (proyecto) con su ubicación, contacto de ventas y modelos."""
    name: Optional[str] = None
    housing_type: Optional[str] = None          # casa | departamento | terreno | mixto
    status: Optional[str] = None                # preventa | construccion | entrega_inmediata | terminado
    state: Optional[str] = None
    municipality: Optional[str] = None
    neighborhood: Optional[str] = None
    amenities: List[str] = Field(default_factory=list)
    delivery_date: Optional[str] = None
    price_on_request: Optional[bool] = None     # true si oculta precios
    sales_phone: Optional[str] = None
    sales_whatsapp: Optional[str] = None
    sales_email: Optional[str] = None
    models: List[ExtractedModel] = Field(default_factory=list)


class ExtractedDeveloper(BaseModel):
    """Datos corporativos de la desarrolladora (extraídos de la home/contacto)."""
    name: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    whatsapp: Optional[str] = None
    profile_url: Optional[str] = None
    description: Optional[str] = None


class DiscoveredLinks(BaseModel):
    """Fase A asistida por LLM: URLs que SON páginas de un desarrollo concreto
    (no la home, no listados, no blog/contacto)."""
    development_urls: List[str] = Field(default_factory=list)
