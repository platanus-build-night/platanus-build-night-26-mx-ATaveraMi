"""Pegamento entre la capa de canal y el Agent.

Mantiene el historial de conversación POR usuario (slots/memoria) en memoria, y
expone ``build_handler`` que produce el ``MessageHandler`` que el canal invoca por
cada mensaje entrante (mismo handler para Kapso y para el simulador).
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

logger = logging.getLogger("viviendin.agent.conversation")

# Historial por wa_user. En producción esto vive en Redis/DB; en memoria basta para la demo.
_history: dict[str, list] = {}


def build_handler(
    repo: InventoryRepo,
    leads: LeadRepo,
    channel: Channel,
    settings: Settings,
) -> MessageHandler:
    async def handler(msg: InboundMessage) -> Optional[str]:
        deps = AgentDeps(
            repo=repo,
            leads=leads,
            channel=channel,
            settings=settings,
            wa_user=msg.wa_user,
            contact_name=msg.contact_name,
        )
        history = _history.get(msg.wa_user, [])
        try:
            result = await agent.run(msg.text, deps=deps, message_history=history)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Error en el agente para %s: %s", msg.wa_user, exc)
            return "Uy, tuve un problemita técnico 😅 ¿me repites lo último?"
        _history[msg.wa_user] = result.all_messages()
        return result.output

    return handler


def reset_history(wa_user: Optional[str] = None) -> None:
    """Limpia el historial (de un usuario o de todos). Útil entre pruebas."""
    if wa_user is None:
        _history.clear()
    else:
        _history.pop(wa_user, None)
