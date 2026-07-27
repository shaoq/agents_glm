"""Control plane: goal normalization, dynamic planning, plan validation, gate
management and termination guarding.

These components may call the Model Port but only ever emit semantic Proposals;
formal state is committed by the deterministic runtime core.
"""
