# Company Brain — High-Level Design (HLD)

## 1. Document Purpose & Hierarchy

This document serves as the **High-Level Design (HLD)** and architectural source of truth for **Company Brain**. It defines what the system is, its product vision, core terminology, major components, data flows, and architectural boundaries.

### Documentation Hierarchy

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

Subsystem-specific Low-Level Design (LLD) documents (such as `database.md`, `authorization.md`, `knowledge-graph.md`, and `retrieval.md`) are created under `docs/architecture/` as each subsystem enters active design. This overview provides the unifying architectural framework for those detailed designs.

---

## 2. Product Vision

Company Brain is a **team-first organizational memory and collective intelligence platform**.

Modern teams suffer from severe context fragmentation: institutional knowledge is scattered across communication channels (Slack, Teams), code repositories (GitHub), documentation (Google Docs, Notion), and issue trackers. When teammates depart, work pivots, or new members join, valuable organizational context is lost.

Company Brain solves this by continuously ingesting collaborative team activity, retaining raw evidence, and compiling it into a **temporal, provenance-aware, permission-governed organizational knowledge graph**. Teammates and AI agents can query this knowledge layer to understand:
- *Why* technical and product decisions were made.
- *Who* has context on specific systems, domains, or past initiatives.
- *How* projects, tasks, code, and documentation relate to one another over time.

---

## 3. Core Concepts & Product Terminology

Company Brain is explicitly **team-first rather than enterprise-first**. It is designed to serve groups of all shapes and sizes without imposing rigid corporate hierarchies.

### Product Hierarchy

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

### Core Entity Definitions

| Concept | Description | Real-World Analogues |
| :--- | :--- | :--- |
| **`Workspace`** | Top-level administrative and tenancy boundary. Houses all users, teams, projects, and knowledge. | A startup, company, college team, hackathon team, research lab, or open-source community. |
| **`User`** | An individual participant within a Workspace. | A collaborator, engineer, researcher, student, or manager. Not assumed to be an "employee". |
| **`Team`** | A collaborative group of Users within a Workspace. | Backend team, UX pod, ML research squad, Hackathon subgroup. |
| **`Project`** | A scoped initiative, product track, or repository of work. | Product launch, feature revamp, repository, research paper. |
| **`Task`** | An actionable unit of work linked to a Project and assigned to Users. | GitHub issue, sprint ticket, milestone goal, action item. |

### Core Knowledge Concepts

- **`Raw Evidence`**: The original, unaltered content ingested from source systems (e.g., an email body, Slack message, GitHub commit diff, document revision).
- **`Normalized Event`**: A standardized schema representing an activity in time (`actor`, `action`, `resource`, `timestamp`, `source_payload_id`).
- **`Knowledge Graph`**: The derived property graph of entities, relationships, facts, and concepts synthesized from normalized events and evidence.
- **`Provenance`**: The verifiable audit trail linking every graph node, edge, and fact to the exact raw evidence and compilation run that generated it.
- **`Temporal Validity`**: The time window (`valid_from`, `valid_to`, `observed_at`) during which an organizational fact or relationship is considered true.

---

## 4. High-Level Architecture

The system follows an **evidence-first, asynchronous compilation pipeline** where raw data is preserved independently of the derived knowledge graph.

### Architectural Diagram

