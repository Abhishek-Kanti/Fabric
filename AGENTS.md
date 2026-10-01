# Company Brain — Agent Instructions & Operating Manual

## 1. Purpose

This repository contains **Company Brain**, a team-first organizational memory and intelligence platform.

The system builds a temporal, provenance-aware, permission-aware organizational knowledge layer from collaborative team activity and information sources (such as Google Workspace, Microsoft 365 / Teams, GitHub, and task management tools).

This document (`AGENTS.md`) is the **repository-level operating manual** for AI coding agents and human contributors. It defines how to work in this repository, coding conventions, architectural invariants, layer boundaries, testing standards, and verification protocols.

---

## 2. Documentation Hierarchy & Mental Model

Maintain a strict separation between operating rules, high-level architecture (HLD), subsystem designs (LLD), and code:

```text
AGENTS.md
    │
    └── HOW YOU SHOULD WORK (Repository operating rules, conventions, invariants)

docs/architecture/overview.md
    │
    └── WHAT THE SYSTEM IS (High-Level Design / architectural source of truth)

docs/architecture/*.md
    │
    └── HOW EACH SUBSYSTEM IS DESIGNED (Low-Level Designs: database, auth, graph, etc.)

source code
    │
    └── ACTUAL IMPLEMENTATION (Clean, verified, phase-scoped code)
```

- **`AGENTS.md`**: Repository operating manual. Contains developer rules, coding standards, layer boundaries, and quality requirements. Does not contain detailed schema listings, implementation specifications, or deep subsystem designs.
- **`docs/architecture/overview.md`**: High-Level Design (HLD). Explains what the system is, product vision, team-first concepts, major components, data flows, boundaries, and candidate technology choices. Does not contain detailed table schemas or low-level algorithms.
- **`docs/architecture/*.md`**: Low-Level Design (LLD) documents created incrementally when designing specific subsystems (e.g., `database.md`, `authorization.md`, `knowledge-graph.md`, `retrieval.md`). Never create speculative LLDs ahead of time.
- **Source Code**: Concrete, tested implementation matching the current development phase.

Do not duplicate the same detailed specification across all levels.

---

## 3. Source of Truth Hierarchy & Operating Rule

### Hierarchy of Authority

When making implementation decisions, use this strict order:

1. **Explicit user instruction**
2. **`AGENTS.md`** (this operating manual)
3. **`docs/architecture/overview.md`** (HLD architecture)
4. **Architecture Decision Records** under `docs/decisions/`
5. **Subsystem LLD documents** under `docs/architecture/*.md`
6. **Existing code and implementation conventions**
7. **Agent assumptions** (lowest priority; never override higher levels)

### Feature Implementation Rule

Before writing or modifying code for any feature:

1. **Read `AGENTS.md`** to verify operating rules and constraints.
2. **Read the relevant HLD section** in `docs/architecture/overview.md`.
3. **Read the relevant LLD document** in `docs/architecture/` if one exists for that subsystem.
4. **Inspect existing implementation and tests** to understand interfaces and dependencies.
5. **Check git status** to ensure a clean baseline.
6. **Check for conflicts**: If the proposed implementation would contradict the architecture, **stop immediately and report the conflict** rather than silently changing the architecture.
7. **Propose architectural decisions**: If an architectural decision is required, propose it and wait for confirmation before implementing. Never invent major architectural decisions silently.
8. **Make the smallest change** that correctly satisfies the task.

---

## 4. Product Terminology: Team-First Model

Company Brain is explicitly **team-first rather than enterprise-first**.

### Core Product Hierarchy

```text
Workspace
    │
    ├── Users
    ├── Teams
    └── Projects
           │
           ├── Members
           └── Tasks
```

### Terminology Invariants

- **`Workspace`**: The top-level administrative and data-isolation boundary. A Workspace may represent:
  - A startup
  - A company
  - A college team
  - A hackathon team
  - A research group
  - An open-source project
  - Any collaborative group
- **`User`**: An individual participant in a Workspace. Do not assume every User is an "employee".
- **`Team`**: A collaborative group of Users within a Workspace.
- **`Project`**: A focused initiative or repository of work owned by a Workspace and associated with Users/Teams.
- **`Task`**: An actionable unit of work within a Project.

**Rule on Legacy Terms**: If an internal database table, code symbol, or configuration currently uses `organization`, do not rename it blindly. Propose the terminology/schema migration plan first and await approval before modifying existing operational code.

---

## 5. Architectural Invariants

Every agent must respect these architectural invariants across all implementations:

