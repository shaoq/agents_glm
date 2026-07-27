"""Worker layer: worker definitions (ResearchPlanner, EvidenceResearcher,
Analyst, ReportWriter, ReportReviewer), worker registry and the executor.

Workers receive a Task-scoped context projection and may only emit a
``TaskResult`` or a semantic ``Proposal``; they cannot touch runtime repositories
directly.
"""
