"""Research capability adapters.

This is the ONLY package permitted to import sibling projects
(``agents_memory``, ``agents_rag``) and provider SDKs (``openai``, ``httpx``).
Adapters wrap those dependencies behind the async Capability Port and normalize
results into ``CapabilityResult`` with source, citation, usage and degradation
metadata.
"""
