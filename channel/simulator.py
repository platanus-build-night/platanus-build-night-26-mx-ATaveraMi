"""Adaptador simulador: REPL local para probar el agente sin WhatsApp ni Kapso.

Mismo ``MessageHandler``, misma ``InboundMessage``: el agente no sabe si habla con
Kapso o con la terminal. Fallback de demo si la conexión del número se atora.
"""

from __future__ import annotations

import asyncio
from typing import Optional

from .base import Channel, InboundMessage, MessageHandler, OutboundResult


class SimulatorChannel(Channel):
    """Canal de consola. ``send_text`` imprime; ``run_repl`` lee de stdin."""

    name = "simulator"

    def __init__(self, wa_user: str = "+5215500000000", contact_name: str = "Comprador Demo") -> None:
        self.wa_user = wa_user
        self.contact_name = contact_name
        self._counter = 0

    async def send_text(
        self,
        to: str,
        body: str,
        *,
        reply_to: Optional[str] = None,
        phone_number_id: Optional[str] = None,
    ) -> OutboundResult:
        print(f"\n🤖 Viviendin → {to}:\n{body}\n")
        self._counter += 1
        return OutboundResult(ok=True, message_id=f"sim-{self._counter}")

    async def run_repl(self, handler: MessageHandler) -> None:
        """Bucle interactivo: cada línea de la terminal es un mensaje del comprador."""
        print("=== Simulador Viviendin (Ctrl-C o 'salir' para terminar) ===")
        print(f"Hablas como {self.contact_name} ({self.wa_user}).\n")
        is_new = True
        loop = asyncio.get_event_loop()
        while True:
            try:
                line = await loop.run_in_executor(None, input, "👤 Tú: ")
            except (EOFError, KeyboardInterrupt):
                print("\n👋 Fin de la simulación.")
                return
            if line.strip().lower() in {"salir", "exit", "quit"}:
                print("👋 Fin de la simulación.")
                return
            if not line.strip():
                continue

            self._counter += 1
            inbound = InboundMessage(
                wa_user=self.wa_user,
                text=line,
                contact_name=self.contact_name,
                message_id=f"sim-in-{self._counter}",
                conversation_id="sim-conv",
                phone_number_id="sim-phone",
                is_new_conversation=is_new,
                message_type="text",
            )
            is_new = False
            reply = await handler(inbound)
            if reply:
                await self.send_text(self.wa_user, reply)
