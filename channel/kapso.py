"""Adaptador de Kapso: envío vía proxy Meta + parseo/verificación del webhook entrante."""

from __future__ import annotations

import hashlib
import hmac
import logging
from typing import Any, Optional

import httpx

from .base import Channel, InboundMessage, OutboundResult
from .config import Settings

logger = logging.getLogger("viviendin.channel.kapso")

# Códigos de Meta que indican que la ventana de servicio de 24h está cerrada:
# hay que reabrir con un template pre-aprobado.
WINDOW_EXPIRED_CODES = {131047, 131051, 131026}


class WindowExpiredError(Exception):
    """La ventana de 24h está cerrada; se requiere un template para reabrirla."""

    def __init__(self, message: str, code: Optional[int] = None) -> None:
        super().__init__(message)
        self.code = code


class KapsoClient:
    """Cliente HTTP delgado para el proxy Meta de Kapso (``POST /{id}/messages``)."""

    def __init__(self, settings: Settings, client: Optional[httpx.AsyncClient] = None) -> None:
        self.settings = settings
        self._client = client or httpx.AsyncClient(timeout=20.0)
        self._owns_client = client is None

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "X-API-Key": self.settings.kapso_api_key,
            "Content-Type": "application/json",
        }

    async def post_message(
        self, payload: dict[str, Any], *, phone_number_id: Optional[str] = None
    ) -> OutboundResult:
        """Envía un payload Meta crudo a ``messages_url`` y normaliza el resultado.

        ``phone_number_id``: el del inbound (para responder por el mismo número); si es None
        usa el del env (mensajes iniciados por el negocio, p.ej. la notificación interna).
        """
        payload.setdefault("messaging_product", "whatsapp")
        url = self.settings.messages_url(phone_number_id)
        try:
            resp = await self._client.post(url, json=payload, headers=self._headers)
        except httpx.HTTPError as exc:  # red caída, timeout, etc.
            logger.error("Error de red enviando a Kapso: %s", exc)
            return OutboundResult(ok=False, error=str(exc))

        data: dict[str, Any]
        try:
            data = resp.json()
        except ValueError:
            data = {"text": resp.text}

        if resp.is_success:
            msg_id = None
            messages = data.get("messages") if isinstance(data, dict) else None
            if messages:
                msg_id = messages[0].get("id")
            return OutboundResult(ok=True, message_id=msg_id, status_code=resp.status_code, raw=data)

        code = _extract_error_code(data)
        if code in WINDOW_EXPIRED_CODES:
            raise WindowExpiredError(
                f"Ventana de 24h cerrada (code {code}); usar template.", code=code
            )
        logger.warning("Kapso respondió %s: %s", resp.status_code, data)
        return OutboundResult(
            ok=False, status_code=resp.status_code, raw=data, error=_extract_error_message(data)
        )

    async def send_text(
        self,
        to: str,
        body: str,
        *,
        reply_to: Optional[str] = None,
        phone_number_id: Optional[str] = None,
    ) -> OutboundResult:
        payload: dict[str, Any] = {
            "to": to,
            "type": "text",
            "text": {"body": body, "preview_url": True},
        }
        if reply_to:
            payload["context"] = {"message_id": reply_to}
        return await self.post_message(payload, phone_number_id=phone_number_id)

    async def send_template(
        self,
        to: str,
        template_name: str,
        language: str,
        components: Optional[list[dict[str, Any]]] = None,
        *,
        phone_number_id: Optional[str] = None,
    ) -> OutboundResult:
        payload: dict[str, Any] = {
            "to": to,
            "type": "template",
            "template": {
                "name": template_name,
                "language": {"code": language},
            },
        }
        if components:
            payload["template"]["components"] = components
        return await self.post_message(payload, phone_number_id=phone_number_id)

    async def mark_read(
        self, message_id: str, *, typing: bool = False, phone_number_id: Optional[str] = None
    ) -> OutboundResult:
        payload: dict[str, Any] = {"status": "read", "message_id": message_id}
        if typing:
            payload["typing_indicator"] = {"type": "text"}
        return await self.post_message(payload, phone_number_id=phone_number_id)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


