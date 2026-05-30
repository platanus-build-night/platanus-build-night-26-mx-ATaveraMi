# Instrucciones — Sesión del Agente (Viviendin)

> Este documento es para la **otra sesión de Claude Code**, que implementa el **agente
> conversacional**. La sesión actual se concentra en el **scraper** (poblar el inventario).
> Trabajamos en paralelo contra un contrato compartido.

## Contexto rápido (lee primero)
- `PLAN.md` — alcance, modelo de negocio (broker), fases, decisiones cerradas.
- `DATA_MODEL.md` — **contrato de datos** (developers → developments → models → leads).
- `data/seed.json` — **inventario de prueba** (30 modelos: 15 CDMX + 15 Querétaro). Úsalo
  para desarrollar el agente SIN esperar al scraper ni a Supabase.
- `db/schema.sql` — DDL de las 4 tablas (cuando montemos Supabase).

## Qué es Viviendin (en una línea)
Agente de WhatsApp que entiende a un comprador de vivienda nueva, le muestra desarrollos
reales que cumplen sus criterios, **captura un lead calificado** con fecha de visita, lo
registra y notifica a un número interno (operamos como **broker**, cobramos comisión).

## Stack del agente: **PydanticAI** (Python)
Implementamos el agente con [PydanticAI](https://ai.pydantic.dev/). Razones: tipado fuerte
con modelos Pydantic que **espejan el contrato de datos**, tools como funciones tipadas, y
salida estructurada validada para el lead.

### Componentes propuestos (a definir/afinar en tu sesión)
```
WhatsApp (Kapso webhook)  →  FastAPI (endpoint público, verifica firma)
                                  │  async: responde 200 rápido, procesa aparte
                                  ▼
                          PydanticAI Agent
                            ├─ deps: conexión a datos (seed.json | Supabase)
                            ├─ system prompt (rol, reglas de match, tono)
                            ├─ tool: search_inventory(filtros) → List[Development]
                            ├─ tool: get_development_detail(id) → Development+models
                            └─ tool: create_lead(Lead) → guarda + notifica número interno
```

### Modelos Pydantic (derivar de DATA_MODEL.md)
Crea `Developer`, `Development`, `Model`, `Lead` como modelos Pydantic con los MISMOS
campos/tipos del contrato. Notas importantes que el agente debe respetar:
- **`price_on_request`**: ~20% del inventario oculta precios (`price = None`). El agente
  NO debe descartar esos desarrollos por presupuesto; los ofrece igual y captura el lead.
- **`housing_type`** incluye `terreno`: si el comprador busca terreno, filtra por
  `area_lot_m2` y precio, NO por recámaras.
- **Contacto de ventas a 2 niveles**: usa `development.sales_whatsapp/phone/email`; si está
  vacío, cae al contacto corporativo del `developer`. Esto es lo que va en la notificación.

### Tools (firmas sugeridas)
- `search_inventory(state?, municipality?, zone?, housing_type?, bedrooms_min?, budget_max?, ...)`
  → filtra a nivel `model` y devuelve los `developments` que tienen al menos un modelo que
  cumple (con su rango de precio y el modelo que matchea). Degrada con gracia si falta data.
- `create_lead(...)` → valida el `Lead`, lo persiste y dispara la notificación interna
  (formato "🏠 NUEVO LEAD" de `PLAN.md`) vía API de Kapso.

### Flujo conversacional (de PLAN.md)
Saludo → extraer slots (zona/ciudad, presupuesto, tipo, recámaras, crédito, horizonte) →
`search_inventory` → presentar 2-3 opciones → comprador elige + propone fecha/hora →
capturar nombre → `create_lead` → confirmar "un asesor te contactará".

## Canal: Kapso (no Baileys)
WhatsApp Cloud API vía Kapso: webhook entrante (verificar firma HMAC, responder 200 en <10s)
+ API REST saliente. Hay un skill `integrate-whatsapp` para esto. **Recomendado:** define una
**capa de canal abstracta** (`on_message` / `send_message`) con dos adaptadores —
`KapsoChannel` y un `SimulatorChannel` local (REPL)— para desarrollar el agente sin depender
de la conexión del número. Mismo agente, misma DB para ambos. Ver detalle de Kapso, ventana
de 24h y templates en `PLAN.md`.

## Contrato entre sesiones (importante)
- **Fuente de verdad del esquema:** `DATA_MODEL.md` + `db/schema.sql`. Si propones un cambio
  de campos, anótalo ahí para que el scraper se entere.
- **Mientras no haya Supabase:** el agente lee de `data/seed.json` (estructura anidada
  developer→developments→models, con ids reales). Diseña tu capa de datos detrás de una
  interfaz (`InventoryRepo`) para cambiar de `seed.json` a Supabase sin tocar el agente.
- El agente **lee** developers/developments/models y **escribe** leads. El scraper **escribe**
  developers/developments/models. No se pisan.

## Primeras tareas sugeridas para tu sesión
1. **Definir la arquitectura del sistema** (cómo se ve todo junto): repo Python, FastAPI +
   PydanticAI + capa de canal + `InventoryRepo`. Dibújalo y valídalo antes de codear.
2. Crear los modelos Pydantic (`Developer`, `Development`, `Model`, `Lead`) desde DATA_MODEL.md.
3. Implementar `InventoryRepo` sobre `data/seed.json` (cargar, filtrar a nivel modelo).
4. Definir el system prompt y las tools del Agent.
5. Probar el loop conversacional con el `SimulatorChannel` (sin WhatsApp todavía).
6. Integrar Kapso (webhook + envío) y la notificación interna al final.

## Decisiones ya tomadas (no re-litigar)
- Objetivo = capturar **lead calificado** (no cerrar la cita).
- DB = **Supabase (Postgres)**. Canal = **Kapso**. Cobertura = nacional.
- Para la demo, el match con `seed.json` (CDMX + Querétaro) es suficiente.

## ✅ La capa de canal YA está construida (`channel/`) — solo enchufa tu handler

La integración con Kapso + el simulador ya existen como paquete autocontenido en `channel/`
(ver `channel/README.md`). **No la reimplementes.** Tú solo escribes un `handler` async:

```python
from channel import InboundMessage  # entrante normalizado, agnóstico del canal

async def handler(msg: InboundMessage) -> str | None:
    # msg.wa_user (E.164), msg.text, msg.contact_name, msg.is_new_conversation, ...
    # aquí corres tu Agent de PydanticAI y devuelves el texto de respuesta
    return respuesta
```

Enchufarlo:
- **Kapso (webhook real):** `app.include_router(build_webhook_router(channel, handler))`
  (verifica firma HMAC, responde 200 en <10s, procesa en background, auto-responde el texto).
- **Simulador (sin WhatsApp):** `await SimulatorChannel().run_repl(handler)` — mismo handler.

Notificación del lead (handoff de broker), úsala dentro de tu tool `create_lead`:
```python
from channel import LeadNotification, send_lead_notification, load_settings
await send_lead_notification(channel, LeadNotification(...), load_settings())
```
Cae a template `nuevo_lead_visita` automáticamente si la ventana de 24h está cerrada.

Exports en `channel/__init__.py`: `KapsoChannel`, `SimulatorChannel`, `build_webhook_router`,
`InboundMessage`, `LeadNotification`, `send_lead_notification`, `load_settings`, `Settings`.
Config por env (ver `.env.example`). Tests sin red: `python -m channel.test_channel`.
