# Plan & Alcance — Agente de WhatsApp para vivienda nueva (Build Night CDMX)

## Visión

Registro centralizado de vivienda nueva en México + un agente de WhatsApp que
entiende qué busca un comprador, le muestra desarrollos que existen, captura un
**lead calificado** y lo entrega a nuestro equipo para concretar la visita y
cobrar **comisión de broker**.

## Modelo de negocio (clave)

Actuamos como **broker**. El agente NO contacta a la desarrolladora. Al cerrar la
conversación, dispara una notificación a **nuestro número interno** con todo lo
necesario para que nuestro equipo le escriba a la desarrolladora y concrete la visita.

## Decisiones de alcance (cerradas)

| Tema | Decisión |
|------|----------|
| **Fuente de datos** | **Lista propia de 297 desarrolladoras** (sitios propios) + extracción con LLM → DB (base) + live-search como fallback. Portales agregadores = complemento opcional. |
| **Objetivo del agente** | Capturar **lead calificado** (no cerrar cita; solo prepara el handoff). |
| **Cobertura** | Nacional / multi-ciudad (los portales agregadores ya son nacionales). |
| **Canal WhatsApp** | **Kapso** (WhatsApp Cloud API oficial). Webhook entrante + API saliente. Capa de canal abstracta con **simulador local como fallback de demo**. |
| **DB** | **Supabase (Postgres)**. |
| **Entrega del lead** | Mensaje de WhatsApp a número interno designado **+ registro en DB**. |

## Estrategia de datos (3 fuentes, 3 tiempos)

1. **Lista propia de 297 desarrolladoras** (sitios propios, en `data/desarrolladoras.txt`)
   + **extracción con LLM** → DB. Es la base y ya cubre el gap de no-listados.
   Técnicamente más seguro que los portales (poco anti-bot). *Lo construimos esta noche.*
2. **Portales agregadores** (Lamudi, Inmuebles24, La Haus, Propiedades.com) —
   complemento para volumen. *Opcional / roadmap.*
3. **Onboarding de oferta** (desarrolladoras suben su inventario) — el camino real
   al "registro completo". *Roadmap / go-to-market.*

> **El reto de la base:** 297 sitios = 297 estructuras distintas. No se escribe un
> parser por sitio; se usa **extracción con LLM** (HTML → Claude con schema fijo →
> `{desarrollos, zona, precios, recámaras, contacto}`). Un pipeline que generaliza.
>
> Para la demo NO se necesitan los 297 procesados: corremos un **subconjunto (20–50)**
> bien extraído y el resto en background (candidato a workflow multi-agente, 1 agente
> por sitio). Como el objetivo es *capturar el lead* (no inventario perfecto), basta
> con dar 2-3 opciones creíbles.

### Datos de la lista (estado)

- `data/desarrolladoras_raw.txt` — 308 URLs originales tal cual.
- `data/desarrolladoras.txt` — **297 dominios únicos** normalizados (11 duplicados eliminados).
- ⚠️ Revisar 4 atípicos: `ecohabitat.com.pe` (Perú, fuera de MX), `creo.us`,
  `idex.cc`, `portocumbres.mrm.website`.

## Arquitectura técnica

```
WhatsApp (comprador)
   │  entrante: webhook  whatsapp.message.received  (verificar firma HMAC)
   ▼
Capa de canal  ──┬─ KapsoChannel    (webhook in / REST API out)
 onMessage()     └─ SimulatorChannel (REPL local, fallback de demo)
 sendMessage()
   ▼
Orquestador del agente  ── Claude (tool use)   [idéntico para ambos canales]
   │   extrae slots, decide búsqueda, conversa
   ├─ tool: search_developments(filtros) ─────► Supabase (developments + models)
   ├─ tool: live_search(query)  [fallback]  ──► web search
   └─ tool: create_lead(...) ─────────────────► Supabase (leads)
                                            └──► notifica número interno (API Kapso)
   ▲  saliente: POST /meta/whatsapp/v24.0/{phone_number_id}/messages  (X-API-Key)
Extractor LLM (fetch HTML → Claude+schema) ── batch ─► upsert developers/developments/models
   ▲ alimentado por data/desarrolladoras.txt (297 sitios)
```

**Stack:** Node.js · **Kapso** (`@kapso/whatsapp-cloud-api` o REST) · Claude
(NLU/orquestación + extracción) · **Supabase (Postgres)** · fetch/Playwright (sitios
con JS pesado) · **ngrok** (URL pública del webhook para la demo).

