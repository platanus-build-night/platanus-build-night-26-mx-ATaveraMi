"""Configuración de la capa de canal (leída de variables de entorno / .env)."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Variables de entorno para Kapso y la notificación interna.

    Los nombres de Kapso siguen la convención del skill ``integrate-whatsapp``:
    ``KAPSO_API_BASE_URL`` es solo el host (sin ``/platform/v1`` ni ``/meta``).
    """

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # --- Kapso (saliente + proxy Meta) ---
    kapso_api_key: str = ""
    kapso_api_base_url: str = "https://api.kapso.ai"
    meta_graph_version: str = "v24.0"
    # ID Meta del número conectado (lo usa todo el envío). Descubrir con:
    #   node scripts/list-platform-phone-numbers.mjs
    kapso_phone_number_id: str = ""

    # --- Webhook entrante ---
    # Secreto del webhook de Kapso para verificar la firma HMAC X-Webhook-Signature.
    kapso_webhook_secret: str = ""
    kapso_webhook_path: str = "/webhooks/kapso"

    # --- Notificación interna del lead (modelo broker) ---
    # Número de nuestro equipo (E.164, p.ej. +5215512345678) que recibe el lead.
    internal_notify_number: str = ""
    # Template UTILITY pre-aprobado para notificar fuera de la ventana de 24h.
    lead_template_name: str = "nuevo_lead_visita"
    lead_template_language: str = "es_MX"

    # --- Correo del lead (documentación + handoff) ---
    # SMTP para mandar el correo del lead. Para Gmail: host=smtp.gmail.com, port=465,
    # user=tu-correo, password=App Password (cuenta con 2FA → Contraseñas de aplicación).
    smtp_host: str = ""
    smtp_port: int = 465
    smtp_user: str = ""
    smtp_password: str = ""
    lead_email_to: str = ""    # a quién llega el correo del lead (tu correo)
    lead_email_from: str = ""  # remitente (default: smtp_user)

    @property
    def email_enabled(self) -> bool:
        return bool(self.smtp_host and self.smtp_user and self.smtp_password and self.lead_email_to)

    def messages_url(self, phone_number_id: str | None = None) -> str:
        """URL del endpoint de envío del proxy Meta de Kapso.

        ``phone_number_id``: el del inbound (para responder por el mismo número). Si no se
        pasa, cae al ``KAPSO_PHONE_NUMBER_ID`` del env (mensajes iniciados por el negocio).
        """
        base = self.kapso_api_base_url.rstrip("/")
        pid = phone_number_id or self.kapso_phone_number_id
        return f"{base}/meta/whatsapp/{self.meta_graph_version}/{pid}/messages"

    @property
    def signature_verification_enabled(self) -> bool:
        return bool(self.kapso_webhook_secret)


@lru_cache
def load_settings() -> Settings:
    """Carga (y cachea) la configuración desde el entorno."""
    return Settings()
