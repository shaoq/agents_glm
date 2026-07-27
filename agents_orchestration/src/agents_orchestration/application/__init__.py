"""Application layer: the use-case OrchestrationService that composes domain,
runtime, orchestration, workers and capabilities into start/drive/inspect/pause/
resume/cancel/gate/artifact/export operations.

The CLI is a thin adapter over this service and must not duplicate domain logic.
"""