### 1. Evidence First
- Raw source evidence and normalized events are retained independently from derived organizational knowledge.
- The knowledge graph is derived state, which can be rebuilt or re-indexed.
- Never make the graph the sole copy of critical organizational information.

### 2. Full Provenance
- Every entity, relation, fact, and property in the knowledge graph must trace back to source evidence.
- Provenance attributes include: source system, external ID, ingestion timestamp, extraction method/model, confidence score, and human confirmation status.

### 3. Temporal Validity
- Organizational knowledge changes over time; historical truth must not be erased.
- Prefer bitemporal validity (`valid_from`, `valid_to`, `observed_at`, supersession, correction history) over destructive updates where historical context matters.

### 4. Deterministic LLM Boundaries
- **LLMs interpret and propose; deterministic software validates and decides.**
- The LLM must NEVER be the authoritative decision-maker for:
  - Authorization and permissions
  - Identity and entity resolution
  - Graph integrity and foreign constraints
  - Provenance tracking
  - Destructive state changes or persistence
- Do not grant an LLM unrestricted execution access to infrastructure or databases.

### 5. Authorization Before Retrieval
- Authorization filtering must occur **before** unauthorized information reaches an LLM prompt.
- Never retrieve an unfiltered context window and attempt to instruct the LLM to hide unauthorized information.

### 6. Single Connected Workspace Graph
- Company Brain maintains one unified knowledge graph per Workspace.
- Teams, projects, and permissions form permission-aware views/subgraphs over that unified graph.
- Do not create isolated, physically siloed graphs per team unless explicitly dictated by architecture.

### 7. Authoritative Source Systems
- Source systems (GitHub, Google Drive, Slack, etc.) remain authoritative for granular, source-specific payloads (e.g., exact code diffs, raw file contents).
- Retrieve deep source data directly from the source system rather than hallucinating or approximating from summaries.

### 8. Model Context Protocol (MCP) Parity
- MCP tools and endpoints must reuse the exact same application, retrieval, and authorization pipeline as the primary REST API.
- Never implement a backdoor, bypass, or separate authorization path for MCP.

---

## 6. Layer Boundaries & Dependency Rules

Maintain strict physical and conceptual separation across the codebase (`backend/app/`):

```text
api/             (External HTTP / MCP handlers; thin adapters; no business logic)
    ↓
schemas/         (Pydantic request/response DTOs; API validation only)
    ↓
application/     (Use cases, workflow orchestrators, application services)
    ↓
domain/          (Core business entities, domain rules, value objects; pure Python)
    ↑
infrastructure/  (Database, graph DB, vector index, message queue, external APIs, auth)
workers/         (Background job consumers, scheduled tasks, asynchronous pipelines)
shared/          (Cross-cutting utilities, exceptions, logging, config, base types)
```

### Dependency Rules

1. **Inward Dependencies**: Dependencies must flow strictly inward toward the domain layer.
2. **`domain/`**: Must have ZERO dependencies on outer layers (`api`, `application`, `infrastructure`, `workers`). Pure business logic and domain exceptions only.
3. **`schemas/`**: DTOs and serialization models for API input/output. Never use API schemas as domain entities or database models.
4. **`application/`**: Orchestrates domain models and interacts with infrastructure via abstract interfaces/ports. Must not depend on `api/`.
5. **`infrastructure/`**: Implements interfaces defined by domain/application layers (PostgreSQL, Neo4j, pgvector, OpenFGA, Redis, external connectors).
6. **`api/`**: Thin controllers. Parses requests, invokes application services, and maps results/exceptions to HTTP/MCP responses.
7. **`workers/`**: Asynchronous consumers for background workflows (ingestion, compilation, embedding generation).
8. **`shared/`**: Common primitives used across layers without circular dependencies.

Do not bypass these layer boundaries for expediency.

---

## 7. Development & Implementation Rules

### 1. Incremental Development
- Build strictly phase-by-phase. The user explicitly designates the current phase (e.g., Phase 1: Foundation).
- Do not jump ahead to future phases.
- Do not implement future systems merely because they appear in HLD or LLD documents.

### 2. Scope Creep Prohibition
- Implement the smallest complete change that satisfies the user prompt and phase requirements.
- Do not add speculative configurations, unused dependencies, or unrequested scaffolding.

### 3. No Fake or Simulated Functionality
- Do not write mock or hardcoded stubs that pretend to perform actual knowledge extraction, graph reasoning, authorization, or search.
- If a subsystem is scheduled for a future phase, define a clean abstract interface or a clearly marked placeholder instead of fake data.

### 4. Preserve Working Code
- Do not rewrite or restructure functioning code merely to suit personal stylistic preferences.
- Refactor only when required by architectural needs or explicit user instructions.