```text
       EXTERNAL DATA SOURCES
 (Google Workspace, M365, GitHub, Slack)
                   │
                   ▼
┌─────────────────────────────────────────┐
│        Connectors & Integrations        │
│  (OAuth, Webhooks, Resource Discovery)  │
└────────────────────┬────────────────────┘
                     │ Ingestion API / Queues
                     ▼
┌─────────────────────────────────────────┐
│       Raw Evidence & Event Store        │
│   (PostgreSQL / Object Store / Audit)   │
└────────────────────┬────────────────────┘
                     │ Async Worker Pipeline
                     ▼
┌─────────────────────────────────────────┐
│           Knowledge Compiler            │
│  ┌───────────────────────────────────┐  │
│  │ 1. Extraction (LLM + Heuristics)  │  │
│  │ 2. Entity & Identity Resolution   │  │
│  │ 3. Ontology Validation & Rules    │  │
│  │ 4. Graph & Vector Index Writing   │  │
│  └───────────────────────────────────┘  │
└──────────────┬──────────────────┬───────┘
               │                  │
               ▼                  ▼
┌───────────────────────┐  ┌───────────────────────┐
│    Knowledge Graph    │  │     Vector Store      │
│  (Nodes & Relations)  │  │ (Embeddings & Chunks) │
└──────────────┬────────┘  └──────────┬────────────┘
               │                      │
               └───────────┬──────────┘
                           ▼
┌──────────────────────────────────────────────────┐
│             Authorization Engine                 │
│      (Workspace / Team / Project ReBAC)          │
└──────────────────────────┬───────────────────────┘
                           ▼
┌──────────────────────────────────────────────────┐
│         Permission-Aware Retrieval               │
│    (Hybrid Graph + Vector + Temporal Filter)     │
└──────────────────────────┬───────────────────────┘
                           │
             ┌─────────────┴─────────────┐
             ▼                           ▼
┌─────────────────────────┐ ┌─────────────────────────┐
│   FastAPI Application   │ │     MCP Server          │
│   (REST API, UI/Agent)  │ │  (AI Tool Interfaces)   │
└─────────────────────────┘ └─────────────────────────┘
```

---

## 5. Major System Components

### 5.1 Connectors & Integrations
- Pluggable adapters that interface with external platforms (GitHub, Google Workspace, Microsoft 365, Slack).
- Responsible for authentication, token refresh, webhook subscription, full initial sync, and incremental delta synchronizations.
- Normalize external payloads into uniform `NormalizedEvent` records.

### 5.2 Raw Evidence & Event Store
- **Evidence-First Invariant**: Raw evidence is never mutated or discarded after graph extraction.
- Stores raw documents, threads, pull requests, and commit logs with cryptographic hash integrity.
- Provides the permanent foundation from which the knowledge graph can be recompiled or re-indexed with new models.

### 5.3 Knowledge Compiler
- A dedicated, asynchronous processing subsystem.
- **Stage 1 (Extraction)**: Extracts candidate entities, facts, and relationships using specialized heuristics and LLM prompts.
- **Stage 2 (Identity & Entity Resolution)**: Resolves ambiguous references (e.g., matching a GitHub username `@akanti` to a Workspace `User`).
- **Stage 3 (Ontology Validation)**: Enforces schema types, permitted relationship types, and temporal attributes using deterministic code.
- **Stage 4 (Persistence)**: Commits validated entities and edges to the Knowledge Graph and writes chunk embeddings to the Vector Store.

### 5.4 Knowledge Graph & Vector Store
- **Knowledge Graph**: Property graph storing nodes (`User`, `Team`, `Project`, `Task`, `Document`, `Topic`, `Decision`) and directional edges (`CONTRIBUTED_TO`, `DECIDED`, `ASSIGNED_TO`, `DEPENDS_ON`, `REFERENCES`).
- **Vector Store**: Semantic index of document chunks, discussion snippets, and community summaries for similarity search.

### 5.5 Graph Analytics Engine
- Asynchronous analytical engine operating over the knowledge graph.
- Executes community detection (e.g., Leiden algorithm), centrality scoring, dependency path discovery, and generates scoped community summaries.
- Graph analytics runs on an asynchronous schedule—never synchronously on every ingested event.

### 5.6 Authorization Engine
- Enforces Workspace boundaries and granular resource access (ReBAC / RBAC).
- Evaluates permissions deterministically **before** retrieval data is returned or supplied to an LLM context.
- Model evaluates:
  - Is User a member of Workspace $W$?
  - Does User have permission to access Project $P$, Team $T$, or Document $D$?
  - Are specific properties, facts, or evidence records restricted?

### 5.7 Permission-Aware Retrieval Engine
- Coordinates hybrid search:
  1. **Query Planning**: Parses user intent into graph traversal targets and semantic search terms.
  2. **Candidate Retrieval**: Fetches graph neighborhoods and vector matches.
  3. **Permission Filtering**: Deterministically strips any node, edge, or chunk the requesting user cannot view.
  4. **Fusion & Temporal Ranking**: Combines graph context, vector context, and temporal relevance.

### 5.8 Agent & LLM Orchestration
- Executes specialized workflows using deterministic state machines (e.g., LangGraph):
  - **Knowledge Agent**: Answers cross-functional queries with verified evidence links.
  - **Project Agent**: Summarizes status, blockers, and recent decisions for a project.
  - **Onboarding / Handover Agent**: Generates structured onboarding pathways from organizational history.
