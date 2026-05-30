"""Dependencias inyectadas al Agent en cada corrida (RunContext.deps)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from channel.base import Channel
from channel.config import Settings

from .inventory import InventoryRepo
from .leads import LeadRepo


@dataclass
class AgentDeps:
    repo: InventoryRepo
    leads: LeadRepo
    channel: Channel
    settings: Settings
    wa_user: str
    contact_name: Optional[str] = None
