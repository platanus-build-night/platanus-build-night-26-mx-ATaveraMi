"""El Agent PydanticAI de Viviendin: system prompt + tools.

El LLM (Gemini) maneja la conversación y decide cuándo buscar inventario y cuándo
capturar el lead. Las tools son funciones tipadas que leen el ``InventoryRepo`` y
escriben el lead + disparan la notificación interna del broker.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional

import httpx
from dotenv import load_dotenv
from pydantic_ai import Agent, RunContext
from pydantic_ai.models.google import GoogleModel
from pydantic_ai.providers.google import GoogleProvider

from channel.notifications import LeadNotification, send_lead_email, send_lead_notification
from scraper.fetch import fetch_html, html_to_markdown

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
   Devuelve `{total_matches, should_narrow, results, alternativas?}`.
   - Si `should_narrow` es true (hay muchas opciones que podrían encajar): **NO presentes
     todavía**. Haz 1-2 preguntas para acotar (presupuesto más fino, recámaras, alguna
     amenidad clave, sub-zona/colonia) y vuelve a buscar. Sigue perfilando.
   - Si `total_matches` es 0: viene el campo `alternativas` con lo que SÍ existe (ya
     verificado contra el inventario). NO improvises otras zonas ni vuelvas a buscar a ciegas.
     Ofrece SOLO lo que traiga `alternativas`, en este orden:
       · `mismo_estado_otros_tipos` → "En {estado} manejo {tipos}, no {lo que pidió}." Es lo
         más cercano a lo que quiere; ofrécelo directo.
       · `mismo_tipo_otros_estados` → trae `cruza_estado: true`. Es OTRO ESTADO, NO una zona
         cercana. NO lo propongas como recomendación: PREGÚNTA si lo consideraría, dejando
         claro que es otro estado (p.ej. "Casas solo tengo en Querétaro — ¿lo considerarías,
         aunque es otro estado?"). NUNCA digas que está "cerca".
     Si `alternativas` viene vacío, di honesto que no tienes nada para eso y pregunta si
     quiere cambiar de zona o tipo. NUNCA inventes opciones.
   - Si es manejable: continúa al paso 3.
3. Presenta **MÁXIMO 5** opciones (idealmente 2-3), cada una en una línea: nombre · zona ·
   precio desde · 1 highlight. **Tú eliges y ordenas el mejor fit** según todo lo que dijo el
   comprador (precio vs su presupuesto, recámaras, disponibilidad) — no te limites al orden
   en que vienen.
4. Deja que elija un DESARROLLO. Luego muéstrale sus MODELOS (recámaras/precio, con
   `get_development_detail`) y pregúntale cuál le interesa — guarda su `model_id`.
5. Para agendar pide SIEMPRE, en este orden, lo que falte:
   a) el **modelo** de interés (`model_id`),
   b) una **fecha y hora tentativa** (no tiene que ser exacta),
   c) el **NOMBRE** del comprador — es INDISPENSABLE; pídeselo explícitamente ("¿a nombre de
      quién agendo la visita?"). NO lo des por hecho ni uses el de WhatsApp sin preguntar.
   Con eso llama a `create_lead`. Si te falta el nombre, NO agendes: pídelo primero.
6. Cierra: "Listo, {nombre}, un asesor de Viviendin te contactará para confirmar la visita 🙌".
   No sigas preguntando ni busques más después de capturar el lead.

## Reglas firmes
- SOLO ofreces desarrollos que devuelve `search_inventory`. NUNCA inventes nombres, precios,
  zonas ni desarrollos. Si no hay datos de un campo, no lo afirmes.
- **Ubicación honesta:** usa la `zone` REAL de cada resultado; NUNCA digas que algo está "en"
  o "cerca de" una zona que el comprador pidió si su `zone` no la menciona. Cada resultado trae
  `location_match`: si vale `"ampliado"` significa que NO hay nada en la zona exacta que pidió y
  ampliaste la búsqueda — DILO claro (p.ej. "No tengo nada en Zibatá justo, pero en Querétaro
  tengo estas opciones…"). Si vale `"zona_exacta"`, sí está en esa zona.
- **Búsqueda por desarrolladora/marca:** si el comprador pregunta "¿tienes algo de X?" o
  menciona una marca (Atlas, MiRA, Vinte…), usa `find_by_developer` y muéstrale sus desarrollos.
  Si menciona recámaras o zona, profundiza/filtra (o usa `get_development_detail` para modelos).
  Si no la encuentras, dilo y ofrece buscar por zona/tipo/presupuesto.
- **NUNCA inventes zonas, alcaldías, estados, tipos ni coberturas.** Para preguntas de qué hay,
  apóyate SIEMPRE en una tool y responde solo con lo que regrese:
  · `inventory_overview` → panorama amplio: en qué ESTADOS/ciudades hay, qué TIPOS, desde cuánto.
    Úsala para "¿en qué estados tienen?", "¿qué manejan?", "¿desde cuánto?" o para orientarte.
  · `list_areas(housing_type, state)` → ZONAS concretas disponibles de un tipo (o estados que sí
    lo tienen si el pedido salió vacío).
- **Alternativas CERCANAS (con sentido geográfico):** si no hay en la zona/colonia exacta pedida
  (resultado vacío o `location_match: "ampliado"`), propón zonas VECINAS que sí tengamos dentro de
  la MISMA ciudad/área metropolitana. Usa `list_areas` para ver qué zonas hay y tu conocimiento de
  qué colonias/municipios son vecinos. Fraséalo honesto: "En la Condesa no tengo, pero aquí cerca
  en la Roma Norte sí tengo esta opción…".
  Llama "cercanas" SOLO a las que de verdad lo son (colonias/municipios contiguos). Las demás del
  mismo estado/ciudad que estén lejos NO las llames cercanas: ofrécelas aparte como "otras opciones
  en [ciudad]". Mejor 1-2 realmente cercanas que 5 dizque cercanas.
- **NUNCA propongas otro ESTADO como 'cercano' ni como recomendación.** Cambiar de estado es un
  salto grande, NO una zona vecina (jamás digas que Querétaro está "cerca" de CDMX). Si en el
  estado/ciudad pedido no hay nada, dilo claro; solo si el comprador se ve abierto, PREGÚNTALE si
  consideraría otro estado, dejando explícito que es otro estado — nunca disfrazado de "cercano".
- **No te contradigas:** las zonas/opciones que listaste vienen de una tool y son las únicas
  reales. No digas después que "no hay" en una zona que tú mismo listaste como disponible, ni
  al revés. Ante la duda, vuelve a llamar a la tool en vez de adivinar.
- **Tipo honesto y decisivo:** si el comprador YA dijo el tipo (p.ej. casa), NO se lo vuelvas a
  preguntar ("¿buscas casa o depa?" cuando ya dijo casa = error). Búscalo con ese `housing_type`.
  Si confirmas que de ESE tipo no hay en la zona, AFÍRMALO de frente y propón la alternativa como
  afirmación, NO como pregunta abierta: "Por la Roma no manejo casas, ahí mi inventario es de
  *departamentos* — ¿te sirve que te muestre departamentos, o prefieres casa en otra zona?".
  Nunca insinúes que tienes casas ahí si no las tienes.
- **Zonas ambiguas entre ciudades:** algunos nombres de colonia existen en varios estados
  (p.ej. "Roma" está en CDMX y en Monterrey). Si `search_inventory` regresa `states_in_results`
  con MÁS de un estado, NO mezcles ni te confundas: para zonas icónicas asume la obvia (la Roma,
  la Condesa, Polanco → CDMX) o pregunta cortito "¿la Roma de CDMX o de Monterrey?". Cuando ya
  sepas la ciudad, vuelve a buscar pasando `state` para anclar.
- **Ubicaciones vagas o direccionales** ("el centro", "el sur", "la zona norte", "por allá"):
  NO las pases literal (no existe una colonia "sur"). Usa tu conocimiento de la geografía de
  México para traducirlas a alcaldías/municipios/colonias REALES, y crúzalo con `list_areas`
  para ver cuáles de esas SÍ tenemos. Puedes pasar VARIAS separadas por coma en `zone` o
  `municipality` (p.ej. `zone="Tlalpan, Coyoacán, Xochimilco"` para "el sur de CDMX"). Si la
  referencia es ambigua o no la ubicas, pregúntale qué alcaldía/zona en particular.
- Si un desarrollo trae `price_on_request: true` o sin precio, ofrécelo igual y di "precio a
  consultar" — NO lo descartes por presupuesto.
- Si busca TERRENO, no preguntes recámaras; enfócate en superficie del lote y presupuesto.
- Si `search_inventory` regresa vacío, amplía criterios (sube presupuesto, zona vecina) o
  pídele otra zona. No te inventes opciones.
- No prometas precios finales ni disponibilidad garantizada; el asesor confirma.
- **Links y "más info":** NUNCA inventes ni adivines URLs. Si el comprador pide la página web,
  da el `url`/`developer_website` REAL (de `get_development_detail`). Si pide más detalles que no
  tienes (amenidades completas, descripción, fechas, más modelos), usa `get_more_info` —entra al
  sitio en vivo— y responde con su `page_excerpt`. Si la página no carga (`page_status` ≠ "ok"),
  comparte el link y di que un asesor le dará el detalle.
- Para `create_lead` necesitas: **nombre del comprador (OBLIGATORIO)**, `development_id`,
  `model_id` del modelo de interés, y fecha/hora tentativa. Sin nombre NO se agenda — pídelo.
- Una fecha tentativa NO tiene que ser exacta: "el sábado", "este fin", "mañana en la tarde"
  son SUFICIENTES (guárdalas tal cual en `visit_date`/`visit_time`). NUNCA insistas en una
  fecha de calendario exacta — eso lo confirma el asesor después.
- **Respeta los datos que YA te dieron.** Si el comprador ya dijo el tipo, la zona o el
  presupuesto, NO se los vuelvas a preguntar — avanza con lo que tienes. Repreguntar algo que ya
  dijo es molesto y se siente a que no le pusiste atención.
- No pidas el mismo dato dos veces. Una vez que tienes el mínimo, captura el lead; no busques
  más precisión.
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
) -> dict:
    """Busca desarrollos que cumplen los criterios del comprador.

    Filtra a nivel modelo (degrada con gracia: precios/recámaras desconocidos NO descartan).
    Devuelve `{total_matches, should_narrow, results}`:
    - `total_matches`: cuántos cuadran en la mejor capa de ubicación.
    - `should_narrow`: True si hay muchos (>5) → conviene seguir perfilando antes de mostrar.
    - `results`: hasta 7 candidatos pre-rankeados por fit; presenta MÁXIMO 5.

    Args:
        state: estado o ciudad (p.ej. "Querétaro", "Ciudad de México").
        municipality: municipio o alcaldía. Acepta VARIOS separados por coma.
        zone: colonia o zona específica. Acepta VARIAS separadas por coma — úsalo para
            resolver referencias direccionales (p.ej. "el sur de CDMX" → "Tlalpan, Coyoacán").
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
async def find_by_developer(ctx: RunContext[AgentDeps], name: str) -> dict:
    """Busca desarrollos por nombre de DESARROLLADORA/marca (p.ej. "Atlas", "MiRA", "Vinte").

    Úsala cuando el comprador pregunte "¿tienes algo de X?" o mencione una marca. Devuelve
    `{found, developers:[{developer, developments:[...]}]}`. Si `found` es false, NO inventes:
    dilo y ofrece buscar por zona/tipo/presupuesto. Tras mostrar los desarrollos, si menciona
    recámaras/zona, profundiza (filtra o usa `get_development_detail` para ver modelos).
    """
    return ctx.deps.repo.find_by_developer(name)


