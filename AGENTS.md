# Company Brain — Agent Instructions

## 1. Purpose

This repository contains Company Brain, an organizational memory and intelligence platform.

The system builds a temporal, provenance-aware, permission-aware organizational knowledge layer from company activity and information sources.

The architecture is defined in:

`docs/architecture/overview.md`

That document is the source of truth for what we are building.

This file defines how AI coding agents should work on the repository.

---

## 2. Source of Truth Hierarchy

When making implementation decisions, use this order:

1. Explicit user instruction
2. `AGENTS.md`
3. `docs/architecture/overview.md`
4. Architecture decision records under `docs/decisions/`
5. Existing code and implementation conventions
6. Agent assumptions

Never override a higher-level decision based on an assumption.

If existing code conflicts with the architecture, identify the conflict before making a major change.

---

## 3. Before Every Task

Before modifying code:

1. Read this file.
2. Read the relevant architecture documentation.
3. Inspect the existing implementation.
4. Understand existing dependencies and interfaces.
5. Check the current git state.
6. Make the smallest change that correctly satisfies the task.

Do not assume that a missing implementation should be invented.

---

## 4. Architectural Principles

### Evidence First

Raw source evidence and normalized events are retained independently from derived organizational knowledge.

The knowledge graph is derived state.

Never make the graph the only copy of important organizational information.

### Provenance

Important knowledge must be traceable to its source evidence.

Knowledge should eventually support:

- source
- timestamp
- evidence
- extraction method
- confidence
- temporal validity
- human confirmation

### Temporal Knowledge

Organizational knowledge changes over time.

Do not destroy historical truth merely because the current state changed.

Prefer:

- `valid_from`
- `valid_to`
- `last_observed`
- supersession
- correction history

over destructive updates where historical context matters.

### LLM Boundaries

LLMs interpret and propose.

Software validates and decides.

The LLM must never be the source of truth for:

- authorization
- permissions
- identity
- graph integrity
- provenance
- destructive state changes

Do not give an LLM unrestricted access to infrastructure.

### Authorization

Authorization must happen before unauthorized information reaches an LLM.

Do not retrieve everything and attempt to hide unauthorized information afterward.

The authorization layer must eventually support permission-aware access to:

- entities
- relationships
- facts
- properties
- evidence
- documents
- conversations
- source data

### One Organizational Graph

Company Brain should maintain one connected organizational knowledge graph.

Teams and projects are permission-aware views/subgraphs of the larger organizational graph.

Do not create isolated knowledge graphs per team unless explicitly required by the architecture.

### Source Systems

Source systems remain authoritative for fine-grained source-specific information.

For example, if a user asks for an exact GitHub diff or changed code line, retrieve that information from the appropriate source system rather than relying on an LLM-generated summary.

### MCP

MCP must eventually use the same application, retrieval, and authorization pipeline as the main application.

Never create a separate authorization path for MCP.

---

## 5. Layer Boundaries

Maintain strict separation between:

### api/

External interfaces such as REST and MCP.

Keep API handlers thin.

### schemas/

Request/response DTOs and API-facing validation models.

Do not use API schemas as domain models.

### domain/

Business concepts and domain rules.

The domain layer must not depend on infrastructure implementations.

### application/

Use cases, workflows, orchestration, and business processes.

### infrastructure/

Concrete implementations:

- PostgreSQL
- Neo4j
- vector storage
- queues
- object storage
- LLM providers
- external connectors
- OpenFGA
- other external services

### workers/

Asynchronous/background execution.

### shared/

Cross-cutting primitives such as:

- logging
- exceptions
- types
- constants
- utilities

Do not bypass these boundaries simply because doing so is faster.

---

## 6. Knowledge Architecture

The long-term pipeline is:

Data Sources
→ Connectors
→ Ingestion
→ Raw Evidence / Event Store
→ Normalized Events
→ Knowledge Compiler
→ Entity / Identity Resolution
→ Ontology Validation
→ Knowledge Graph
→ Embeddings / Indexes / Graph Analytics
→ Permission-Aware Retrieval
→ LLM / Agents
→ Answer

The Knowledge Compiler is a first-class subsystem.

Do not collapse ingestion, extraction, graph construction, and retrieval into one service or one large agent.

