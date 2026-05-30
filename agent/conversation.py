"""Pegamento entre la capa de canal y el Agent.

Mantiene el historial de conversación POR usuario (slots/memoria) en un
``SessionStore`` persistente, y expone ``build_handler`` que produce el
``MessageHandler`` que el canal invoca por cada mensaje entrante (mismo handler
para Kapso y para el simulador).

Carga de contexto: en cada mensaje se recupera TODO el historial previo de ese
``wa_user`` (incluso tras reiniciar el server) y se le pasa al agente. Una palabra
de reinicio ("reset", "reiniciar", …) borra el historial y empieza de cero.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

from channel.base import Channel, InboundMessage, MessageHandler
from channel.config import Settings

from .agent import agent
from .deps import AgentDeps
from .inventory import InventoryRepo
from .leads import LeadRepo

logger = logging.getLogger("viviendin.agent.conversation")


def _build_store():
    """Elige backend de historial. Supabase si hay SUPABASE_DB_URL (o SESSIONS_BACKEND=supabase);
    si no, cae al archivo JSON local (solo para dev sin DB).

    El archivo es efímero (se pierde en redeploys); producción DEBE usar Supabase.
    """
    backend = os.getenv(
        "SESSIONS_BACKEND", "supabase" if os.getenv("SUPABASE_DB_URL") else "json"
    )
    if backend == "supabase":
        from db.sessions_store import SupabaseSessionStore

        logger.info("Historial de conversación: Supabase (agent_sessions)")
        return SupabaseSessionStore()
    from .sessions import SessionStore

    logger.warning("Historial de conversación: archivo JSON (efímero; NO usar en prod)")
    return SessionStore()


# Store de historial compartido. Backend según entorno (Supabase en prod).
_store = _build_store()

# Palabras que reinician la conversación (borran el historial de ese usuario).
RESET_WORDS = {
    "reset", "reiniciar", "/reset", "/reiniciar", "empezar de nuevo",
    "nueva conversacion", "nueva conversación", "borrar conversacion",
    "borrar conversación", "reinicia",
}
RESET_REPLY = "Listo, reinicié nuestra conversación 🧹 ¿Qué tipo de vivienda buscas y en qué zona?"


def build_handler(
    repo: InventoryRepo,
    leads: LeadRepo,
    channel: Channel,
    settings: Settings,
    store: object | None = None,
) -> MessageHandler:
    store = store or _store

    async def handler(msg: InboundMessage) -> Optional[str]:
        # Reinicio explícito: borra el historial y arranca limpio.
        if msg.text.strip().lower() in RESET_WORDS:
            store.reset(msg.wa_user)
            logger.info("Historial reiniciado por %s", msg.wa_user)
            return RESET_REPLY

        deps = AgentDeps(
            repo=repo,
            leads=leads,
            channel=channel,
            settings=settings,
            wa_user=msg.wa_user,
            contact_name=msg.contact_name,
        )
        history = store.get(msg.wa_user)  # carga el contexto previo (sobrevive reinicios)
        try:
            result = await agent.run(msg.text, deps=deps, message_history=history)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Error en el agente para %s: %s", msg.wa_user, exc)
            return "Uy, tuve un problemita técnico 😅 ¿me repites lo último?"
        store.set(msg.wa_user, result.all_messages())  # guarda la conversación actualizada
        return result.output

    return handler


def reset_history(wa_user: Optional[str] = None, store: object | None = None) -> None:
    """Limpia el historial (de un usuario o de todos). Útil entre pruebas."""
    (store or _store).reset(wa_user)
