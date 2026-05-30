# Deploy del webhook (recibir webhooks 24/7)

`ngrok` es solo para desarrollo (URL efímera, se cae al cerrar la laptop). Para recibir
webhooks de forma constante hay que desplegar el server.

## Decisiones (resumen)

- **FastAPI** — async-nativo, mismo stack que el agente (PydanticAI) y que rialtor en prod.
- **Un solo deployable** — el webhook y el agente corren en la **misma app FastAPI**. La
  sesión del agente crea el `app` e incluye el router:
  `app.include_router(build_webhook_router(channel, handler))`. `channel/demo_app.py` es solo
  para probar el canal aislado (echo bot).
- **Host: Render** — mismo patrón que rialtor (`render.yaml` en la raíz).
- **`BackgroundTasks` (ack 200 + proceso async)** — Kapso exige 200 en <10s o reintenta; el
  agente LLM puede tardar más. Procesar inline causaría reintentos → doble respuesta. Por eso
  respondemos 200 de inmediato y procesamos en background.

## ⚠️ Lo crítico para "constantemente"

1. **Plan always-on.** El **free tier de Render se duerme** tras ~15 min sin tráfico →
   cold start de 30-60s → **webhooks perdidos/retrasados**. Usa `plan: starter` ($7/mes)
   para producción. Para una demo de horas, el free aguanta si lo mantienes despierto
   (un ping cada pocos minutos a `/health`, p.ej. con UptimeRobe o un cron).

2. **Repo de la org NO se conecta a Render.** Espeja tu código a un repo personal y conecta
   Render a ese (ver `../README.md` → "Deploying").

3. **Retries de Kapso:** ante non-200, reintenta a los 10s/40s/90s. Como damos 200 antes de
   procesar, un fallo *posterior* no se reintenta. Aceptable para demo; en prod, encolar.

4. **Idempotencia (mejora pendiente):** Kapso puede reentregar (header `X-Idempotency-Key`).
   Conviene deduplicar por `message_id` para no responder dos veces. Con `--workers 2` un set
   en memoria no se comparte; lo correcto sería Supabase/Redis. No implementado aún.

## Pasos

1. **Mirror a repo personal** (una vez):
   ```bash
   git remote set-url --add --push origin https://github.com/platanus-build-night/platanus-build-night-26-mx-ATaveraMi.git
   git remote set-url --add --push origin https://github.com/<tu-user>/<tu-repo>.git
   git push
   ```
2. **Render → New → Blueprint** apuntando a tu repo personal (lee `render.yaml`).
3. **Llena los secretos** (`sync: false`) en el dashboard: `KAPSO_API_KEY`,
   `KAPSO_PHONE_NUMBER_ID`, `KAPSO_WEBHOOK_SECRET`, `INTERNAL_NOTIFY_NUMBER`.
4. **Verifica** `https://<tu-app>.onrender.com/health`.
5. **Registra el webhook** de Kapso a `https://<tu-app>.onrender.com/webhooks/kapso`
   (events `whatsapp.message.received`, payload v2) con el skill `integrate-whatsapp`:
   ```bash
   node scripts/create.js --phone-number-id <ID> \
     --url https://<tu-app>.onrender.com/webhooks/kapso \
     --events whatsapp.message.received --payload-version v2
   ```
6. **Cuando el agente esté listo:** en `render.yaml` cambia `startCommand` al app del agente
   (`uvicorn app:app ...`) y `buildCommand` a un `requirements.txt` raíz que mezcle las deps
   del canal + agente. Re-deploy.

## Alternativa rápida (sin deploy): ngrok

Para desarrollo / demo en la laptop: `./channel/run_demo.sh` (levanta uvicorn + ngrok).
Sirve para iterar, pero la URL cambia y no es 24/7.
