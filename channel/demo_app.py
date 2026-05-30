"""App FastAPI de demo para probar el webhook de Kapso end-to-end.

Usa un handler de eco (no el agente real). Sirve para validar la conexión del número,
ngrok y la verificación de firma ANTES de enchufar el agente.

Correr::

    uvicorn channel.demo_app:app --reload --port 8000
    # exponer con: ngrok http 8000
    # registrar el webhook con el skill integrate-whatsapp:
    #   node scripts/create.js --phone-number-id <id> \
    #     --url https://<ngrok>/webhooks/kapso \
    #     --events whatsapp.message.received --payload-version v2

La otra sesión (agente PydanticAI) reemplaza ``echo_handler`` por su propio handler.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from .base import InboundMessage
from .config import load_settings
from .kapso import KapsoChannel
from .webhook import build_webhook_router

settings = load_settings()
channel = KapsoChannel(settings)

# Mini "contexto por usuario" SOLO para la demo (cuenta de mensajes). El agente real lo
# reemplaza por su propio historial de conversación. Demuestra qué borra /refresh.
_demo_context: dict[str, int] = {}


async def echo_handler(msg: InboundMessage) -> str:
    n = _demo_context.get(msg.wa_user, 0) + 1
    _demo_context[msg.wa_user] = n
    name = msg.contact_name or "ahí"
    return f"Hola {name} 👋 Recibí: «{msg.text}». (mensaje #{n} de esta conversación)"


async def reset_context(wa_user: str) -> None:
    """Borra el contexto que guardamos de este usuario (aquí, el contador de la demo)."""
    _demo_context.pop(wa_user, None)


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield
    await channel.aclose()


app = FastAPI(title="Viviendin — Canal WhatsApp (demo)", lifespan=lifespan)
app.include_router(build_webhook_router(channel, echo_handler, on_reset=reset_context))


@app.get("/health")
async def health() -> dict[str, object]:
    return {
        "ok": True,
        "channel": channel.name,
        "phone_number_id_set": bool(settings.kapso_phone_number_id),
        "signature_check": settings.signature_verification_enabled,
        "internal_notify_set": bool(settings.internal_notify_number),
    }