---

## 7. AI Architecture

Do not build one giant autonomous agent.

Prefer specialized workflows/agents with deterministic software around them.

Potential agents include:

- Knowledge Agent
- Question Answering
- Project Agent
- Employee Assistance
- Onboarding
- Handover

Use AI where semantic interpretation is required.

Use deterministic code for:

- validation
- authorization
- persistence
- graph integrity
- state transitions
- policy enforcement
- provenance
- data normalization where deterministic rules are sufficient

---

## 8. Connectors

External integrations must be replaceable.

Connectors should conceptually support:

- authorization
- token refresh
- disconnect
- resource discovery
- initial synchronization
- subscriptions/webhooks
- event normalization

Initial integrations:

- Google Workspace
- Microsoft 365 / Teams
- GitHub

Do not tightly couple the core domain to a specific external provider.

---

## 9. Graph Analytics

Graph analytics is separate from ingestion and graph construction.

Leiden/community detection, centrality, dependency paths, and community summaries belong to graph analytics.

Community detection does not define authorization.

Community summaries must respect permissions.

Do not run expensive graph-wide analytics after every individual event unless explicitly designed to do so.

---

## 10. Technology Principles

Current technology direction:

- Python
- FastAPI
- PostgreSQL
- Neo4j as a graph database candidate
- pgvector as an initial vector-search candidate
- Queue/event infrastructure
- OpenFGA as an authorization candidate
- LangGraph for stateful AI workflows

A technology marked as a "candidate" must not be treated as permanently locked without an explicit architectural decision.

Do not introduce additional infrastructure merely because it is popular or convenient.

Prefer simple infrastructure during early development.

---

## 11. Implementation Rules

### Do not over-engineer

Build incrementally.

Do not implement future systems merely because the final architecture contains them.

### Do not create fake functionality

Do not create hardcoded or simulated implementations that pretend to perform:

- knowledge extraction
- graph reasoning
- authorization
- retrieval
- AI reasoning
- external synchronization

If a subsystem is not implemented yet, establish a clean interface or clearly marked placeholder instead.

### Do not silently change architecture

If implementation requires changing a core architectural decision:

1. Stop.
2. Explain the conflict.
3. Explain the proposed alternative.
4. Wait for approval.

### Preserve working code

Do not rewrite functioning code simply to match personal preferences.

Refactor only when required by the task or architecture.

### Prefer explicitness

Prefer clear interfaces and explicit dependencies over hidden magic.

---

## 12. Testing

Every implemented feature should have appropriate tests.

Prefer:

- unit tests for domain logic
- integration tests for infrastructure
- API tests for endpoints
- end-to-end tests for important user flows

Do not claim a feature is complete without running relevant tests.

---

## 13. Security & Privacy

Company Brain is not intended to be a surveillance system.

Monitoring must be:

- explicit
- opt-in
- controllable
- work-related
- permission-aware

Do not introduce:

- keystroke logging
- indiscriminate screenshots
- personal browsing surveillance
- hidden monitoring

Security and privacy constraints are architectural requirements, not optional UI features.

---

## 14. Documentation

When implementing a significant architectural component:

- document important decisions
- update relevant architecture documentation
- create an ADR when introducing or changing a significant architectural decision

Do not silently accumulate architectural decisions inside code.

---

## 15. AGENTS.md Modification Policy

Do not modify this file automatically.

If you discover a recurring and confirmed:

- architectural rule
- project convention
- important mistake
- security constraint
- implementation pitfall

that should be remembered for future work, propose an update to this file.

Only modify `AGENTS.md` after explicit approval.

Avoid adding temporary task-specific instructions.

---

## 16. Completion Protocol

Before declaring a task complete:

1. Review the changes.
2. Check that architecture boundaries remain intact.
3. Run relevant tests.
4. Check for accidental changes.
5. Report important assumptions.
6. Report any deferred work.
7. Report any architectural concerns.

Do not claim something works if it has not been verified.

---

## 17. Current Development Strategy

Company Brain is being developed incrementally.

Do not jump ahead to later phases.

The current implementation phase will always be explicitly specified by the user.

Implement the current phase thoroughly before proceeding to the next phase.