"""Notificación interna del lead (modelo broker).

Al capturar un lead calificado, el agente NO contacta a la desarrolladora: dispara una
notificación a NUESTRO número interno con todo lo necesario para que el equipo concrete
la visita. Formato definido en ``PLAN.md``.

Consideración de la ventana de 24h (Meta/Kapso): la notificación es un mensaje iniciado
por el negocio. Si el número interno NO escribió al bot en las últimas 24h, Meta exige un
template UTILITY pre-aprobado (``nuevo_lead_visita``). ``send_lead_notification`` intenta
texto plano y, si la ventana está cerrada, cae al template automáticamente.
"""

from __future__ import annotations

import logging
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Any, Optional

from .base import Channel, OutboundResult
from .config import Settings
from .kapso import KapsoChannel, WindowExpiredError

logger = logging.getLogger("viviendin.channel.notifications")


@dataclass(slots=True)
class LeadNotification:
    """Datos mínimos para armar el mensaje interno. Todo es opcional salvo lo esencial."""

    buyer_name: Optional[str]
    wa_user: str
    housing_type: Optional[str] = None
    zone: Optional[str] = None
    bedrooms: Optional[int] = None
    budget: Optional[float] = None
    credit: Optional[str] = None
    horizon: Optional[str] = None
    development_name: Optional[str] = None
    developer_name: Optional[str] = None
    model_name: Optional[str] = None
    visit_date: Optional[str] = None
    visit_time: Optional[str] = None
    developer_contact: Optional[str] = None
    """Contacto de ventas para el handoff: ``development.sales_whatsapp`` o el corporativo."""

    def __post_init__(self) -> None:
        if not self.wa_user:
            raise ValueError("LeadNotification requiere wa_user")


def _money(value: Optional[float]) -> str:
    if value is None:
        return "sin definir"
    try:
        return f"${value:,.0f} MXN"
    except (ValueError, TypeError):
        return str(value)


def format_lead_notification(lead: LeadNotification) -> str:
    """Arma el texto '🏠 NUEVO LEAD' según el formato de PLAN.md (degrada campos vacíos)."""
    name = lead.buyer_name or "(sin nombre)"
    tipo = lead.housing_type or "vivienda"
    zona = lead.zone or "zona por definir"
    rec = f"{lead.bedrooms} rec" if lead.bedrooms is not None else "rec n/d"
    credito = lead.credit or "n/d"
    horizonte = lead.horizon or "n/d"
    desarrollo = lead.development_name or "(por definir)"
    desarrolladora = f" ({lead.developer_name})" if lead.developer_name else ""
    modelo = f"\nModelo de interés: {lead.model_name}" if lead.model_name else ""
    visita = " ".join(p for p in (lead.visit_date, lead.visit_time) if p) or "por acordar"
    contacto = lead.developer_contact or "(sin contacto registrado — usar corporativo)"

    return (
        "🏠 NUEVO LEAD — visita por concretar\n\n"
        f"Comprador: {name} · {lead.wa_user}\n"
        f"Busca: {tipo} en {zona}, {rec}, ~{_money(lead.budget)}, crédito {credito}\n"
        f"Horizonte: {horizonte}\n\n"
        f"Desarrollo de interés: {desarrollo}{desarrolladora}{modelo}\n"
        f"Visita propuesta: {visita}\n\n"
        f"➡️ Escribir a la desarrolladora: {contacto}"
    )


def draft_developer_message(lead: LeadNotification) -> str:
    """Mensaje YA REDACTADO para que el equipo se lo reenvíe a la desarrolladora."""
    tipo = lead.housing_type or "vivienda"
    zona = lead.zone or "la zona de su interés"
    rec = f", {lead.bedrooms} recámaras" if lead.bedrooms is not None else ""
    modelo = f" (modelo {lead.model_name})" if lead.model_name else ""
    presupuesto = f", presupuesto aproximado {_money(lead.budget)}" if lead.budget else ""
    visita = " ".join(p for p in (lead.visit_date, lead.visit_time) if p) or "por acordar"
    comprador = lead.buyer_name or "un comprador"
    return (
        f"Hola, les escribo de parte de Viviendin (brokeraje de vivienda nueva). "
        f"Tengo un comprador interesado en {lead.development_name or 'su desarrollo'}{modelo}: "
        f"{comprador} busca {tipo} en {zona}{rec}{presupuesto}. "
        f"Le gustaría agendar una visita ({visita}). ¿Podemos coordinarla? "
        f"Contacto del comprador: {lead.wa_user}. ¡Gracias!"
    )