### 5. Explicitness Over Magic
- Prefer explicit dependency injection, clear function signatures, and transparent data structures over hidden metaprogramming or global mutable state.

---

## 8. Coding Conventions

- **Language & Runtime**: Python 3.12+, FastAPI, Pydantic v2.
- **Type Annotations**: Comprehensive type hints on all public functions, classes, and methods. Use `from typing import ...` or Python 3.10+ built-in union syntax (`X | None`).
- **Async First**: Use asynchronous I/O (`async def`) for database operations, HTTP requests, and external service calls.
- **Error Handling**:
  - Raise domain-specific exceptions in `domain/` and `application/`.
  - Catch and translate exceptions into proper HTTP status codes in `api/` exception handlers.
  - Never allow raw unhandled 500 tracebacks to leak sensitive internal stack details to clients.
- **Configuration**:
  - All configurable parameters must be declared in `app/config.py` using `pydantic-settings`.
  - Secrets and credentials must be read from environment variables; never hardcode credentials.
- **Linting & Formatting**: Follow standard PEP 8, formatted cleanly. Keep imports organized: standard library, third-party libraries, local application modules.

---

## 9. Testing & Verification Standards

Every implemented feature must be verified before completing a task:

1. **Test Coverage Expectations**:
   - **Unit Tests (`tests/unit/`)**: Verify domain logic, configuration parsing, data validation, and isolated algorithms without external infrastructure.
   - **Integration Tests (`tests/integration/`)**: Verify API endpoints, database interactions, and service integrations with mock or containerized dependencies.
   - **End-to-End Tests (`tests/e2e/`)**: Verify complete user and agent workflows.
2. **Execution Requirement**:
   - Always run the relevant test suite (e.g., `.venv\Scripts\pytest` or `uv run pytest`) before declaring a task complete.
   - Fix all test failures and regressions introduced by changes.
3. **No Unverified Claims**:
   - Never report that an endpoint, feature, or service is working unless it has been executed and validated against actual tests or live verification.

---

## 10. Security, Privacy & Compliance Guardrails

Company Brain is an organizational intelligence tool, NOT an employee surveillance platform.

### Strict Privacy Prohibitions
- **NO keystroke logging**
- **NO indiscriminate screenshot capture**
- **NO personal browsing history tracking**
- **NO covert or hidden activity monitoring**

### Compliance Rules
- Ingestion of user communications and activity must be **explicit, opt-in, work-related, and permission-aware**.
- Authorization rules must be strictly enforced at query time.
- Provide clear boundaries for data retention, audit logging, and data deletion per Workspace.

---

## 11. Important Anti-Patterns & Mistakes to Avoid

1. **Collapsing Subsystems**: Never combine Ingestion, Knowledge Compiler, Graph Construction, and Retrieval into a single monolithic script or agent.
2. **Prompt-Based Authorization**: Never rely on system prompts or LLM guardrails to enforce access control. Authorization must be computed deterministically before prompt construction.
3. **Unbounded Autonomous Execution**: Avoid unconstrained autonomous agent loops with write access to core infrastructure. Use deterministic state machines (e.g., LangGraph) with guarded transitions.
4. **Graph-Wide Analytics on Every Event**: Do not trigger expensive whole-graph community detection or global graph algorithms on every incoming event. Schedule analytics asynchronously.
5. **Speculative LLD Bloat**: Do not generate empty or half-baked subsystem documents in `docs/architecture/` before their design phase officially begins.

---

## 12. Completion Protocol

Before declaring any task complete, perform this checklist:

1. [ ] **Verify Scope**: Ensure changes directly address the prompt and stay within the current phase.
2. [ ] **Verify Layer Boundaries**: Confirm that dependencies flow inward and layers remain properly isolated.
3. [ ] **Run Test Suite**: Run `pytest` and verify that all unit and integration tests pass cleanly.
4. [ ] **Check Git State**: Inspect `git status` and `git diff` for accidental modifications or unwanted untracked files.
5. [ ] **Report Assumptions & Decisions**: Clearly communicate any architectural assumptions made, ADRs needed, or deferred items.
6. [ ] **Maintain Documentation**: If a significant architectural decision was made, update `docs/architecture/overview.md` or create an ADR in `docs/decisions/`.

---

## 13. AGENTS.md Modification Policy

- Do not modify this file automatically without explicit user instruction.
- Only update this file to record recurring, confirmed:
  - Repository operating rules
  - Critical architectural constraints
  - Coding conventions
  - Security guardrails
  - Verified pitfalls and anti-patterns
- Do not add temporary, task-specific instructions to this file.