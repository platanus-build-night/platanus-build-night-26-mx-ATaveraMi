"""Persistencia mínima de leads (sink JSON).

En producción esto va a Supabase (tabla ``leads``). Para la demo y el simulador
escribimos a un archivo JSON con la misma forma del contrato, para no bloquear el
desarrollo del agente. Cambiar esta clase por una que haga INSERT a Supabase no
toca al agente (depende solo de ``save``).
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .models import Lead


class LeadRepo:
    def __init__(self, path: str | Path = "data/leads.json") -> None:
        self.path = Path(path)

    def save(self, lead: Lead) -> str:
        record = lead.model_dump()
        record["id"] = str(uuid.uuid4())
        record["created_at"] = datetime.now(timezone.utc).isoformat()
        existing: list[dict] = []
        if self.path.exists():
            try:
                existing = json.loads(self.path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                existing = []
        existing.append(record)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return record["id"]
