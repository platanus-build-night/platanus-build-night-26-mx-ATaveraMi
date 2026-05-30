"""App FastAPI de producción: webhook de Kapso → Agent → respuesta + notificación.

    uvicorn agent.app:app --reload --port 8000
    # exponer con ngrok y registrar el webhook (ver channel/demo_app.py / integrate-whatsapp)

Reemplaza el ``echo_handler`` de channel/demo_app.py por el handler real del agente.
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI

from channel.config import load_settings
from channel.kapso import KapsoChannel
from channel.webhook import build_webhook_router

from .conversation import build_handler
from .inventory import InventoryRepo
from .leads import LeadRepo

settings = load_settings()
channel = KapsoChannel(settings)
repo = InventoryRepo.from_paths(
    [p.strip() for p in os.getenv("INVENTORY_PATHS", "data/seed.json").split(",")]
)
leads = LeadRepo(os.getenv("LEADS_PATH", "data/leads.json"))
handler = build_handler(repo, leads, channel, settings)


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield
    await channel.aclose()


app = FastAPI(title="Viviendin — Agente WhatsApp", lifespan=lifespan)
app.include_router(build_webhook_router(channel, handler))


@app.get("/health")
async def health() -> dict[str, object]:
    return {
        "ok": True,
        "channel": channel.name,
        "developers": len(repo.developers),
        "developments": sum(len(d.developments) for d in repo.developers),
        "phone_number_id_set": bool(settings.kapso_phone_number_id),
        "internal_notify_set": bool(settings.internal_notify_number),
    }