@agent.tool
async def inventory_overview(ctx: RunContext[AgentDeps]) -> dict:
    """Panorama del inventario para ORIENTARTE y decidir con datos (no para presentar al
    comprador tal cual). Devuelve en qué ESTADOS hay propiedades (con cuántas y qué tipos),
    los tipos disponibles y el rango de precios global.

    Úsala cuando el comprador pregunte cosas amplias ("¿en qué estados/ciudades tienen?",
    "¿qué tipos manejan?", "¿desde cuánto?") o cuando no sepas por dónde guiar. NO inventes
    coberturas: responde solo con lo que regrese.
    """
    return ctx.deps.repo.overview()


@agent.tool
async def list_areas(
    ctx: RunContext[AgentDeps],
    housing_type: Optional[str] = None,
    state: Optional[str] = None,
) -> dict:
    """Lista las zonas y estados REALES donde hay inventario de un tipo.

    Úsala cuando el comprador pregunte "¿qué zonas/estados/opciones tienes?", o cuando una
    búsqueda salga vacía, para ofrecer alternativas verdaderas. Devuelve
    `{total_here, zones_available_here, states_with_this_type}`. NO inventes zonas: responde
    solo con lo que esto regrese.

    Args:
        housing_type: "casa" | "departamento" | "terreno" (opcional, para filtrar).
        state: estado/ciudad para acotar (opcional). Si sale vacío, usa `states_with_this_type`.
    """
    return ctx.deps.repo.list_areas(housing_type=housing_type, state=state)


