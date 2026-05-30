"""Router de FastAPI para el webhook entrante de Kapso.

Responsabilidades (en orden):
1. Leer el cuerpo crudo y verificar la firma HMAC ANTES de parsear JSON.
2. Responder 200 OK de inmediato (Kapso exige <10s, si no reintenta).
3. Procesar el mensaje en background: invocar el ``MessageHandler`` del agente y,
   si devuelve texto, enviarlo como respuesta por el mismo canal.
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, BackgroundTasks, Header, Request, Response

from .base import InboundMessage, MessageHandler
from .commands import DEFAULT_RESET_REPLY, ResetHandler, is_reset_command
from .kapso import KapsoChannel

logger = logging.getLogger("viviendin.channel.webhook")

INBOUND_EVENT = "whatsapp.message.received"


def build_webhook_router(
    channel: KapsoChannel,
    handler: MessageHandler,
    *,
    path: str | None = None,
    auto_reply: bool = True,
    on_reset: ResetHandler | None = None,
    reset_reply: str = DEFAULT_RESET_REPLY,
    mark_read_typing: bool = True,
) -> APIRouter:
    """Construye el ``APIRouter`` del webhook.

    Args:
        channel: canal Kapso (para verificar firma y responder).
        handler: callback del agente ``(InboundMessage) -> Optional[str]``.
        path: ruta del endpoint (default: ``settings.kapso_webhook_path``).
        auto_reply: si True, el texto devuelto por el handler se envía como respuesta.
        on_reset: callback del agente ``(wa_user) -> None`` que borra el contexto que el
            agente guarda de ese usuario. Si se pasa, los comandos ``/refresh`` (y
            sinónimos, ver ``commands.RESET_COMMANDS``) NO llegan al handler: se ejecuta
            ``on_reset`` y se responde ``reset_reply``.
        reset_reply: confirmación que se envía tras reiniciar.
        mark_read_typing: si True (default), al recibir un mensaje lo marca como leído y
            muestra el indicador "escribiendo…" mientras el agente prepara la respuesta.
            El indicador se descarta al enviar la respuesta o tras ~25s (límite de Meta).
    """
    router = APIRouter()
    route_path = path or channel.settings.kapso_webhook_path

    async def _reply(inbound: InboundMessage, text: str) -> None:
        """Envía ``text`` al usuario, respondiendo por el mismo número del inbound."""
        result = await channel.send_text(
            inbound.wa_user,
            text,
            reply_to=inbound.message_id,
            phone_number_id=inbound.phone_number_id,
        )
        if not result.ok and inbound.message_id:
            # Reintentar sin "quote": el context puede fallar si el mensaje citado expiró.
            logger.warning("Reintentando respuesta sin quote para %s", inbound.wa_user)
            result = await channel.send_text(
                inbound.wa_user, text, phone_number_id=inbound.phone_number_id
            )
        if not result.ok:
            logger.error("No se pudo responder a %s: %s", inbound.wa_user, result.error)

    async def _mark_read(inbound: InboundMessage) -> None:
        """Marca el mensaje como leído (palomita azul) + indicador 'escribiendo…'."""
        if not (mark_read_typing and inbound.message_id):
            return
        try:
            await channel.mark_read(
                inbound.message_id, typing=True, phone_number_id=inbound.phone_number_id
            )
        except Exception:  # nunca bloquear el procesamiento por esto
            logger.debug("No se pudo marcar leído/typing para %s", inbound.wa_user)

    async def _process(inbound: InboundMessage) -> None:
        # Acusar recibo: leído + "escribiendo…" mientras se prepara la respuesta.
        await _mark_read(inbound)

        # Comando de reinicio: limpia el contexto del agente y confirma, sin invocar al agente.
        if on_reset is not None and is_reset_command(inbound.text):
            try:
                await on_reset(inbound.wa_user)
            except Exception:
                logger.exception("Error reiniciando el contexto de %s", inbound.wa_user)
            logger.info("Conversación reiniciada para %s", inbound.wa_user)
            if auto_reply:
                await _reply(inbound, reset_reply)
            return

        try:
            reply = await handler(inbound)
        except Exception:  # nunca tirar el background task
            logger.exception("Error en el handler del agente para %s", inbound.wa_user)
            return
        if not (auto_reply and reply):
            return
        # Responder por el MISMO número que recibió el mensaje (phone_number_id del inbound).
        await _reply(inbound, reply)

    @router.post(route_path)
    async def kapso_webhook(  # noqa: D401
        request: Request,
        background: BackgroundTasks,
        x_webhook_signature: str | None = Header(default=None),
        x_webhook_event: str | None = Header(default=None),
    ) -> Response:
        raw = await request.body()

        if not channel.verify_signature(raw, x_webhook_signature):
            logger.warning("Firma de webhook inválida (event=%s)", x_webhook_event)
            return Response(status_code=401, content="invalid signature")

        # Solo nos interesan los mensajes entrantes; ack 200 a todo lo demás.
        if x_webhook_event and x_webhook_event != INBOUND_EVENT:
            return Response(status_code=200, content="ignored")

        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("Webhook con JSON inválido")
            return Response(status_code=400, content="invalid json")

        # El payload puede traer varios mensajes en lote ("data": [...]).
        for inbound in channel.parse_webhook(payload):
            background.add_task(_process, inbound)

        # 200 inmediato: el procesamiento ocurre en background.
        return Response(status_code=200, content="ok")

    return router