- LLMs are strictly bounded: they interpret language and formulate responses, but do not make authorization or state-change decisions.

### 5.9 External Interfaces: REST API & MCP Server
- **REST API (FastAPI)**: Primary interface for web clients, administrative controls, and system integration.
- **Model Context Protocol (MCP) Server**: Exposes Company Brain knowledge tools to external AI tools (such as Claude Desktop, Cursor, or custom IDE extensions) using the exact same retrieval and authorization pipeline as the REST API.

---

## 6. Major Data Flows

### Flow A: Ingestion & Raw Evidence Capture
```text
External Source ──(Webhook/Poll)──► Connector ──► Normalized Event + Raw Payload
                                                       │
                                                       ▼
                                            [Raw Evidence Store]
                                            [Event Store (Postgres)]
                                                       │
                                                       ▼
                                            Enqueue Compilation Task
```

### Flow B: Asynchronous Knowledge Compilation
```text
Event Task ──► Knowledge Compiler ──► Entity Extraction (LLM/Regex)
                                           │
                                           ▼
                                    Entity Resolution (Deterministic)
                                           │
                                           ▼
                                    Ontology Check (Deterministic)
                                           │
                                           ▼
                         ┌─────────────────┴─────────────────┐
                         ▼                                   ▼
             Write Nodes/Edges to Graph             Write Chunks to Vector DB
             (Neo4j / Graph Storage)                 (pgvector / Vector Index)
```

### Flow C: Query, Retrieval, and Grounded Answering
```text
User Request ──► FastAPI / MCP ──► Authenticate & Identify User / Workspace
                                           │
                                           ▼
                               Query Planner (Intent & Terms)
                                           │
                                           ▼
                        Hybrid Retrieval (Graph + Vector)
                                           │
                                           ▼
                     Deterministic Authorization Filter (ReBAC)
                                           │
                                           ▼
                               Context Assembly + Provenance
                                           │
                                           ▼
                               LLM Synthesis & Citation
                                           │
                                           ▼
                       Response with Evidence Links to User
```

---

## 7. Subsystem Architecture Summaries

### 7.1 Data Sources & Integrations
- Connectors run as isolated modules adhering to a standard interface (`BaseConnector`).
- Lifecycle operations: `authorize()`, `refresh_credentials()`, `discover_resources()`, `sync_incremental()`, `handle_webhook()`.
- Resilient backoff, rate limiting, and cursor-based pagination.

### 7.2 Raw Evidence & Provenance
- Raw data payloads stored in immutable storage (PostgreSQL JSONB / S3-compatible object store).
- Every evidence item receives a unique `evidence_id`, SHA-256 payload hash, and source metadata.
- All derived graph facts carry an `evidence_id` foreign key.

### 7.3 Knowledge Compiler
- Separated from the real-time API. Runs in worker processes.
- Maintains strict ontology rules: rejecting invalid entity types or unrecognized relationships.
- Resolves identities across providers (e.g., mapping `github:akanti` and `google:akanti@company.com` to `User:user_123`).

### 7.4 Knowledge Graph & Temporal Modeling
- Modeled as a directed property graph.
- Nodes represent domain entities (`Workspace`, `User`, `Team`, `Project`, `Task`, `Document`, `Decision`, `Concept`).
- Edges represent relationships with temporal intervals (`valid_from`, `valid_to`, `superseded_at`).
- Supports queries like "What was the architecture of Project X in Q2 2025 before Decision Y?"

### 7.5 Authorization Architecture
- Authorization is evaluated before retrieval candidates reach the LLM context.
- Fine-grained relationship model:
  - Users have roles within Workspaces (`admin`, `member`, `guest`).
  - Users belong to Teams and have explicit or inherited access to Projects.
  - Nodes and documents inherit permission attributes from their parent Workspace/Project.

### 7.6 Retrieval Architecture
- Avoids naive vector-only RAG.
- Combines semantic similarity (dense embeddings) with explicit structural traversal (graph edges) and temporal filters.
- Re-ranks results considering evidence recency, authority, and confidence.

### 7.7 Agent Workflows
- State machines with strict state boundaries.
- No single autonomous "super-agent"; instead, specialized task graphs with defined inputs, validation steps, and output schemas.
- Hallucination mitigation: All statements must cite underlying evidence IDs.

