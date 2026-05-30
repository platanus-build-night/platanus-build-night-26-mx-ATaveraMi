#!/usr/bin/env bash
# Levanta el webhook de Viviendin + túnel ngrok para la demo.
#
# Uso:
#   ./channel/run_demo.sh           # puerto 8000 por defecto
#   PORT=9000 ./channel/run_demo.sh
#
# Deja corriendo uvicorn y ngrok. Ctrl-C para detener ambos.
# La URL pública de ngrok aparece en el panel: http://localhost:4040

set -euo pipefail
cd "$(dirname "$0")/.."

PORT="${PORT:-8000}"
PY=".venv/bin/python"
[ -x "$PY" ] || PY="python3"

echo "▶ Arrancando webhook en http://localhost:${PORT} (ruta: /webhooks/kapso)"
"$PY" -m uvicorn channel.demo_app:app --port "${PORT}" --host 0.0.0.0 &
UVICORN_PID=$!

cleanup() {
  echo "\n⏹  Deteniendo…"
  kill "${UVICORN_PID}" 2>/dev/null || true
  kill "${NGROK_PID:-}" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

sleep 2
echo "▶ Abriendo túnel ngrok → puerto ${PORT}"
ngrok http "${PORT}" &
NGROK_PID=$!

sleep 3
echo ""
echo "─────────────────────────────────────────────────────────────"
echo " Panel ngrok (copia la URL https):  http://localhost:4040"
echo " Health check:                       http://localhost:${PORT}/health"
echo ""
echo " Cuando tengas la URL pública (https://XXXX.ngrok-free.app),"
echo " registra el webhook con el skill integrate-whatsapp:"
echo ""
echo "   node scripts/create.js --phone-number-id <ID> \\"
echo "     --url https://XXXX.ngrok-free.app/webhooks/kapso \\"
echo "     --events whatsapp.message.received --payload-version v2"
echo "─────────────────────────────────────────────────────────────"
echo ""
wait