@agent.tool
async def get_development_detail(ctx: RunContext[AgentDeps], development_id: str) -> Optional[dict]:
    """Detalle de un desarrollo desde la DB (modelos, amenidades, contacto y la URL/website REAL).

    Úsala si el comprador pide el **link/página web** del desarrollo: regresa `url` y
    `developer_website` reales — NUNCA inventes una URL.
    """
    return ctx.deps.repo.get_development(development_id)


@agent.tool
async def get_more_info(ctx: RunContext[AgentDeps], development_id: str) -> dict:
    """Trae MÁS información entrando EN VIVO a la página web del desarrollo.

    Úsala cuando el comprador pida detalles que no están en la DB (amenidades completas,
    descripción, modelos/precios adicionales, fechas) o pida "más info". Regresa el link real,
    lo de la DB y un extracto del sitio (`page_excerpt`) para que respondas con datos reales.
    Si la página no carga, comparte el link y di que un asesor dará el detalle. NO inventes.
    """
    detail = ctx.deps.repo.get_development(development_id)
    if not detail:
        return {"error": "no encontré ese desarrollo en el inventario"}

    dr = ctx.deps.repo.developer_of(development_id)
    url = detail.get("url") or (dr.website if dr else None)

    page_excerpt: Optional[str] = None
    page_status = "sin_url"
    if url:
        try:
            async with httpx.AsyncClient() as client:
                html = await fetch_html(client, url)
            if html:
                page_excerpt = html_to_markdown(html)[:6000]
                page_status = "ok"
            else:
                page_status = "no_accesible"
        except Exception as exc:  # noqa: BLE001
            logger.warning("No se pudo leer la página de %s: %s", url, exc)
            page_status = "error"

    return {
        "development_id": development_id,
        "name": detail.get("name"),
        "url": url,
        "developer_website": detail.get("developer_website"),
        "db_info": {
            k: detail.get(k)
            for k in ("zone", "price_desde", "status", "amenities", "delivery_date",
                      "sales_contact", "models")
        },
        "page_status": page_status,
        "page_excerpt": page_excerpt,
    }


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

    Llamar SOLO cuando ya tienes: (1) el NOMBRE del comprador (obligatorio — pídelo, no lo
    adivines), (2) el `development_id` elegido, (3) el `model_id` del modelo de interés, y
    (4) una fecha/hora tentativa. Si falta el nombre, esta tool te lo regresa para que lo pidas.

    Args:
        name: nombre del comprador. OBLIGATORIO — pídelo explícitamente antes de agendar.
        model_id: id del modelo/prototipo de interés (de `get_development_detail`/`search_inventory`).
    """
    deps = ctx.deps

    # El nombre es indispensable: no se cae al contacto de WhatsApp en silencio.
    if not (name and name.strip()):
        return {
            "status": "falta_nombre",
            "message": "Antes de agendar necesito el NOMBRE del comprador. Pídeselo "
                       f"(puedes sugerir el de WhatsApp: {deps.contact_name or 's/n'}).",
        }

    detail = deps.repo.get_development(development_id) or {}
    developer = deps.repo.developer_of(development_id)

    # Si el desarrollo TIENE modelos, hay que saber cuál le interesa. Si no tiene
    # (p.ej. preventa de lujo "a consultar"), se agenda sin modelo.
    models = detail.get("models", [])
    if models and not model_id:
        return {
            "status": "falta_modelo",
            "message": "Este desarrollo tiene modelos. Muéstraselos (recámaras/precio) y "
                       "pregúntale cuál le interesa; pásame su model_id antes de agendar.",
            "modelos": [
                {"model_id": m.get("model_id"), "name": m.get("name"),
                 "bedrooms": m.get("bedrooms"), "price": m.get("price")}
                for m in models
            ],
        }

    # Resolver el nombre del modelo de interés (para el registro y el aviso).
    model_name = None
    if model_id:
        model_name = next(
            (m.get("name") for m in detail.get("models", []) if m.get("model_id") == model_id),
            None,
        )

    lead = Lead(
        wa_user=deps.wa_user,
        name=name.strip(),
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

    notif = LeadNotification(
        buyer_name=name.strip(),
        wa_user=deps.wa_user,
        housing_type=housing_type,
        zone=search_zone or detail.get("zone"),
        bedrooms=bedrooms,
        budget=budget,
        credit=credit,
        horizon=horizon,
        development_name=detail.get("name"),
        developer_name=developer.name if developer else None,
        model_name=model_name,
        visit_date=visit_date,
        visit_time=visit_time,
        developer_contact=deps.repo.sales_contact(development_id),
    )
    # Correo del lead (documentación + a quién escribir + mensaje redactado). Blocking → hilo.
    email_sent = await asyncio.to_thread(send_lead_email, notif, deps.settings)

    # Notificación interna por WhatsApp (opcional: solo si hay número interno configurado).
    notified = False
    if deps.settings.internal_notify_number:
        try:
            result = await send_lead_notification(deps.channel, notif, deps.settings)
            notified = result.ok
        except Exception as exc:  # noqa: BLE001
            logger.exception("Falló la notificación interna del lead: %s", exc)

    return {
        "lead_id": lead_id,
        "status": "notificado" if (email_sent or notified) else "nuevo",
        "email_sent": email_sent,
        "internal_notified": notified,
        "development": detail.get("name"),
    }
