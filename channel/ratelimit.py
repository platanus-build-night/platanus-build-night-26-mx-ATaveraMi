"""Rate limiting para el webhook — proteger la API de abuso (flood → costo de LLM/WhatsApp).

Threat model: el endpoint del webhook es público. Si alguien lo floodea con mensajes,
cada uno dispara el agente (Gemini = $) y envíos de WhatsApp ($). Esto acota el daño.

Decisión clave para webhooks: NUNCA devolver 429 — Kapso reintenta ante non-200 y el flood
empeora. En vez de eso, el webhook responde 200 SIEMPRE y este limiter decide si el mensaje
se PROCESA (llama al agente) o se DESCARTA en silencio. Protege la parte cara, no el HTTP.

Dos diques con ventana deslizante (in-memory; válido con --workers 1, que es como se deploya):
- por remitente (`wa_user`): frena a un solo número que dispara muchos mensajes.
- global: techo total de mensajes/min — protege el presupuesto de la API aunque el ataque
  venga de muchos números distintos (spoofeados).
"""

from __future__ import annotations

import logging
from collections import defaultdict, deque
from typing import Callable

logger = logging.getLogger("viviendin.channel.ratelimit")


class RateLimiter:
    """Ventana deslizante en memoria. ``allow(sender, now)`` → True si se debe procesar."""

    def __init__(
        self,
        *,
        per_sender_max: int = 12,
        per_sender_window_s: float = 60.0,
        global_max: int = 120,
        global_window_s: float = 60.0,
    ) -> None:
        self.per_sender_max = per_sender_max
        self.per_sender_window_s = per_sender_window_s
        self.global_max = global_max
        self.global_window_s = global_window_s
        self._by_sender: dict[str, deque[float]] = defaultdict(deque)
        self._global: deque[float] = deque()

    @staticmethod
    def _trim(dq: deque[float], cutoff: float) -> None:
        while dq and dq[0] < cutoff:
            dq.popleft()

    def allow(self, sender: str, now: float) -> bool:
        """¿Procesar este mensaje? Registra el hit si pasa; lo descarta si excede un dique."""
        # Dique global (techo de presupuesto).
        self._trim(self._global, now - self.global_window_s)
        if len(self._global) >= self.global_max:
            logger.warning("Rate limit GLOBAL alcanzado (%d/%ss); descartando mensaje de %s",
                           self.global_max, self.global_window_s, sender)
            return False

        # Dique por remitente.
        dq = self._by_sender[sender]
        self._trim(dq, now - self.per_sender_window_s)
        if len(dq) >= self.per_sender_max:
            logger.warning("Rate limit por remitente %s (%d/%ss); descartando",
                           sender, self.per_sender_max, self.per_sender_window_s)
            return False

        # Pasa: registrar en ambas ventanas.
        dq.append(now)
        self._global.append(now)
        return True
