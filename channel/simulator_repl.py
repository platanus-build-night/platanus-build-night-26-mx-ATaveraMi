"""Entrypoint del simulador local (sin WhatsApp).

Correr::

    python -m channel.simulator_repl

Por defecto usa un handler de eco. Para probar el agente real, importa este módulo y
pásale el handler del agente a ``SimulatorChannel.run_repl``.
"""

from __future__ import annotations

import asyncio

from .base import InboundMessage
from .simulator import SimulatorChannel


async def echo_handler(msg: InboundMessage) -> str:
    return f"(eco) Recibí: «{msg.text}»"


def main() -> None:
    asyncio.run(SimulatorChannel().run_repl(echo_handler))


if __name__ == "__main__":
    main()
