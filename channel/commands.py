"""Comandos de control que el comprador puede mandar por WhatsApp.

El comando de **reinicio** (``/refresh`` y sinónimos) borra el contexto que el agente
guarda de la conversación de ese usuario. La capa de canal detecta el comando ANTES de
pasar el mensaje al agente; la limpieza real del estado la hace un callback que el agente
provee (``ResetHandler``), porque el historial vive en el dominio del agente, no del canal.
"""

from __future__ import annotations

from typing import Awaitable, Callable

# Comandos que reinician la conversación. Insensible a mayúsculas/espacios.
# Acepta con y sin "/" para que sea tolerante a cómo lo escriba el usuario.
RESET_COMMANDS: frozenset[str] = frozenset(
    {
        "/refresh",
        "/reiniciar",
        "/reset",
        "/nuevo",
        "/empezar",
        "/start",
        "reiniciar",
        "empezar de nuevo",
    }
)

# Respuesta por defecto tras reiniciar.
DEFAULT_RESET_REPLY = (
    "🔄 Listo, reinicié nuestra conversación. Empecemos de cero: "
    "¿qué tipo de vivienda buscas y en qué zona?"
)

# El agente implementa esto para limpiar SU estado del usuario (historial, slots, etc.).
ResetHandler = Callable[[str], Awaitable[None]]


def is_reset_command(text: str) -> bool:
    """True si ``text`` es un comando de reinicio (tolerante a mayúsculas/espacios)."""
    if not text:
        return False
    return text.strip().lower() in RESET_COMMANDS
