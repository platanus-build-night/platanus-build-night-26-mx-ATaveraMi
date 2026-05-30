"""Tests sin red de la capa de canal. Correr: ``python -m channel.test_channel``.

Cubre: verificación de firma HMAC, parseo de webhook v2, y formato de la notificación.
No requiere Kapso ni conexión (no hace requests salientes).
"""

from __future__ import annotations

import hashlib
import hmac
import json

from .config import Settings
from .kapso import KapsoChannel
from .notifications import LeadNotification, format_lead_notification

SAMPLE_INBOUND = {
    "message": {
        "id": "wamid.ABC",
        "timestamp": "1730092800",
        "type": "text",
        "text": {"body": "Busco depa en la Del Valle de 2 recámaras"},
        "kapso": {"direction": "inbound", "content": "Busco depa en la Del Valle de 2 recámaras"},
    },
    "conversation": {
        "id": "conv_1",
        "phone_number": "+5215512345678",
        "phone_number_id": "111",
        "kapso": {"contact_name": "Andrés"},
    },
    "is_new_conversation": True,
    "phone_number_id": "111",
}


def test_signature_ok() -> None:
    s = Settings(kapso_webhook_secret="s3cr3t")
    ch = KapsoChannel(s)
    body = b'{"hello":"world"}'
    sig = hmac.new(b"s3cr3t", body, hashlib.sha256).hexdigest()
    assert ch.verify_signature(body, sig) is True
    assert ch.verify_signature(body, f"sha256={sig}") is True  # prefijo tolerado
    assert ch.verify_signature(body, "deadbeef") is False
    assert ch.verify_signature(body, None) is False
    print("✓ firma HMAC (con y sin prefijo sha256=)")


def test_signature_disabled() -> None:
    ch = KapsoChannel(Settings(kapso_webhook_secret=""))
    assert ch.verify_signature(b"x", None) is True  # modo demo
    print("✓ firma omitida sin secreto (demo)")


def test_parse_inbound() -> None:
    ch = KapsoChannel(Settings())
    msg = ch.parse_inbound(SAMPLE_INBOUND)
    assert msg is not None
    assert msg.wa_user == "+5215512345678"
    assert "Del Valle" in msg.text
    assert msg.contact_name == "Andrés"
    assert msg.is_new_conversation is True
    assert msg.message_id == "wamid.ABC"
    # eco saliente se ignora
    out = dict(SAMPLE_INBOUND)
    out_msg = json.loads(json.dumps(SAMPLE_INBOUND))
    out_msg["message"]["kapso"]["direction"] = "outbound"
    assert ch.parse_inbound(out_msg) is None
    print("✓ parseo de webhook v2")


def test_parse_webhook_batch() -> None:
    ch = KapsoChannel(Settings())
    # Shape plano (un objeto) → 1 mensaje.
    assert len(ch.parse_webhook(SAMPLE_INBOUND)) == 1
    # Shape en lote ("data": [...]) → varios mensajes, ignorando ecos salientes.
    item2 = json.loads(json.dumps(SAMPLE_INBOUND))
    item2["message"]["id"] = "wamid.DEF"
    echo = json.loads(json.dumps(SAMPLE_INBOUND))
    echo["message"]["kapso"]["direction"] = "outbound"
    batch = {"data": [SAMPLE_INBOUND, item2, echo]}
    parsed = ch.parse_webhook(batch)
    assert len(parsed) == 2
    assert {m.message_id for m in parsed} == {"wamid.ABC", "wamid.DEF"}
    print("✓ parseo de webhook en lote (data[])")


def test_format_notification() -> None:
    lead = LeadNotification(
        buyer_name="Andrés",
        wa_user="+5215512345678",
        housing_type="departamento",
        zone="Del Valle, CDMX",
        bedrooms=2,
        budget=8400000,
        credit="bancario",
        horizon="3-6m",
        development_name="Be Grand Reforma",
        developer_name="Be Grand",
        visit_date="sábado",
        visit_time="12:00",
        developer_contact="+525552781900",
    )
    text = format_lead_notification(lead)
    assert "NUEVO LEAD" in text
    assert "Andrés" in text
    assert "$8,400,000 MXN" in text
    assert "Be Grand Reforma" in text
    assert "+525552781900" in text
    print("✓ formato de notificación")
    print("\n--- ejemplo de notificación interna ---\n" + text + "\n")


def main() -> None:
    test_signature_ok()
    test_signature_disabled()
    test_parse_inbound()
    test_parse_webhook_batch()
    test_format_notification()
    print("Todos los tests pasaron ✅")


if __name__ == "__main__":
    main()