class KapsoChannel(Channel):
    """Canal de WhatsApp sobre Kapso. Envía vía REST; recibe vía webhook (ver ``webhook.py``)."""

    name = "kapso"

    def __init__(self, settings: Settings, client: Optional[httpx.AsyncClient] = None) -> None:
        self.settings = settings
        self.client = KapsoClient(settings, client=client)

    async def send_text(
        self,
        to: str,
        body: str,
        *,
        reply_to: Optional[str] = None,
        phone_number_id: Optional[str] = None,
    ) -> OutboundResult:
        return await self.client.send_text(
            to, body, reply_to=reply_to, phone_number_id=phone_number_id
        )

    async def send_template(
        self,
        to: str,
        template_name: str,
        language: str,
        components: Optional[list[dict[str, Any]]] = None,
        *,
        phone_number_id: Optional[str] = None,
    ) -> OutboundResult:
        return await self.client.send_template(
            to, template_name, language, components, phone_number_id=phone_number_id
        )

    async def mark_read(
        self, message_id: str, *, typing: bool = False, phone_number_id: Optional[str] = None
    ) -> OutboundResult:
        return await self.client.mark_read(
            message_id, typing=typing, phone_number_id=phone_number_id
        )

    # --- Webhook entrante ---

    def verify_signature(self, raw_body: bytes, signature: Optional[str]) -> bool:
        """Verifica ``X-Webhook-Signature`` = HMAC-SHA256(secret, raw_body) en hex.

        Si no hay secreto configurado (modo demo), no bloquea: devuelve ``True`` con warning.
        """
        if not self.settings.signature_verification_enabled:
            logger.warning(
                "KAPSO_WEBHOOK_SECRET no configurado: se omite la verificación de firma."
            )
            return True
        if not signature:
            return False
        expected = hmac.new(
            self.settings.kapso_webhook_secret.encode("utf-8"), raw_body, hashlib.sha256
        ).hexdigest()
        provided = signature.strip()
        # Kapso puede mandar el hex pelón o con prefijo "sha256=".
        return hmac.compare_digest(expected, provided) or hmac.compare_digest(
            f"sha256={expected}", provided
        )

    def parse_webhook(self, payload: dict[str, Any]) -> list[InboundMessage]:
        """Parsea un payload de webhook completo a una lista de ``InboundMessage``.

        Kapso puede entregar mensajes **en lote**: ``{"data": [ {item}, ... ]}`` (también con
        buffering activado). También acepta el shape plano (un solo objeto al nivel raíz).
        Ignora items que no sean mensajes entrantes procesables.
        """
        raw_items = payload.get("data")
        items = raw_items if isinstance(raw_items, list) else [payload]
        out: list[InboundMessage] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            msg = self.parse_inbound(item)
            if msg and msg.wa_user:
                out.append(msg)
        return out

    def parse_inbound(self, payload: dict[str, Any]) -> Optional[InboundMessage]:
        """Convierte UN item v2 de ``whatsapp.message.received`` en ``InboundMessage``.

        Devuelve ``None`` si no es un mensaje entrante procesable (status, eco saliente, etc.).
        """
        message = payload.get("message")
        if not isinstance(message, dict):
            return None

        kapso = message.get("kapso") or {}
        if kapso.get("direction") not in (None, "inbound"):
            return None  # ignorar ecos salientes / estados

        conversation = payload.get("conversation") or {}
        conv_kapso = conversation.get("kapso") or {}

        # Texto: preferir el "content" normalizado de Kapso (incluye transcript de audio);
        # caer a text.body para mensajes de texto plano.
        text = kapso.get("content")
        if not text:
            text_obj = message.get("text") or {}
            text = text_obj.get("body", "")
        if text is None:
            text = ""

        wa_user = conversation.get("phone_number", "")

        return InboundMessage(
            wa_user=wa_user,
            text=text,
            contact_name=conv_kapso.get("contact_name"),
            message_id=message.get("id"),
            conversation_id=conversation.get("id"),
            phone_number_id=payload.get("phone_number_id") or conversation.get("phone_number_id"),
            is_new_conversation=bool(payload.get("is_new_conversation", False)),
            message_type=message.get("type", "text"),
            timestamp=message.get("timestamp"),
            raw=payload,
        )

    async def aclose(self) -> None:
        await self.client.aclose()


def _extract_error_code(data: Any) -> Optional[int]:
    if isinstance(data, dict):
        err = data.get("error")
        if isinstance(err, dict):
            code = err.get("code")
            if isinstance(code, int):
                return code
    return None


def _extract_error_message(data: Any) -> Optional[str]:
    if isinstance(data, dict):
        err = data.get("error")
        if isinstance(err, dict):
            return err.get("message") or err.get("error_data", {}).get("details")
    return None
