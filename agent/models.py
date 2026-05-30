"""Modelos Pydantic del agente — espejo del contrato de DATA_MODEL.md.

Estos son los modelos que el agente usa en memoria (cargados desde seed.json /
scraped.json) y para validar el lead de salida. Todo nullable salvo lo mínimo,
igual que el contrato (los 297 sitios son heterogéneos).
"""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


class Model(BaseModel):
    """Modelo / prototipo (unidad vendible). Lo que el comprador filtra."""

    model_config = {"extra": "ignore"}

    id: Optional[str] = None
    name: Optional[str] = None
    housing_type: Optional[str] = None        # casa | departamento | terreno
    bedrooms: Optional[int] = None            # null en terrenos
    bathrooms: Optional[float] = None
    parking: Optional[str] = None             # puede ser rango ("2 a 3")
    area_built_m2: Optional[float] = None
    area_lot_m2: Optional[float] = None       # clave en terrenos
    price: Optional[float] = None             # MXN; null si price_on_request
    currency: str = "MXN"
    availability: Optional[str] = None


class Development(BaseModel):
    """Desarrollo (proyecto). Lo que se le presenta al comprador."""

    model_config = {"extra": "ignore"}

    id: Optional[str] = None
    name: Optional[str] = None
    housing_type: Optional[str] = None        # casa | departamento | terreno | mixto
    status: Optional[str] = None
    state: Optional[str] = None
    municipality: Optional[str] = None
    neighborhood: Optional[str] = None
    price_from: Optional[float] = None
    price_to: Optional[float] = None
    price_on_request: bool = False
    amenities: Optional[List[str]] = None
    delivery_date: Optional[str] = None
    url: Optional[str] = None
    sales_phone: Optional[str] = None
    sales_whatsapp: Optional[str] = None
    sales_email: Optional[str] = None
    models: List[Model] = Field(default_factory=list)


class Developer(BaseModel):
    """Desarrolladora (empresa). Identidad + contacto corporativo (respaldo del handoff)."""

    model_config = {"extra": "ignore"}

    id: Optional[str] = None
    name: Optional[str] = None
    website: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    whatsapp: Optional[str] = None
    description: Optional[str] = None
    cities: Optional[List[str]] = None
    developments: List[Development] = Field(default_factory=list)


class Lead(BaseModel):
    """Comprador calificado — salida del agente (se persiste y se notifica al interno)."""

    wa_user: str
    name: Optional[str] = None
    budget: Optional[float] = None
    search_state: Optional[str] = None
    search_zone: Optional[str] = None
    housing_type: Optional[str] = None
    bedrooms: Optional[int] = None
    credit: Optional[str] = None              # infonavit | bancario | contado | cofinavit | na
    horizon: Optional[str] = None             # ya | 3-6m | explorando
    development_id: Optional[str] = None
    model_id: Optional[str] = None
    visit_date: Optional[str] = None
    visit_time: Optional[str] = None
    status: str = "nuevo"
    notes: Optional[str] = None
