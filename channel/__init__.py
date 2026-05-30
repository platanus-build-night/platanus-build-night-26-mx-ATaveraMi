"""Capa de canal de WhatsApp para Viviendin.

Abstracción de canal con dos adaptadores intercambiables:

- ``KapsoChannel``     — WhatsApp Cloud API vía Kapso (webhook entrante + REST saliente).
- ``SimulatorChannel`` — REPL local para desarrollar el agente sin depender del número.

El agente (PydanticAI) se enchufa pasando un ``MessageHandler`` async que recibe un
``InboundMessage`` y devuelve el texto de respuesta (o ``None`` si ya respondió por su cuenta).

Ejemplo mínimo (FastAPI + Kapso)::

    from fastapi import FastAPI
    from channel import KapsoChannel, build_webhook_router, load_settings

    settings = load_settings()
    channel = KapsoChannel(settings)

    async def handler(msg):
        return f"Recibí: {msg.text}"

    app = FastAPI()
    app.include_router(build_webhook_router(channel, handler))

Ejemplo (simulador local, sin WhatsApp)::

    import asyncio
    from channel import SimulatorChannel

    async def handler(msg):
        return f"Recibí: {msg.text}"

    asyncio.run(SimulatorChannel().run_repl(handler))
"""

from .base import Channel, InboundMessage, MessageHandler, OutboundResult
from .config import Settings, load_settings
from .kapso import KapsoChannel, KapsoClient, WindowExpiredError
from .notifications import (
    LeadNotification,
    format_lead_notification,
    send_lead_notification,
)
from .simulator import SimulatorChannel
from .webhook import build_webhook_router

__all__ = [
    "Channel",
    "InboundMessage",
    "MessageHandler",
    "OutboundResult",
    "Settings",
    "load_settings",
    "KapsoChannel",
    "KapsoClient",
    "WindowExpiredError",
    "SimulatorChannel",
    "build_webhook_router",
    "LeadNotification",
    "format_lead_notification",
    "send_lead_notification",
]
