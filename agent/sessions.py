"""Historial de conversación persistente, por ``wa_user``.

Antes el historial vivía solo en RAM → se perdía al reiniciar el server (cold start
= el agente "olvidaba" la conversación aunque en WhatsApp el hilo siguiera ahí).
Aquí lo persistimos a disco para que sobreviva reinicios y se cargue en cada mensaje.

Serializa el historial de PydanticAI (``list[ModelMessage]``) con
``ModelMessagesTypeAdapter`` (round-trip nativo de la librería).

Producción: cambiar el backend de archivo por Redis/Supabase no toca al agente
(la interfaz es ``get`` / ``set`` / ``reset``). Para un solo proceso, el archivo basta.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from pydantic_ai.messages import ModelMessage, ModelMessagesTypeAdapter

logger = logging.getLogger("viviendin.agent.sessions")


class SessionStore:
    def __init__(self, path: str | Path = "data/sessions.json") -> None:
        self.path = Path(path)
        self._cache: dict[str, list[ModelMessage]] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("No se pudo leer %s: %s (arranco sin historial)", self.path, exc)
            return
        for wa_user, messages_json in raw.items():
            try:
                self._cache[wa_user] = ModelMessagesTypeAdapter.validate_python(messages_json)
            except Exception as exc:  # noqa: BLE001 — un usuario corrupto no tumba el resto
                logger.warning("Historial corrupto de %s, se ignora: %s", wa_user, exc)

    def _persist(self) -> None:
        data = {
            wa_user: ModelMessagesTypeAdapter.dump_python(messages, mode="json")
            for wa_user, messages in self._cache.items()
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    # --- interfaz usada por el handler ---
    def get(self, wa_user: str) -> list[ModelMessage]:
        return self._cache.get(wa_user, [])

    def set(self, wa_user: str, messages: list[ModelMessage]) -> None:
        self._cache[wa_user] = messages
        self._persist()

    def reset(self, wa_user: str | None = None) -> None:
        """Borra el historial de un usuario (o de todos si ``wa_user`` es None)."""
        if wa_user is None:
            self._cache.clear()
        else:
            self._cache.pop(wa_user, None)
        self._persist()
