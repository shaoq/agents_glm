## ADDED Requirements

### Requirement: Structured evidence set
The report pipeline SHALL consume a versioned EvidenceSet containing evidence identity, source, timestamp,
freshness, citation, trust metadata, conflict state, sufficiency, and degradation.

#### Scenario: Evidence is used in analysis
- **WHEN** Analyst receives an EvidenceSet
- **THEN** every material conclusion in its AnalysisArtifact references supporting Evidence IDs or is marked unsupported

### Requirement: Immutable intermediate artifacts
Raw model responses, capability results, EvidenceSet, AnalysisArtifact, Report Draft, and Review Proposal SHALL
be stored as immutable content-hashed Artifacts.

#### Scenario: Report draft is revised
- **WHEN** ReportReviewer requests a revision
- **THEN** the revised draft receives a new Artifact identity and the previous draft remains available in history

### Requirement: Structured report review
ReportReviewer SHALL return one of PASS, REVISE, RESEARCH_GAP, CONFLICT, or ESCALATE with findings linked to
criteria and Artifact content.

#### Scenario: Reviewer finds missing required evidence
- **WHEN** a report claim lacks evidence required by the Completion Contract
- **THEN** Reviewer returns RESEARCH_GAP or ESCALATE rather than PASS

#### Scenario: Revision budget is exhausted
- **WHEN** Reviewer requests another revision after the effective revision limit is reached
- **THEN** the runtime stops revising and applies partial, fail, or escalate policy

### Requirement: Completion verification
Before successful finalization, TerminationGuard SHALL validate the current Completion Contract, Required Task
results, Evidence Sufficiency, unresolved conflicts, candidate Artifact hashes, pending Attempts, State Version,
Plan Version, Deadline, and degradation disclosure.

#### Scenario: Required criterion is unknown
- **WHEN** a Required Completion Criterion evaluates to UNKNOWN
- **THEN** the Run cannot enter clean successful completion

#### Scenario: Candidate state remains current
- **WHEN** all required criteria are satisfied and Candidate State Version is unchanged
- **THEN** the runtime may atomically commit successful terminal state and final Artifact metadata

### Requirement: Final artifact set
Every successfully or partially delivered Run SHALL produce `report.md`, `report.json`, and `run-summary.json`
as immutable Artifacts.

#### Scenario: Successful report is exported
- **WHEN** an operator exports Artifacts for a completed Run
- **THEN** all three files are written with hashes matching stored Artifact metadata

#### Scenario: Partial report is delivered
- **WHEN** explicit policy allows partial completion
- **THEN** the report and run summary list unmet criteria, missing sources, unresolved conflicts, and degradation

### Requirement: Citation integrity
The Markdown and JSON reports SHALL include citations resolvable to Evidence and original source metadata.
Unknown or unsupported claims MUST be labeled rather than assigned fabricated citations.

#### Scenario: Citation references missing evidence
- **WHEN** final validation finds a citation whose Evidence ID is absent
- **THEN** successful finalization is rejected until the report is corrected or explicitly downgraded

### Requirement: No report-triggered side effects
Final report generation and export SHALL NOT publish, email, deploy, pay, execute code, or mutate user files
outside the explicit export target.

#### Scenario: Report recommends an external action
- **WHEN** report content recommends publishing or paying
- **THEN** the action remains text in an Artifact and no external side effect is invoked
