# Capa de canal de WhatsApp — Viviendin

Integración con **Kapso** (WhatsApp Cloud API oficial) + **simulador local** de fallback.
El agente (PydanticAI) se enchufa con un solo callback y nunca toca el payload crudo de Kapso.

## Qué resuelve

- **Recibe** mensajes de WhatsApp por webhook (verifica firma HMAC, responde 200 en <10s,
  procesa en background).
- **Envía** texto y templates por la REST de Kapso (proxy Meta).
- **Notifica el lead** al número interno (modelo broker), con fallback automático a
  template UTILITY cuando la ventana de 24h está cerrada.
- **Simula** todo en una terminal cuando el número aún no está conectado.

## Contrato con el agente (lo único que necesitas saber)

```python
from channel import InboundMessage  # mensaje entrante normalizado

# El agente implementa un handler async:
async def handler(msg: InboundMessage) -> str | None:
    # msg.wa_user, msg.text, msg.contact_name, msg.is_new_conversation, ...
    return "texto de respuesta"   # o None si el agente ya respondió por su cuenta
```

`InboundMessage` (campos clave): `wa_user` (E.164), `text` (incluye transcripción de audio),
`contact_name`, `message_id`, `conversation_id`, `is_new_conversation`, `message_type`, `raw`.

## Uso con Kapso (producción / demo con número)

```python
from fastapi import FastAPI
from channel import KapsoChannel, build_webhook_router, load_settings

settings = load_settings()
channel = KapsoChannel(settings)

app = FastAPI()
app.include_router(build_webhook_router(channel, handler))  # handler = el del agente
```

El router publica `POST /webhooks/kapso` (configurable). Si el handler devuelve texto,
se envía como respuesta automáticamente (`auto_reply=True`).

## Uso con el simulador (sin WhatsApp)

```python
import asyncio
from channel import SimulatorChannel

asyncio.run(SimulatorChannel().run_repl(handler))   # mismo handler del agente
```

## Notificación del lead (handoff de broker)

```python
from channel import LeadNotification, send_lead_notification, load_settings

settings = load_settings()
await send_lead_notification(channel, LeadNotification(
    buyer_name="Andrés", wa_user="+5215512345678",
    housing_type="departamento", zone="Del Valle, CDMX", bedrooms=2, budget=8_400_000,
    credit="bancario", horizon="3-6m",
    development_name="Be Grand Reforma", developer_name="Be Grand",
    visit_date="sábado", visit_time="12:00",
    developer_contact="+525552781900",   # development.sales_whatsapp o el corporativo
), settings)
```

`send_lead_notification` intenta texto plano; si Meta responde "ventana de 24h cerrada"
(codes 131047/131051/131026) cae al template `nuevo_lead_visita` automáticamente.
Truco de demo: que el número interno mande "hola" al bot antes de demostrar → ventana
abierta → texto plano sin necesidad de template aprobado.

## Variables de entorno

Ver [`../.env.example`](../.env.example). Mínimas para enviar: `KAPSO_API_KEY`,
`KAPSO_PHONE_NUMBER_ID`. Para webhook firmado: `KAPSO_WEBHOOK_SECRET`. Para el lead:
`INTERNAL_NOTIFY_NUMBER`.

## Puesta en marcha

```bash
pip install -r channel/requirements.txt
cp .env.example .env   # y completar valores

# 1) Probar sin red (firma, parseo, formato):
python -m channel.test_channel

# 2) Simulador local (sin WhatsApp):
python -m channel.simulator_repl

# 3) Webhook real (demo con eco):
uvicorn channel.demo_app:app --reload --port 8000
ngrok http 8000
# registrar el webhook (skill integrate-whatsapp):
#   node scripts/create.js --phone-number-id <id> \
#     --url https://<ngrok>/webhooks/kapso \
#     --events whatsapp.message.received --payload-version v2
```

## Descubrir IDs / conectar el número

Se hace con el skill **`integrate-whatsapp`** (Node), no desde Python:

- `node scripts/list-platform-phone-numbers.mjs` → `phone_number_id` + `business_account_id`.
- Conectar número MX: setup link (el auto-provision de Kapso es solo US).
- Crear el template `nuevo_lead_visita` (UTILITY, 1 body param posicional `{{1}}`) y
  esperar aprobación de Meta.

## Notas de diseño

- **`phone_number_id` se deriva del inbound.** Para responder, el router usa el
  `phone_number_id` que viene en el propio webhook (respondes por el mismo número que
  recibió el mensaje). `KAPSO_PHONE_NUMBER_ID` (env) es solo el **fallback** para mensajes
  iniciados por el negocio (la notificación interna del lead). Validado contra la
  integración en producción de `rialtor`.
- **Webhooks en lote.** Kapso puede entregar varios mensajes en `{"data": [...]}` (y con
  buffering). `parse_webhook()` maneja el lote y el shape plano; el router procesa todos.
- El envío usa `httpx.AsyncClient`; el canal expone `aclose()` (llamado en el `lifespan`).
- La verificación de firma se hace sobre los **bytes crudos** antes de parsear JSON, y
  tolera el header con o sin prefijo `sha256=`.
- Si el envío con `context` (quote) falla, el router reintenta sin quote (el mensaje citado
  pudo expirar) — patrón tomado de producción.
- Eventos que no son `whatsapp.message.received` y ecos salientes se ignoran (ack 200).
- El template del lead manda todo el texto formateado en un solo parámetro `{{1}}` para no
  acoplar el código al esquema exacto del template aprobado.
```