### 7.8 Model Context Protocol (MCP) Architecture
- MCP tools run against the existing application services.
- Never bypasses authorization or tenant boundaries.
- Enables external LLM environments to securely ask questions about the Workspace.

---

## 8. Major Technology Choices & Candidates

| Area | Current Choice / Candidate | Status | Rationale & Trade-offs |
| :--- | :--- | :--- | :--- |
| **Language & Framework** | Python 3.12+ / FastAPI | **Adopted** | High async performance, robust typing, mature AI and data ecosystem. |
| **Relational Database** | PostgreSQL 16+ | **Adopted** | Authoritative store for metadata, workspaces, users, projects, tasks, event logs, and raw evidence metadata. |
| **Graph Database** | Neo4j | *Candidate* | Rich Cypher query capabilities, graph algorithms, and maturity. Evaluated against Postgres graph extensions. |
| **Vector Storage** | pgvector (PostgreSQL) | *Candidate* | Keeps operational complexity minimal during early phases by unifying relational and vector data in Postgres. |
| **Authorization** | OpenFGA / Zanzibar model | *Candidate* | Highly scalable relationship-based access control. Evaluated against native Postgres row-level policies. |
| **Agent Orchestration** | LangGraph | *Candidate* | Provides stateful, deterministic graph workflows with cyclic support and human-in-the-loop checkpoints. |
| **Message Queue** | Redis / PostgreSQL LISTEN | *Candidate* | Asynchronous job dispatch for ingestion and compilation workers. |

*Note: Technologies designated as "Candidate" remain subject to evaluation during their respective implementation phases. No infrastructure will be adopted without an explicit decision.*

---

## 9. Major Architectural Boundaries

1. **Raw Evidence vs. Derived Knowledge**: Raw events and evidence are permanent and immutable. The knowledge graph is derived state and can be regenerated.
2. **Deterministic Code vs. Probabilistic LLMs**: Deterministic software handles authorization, persistence, entity resolution confirmation, and graph integrity. LLMs handle text extraction, summarization, and query reasoning.
3. **Pre-Retrieval Authorization**: Authorization logic sits in front of the retrieval pipeline, ensuring unprivileged data is pruned before prompt construction.
4. **Layer Decoupling**: API handlers (`api/`) and MCP adapters (`api/mcp/`) are thin wrappers around application use cases (`application/`), with business rules isolated in `domain/`.

---

## 10. Subsystem Low-Level Design (LLD) Roadmap

As implementation progresses through designated phases, detailed LLD documents will be created under `docs/architecture/`:

```text
docs/
└── architecture/
    ├── overview.md              ◄ This document (HLD Source of Truth)
    ├── database.md              ◄ Detailed relational tables, columns, indexes, migrations
    ├── authorization.md         ◄ ReBAC/RBAC schemas, permission tuples, enforcement logic
    ├── knowledge-graph.md       ◄ Entity types, relationship schemas, ontology, Cypher specs
    ├── knowledge-compiler.md    ◄ Extraction prompts, resolution algorithms, compilation stages
    ├── retrieval.md             ◄ Hybrid search, ranking, temporal fusion, context builder
    ├── integrations.md          ◄ Connector specs (GitHub, Google, M365), webhook protocols
    ├── agents.md                ◄ Specialized agent state graphs, tools, prompts
    └── mcp.md                   ◄ MCP tool schemas, transport protocols, client integrations
```

*Rule: LLD documents are not created speculatively. They are authored when their corresponding subsystem is scheduled for active design and implementation.*

---

## 11. Security, Privacy & Non-Goals

### Privacy & Security Guarantees
- **Explicit & Opt-in**: Data sources must be explicitly connected by Workspace administrators or users.
- **Work-Related Context Only**: Ingestion focuses strictly on designated project repositories, team channels, and shared workspace documents.
- **Tenant Isolation**: Strict logical data separation between Workspaces. No cross-workspace data leakage.

### Explicit Non-Goals
- **No Employee Surveillance**: No keystroke logging, no indiscriminate screen recording, no personal web browsing tracking, and no covert activity monitoring.
- **No Autonomous Infrastructure Mutation**: Agents will not have unsupervised write permissions to execute arbitrary code or alter production infrastructure.
- **No Unfiltered Prompt Hiding**: We will never retrieve unauthorized records with the expectation that an LLM prompt will reliably conceal them from the user.