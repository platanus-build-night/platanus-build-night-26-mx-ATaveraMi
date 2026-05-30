"""El Agent PydanticAI de Viviendin: system prompt + tools.

El LLM (Gemini) maneja la conversación y decide cuándo buscar inventario y cuándo
capturar el lead. Las tools son funciones tipadas que leen el ``InventoryRepo`` y
escriben el lead + disparan la notificación interna del broker.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

from dotenv import load_dotenv
from pydantic_ai import Agent, RunContext
from pydantic_ai.models.google import GoogleModel
from pydantic_ai.providers.google import GoogleProvider

from channel.notifications import LeadNotification, send_lead_notification

from .deps import AgentDeps
from .models import Lead

load_dotenv()
logger = logging.getLogger("viviendin.agent")

SYSTEM_PROMPT = """\
Eres el asistente de WhatsApp de **Viviendin**, un broker de vivienda nueva en México.
Tu trabajo: entender qué busca el comprador, mostrarle desarrollos REALES que existen en
nuestro inventario, y capturar un lead calificado con una fecha tentativa de visita. NO
cierras la cita tú; un asesor humano concreta. Hablas en español de México, cálido y breve
(es WhatsApp: mensajes cortos, máximo ~4 líneas, con uno o dos emojis a lo mucho).

## Cómo conversas
1. Saluda y pregunta qué busca. Ve llenando datos POCO A POCO (no interrogues con una lista):
   zona/ciudad, presupuesto, tipo (casa/departamento/terreno), recámaras, crédito
   (infonavit/bancario/contado/cofinavit) y horizonte de compra (ya / 3-6 meses / explorando).
2. Cuando tengas al menos zona + (presupuesto o tipo), usa la tool `search_inventory`.
3. Presenta MÁXIMO 2-3 opciones, cada una en una línea: nombre · zona · precio desde · 1 highlight.
4. Deja que elija una. Pídele una fecha y hora tentativa de visita.
5. Confirma su nombre (si WhatsApp ya lo trae, solo verifícalo).
6. Llama a `create_lead` con todo lo que reuniste.
7. Cierra: "Listo, un asesor de Viviendin te contactará para confirmar la visita 🙌". No sigas
   preguntando ni busques más después de capturar el lead.

## Reglas firmes
- SOLO ofreces desarrollos que devuelve `search_inventory`. NUNCA inventes nombres, precios,
  zonas ni desarrollos. Si no hay datos de un campo, no lo afirmes.
- Si un desarrollo trae `price_on_request: true` o sin precio, ofrécelo igual y di "precio a
  consultar" — NO lo descartes por presupuesto.
- Si busca TERRENO, no preguntes recámaras; enfócate en superficie del lote y presupuesto.
- Si `search_inventory` regresa vacío, amplía criterios (sube presupuesto, zona vecina) o
  pídele otra zona. No te inventes opciones.
- No prometas precios finales ni disponibilidad garantizada; el asesor confirma.
- Para `create_lead` necesitas como mínimo: el desarrollo elegido (`development_id`) y una
  fecha/hora tentativa. El nombre ya viene del contacto de WhatsApp si no lo dan.
"""


def _build_agent() -> Agent[AgentDeps, str]:
    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError("Falta GEMINI_API_KEY (ponla en .env). Ver .env.example")
    model = GoogleModel(
        os.getenv("AGENT_MODEL", "gemini-2.5-flash"),
        provider=GoogleProvider(api_key=api_key),
    )
    return Agent(model, deps_type=AgentDeps, instructions=SYSTEM_PROMPT)


agent = _build_agent()


@agent.tool
async def search_inventory(
    ctx: RunContext[AgentDeps],
    state: Optional[str] = None,
    municipality: Optional[str] = None,
    zone: Optional[str] = None,
    housing_type: Optional[str] = None,
    bedrooms_min: Optional[int] = None,
    budget_max: Optional[float] = None,
) -> list[dict]:
    """Busca desarrollos que cumplen los criterios del comprador.

    Filtra a nivel modelo y devuelve hasta 3 desarrollos con al menos un modelo que
    cumple (o, si el sitio no listó modelos, igual lo ofrece por zona/tipo). Degrada
    con gracia: precios/recámaras desconocidos NO descartan.

    Args:
        state: estado o ciudad (p.ej. "Querétaro", "Ciudad de México").
        municipality: municipio o alcaldía.
        zone: colonia o zona específica.
        housing_type: "casa" | "departamento" | "terreno".
        bedrooms_min: mínimo de recámaras (ignorar en terrenos).
        budget_max: presupuesto máximo en MXN.
    """
    return ctx.deps.repo.search(
        state=state,
        municipality=municipality,
        zone=zone,
        housing_type=housing_type,
        bedrooms_min=bedrooms_min,
        budget_max=budget_max,
    )


@agent.tool
async def get_development_detail(ctx: RunContext[AgentDeps], development_id: str) -> Optional[dict]:
    """Devuelve el detalle completo de un desarrollo (modelos, amenidades, contacto)."""
    return ctx.deps.repo.get_development(development_id)


@agent.tool
async def create_lead(
    ctx: RunContext[AgentDeps],
    development_id: str,
    visit_date: Optional[str] = None,
    visit_time: Optional[str] = None,
    name: Optional[str] = None,
    budget: Optional[float] = None,
    search_state: Optional[str] = None,
    search_zone: Optional[str] = None,
    housing_type: Optional[str] = None,
    bedrooms: Optional[int] = None,
    credit: Optional[str] = None,
    horizon: Optional[str] = None,
    model_id: Optional[str] = None,
    notes: Optional[str] = None,
) -> dict:
    """Registra el lead calificado y notifica al equipo interno (handoff de broker).

    Llamar solo cuando el comprador eligió un desarrollo y propuso una visita tentativa.
    `development_id` viene de `search_inventory`. El nombre cae al contacto de WhatsApp.
    """
    deps = ctx.deps
    buyer_name = name or deps.contact_name

    lead = Lead(
        wa_user=deps.wa_user,
        name=buyer_name,
        budget=budget,
        search_state=search_state,
        search_zone=search_zone,
        housing_type=housing_type,
        bedrooms=bedrooms,
        credit=credit,
        horizon=horizon,
        development_id=development_id,
        model_id=model_id,
        visit_date=visit_date,
        visit_time=visit_time,
        status="nuevo",
        notes=notes,
    )
    lead_id = deps.leads.save(lead)

    detail = deps.repo.get_development(development_id) or {}
    developer = deps.repo.developer_of(development_id)
    notif = LeadNotification(
        buyer_name=buyer_name,
        wa_user=deps.wa_user,
        housing_type=housing_type,
        zone=search_zone or detail.get("zone"),
        bedrooms=bedrooms,
        budget=budget,
        credit=credit,
        horizon=horizon,
        development_name=detail.get("name"),
        developer_name=developer.name if developer else None,
        visit_date=visit_date,
        visit_time=visit_time,
        developer_contact=deps.repo.sales_contact(development_id),
    )
    try:
        result = await send_lead_notification(deps.channel, notif, deps.settings)
        notified = result.ok
    except Exception as exc:  # noqa: BLE001
        logger.exception("Falló la notificación interna del lead: %s", exc)
        notified = False

    return {
        "lead_id": lead_id,
        "status": "notificado" if notified else "nuevo",
        "internal_notified": notified,
        "development": detail.get("name"),
    }