def format_lead_email(lead: LeadNotification) -> str:
    """Cuerpo del correo: resumen del lead + a quién escribir + mensaje listo para reenviar."""
    contacto = lead.developer_contact or "(sin contacto registrado — buscar el corporativo)"
    return (
        f"{format_lead_notification(lead)}\n\n"
        "──────────────────────\n"
        f"✉️  A QUIÉN ESCRIBIR (desarrolladora): {contacto}\n\n"
        "📝  MENSAJE SUGERIDO (copia y pega para la desarrolladora):\n"
        f"{draft_developer_message(lead)}\n"
    )


def send_lead_email(lead: LeadNotification, settings: Settings) -> bool:
    """Envía el correo del lead por SMTP. Best-effort: loguea y devuelve False si falla."""
    if not settings.email_enabled:
        logger.info("Correo del lead no configurado (SMTP_*/LEAD_EMAIL_TO) — se omite.")
        return False
    msg = EmailMessage()
    who = lead.buyer_name or lead.wa_user
    msg["Subject"] = f"🏠 Nuevo lead Viviendin — {lead.development_name or 'visita'} ({who})"
    msg["From"] = settings.lead_email_from or settings.smtp_user
    msg["To"] = settings.lead_email_to
    msg.set_content(format_lead_email(lead))
    ctx = ssl.create_default_context()
    try:
        if settings.smtp_port == 465:
            with smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, context=ctx) as s:
                s.login(settings.smtp_user, settings.smtp_password)
                s.send_message(msg)
        else:
            with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as s:
                s.starttls(context=ctx)
                s.login(settings.smtp_user, settings.smtp_password)
                s.send_message(msg)
        logger.info("Correo del lead enviado a %s", settings.lead_email_to)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.error("No se pudo enviar el correo del lead: %s", exc)
        return False


def _template_components(text: str) -> list[dict[str, Any]]:
    """Componentes para el template ``nuevo_lead_visita`` (un body param posicional {{1}})."""
    return [{"type": "body", "parameters": [{"type": "text", "text": text}]}]


async def send_lead_notification(
    channel: Channel,
    lead: LeadNotification,
    settings: Settings,
) -> OutboundResult:
    """Notifica el lead al número interno. Cae a template si la ventana de 24h está cerrada.

    Devuelve el ``OutboundResult`` del envío (texto o template). Loguea y no lanza salvo
    errores de configuración.
    """
    to = settings.internal_notify_number
    if not to:
        logger.error("INTERNAL_NOTIFY_NUMBER no configurado: no se puede notificar el lead.")
        return OutboundResult(ok=False, error="internal_notify_number_missing")

    text = format_lead_notification(lead)

    try:
        result = await channel.send_text(to, text)
    except WindowExpiredError:
        result = OutboundResult(ok=False, error="window_expired")
    else:
        if result.ok:
            return result

    # Texto falló por ventana cerrada → intentar con template (solo Kapso lo soporta).
    if isinstance(channel, KapsoChannel) and result.error in {"window_expired", None}:
        logger.info("Ventana de 24h cerrada; enviando template '%s'.", settings.lead_template_name)
        try:
            return await channel.send_template(
                to,
                settings.lead_template_name,
                settings.lead_template_language,
                _template_components(text),
            )
        except WindowExpiredError as exc:
            logger.error("Template también falló: %s", exc)
            return OutboundResult(ok=False, error=str(exc))

    return result
