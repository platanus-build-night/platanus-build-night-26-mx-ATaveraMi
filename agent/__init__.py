"""Agente conversacional de Viviendin (PydanticAI).

Entiende a un comprador de vivienda nueva, le muestra desarrollos reales del
inventario (seed.json / scraped.json), captura un lead calificado y dispara la
notificación interna del broker. Se enchufa a la capa ``channel`` vía un
``MessageHandler`` (ver ``agent.conversation.build_handler``).

Contrato de datos: DATA_MODEL.md. Tareas/flujo: AGENT_SESSION.md / PLAN.md.
"""
