"""Historial de conversación del agente en Supabase (reemplaza el SessionStore de archivo).

Misma interfaz que ``agent.sessions.SessionStore`` (``get`` / ``set`` / ``reset``), así que
``conversation.py`` puede intercambiarlo sin tocar el agente. Persiste el blob nativo de
PydanticAI (``list[ModelMessage]``) — que incluye tool-calls/tool-returns, no solo textos —
en la tabla ``agent_sessions``.

A diferencia del archivo JSON: sobrevive redeploys/reinicios y no depende del disco local.
La tabla ``agent_sessions`` ya existe en Supabase (creada una sola vez, fuera del repo).
"""
from __future__ import annotations

import json
import logging

from psycopg.types.json import Jsonb
from pydantic_ai.messages import ModelMessage, ModelMessagesTypeAdapter

from .conn import connect

logger = logging.getLogger("viviendin.db.sessions")


class SupabaseSessionStore:
    """Store de historial respaldado por Supabase. Interfaz: get / set / reset."""

    def get(self, wa_user: str) -> list[ModelMessage]:
        try:
            with connect() as conn, conn.cursor() as cur:
                cur.execute(
                    "select history from agent_sessions where wa_user = %s", (wa_user,)
                )
                row = cur.fetchone()
        except Exception as exc:  # noqa: BLE001 — DB caída no debe tumbar el chat
            logger.warning("No se pudo leer historial de %s: %s (arranco sin contexto)", wa_user, exc)
            return []
        if not row or not row[0]:
            return []
        raw = row[0]
        if isinstance(raw, str):  # por si el driver devuelve texto
            raw = json.loads(raw)
        try:
            return ModelMessagesTypeAdapter.validate_python(raw)
        except Exception as exc:  # noqa: BLE001 — historial corrupto: empezar limpio
            logger.warning("Historial corrupto de %s, se ignora: %s", wa_user, exc)
            return []

    def set(self, wa_user: str, messages: list[ModelMessage]) -> None:
        payload = ModelMessagesTypeAdapter.dump_python(messages, mode="json")
        try:
            with connect() as conn, conn.cursor() as cur:
                cur.execute(
                    """
                    insert into agent_sessions (wa_user, history, updated_at)
                    values (%s, %s, now())
                    on conflict (wa_user)
                    do update set history = excluded.history, updated_at = now()
                    """,
                    (wa_user, Jsonb(payload)),
                )
                conn.commit()
        except Exception as exc:  # noqa: BLE001 — no perder la respuesta por fallo de escritura
            logger.error("No se pudo guardar historial de %s: %s", wa_user, exc)

    def reset(self, wa_user: str | None = None) -> None:
        try:
            with connect() as conn, conn.cursor() as cur:
                if wa_user is None:
                    cur.execute("delete from agent_sessions")
                else:
                    cur.execute("delete from agent_sessions where wa_user = %s", (wa_user,))
                conn.commit()
        except Exception as exc:  # noqa: BLE001
            logger.error("No se pudo reiniciar historial (%s): %s", wa_user, exc)
