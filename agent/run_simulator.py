"""Corre el agente en el simulador local (REPL) — sin WhatsApp ni Kapso.

    python -m agent.run_simulator
    python -m agent.run_simulator --inventory data/scraped.json

Cada línea que escribes es un mensaje del comprador. La notificación interna del
lead se "envía" imprimiéndose en consola (canal simulador).
"""
from __future__ import annotations

import argparse
import asyncio
import os

from channel.config import load_settings
from channel.simulator import SimulatorChannel

from .conversation import build_handler
from .inventory import InventoryRepo
from .leads import LeadRepo


def main() -> None:
    ap = argparse.ArgumentParser(description="Simulador del agente Viviendin")
    ap.add_argument(
        "--inventory",
        default=os.getenv("INVENTORY_PATHS", "data/seed.json"),
        help="archivo(s) de inventario separados por coma (default: data/seed.json)",
    )
    ap.add_argument("--leads", default="data/leads.json")
    args = ap.parse_args()

    repo = InventoryRepo.from_paths([p.strip() for p in args.inventory.split(",")])
    n_dev = sum(len(d.developments) for d in repo.developers)
    print(f"Inventario: {len(repo.developers)} desarrolladoras · {n_dev} desarrollos "
          f"(de {args.inventory})\n")

    settings = load_settings()
    # En el simulador no hay número interno real: lo apuntamos a uno demo para que la
    # notificación del broker se imprima en consola.
    if not settings.internal_notify_number:
        settings = settings.model_copy(update={"internal_notify_number": "+5215500000001"})

    channel = SimulatorChannel()
    handler = build_handler(repo, LeadRepo(args.leads), channel, settings)
    asyncio.run(channel.run_repl(handler))


if __name__ == "__main__":
    main()
