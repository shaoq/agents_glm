"""Deterministic test doubles for agents_orchestration (not shipped to production).

Backs the default (offline) test suite only: phase-port doubles fed into the
real ``build_production_coordinator`` injection seam, and a real-adapter test
registry. Nothing here touches the network, a sibling project, or ``.env``.
"""
