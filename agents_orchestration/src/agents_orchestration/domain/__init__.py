"""Pure domain layer.

Immutable identifiers, models, typed enums, state machines and domain events.

Boundary rule (enforced by architecture tests): this package MUST NOT import any
infrastructure provider — no ``sqlite3``, ``typer``, ``rich``, ``openai``,
``httpx``/``requests``, ``pydantic_settings``, and no sibling project
(``agents_memory`` / ``agents_rag``). Only the Python standard library and
``pydantic`` are permitted.
"""
