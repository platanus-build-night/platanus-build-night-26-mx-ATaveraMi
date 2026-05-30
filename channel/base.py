"""Contrato compartido de la capa de canal.

Define el mensaje entrante normalizado, el tipo de handler que implementa el agente,
y la interfaz ``Channel`` que ambos adaptadores (Kapso, simulador) cumplen para *enviar*.
La *recepción* difiere por adaptador (webhook HTTP en Kapso, REPL en el simulador) pero
en ambos casos termina invocando el mismo ``MessageHandler``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional


@dataclass(slots=True)
class InboundMessage:
    """Mensaje entrante normalizado, agnóstico del canal.

    El agente solo depende de esta forma; no del payload crudo de Kapso ni del REPL.
    """

    wa_user: str
    """Teléfono del comprador en E.164 (p.ej. ``+5215512345678``)."""

    text: str
    """Texto del mensaje. Para audio trae la transcripción; para media, la descripción."""

    contact_name: Optional[str] = None
    """Nombre de WhatsApp del contacto, si Kapso lo expone."""

    message_id: Optional[str] = None
    """``wamid...`` del mensaje (para responder con ``context`` o marcar leído)."""

    conversation_id: Optional[str] = None
    phone_number_id: Optional[str] = None
    is_new_conversation: bool = False
    message_type: str = "text"
    timestamp: Optional[str] = None
    raw: dict[str, Any] = field(default_factory=dict)
    """Payload crudo completo, por si el agente necesita algo no normalizado."""


@dataclass(slots=True)
class OutboundResult:
    """Resultado de un envío saliente."""

    ok: bool
    message_id: Optional[str] = None
    status_code: Optional[int] = None
    raw: dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None


# El agente implementa esto. Devuelve el texto de respuesta, o None si no hay respuesta
# automática (p.ej. el agente ya envió mensajes por su cuenta vía el canal).
MessageHandler = Callable[[InboundMessage], Awaitable[Optional[str]]]


class Channel(ABC):
    """Interfaz de *envío* común a Kapso y al simulador."""

    name: str = "channel"

    @abstractmethod
    async def send_text(
        self,
        to: str,
        body: str,
        *,
        reply_to: Optional[str] = None,
        phone_number_id: Optional[str] = None,
    ) -> OutboundResult:
        """Envía un mensaje de texto a ``to`` (E.164).

        ``phone_number_id``: número emisor (el del inbound para responder); None = env.
        """

    async def aclose(self) -> None:
        """Libera recursos (conexiones HTTP, etc.). Por defecto no-op."""
        return None