### Esquema de datos

Contrato completo en **`DATA_MODEL.md`**. Jerarquía de 3 niveles:
`developers` → `developments` → `models` (+ `leads`). Todo nullable salvo nombre +
relación; se guarda el `raw` (jsonb) de cada extracción para reprocesar.

## Flujo conversacional

1. Saludo + entender intención.
2. Extraer slots: ciudad/zona, presupuesto, tipo, recámaras, crédito, horizonte de compra.
3. `search_developments` → si 0 resultados: ampliar criterios o `live_search`.
4. Presentar 2-3 opciones (nombre · zona · precio desde · 1 highlight).
5. Comprador elige una + propone fecha/hora tentativa de visita.
6. Capturar nombre (el WhatsApp ya lo tenemos).
7. `create_lead` → registra en DB + dispara notificación interna y confirma al
   comprador "un asesor te contactará".

### Formato de la notificación interna

```
🏠 NUEVO LEAD — visita por concretar

Comprador: {nombre} · {wa_user}
Busca: {tipo} en {zona}, {recamaras} rec, ~{presupuesto}, crédito {credito}
Horizonte: {horizonte}

Desarrollo de interés: {desarrollo} ({desarrolladora})
Visita propuesta: {fecha} {hora}

➡️ Escribir a la desarrolladora: {contacto_desarrolladora}
```

> **Ventana de 24h (Kapso/Meta):** la notificación interna es un mensaje iniciado por
> el negocio. Si el número interno no escribió al bot en las últimas 24h, Meta exige un
> **template pre-aprobado** (`nuevo_lead_visita`, UTILITY). Truco de demo: el número
> interno manda "hola" al bot antes de demostrar → ventana abierta → texto plano.

## Plan por fases (time-boxed, ~1 noche, 1 persona)

- **Fase 0 — Setup (~45–60 min, riesgo externo):** repo Node, deps, `.env`, cuenta
  Kapso + `KAPSO_API_KEY`. **🔴 Crítico/primero:** conectar número MX vía setup link
  (el auto-provision de Kapso es solo US) y obtener `phone_number_id`. Levantar ngrok
  + endpoint webhook con verificación de firma. Echo bot vía API. En paralelo:
  construir la **capa de canal + simulador** para no bloquearse por el número.
- **Fase 1 — Data (2–2.5h):** esquema DB + pipeline de **extracción con LLM** sobre un subconjunto de la lista de 297 (fetch HTML → Claude con schema → `developments`). Resto en background.
- **Fase 2 — Agente core (2.5–3h):** loop Claude con tool use, `search_developments`, manejo de slots, presentación de resultados.
- **Fase 3 — Lead + notificación (1.5h):** `create_lead` → Supabase + mensaje interno vía API Kapso (con la consideración de ventana de 24h / template).
- **Fase 4 — Pulido + demo (1–2h):** `live_search` fallback (si alcanza), mensajes pulidos, guion de demo (incluye "abrir ventana de 24h del número interno"), opcional Flow estático de cierre. Logo + `build-night-project.json` ya listos.

## Riesgos y mitigaciones

- **Sitios heterogéneos / con JS pesado** → la extracción LLM generaliza, pero algunos
  sitios necesitan headless (Playwright). Mitigación: priorizar sitios estáticos para
  la demo y tener un **dataset semilla en JSON como plan B** por si la extracción falla.
- **Costo de LLM por 297 sitios** → procesar solo subconjunto en vivo; resto en background/batch.
- **🔴 Conexión del número MX en Kapso se atora** (depende de tercero: Twilio/Meta) →
  mitigación: **capa de canal abstracta + simulador local** para demostrar el mismo
  agente sin depender del número. Atacar la conexión del número de primero.
- **Webhook necesita URL pública** → ngrok para la demo; Render/Vercel si se quiere estable.
- **Notificación interna fuera de ventana 24h** → template `nuevo_lead_visita` pre-aprobado
  (registrar temprano, Meta tarda) o abrir la ventana antes de demostrar.
- **Tiempo** → el core es Supabase + agente + notificación. `live_search`, dashboard y Flow son nice-to-have.

## Out of scope (piloto)

- Agendar/confirmar la cita automáticamente con la desarrolladora.
- Crawl masivo de los 297 sitios / onboarding de oferta (solo subconjunto para demo).
- WhatsApp Flows dinámicos con data endpoint encriptado — migración futura.
