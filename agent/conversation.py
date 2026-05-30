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
from typing import Optional

from channel.base import Channel, InboundMessage, MessageHandler
from channel.config import Settings

from .agent import agent
from .deps import AgentDeps
from .inventory import InventoryRepo
from .leads import LeadRepo
from .sessions import SessionStore

logger = logging.getLogger("viviendin.agent.conversation")

# Store persistente compartido (data/sessions.json). En producción → Redis/Supabase.
_store = SessionStore()

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
    store: Optional[SessionStore] = None,
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


def reset_history(wa_user: Optional[str] = None, store: Optional[SessionStore] = None) -> None:
    """Limpia el historial (de un usuario o de todos). Útil entre pruebas."""
    (store or _store).reset(wa_user)
