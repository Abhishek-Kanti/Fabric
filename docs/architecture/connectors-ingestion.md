# Connectors & Ingestion Low-Level Design (LLD)

## 1. Document Overview & Context

This document is the **Low-Level Design (LLD)** for the **Connectors, Webhooks, Ingestion Pipeline, Raw Evidence Store, Normalized Events, Connector Resource Mapping, and Deletion Lifecycle** subsystem of **Company Brain**. It builds directly upon the High-Level Design in [`docs/architecture/overview.md`](file:///F:/Projects/Fabric/docs/architecture/overview.md), the repository operating manual in [`AGENTS.md`](file:///F:/Projects/Fabric/AGENTS.md), the relational persistence layer in [`docs/architecture/database.md`](file:///F:/Projects/Fabric/docs/architecture/database.md), the identity architecture in [`docs/architecture/identity.md`](file:///F:/Projects/Fabric/docs/architecture/identity.md), and the authorization model in [`docs/architecture/authorization.md`](file:///F:/Projects/Fabric/docs/architecture/authorization.md).

Company Brain is explicitly **team-first rather than enterprise-only**. It continuously captures collaborative activity across diverse tools:
- **GitHub** (Repositories, Commits, Pull Requests, Reviews, Issues, Discussions)
- **Google Workspace** (Google Drive Docs, Sheets, Gmail threads, Calendar events)
- **Microsoft 365 / Teams** (Teams channel messages, chat threads, SharePoint docs, Outlook)
- **Slack** (Channel discussions, threads, canvases)

```text
┌────────────────────────────────────────────────────────────────────────┐
│                   CONNECTORS & INGESTION SUBSYSTEM                     │
├────────────────────────────────────────────────────────────────────────┤
│ 1. Connectors            Pluggable adapters for external platforms     │
│                          (OAuth refresh, API clients, rate limiters)   │
├────────────────────────────────────────────────────────────────────────┤
│ 2. Resource Scoping      Persisted connector_resources defining        │
│                          authoritative project & workspace mappings    │
├────────────────────────────────────────────────────────────────────────┤
│ 3. Ingestion Receivers   Synchronous durable webhook inbox (< 3s SLA)  │
│                          & cursor-based delta polling workers          │
├────────────────────────────────────────────────────────────────────────┤
│ 4. Tiered Evidence Store Layered immutability: Tier 1 wire quarantine, │
│                          Tier 2 sanitized raw evidence with SHA-256    │
├────────────────────────────────────────────────────────────────────────┤
│ 5. Normalized Events     Standardized domain activity records with     │
│                          actor metadata ready for compilation queue    │
├────────────────────────────────────────────────────────────────────────┤
│ 6. Centralized IAM       Sovereign AuthorizationService governing      │
│    Integration           pre-retrieval filtering & graph safety        │
├────────────────────────────────────────────────────────────────────────┤
│ 7. Erasure Lifecycle     Comprehensive deletion across all 9 storage   │
│                          locations (GDPR, CCPA, retention policies)    │
└────────────────────────────────────────────────────────────────────────┘
```

> [!IMPORTANT]
> **Design-First Invariant**: This document is an architectural design and specification. **No connector code, webhook endpoints, database models, or migration scripts will be written in this phase.**

---

## 2. Core Architectural Principles & Invariants

Every component in the Ingestion and Connector pipeline must strictly adhere to these invariants:

1. **Evidence First (Layered Immutability & Provenance)**:
   - Raw source evidence is captured and preserved with cryptographic integrity.
   - The Knowledge Graph and Vector Store are derived state that can be recompiled or re-indexed from sanitized evidence at any time.
   - We maintain a strict boundary between Tier 1 (restricted, encrypted wire buffer), Tier 2 (sanitized raw evidence), Tier 3 (normalized events), and Tier 4 (derived graph/vector indexes).
2. **Full Provenance & Dual Content Hashing**:
   - Every ingested event tracks two hashes:
     - `raw_wire_sha256`: Cryptographic hash of the exact wire payload received from the provider, used for signature audit and transport deduplication.
     - `payload_sha256`: Cryptographic hash of the sanitized payload envelope, used for immutable storage deduplication, graph provenance, and downstream citations.
   - Downstream graph nodes, facts, and embeddings maintain a direct foreign key link back to the originating `evidence_id`.
3. **Deterministic Ingestion Boundary (Zero LLMs in the Ingestion Path)**:
   - Ingestion is **100% deterministic software**: signature validation, payload parsing, pre-storage secret sanitization, deduplication, raw persistence, event normalization, and outbox dispatch.
   - LLMs and heuristic extraction belong strictly to the downstream **Knowledge Compiler** (Flow B), which runs asynchronously. No LLM calls occur during the real-time webhook or ingestion path.
4. **Strict Workspace Tenancy & Pre-Ingestion Verification**:
   - Every external connection, webhook endpoint, evidence record, and normalized event is strictly bound to a `workspace_id`.
   - Before accepting any webhook payload, the endpoint verifies connection ownership and workspace association. Cross-workspace routing is physically prohibited.
5. **Durable Synchronous Ingestion Before Acknowledgement**:
   - An inbound webhook must **never** be acknowledged with `202 Accepted` or `200 OK` until its payload has been **durably committed** to the PostgreSQL inbox (`raw_evidence_inbox`).
   - Reliability guarantees at-least-once processing under crash-recovery assumptions (PostgreSQL ACID persistence and WAL).
6. **Explicit Four-Layer Permission Model & Attested Redistribution**:
   - Connector data access is governed by four distinct layers: (1) provider grants, (2) admin allowlists, (3) connector effective access, and (4) Company Brain user retrieval permissions.
   - **Workspace membership alone does NOT grant access to every ingested resource.** Ingested private resources remain restricted to authorized project members.
   - Mapping an external container to a broader project is an explicit act of administrative redistribution requiring formal administrator attestation.
7. **Identity Resolution Boundary**:
   - Ingestion preserves immutable provider subject identifiers and raw metadata.
   - Ingestion workers and LLMs must **never** automatically merge external authors into canonical `User` records or make authorization decisions.
8. **Operational Immutability vs. Lawful Erasure & Retention**:
   - Normal pipeline operations do not edit evidence in-place.
   - Operational immutability does not prevent compliance with statutory erasure obligations (e.g. GDPR Article 17 / CCPA) or workspace retention policies. Erasure requests propagate through all 9 storage locations via defined, verifiable deletion workflows, respecting lawful exceptions where applicable.
9. **Authoritative Source Systems**:
   - External platforms remain authoritative for deep source payloads (e.g. full git trees, binaries). Company Brain ingests textual evidence, metadata, diffs, comments, and references.
10. **System Worker Isolation**:
    - Ingestion workers operate under an internal `SystemContext` (write-only pipeline). They cannot invoke user-facing retrieval or query endpoints.
11. **Privacy Guardrails**:
    - In accordance with [`AGENTS.md` Section 10](file:///F:/Projects/Fabric/AGENTS.md), Company Brain strictly prohibits keystroke logging, screen capture, personal browsing tracking, and covert activity monitoring.

---

## 3. Connector Lifecycle & Unified Architecture (`BaseConnector`)

### 3.1 The Pluggable Connector Model

All external integrations implement the abstract interface `BaseConnector`. This decouples the core ingestion engine from provider-specific APIs:

```mermaid
classDiagram
    class BaseConnector {
        <<interface>>
        +provider_name: str
        +authenticate(connection_config) AuthCredentials
        +refresh_credentials(connection) AuthCredentials
        +verify_webhook_signature(headers, body, secret) bool
        +discover_resources(connection) List[ResourceDescriptor]
        +sync_initial(connection, resource, cursor) SyncBatchResult
        +sync_delta(connection, resource, cursor) SyncBatchResult
        +handle_webhook(headers, body) List[RawEvidenceDraft]
    }

    class GitHubConnector {
        +verify_webhook_signature()
        +sync_delta()
        +handle_webhook()
    }
    class GoogleWorkspaceConnector {
        +verify_webhook_signature()
        +sync_delta()
        +handle_webhook()
    }
    class Microsoft365Connector {
        +verify_webhook_signature()
        +sync_delta()
        +handle_webhook()
    }
    class SlackConnector {
        +verify_webhook_signature()
        +sync_delta()
        +handle_webhook()
    }

    BaseConnector <|-- GitHubConnector
    BaseConnector <|-- GoogleWorkspaceConnector
    BaseConnector <|-- Microsoft365Connector
    BaseConnector <|-- SlackConnector
```

### 3.2 Connector Connection Lifecycle State Machine

A connector instance (e.g., "Stanford Robotics GitHub Connection") transitions through distinct lifecycle states:

```mermaid
stateDiagram-v2
    [*] --> PENDING_AUTH : Admin Initiates OAuth / API Setup
    PENDING_AUTH --> ACTIVE : Valid Credentials & Permissions Granted
    ACTIVE --> SYNCING : Initial or Delta Sync in Progress
    SYNCING --> ACTIVE : Batch Sync Complete / Idle
    ACTIVE --> DEGRADED : Rate Limited / Token Expired / Transient 5xx
    DEGRADED --> ACTIVE : Token Refreshed / Rate Limit Window Reset
    DEGRADED --> REVOKED : Refresh Token Invalid / Scopes Revoked
    ACTIVE --> REVOKED : Admin Disconnects / Uninstalls App
    REVOKED --> [*] : Connection Archived (Evidence Retained)
```

| Lifecycle State | Description | Webhook Processing | Polling / Sync Allowed |
| :--- | :--- | :---: | :---: |
| **`PENDING_AUTH`** | Connection initiated; awaiting OAuth completion or admin consent | NO | NO |
| **`ACTIVE`** | Credentials healthy; receiving webhooks and scheduling delta syncs | **YES** | **YES** |
| **`SYNCING`** | Background sync actively paging through provider history | **YES** | **YES** |
| **`DEGRADED`** | Backing off due to 429 rate limit or waiting for automated token refresh | **YES** (Inbox buffer) | Paused temporarily |
| **`REVOKED`** | OAuth token revoked or app uninstalled by customer admin | NO | NO |

### 3.3 Proposed Entity Specification: `connector_connections`

```text
connector_connections
├── id (UUIDv7, PK)
├── workspace_id (UUIDv7, FK -> workspaces.id ON DELETE RESTRICT)
├── provider (VARCHAR(50): 'github', 'google', 'microsoft', 'slack')
├── name (VARCHAR(255), e.g. "Acme Corp GitHub Org")
├── status (VARCHAR(30): 'pending_auth', 'active', 'syncing', 'degraded', 'revoked')
├── external_account_id (VARCHAR(255), External Org/Tenant ID, e.g. GitHub Org ID or M365 Tenant ID)
├── auth_type (VARCHAR(30): 'oauth2_app', 'github_app', 'service_account', 'webhook_only')
├── encrypted_credentials (BYTEA, Encrypted tokens/keys via AES-256-GCM)
├── webhook_secret (BYTEA, Encrypted webhook signing secret)
├── config (JSONB, Connection settings, sync frequency)
├── last_synced_at (TIMESTAMPTZ, Timestamp of last successful delta sync)
├── created_at (TIMESTAMPTZ)
├── updated_at (TIMESTAMPTZ)
└── deleted_at (TIMESTAMPTZ, Soft-disconnect timestamp)
```

#### Relational Constraints:
- `FOREIGN KEY (workspace_id) REFERENCES workspaces(id) ON DELETE RESTRICT`
- `UNIQUE (workspace_id, provider, external_account_id) WHERE deleted_at IS NULL`
- `INDEX (workspace_id, provider, status) WHERE deleted_at IS NULL`

---

## 4. Connector Resource Permissions, Scoping & Redistribution Policy

### 4.1 Provider-Specific Resource Scoping

Connector access is scoped across four external platforms:

1. **Slack**:
   - *Resources*: Public channels, private channels (only where the Company Brain bot is explicitly invited), canvases, threads.
   - *Direct Messages (DMs)*: Excluded by default. Ingestion of 1:1 or multi-party DMs requires explicit multi-party consent and workspace admin enablement.
2. **GitHub**:
   - *Resources*: Repositories (selected repositories or all repositories in organization), pull requests, issues, discussions, commits, review comments.
   - *Distinction*: Public vs. private repositories; organization vs. personal forks.
3. **Google Workspace**:
   - *Resources*: Google Drive files, folders, shared drives, Google Drive permission metadata, Gmail project-labeled threads (opt-in), Calendar events.
   - *Distinction*: My Drive vs. Shared Drives; explicitly shared folders vs. entire domain drive.
4. **Microsoft 365 / Teams**:
   - *Resources*: Teams, channels (standard vs. private vs. shared channels), SharePoint document libraries, Outlook shared/project mailboxes.
   - *Distinction*: Tenant-wide directory vs. specific joined Teams and SharePoint sites.

### 4.2 The Four Critical Layers of Permission

To eliminate ambiguity and prevent unintended data exposure, Company Brain defines four distinct permission layers:

```mermaid
graph TD
    L1["1. Provider Grants<br/>(OAuth scopes & tenant admin consent)"]
    L2["2. Workspace Admin Selection<br/>(Allowed repositories, channels, folders)"]
    L3["3. Connector Effective Access<br/>(Intersection: L1 ∩ L2 ∩ Bot Membership)"]
    L4["4. User Retrieval Permissions<br/>(Company Brain Sovereign ReBAC: Project & Workspace Roles)"]

    L1 --> L2 --> L3 -->|Ingests Evidence| Store["Raw Evidence & Knowledge Graph"]
    Store -->|Pre-Retrieval Filter| L4
```

| Layer | Defined By | Description | Example |
| :--- | :--- | :--- | :--- |
| **1. Provider Grants** | External IdP / Tenant Admin | Maximum technical permissions granted to the application during installation or OAuth consent. | GitHub App installed with `issues:read`, `pull_requests:read`, `contents:read` on Acme Org. |
| **2. Admin Selection** | Company Brain Workspace Admin | Explicit allowlists configured in Company Brain selecting which specific external resources to sync. | Admin selects only `acme/slam-engine` and `#slam-dev` channel. |
| **3. Connector Effective Access** | Mathematical Intersection | The actual resources the connector can read: `Provider Grant ∩ Admin Allowlist ∩ Bot Membership`. | Connector can only read `#slam-dev` if bot has been invited, even if admin selected it. |
| **4. User Retrieval Permissions** | Company Brain `AuthorizationService` | Access rights evaluated when a Company Brain user queries organizational memory. | A user can only see `acme/slam-engine` evidence if they are authorized on the mapped Company Brain project. |

---

### 4.3 The Source-Permissions Policy: Redistribution & Administrator Attestation

A critical architectural question is whether Company Brain's sovereign project mapping may expose ingested content to people who could not access it in the original source system.

#### The Reality: Project Mapping is an Act of Redistribution
**Yes, by design.** In a project-mapping model, mapping an external container to a Company Brain project means that all authorized members of that project can query the organizational memory derived from that container.

#### Concrete Example: Private Slack Channel Mapped to a Broader Project
- Consider a private Slack channel `#slam-leads` containing **5 senior engineers**.
- A Workspace Administrator maps `#slam-leads` to the Company Brain project `SLAM Core`.
- Project `SLAM Core` contains **20 members** (including 15 junior developers and cross-functional collaborators who are **not** in `#slam-leads` in Slack).
- **Result**: Under Company Brain's sovereign ReBAC model, any of the 20 members authorized on `SLAM Core` can retrieve organizational facts, code references, and summaries originated from `#slam-leads`.

#### Security Policy & Administrative Guardrails:
1. **Administrative Authority & Attestation**:
   - A Workspace Administrator has the platform-level capability to map resources.
   - However, because mapping an external private container to a project with broader membership redistributes private discussions to a wider group, the mapping interface requires an **explicit administrative attestation**:
     ```text
     [!] REDISTRIBUTION WARNING
     External Resource: Slack Private Channel "#slam-leads" (5 members in Slack)
     Target Project:    Company Brain Project "SLAM Core" (20 members in Company Brain)

     Mapping this private channel to "SLAM Core" will make all ingested discussions,
     decisions, and context queryable by all 20 project members.

     [X] I confirm that I am authorized to share this channel's context with Project "SLAM Core".
     ```
   - The attestation is recorded in the audit log: `{action: 'connector:map_resource', attested_by: user_id, expanded_audience: true}`.
2. **Handling Restrictions That Cannot Safely Be Represented by a Broad Project**:
   - If an external resource contains sensitive discussions (e.g. executive compensation in `#exec-comp`, confidential HR records, or restricted patent drafts) that must **not** be visible to all members of a general project:
     - **Rule A (Dedicated Scoped Project)**: The administrator must create a dedicated private Company Brain Project (e.g., `Executive Compensation` or `Patents`) with restricted membership matching the confidential group, and map the resource there.
     - **Rule B (Internal Only / Quarantined)**: The resource can be ingested with `default_visibility = 'internal_only'` and `mapped_project_id = NULL`. In this state, it is completely hidden (`DENY`) from general user search and LLM context.
     - **Rule C (Allowlist Exclusion)**: The administrator can exclude the container from the connector allowlist entirely (Layer 2), so its payloads are never ingested into Company Brain.
3. **Handling Source Permission Changes After Ingestion**:
   - Third-party permissions and channel memberships mutate constantly.
   - Company Brain **does NOT continuously mirror external ACL changes into Company Brain project memberships**.
   - If Alice is removed from `#slam-leads` in Slack, but remains an active member of `SLAM Core` in Company Brain, Alice **continues** to have access to historical and ongoing `SLAM Core` knowledge.
   - Conversely, if Bob is added to `#slam-leads` in Slack, Bob does **not** gain access in Company Brain until an administrator or project lead adds Bob to Project `SLAM Core`.
   - When Alice departs the team, an administrator removes Alice from Project `SLAM Core` (or deactivates Alice in Company Brain), which **immediately and deterministically terminates Alice's retrieval access** across all mapped evidence, vector chunks, and graph nodes in real time.
4. **Trade-Off Analysis: Sovereign Model vs. Continuous ACL Mirroring**:
   - *Why Full ACL Mirroring is Rejected for V1*: Attempting to replicate live ACL graphs from Slack, Google Drive, GitHub, and Microsoft 365 causes severe authorization sync lag, permission drift, race conditions, edge-case leaks, and massive API rate-limit exhaustion.
   - *Security Consequence*: Access relies on the integrity of Company Brain's project memberships and the deliberate mapping choices of administrators.
   - *Recommended V1 Policy (Decision Requiring Approval)*:
     - **Adopt Administrator-Attested Sovereign Project Mapping**.
     - **Default to Deny for Unmapped Resources**: All newly discovered external containers default to `mapped_project_id = NULL` and `default_visibility = 'internal_only'` (DENY for all regular users). Content becomes queryable only after an explicit, attested administrative mapping.

---

### 4.4 Persisted Resource Mapping: `connector_resources`

To establish an authoritative bridge between external source containers and internal Company Brain authorization domains, we introduce the **`connector_resources`** model:

```text
connector_resources
├── id (UUIDv7, PK)
├── workspace_id (UUIDv7, FK -> workspaces.id ON DELETE RESTRICT)
├── connection_id (UUIDv7, FK -> connector_connections.id ON DELETE RESTRICT)
├── provider_resource_id (VARCHAR(255), External ID, e.g. 'repo:4182931', 'channel:C0123456')
├── resource_type (VARCHAR(50): 'repository', 'channel', 'folder', 'shared_drive')
├── display_name (VARCHAR(255), Human-readable name, e.g. "acme/slam-engine", "#robotics-dev")
├── sync_scope (VARCHAR(50): 'all', 'prs_only', 'discussions_only', 'read_only')
├── mapped_project_id (UUIDv7, Nullable FK -> projects.id ON DELETE SET NULL)
├── default_visibility (VARCHAR(30): 'project_scoped', 'workspace_global', 'internal_only')
├── sync_status (VARCHAR(30): 'active', 'paused', 'error')
├── sync_cursor (JSONB, High-water marks, change tokens, deltaLinks for this specific resource)
├── last_synced_at (TIMESTAMPTZ, Last successful sync timestamp)
├── created_at (TIMESTAMPTZ)
├── updated_at (TIMESTAMPTZ)
└── deleted_at (TIMESTAMPTZ, Soft-deletion timestamp)
```

#### Relational Constraints:
- `FOREIGN KEY (workspace_id, connection_id) REFERENCES connector_connections(workspace_id, id) ON DELETE RESTRICT`
- `FOREIGN KEY (workspace_id, mapped_project_id) REFERENCES projects(workspace_id, id) ON DELETE SET NULL`
- `UNIQUE (workspace_id, connection_id, provider_resource_id) WHERE deleted_at IS NULL`
- `INDEX idx_connector_resources_lookup ON connector_resources(workspace_id, connection_id, resource_type)`
- `INDEX idx_connector_resources_project ON connector_resources(workspace_id, mapped_project_id) WHERE deleted_at IS NULL`

#### Schema Change vs. Conceptual Decision Analysis:
1. **Schema Changes Required**:
   - The creation of the `connector_resources` table with composite foreign key constraints linking to `workspaces`, `connector_connections`, and `projects`.
   - Adding `connector_resource_id` (UUIDv7, FK -> `connector_resources.id`) to `raw_evidence` and `normalized_events`.
2. **Conceptual Decisions (Policy)**:
   - **One-to-One / Many-to-One Project Mapping**: An external resource (e.g. GitHub repo) maps authoritatively to at most one Company Brain Project (or workspace-global) at a given time. Multiple external resources (e.g. repo `slam-core` and Slack channel `#slam-team`) can map to the same Company Brain Project (`SLAM Core`).
   - **Default Visibility Fallback**: If an external resource is mapped to `mapped_project_id = NULL`:
     - If `default_visibility = 'workspace_global'`, non-guest workspace members can read it.
     - If `default_visibility = 'internal_only'` or `project_scoped`, it defaults strictly to **DENY** for all non-admin users until explicitly mapped.
     - In adherence to [`authorization.md` Section 3.2](file:///F:/Projects/Fabric/docs/architecture/authorization.md), `mapped_project_id IS NULL` alone **NEVER** grants access.

### 4.5 Non-Authoritative Status of `project_hint`

Inbound messages, git commit text, or pull request descriptions may contain project references (e.g. `fixes PROJ-142`, `[Project Apollo]`, or `#apollo-channel`).
- These values are captured in `normalized_events.context.project_hint` as heuristic metadata.
- **Strict Invariant**: A `project_hint` extracted from source text or suggested by an LLM is **never** an authoritative mapping and must **never** grant access or set `mapped_project_id`.
- Authoritative mapping derives strictly from an explicit administrative assignment in `connector_resources.mapped_project_id`.

---

## 5. Retrieval Authorization, Graph Safety & Lifecycle Propagation

### 5.1 Pre-Retrieval Filtering via `AuthorizationService`

In accordance with [`AGENTS.md` Invariant 5](file:///F:/Projects/Fabric/AGENTS.md) and [`authorization.md` Section 6](file:///F:/Projects/Fabric/docs/architecture/authorization.md):
> **Authorization filtering must occur BEFORE unauthorized information reaches an LLM prompt.**

When a user or agent issues a query:
1. The application calls `AuthorizationService.get_accessible_scope(identity)`.
2. The service compiles the user's accessible `accessible_project_ids` and `is_not_guest` boolean in a single indexed SQL CTE query.
3. The resulting scope is injected into the vector search and graph retrieval operations:

```sql
-- Vector retrieval pre-filtering enforcing connector resource mapping
SELECT vc.id, vc.content, vc.embedding <=> :query_vector AS distance
FROM vector_chunks vc
JOIN raw_evidence re ON re.id = vc.evidence_id AND re.workspace_id = vc.workspace_id
JOIN connector_resources cr ON cr.id = re.connector_resource_id AND cr.workspace_id = re.workspace_id
WHERE vc.workspace_id = :workspace_id
  AND vc.deleted_at IS NULL
  AND re.deleted_at IS NULL
  AND cr.deleted_at IS NULL
  AND (
      -- 1. Project-scoped resource: must belong to user's accessible projects
      (cr.mapped_project_id IS NOT NULL AND cr.mapped_project_id = ANY(:accessible_project_ids))
      OR
      -- 2. Workspace-global resource: requires explicit workspace_global visibility and non-guest role
      (cr.default_visibility = 'workspace_global' AND :is_not_guest = TRUE)
  )
ORDER BY distance ASC
LIMIT :top_k;
```

### 5.2 Knowledge Graph Traversal Safety (Option A Adopted)

When traversing the Knowledge Graph derived from connector evidence:
- **Path Node Authorization**: Every intermediate node and target node must belong to the user's accessible scope.
- **Unauthorized Intermediate Hop Prohibition**: An unauthorized private or project-scoped node must **never** serve as an intermediate traversal hop merely because the start or end nodes are authorized.
- **Endpoint-Derived Relationship Authorization (Option A)**: A relationship is traversable if and only if:
  1. `rel.workspace_id == $workspace_id`,
  2. Both endpoint nodes are authorized for the user, and
  3. `type(rel) IN $allowed_relationship_types`.

### 5.3 Source Evidence & Citations Access

When an LLM generates an answer citing source evidence:
- Citations return `external_url` and short snippets only if the underlying `evidence_id` belongs to a `connector_resource` the user is authorized to read.
- If a user lacks permission to the source project, the citation is stripped before the response leaves the service layer, preventing information leakage via citations.

### 5.4 Full MCP & Agent Parity

In accordance with [`AGENTS.md` Invariant 8](file:///F:/Projects/Fabric/AGENTS.md):
- Model Context Protocol (MCP) tools and AI background agents execute through the exact same `AuthorizationService.get_accessible_scope(identity)` pipeline.
- There are zero backdoors or administrative bypasses for agents.

---

### 5.5 Comprehensive Resource Lifecycle & Retrieval Matrix

To guarantee consistent authorization across all states, retrieval behavior is strictly defined:

| Resource Lifecycle State | Ingestion Pipeline Behavior | User Retrieval Behavior | Rationale / Enforcement |
| :--- | :--- | :--- | :--- |
| **Unmapped** (`mapped_project_id IS NULL`, `default_visibility = 'internal_only'`) | Ingestion active; evidence saved to Tier 2 storage | **DENY** (Hidden from search, vector, graph, MCP, agents) | Default-deny posture. Only Workspace Admins can view resource in connector settings. |
| **Workspace Global** (`mapped_project_id IS NULL`, `default_visibility = 'workspace_global'`) | Ingestion active | **ALLOW** for non-guest workspace members; **DENY** for guests | Explicitly classified workspace knowledge. |
| **Paused** (`sync_status = 'paused'`) | Ingestion and polling halted | **ALLOW** for authorized project members | Historical evidence remains valid organizational memory. |
| **Disconnected / Soft-Deleted** (`cr.deleted_at IS NOT NULL`) | Ingestion halted | **Mode A (Default)**: Evidence preserved under Project ReBAC.<br/>**Mode B (Purge)**: Triggers erasure across all stores. | Admin selects whether to retain historical project memory or purge completely. |
| **Revoked Connection** (`status = 'revoked'`) | Ingestion and webhooks halted | **ALLOW** for authorized project members | Historical evidence is preserved unless explicitly purged. |
| **Mapped to Deleted Project** (`projects.deleted_at IS NOT NULL`) | Ingestion halted or unmapped | **IMMEDIATE DENY** across all retrieval paths | `AccessibleScope` CTE requires `p.deleted_at IS NULL`; project ID drops from allowed scope instantly. |
| **Mapped to Archived Project** (`projects.status = 'archived'`) | Ingestion paused or read-only | **READ-ONLY ALLOW** for project members | Historical context remains readable; mutations to project/tasks are rejected. |

---

## 6. Identity Resolution Boundary & Preservation

### 6.1 Preservation of Provider Subject Identifiers

In accordance with [`identity.md` Section 7 & 8](file:///F:/Projects/Fabric/docs/architecture/identity.md):
- Ingestion and normalization capture the provider's immutable subject identifier:
  - **GitHub**: Integer User ID (`actor.provider_subject_id = "4182931"`, immutable; not username).
  - **Google Workspace**: OIDC `sub` claim (`actor.provider_subject_id = "102938475619283746510"`).
  - **Microsoft 365**: Entra ID `oid` (`actor.provider_subject_id = "c2b3a4d5-e6f7-..."`).
  - **Slack**: Team-scoped User ID (`actor.provider_subject_id = "U0123ABCD"`).
- Ingestion also preserves raw author strings, commit emails, and display names as unverified evidence metadata.

### 6.2 Zero Automatic Merging Invariant

- **Strict Invariant**: Ingestion workers and normalization pipelines must **never** automatically merge external authors into canonical Company Brain `User` records.
- Matching commit emails or display names does **not** create or alter an authenticated user.
- LLMs are strictly prohibited from mutating user identity tables or creating authoritative links.

### 6.3 Three-Tier Graph Authorship Representation

When the downstream Knowledge Compiler builds authorship edges in the Knowledge Graph, it strictly adheres to the three tiers defined in [`identity.md` Section 7.2](file:///F:/Projects/Fabric/docs/architecture/identity.md):

```text
┌────────────────────────────────────────────────────────────────────────┐
│ 1. AUTHORITATIVE (Confidence = 1.0)                                     │
│    (:Artifact)-[:AUTHORED_BY]->(:User)                                 │
│    - Backed by immutable provider_subject_id linked to a verified       │
│      external_identity record, a verified GPG/SSH key, or admin confirm│
├────────────────────────────────────────────────────────────────────────┤
│ 2. PROBABLE / CANDIDATE (Confidence ∈ [0.50, 0.99])                    │
│    (:Artifact)-[:POSSIBLY_AUTHORED_BY {confidence, method, evidence}]->(:User) │
│    - Heuristic match (commit email string, display name match, LLM hint)│
│    - Searchable as tentative; promoted ONLY via user/admin confirmation│
├────────────────────────────────────────────────────────────────────────┤
│ 3. UNRESOLVED (Confidence < 0.50)                                      │
│    (:Artifact)-[:HAS_EXTERNAL_AUTHOR]->(:ExternalAuthor {raw_email})    │
│    - Distinct external node; AUTHORED_BY is never used for unresolved   │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 7. Layered Storage, Secret Sanitization & Complete Erasure Lifecycle

### 7.1 Reconciling Webhook Inbox Durability with Secret Sanitization

To resolve the contradiction between **verifying raw wire signatures**, **persisting webhooks durably before HTTP acknowledgement**, and **preventing unsanitized credentials from entering ordinary application storage**, Company Brain establishes a strict **four-tier storage hierarchy**:

```text
┌────────────────────────────────────────────────────────────────────────┐
│ TIER 1: Restricted Ingestion Quarantine Buffer (raw_evidence_inbox)    │
│ - Exact raw request bytes received over the wire.                      │
│ - Encrypted at rest via AES-256-GCM (restricted KMS envelope key).     │
│ - Access Boundary: Strictly isolated infrastructure table. ZERO        │
│   application endpoints, search queries, vector engines, or LLMs       │
│   have access to this table. Accessible ONLY to WebhookWorker.         │
│ - Dual Hash: Computes raw_wire_sha256 for transport deduplication      │
│   and HMAC verification audit.                                         │
│ - Short-Lived TTL: Upon successful Tier 2 processing, payload body is   │
│   immediately nullified/truncated (raw_payload = NULL), retaining only │
│   delivery metadata. Max retention for failed entries: 7 days.         │
├────────────────────────────────────────────────────────────────────────┤
│ TIER 2: Sanitized Raw Evidence (raw_evidence)                          │
│ - Deterministically sanitized of API keys, tokens, and private keys    │
│   BEFORE relational or object persistence.                             │
│ - Computes sanitized payload_sha256 over sanitized envelope.           │
│ - Primary operational store for evidence, graph linking, & citations.  │
│ - Operational Immutability: Never mutated in-place during pipeline.    │
├────────────────────────────────────────────────────────────────────────┤
│ TIER 3: Normalized Events (normalized_events)                          │
│ - Standardized domain activity envelopes decoupling downstream logic.  │
├────────────────────────────────────────────────────────────────────────┤
│ TIER 4: Derived Knowledge & Vector Indexes                             │
│ - Graph nodes, edges, vector chunks, and retrieval caches.             │
│ - Recompilable and re-indexable from Tier 2 evidence at any time.      │
└────────────────────────────────────────────────────────────────────────┘
```

#### Deterministic Pre-Storage Sanitization Rules:
When `WebhookWorker` dequeues an inbox record, it applies regex sanitizers before Tier 2 persistence:
- Private Keys (`-----BEGIN RSA PRIVATE KEY-----`) $\rightarrow$ `[REDACTED_PRIVATE_KEY]`
- AWS Access Keys (`AKIA[0-9A-Z]{16}`) $\rightarrow$ `[REDACTED_AWS_KEY]`
- GitHub Tokens (`ghp_[0-9a-zA-Z]{36}`, `github_pat_...`) $\rightarrow$ `[REDACTED_GH_TOKEN]`
- Generic API Secrets (`sk_live_[0-9a-zA-Z]{24}`) $\rightarrow$ `[REDACTED_API_SECRET]`

#### Dual Content Hash Specification:
- **`raw_wire_sha256`**: SHA-256 computed over raw wire bytes. Stored in `raw_evidence_inbox`. Used for HTTP transport deduplication and HMAC replay verification.
- **`payload_sha256`**: SHA-256 computed over sanitized payload envelope. Stored in `raw_evidence`. Used for organizational memory deduplication, foreign keys, graph provenance, and citations.

---

### 7.2 Complete Deletion and Retention Lifecycle Coverage

Company Brain implements an end-to-end, auditable deletion workflow across all **nine storage locations**:

```mermaid
graph TD
    Trigger["Deletion Trigger<br/>(Retention Schedule Expiry / Verified GDPR Art. 17 Request / Admin Purge)"]
    
    Trigger --> Orchestrator["ErasureOrchestrator<br/>(Idempotent State Machine)"]
    
    Orchestrator --> L1["1. raw_evidence_inbox<br/>(Purge matching wire payloads)"]
    Orchestrator --> L2["2. Original Wire Archive<br/>(Purge matching S3 cold objects)"]
    Orchestrator --> L3["3. raw_evidence<br/>(Soft-delete row, zero payload, delete S3 objects)"]
    Orchestrator --> L4["4. normalized_events<br/>(Tombstone matching event envelopes)"]
    Orchestrator --> L5["5. quarantined_events<br/>(Purge DLQ failure snapshots)"]
    Orchestrator --> L6["6. Knowledge Graph<br/>(Prune/detach nodes & edges via Cypher)"]
    Orchestrator --> L7["7. Vector Embeddings<br/>(Delete pgvector vector_chunks rows)"]
    Orchestrator --> L8["8. Retrieval Caches<br/>(Evict Redis semantic & query cache keys)"]
    Orchestrator --> L9["9. Backups & Replicas<br/>(Document PITR expiry cycle; no live query leakage)"]
    
    L1 & L2 & L3 & L4 & L5 & L6 & L7 & L8 --> Verify["Verification Pass<br/>(Confirm 0 queryable records remain)"]
    Verify --> Audit["Append Audit Event<br/>(Pseudonymized proof of deletion execution)"]
```

#### Detailed Location Coverage:
1. **`raw_evidence_inbox`**: Delete all pending, processing, or completed records matching the target resource or user.
2. **Original Wire Archive**: If cold storage is enabled, invoke S3 `DeleteObjects` on matching wire archives.
3. **`raw_evidence` & Object Storage**: Mark relational rows as soft-deleted (`deleted_at = NOW()`), overwrite `raw_payload = NULL`, and delete backing S3 payload objects referenced by `payload_uri`.
4. **`normalized_events`**: Soft-delete and tombstone associated activity records.
5. **`quarantined_events` (DLQ)**: Purge failure records and payload snapshots belonging to the target entity to prevent secret or personal data retention in dead-letter queues.
6. **Knowledge Graph**: Execute parameterized Cypher mutations matching `evidence_id` to delete extracted fact nodes, prune relationships, or detach orphan entities.
7. **Vector Embeddings (`vector_chunks`)**: Execute `DELETE FROM vector_chunks WHERE evidence_id = :evidence_id`, immediately removing embeddings from HNSW index lookups.
8. **Retrieval Caches**: Evict Redis cache keys for affected workspaces, projects, and queries.
9. **Backups, Replicas, and WAL Logs**: Database write-ahead logs (WAL) and point-in-time recovery (PITR) snapshots are immutable by design for disaster recovery. Live queries immediately exclude deleted records via `deleted_at`. Backups expire naturally per the backup retention cycle (e.g. 30 days).

#### Propagation, Retries, and Verification:
- Orchestrated via `ErasureOrchestrator` using a persistent job model (`PENDING`, `IN_PROGRESS`, `VERIFYING`, `COMPLETED`, `FAILED`).
- Every stage is idempotent. If a transient error occurs during graph pruning, the orchestrator retries with exponential backoff.
- The `VERIFYING` stage executes verification queries across relational, vector, and graph datastores to confirm zero accessible records remain before marking the job `COMPLETED`.

#### Legal Exceptions, Lawful Retention & Configured Schedules:
- **Statutory Erasure Obligations (GDPR Art. 17 / CCPA)**: Right to erasure applies to personal data.
- **Lawful Retention & Legal Exceptions**: In accordance with applicable laws (e.g. GDPR Art. 17(3)(b) compliance with a legal obligation, Art. 17(3)(e) establishment, exercise, or defense of legal claims), Company Brain preserves append-only security logs in `audit_events` (documenting the execution of the erasure itself and administrative actions), with personal identifiers pseudonymized.
- **Customer-Configured Retention Schedules**: Workspaces may define automatic retention schedules (e.g. 90-day communication retention), which execute through this exact same pipeline.

---

## 8. Target Connector Specifications

### 8.1 GitHub Connector

#### A. Ingested Artifacts & Granularity
- **Commits**: Message, commit SHA, tree SHA, parent SHAs, diff patch stats, GPG/SSH signature verification status, committer/author metadata.
- **Pull Requests**: Number, title, body, state, creator, assignees, reviewers, milestone, branch heads, merge commit SHA.
- **Reviews & Inline Comments**: Review state, body text, diff hunks, path, line numbers.
- **Issues & Discussions**: Number, title, body, creator, assignees, labels, comments, discussion threads.

#### B. Resource Discovery & Scoping
- Invocations of `BaseConnector.discover_resources()` page `GET /orgs/{org}/repos` or `GET /user/repos`.
- Admin selects repositories to populate `connector_resources`.

#### C. Webhook Verification Protocol
- Header: `X-Hub-Signature-256: sha256=<hex_hmac>`
- Algorithm: HMAC-SHA256 computed over raw request body using connection `webhook_secret`. Constant-time comparison (`hmac.compare_digest`).
- Delivery Headers: `X-GitHub-Event`, `X-GitHub-Delivery` (used for request deduplication).

#### D. Polling & Delta Synchronization
- High-water mark stored in `connector_resources.sync_cursor` as `last_commit_sha` and `since` ISO timestamp.
- Commits: `GET /repos/{owner}/{repo}/commits?since={timestamp}`.
- Pull Requests: `GET /repos/{owner}/{repo}/pulls?state=all&sort=updated&direction=desc`.

#### E. Rate Limit Management
- Response Headers: `X-RateLimit-Remaining`, `X-RateLimit-Reset` (epoch timestamp).
- Strategy: When `X-RateLimit-Remaining < 100`, delay polling until reset epoch. On HTTP 403/429 with secondary rate limits, apply exponential backoff.

---

### 8.2 Google Workspace Connector

#### A. Ingested Artifacts & Granularity
- **Google Drive Docs / Sheets**: Document ID, title, MIME type, revision ID, exported text/markdown, author, comments.
- **Gmail (Opt-in Project Labels Only)**: Thread ID, message ID, subject, participants, plain text body (HTML stripped), attachment metadata (no binary mirroring).
- **Google Calendar**: Event ID, summary, description, start/end time, organizer, attendees.

#### B. Resource Discovery & Scoping
- Lists shared drives via `GET /drive/v3/drives` and top-level folders via `GET /drive/v3/files?q=mimeType='application/vnd.google-apps.folder'`.
- Admin selects folders/drives to populate `connector_resources`.

#### C. Webhook / Push Notification Protocol
- Inbound: Google Cloud Pub/Sub push subscription or Google Drive Webhooks (`watch` API).
- Verification Headers: `X-Goog-Channel-ID`, `X-Goog-Channel-Token` (cryptographically validated against stored token), `X-Goog-Resource-State`.

#### D. Polling & Delta Synchronization
- Protocol: `GET /drive/v3/changes?pageToken={startPageToken}`.
- Document text export: `GET /drive/v3/files/{fileId}/export?mimeType=text/markdown`.
- Cursor: Persist `newStartPageToken` in `connector_resources.sync_cursor`.

#### E. Quota & Backoff Management
- Backoff on HTTP 429 and 503: Full jitter exponential backoff (`min(60, 2^attempt + uniform(0, 1))`).

---

### 8.3 Microsoft 365 / Teams Connector

#### A. Ingested Artifacts & Granularity
- **Teams Channel Messages**: Team ID, channel ID, message ID, subject, content, author UPN/OID, thread replies.
- **SharePoint Documents**: Drive item ID, name, web URL, last modified by, plain text content.
- **Outlook (Opt-in Shared Mailbox)**: Conversation ID, message ID, subject, sender, recipients, text body.

#### B. Resource Discovery & Scoping
- Teams: `GET /v1.0/groups?$filter=resourceProvisioningOptions/Any(x:x eq 'Team')`.
- Channels: `GET /v1.0/teams/{team-id}/channels`.
- Admin selects teams/channels to populate `connector_resources`.

#### C. Webhook Verification Protocol
- Validation Challenge: Initial handshake query param `?validationToken={token}` echoed as `text/plain` within 10 seconds.
- Payload Verification: Verify `clientState` matches connection secret token. Decrypt rich notification envelopes using private certificate.

#### D. Polling & Delta Synchronization
- Teams Messages Delta: `GET /v1.0/teams/{team-id}/channels/{channel-id}/messages/delta`.
- Drive Delta: `GET /v1.0/drives/{drive-id}/root/delta`.
- Cursor: Persist `@odata.deltaLink` into `connector_resources.sync_cursor`.

#### E. Throttling Management
- Intercept HTTP 429, parse `Retry-After` header in seconds, suspend polling worker until requested interval expires.

---

### 8.4 Slack Connector

#### A. Ingested Artifacts & Granularity
- **Messages & Threads**: Channel ID, timestamp (`ts`), thread timestamp (`thread_ts`), user ID, text blocks, reactions.
- **Canvases**: Document ID, title, text content.

#### B. Resource Discovery & Scoping
- Invocations of `conversations.list?types=public_channel,private_channel`.
- Admin selects channels to populate `connector_resources`. Bot must be member of private channels.

#### C. Webhook Verification Protocol
- Endpoint: Slack Events API.
- Timestamp Replay Mitigation: Reject if `abs(now - X-Slack-Request-Timestamp) > 300` seconds.
- HMAC Verification: Compute HMAC-SHA256 over `v0:{timestamp}:{raw_body}` using `slack_signing_secret`. Compare with `X-Slack-Signature` using `compare_digest`.
- URL Challenge: Echo `{"challenge": "..."}` on `url_verification` event.

#### D. Polling & Backfill Protocol
- History: `conversations.history?channel={channel}&oldest={cursor}&limit=100`.
- Cursor: Persist `response_metadata.next_cursor` in `connector_resources.sync_cursor`.

#### E. Rate Limit Management
- Handle HTTP 429 with `Retry-After`. Slack Tier 2 (20 req/min) and Tier 3 (50 req/min) pacing.

---

## 9. Storage Architecture: Relational Metadata & Payload Decoupling

### 9.1 The Webhook Inbox Store: `raw_evidence_inbox`

```text
raw_evidence_inbox
├── id (UUIDv7, PK)
├── workspace_id (UUIDv7, FK -> workspaces.id ON DELETE CASCADE)
├── connection_id (UUIDv7, FK -> connector_connections.id ON DELETE CASCADE)
├── provider (VARCHAR(50))
├── event_type (VARCHAR(100))
├── raw_wire_sha256 (CHAR(64), Hash of exact wire body for idempotency)
├── raw_headers (JSONB, Delivery headers, signature headers)
├── raw_payload (BYTEA, Encrypted wire body via AES-256-GCM; NULL once completed)
├── status (VARCHAR(30): 'pending', 'processing', 'completed', 'failed')
├── attempts (INTEGER, Default 0)
├── last_error (TEXT)
├── created_at (TIMESTAMPTZ, Ingestion timestamp)
├── processed_at (TIMESTAMPTZ)
└── expires_at (TIMESTAMPTZ, Auto-purge TTL, default 7 days)
```

#### Relational Constraints:
- `FOREIGN KEY (workspace_id, connection_id) REFERENCES connector_connections(workspace_id, id) ON DELETE CASCADE`
- `INDEX idx_inbox_queue ON raw_evidence_inbox(workspace_id, status, created_at ASC)`
- `UNIQUE (workspace_id, connection_id, raw_wire_sha256) WHERE status IN ('pending', 'processing')`

### 9.2 The Raw Evidence Store: `raw_evidence`

```text
raw_evidence
├── id (UUIDv7, PK)
├── workspace_id (UUIDv7, FK -> workspaces.id ON DELETE RESTRICT)
├── connection_id (UUIDv7, FK -> connector_connections.id ON DELETE RESTRICT)
├── connector_resource_id (UUIDv7, FK -> connector_resources.id ON DELETE RESTRICT)
├── source_system (VARCHAR(50): 'github', 'google', 'microsoft', 'slack')
├── source_resource_type (VARCHAR(50): 'commit', 'pull_request', 'document', 'message')
├── external_id (VARCHAR(255), External ID, e.g. commit SHA, PR number, message ts)
├── external_url (VARCHAR(1024), Canonical URL on source platform)
├── payload_sha256 (CHAR(64), SHA-256 hash of sanitized payload)
├── content_type (VARCHAR(100), e.g. 'application/json', 'text/markdown')
├── storage_tier (VARCHAR(20): 'inline', 'object_store')
├── raw_payload (JSONB, Sanitized inline payload; NULL if in object storage)
├── payload_uri (VARCHAR(512), Object store URI e.g. 's3://fabric-evidence/ws1/...'; NULL if inline)
├── byte_size (INTEGER, Size of sanitized payload)
├── external_occurred_at (TIMESTAMPTZ, Provider timestamp)
├── ingested_at (TIMESTAMPTZ, System recording timestamp)
├── created_at (TIMESTAMPTZ)
└── deleted_at (TIMESTAMPTZ, Soft-deletion timestamp for retention/GDPR)
```

#### Relational Constraints:
- `FOREIGN KEY (workspace_id, connection_id) REFERENCES connector_connections(workspace_id, id) ON DELETE RESTRICT`
- `FOREIGN KEY (workspace_id, connector_resource_id) REFERENCES connector_resources(workspace_id, id) ON DELETE RESTRICT`
- `UNIQUE (workspace_id, source_system, external_id, payload_sha256) WHERE deleted_at IS NULL`
- `INDEX idx_raw_evidence_lookup ON raw_evidence(workspace_id, connector_resource_id, external_occurred_at DESC)`
- `INDEX idx_raw_evidence_hash ON raw_evidence(workspace_id, payload_sha256)`

### 9.3 The Normalized Event Store: `normalized_events`

```text
normalized_events
├── id (UUIDv7, PK)
├── workspace_id (UUIDv7, FK -> workspaces.id ON DELETE RESTRICT)
├── evidence_id (UUIDv7, FK -> raw_evidence.id ON DELETE RESTRICT)
├── connector_resource_id (UUIDv7, FK -> connector_resources.id ON DELETE RESTRICT)
├── event_type (VARCHAR(100), e.g. 'code.pr.opened', 'communication.message.posted')
├── action (VARCHAR(50), e.g. 'created', 'updated', 'deleted', 'merged', 'commented')
├── actor (JSONB, Immutable provider_subject_id, username, email, display_name)
├── resource (JSONB, External ID, title, resource type, URL)
├── context (JSONB, Container hints, channel name, thread parent ID, project_hint)
├── occurred_at (TIMESTAMPTZ, Source event timestamp)
├── ingested_at (TIMESTAMPTZ, Company Brain recording timestamp)
├── compilation_status (VARCHAR(30): 'pending', 'processing', 'compiled', 'failed', 'ignored')
├── compilation_attempts (INTEGER, Default 0)
├── last_error (TEXT)
├── compiled_at (TIMESTAMPTZ)
├── created_at (TIMESTAMPTZ)
└── deleted_at (TIMESTAMPTZ, Tombstone timestamp for erasure)
```

- `FOREIGN KEY (workspace_id, evidence_id) REFERENCES raw_evidence(workspace_id, id) ON DELETE RESTRICT`
- `FOREIGN KEY (workspace_id, connector_resource_id) REFERENCES connector_resources(workspace_id, id) ON DELETE RESTRICT`
- `INDEX idx_normalized_events_queue ON normalized_events(workspace_id, compilation_status, occurred_at ASC)`

---

## 10. Durable Ingestion Pipelines & Execution Architecture

### 10.1 Push Ingestion Pipeline (Real-Time Webhooks)

```text
External Provider (GitHub / Slack / MSFT / Google)
                     │
                     ▼ 1. POST /api/v1/webhooks/{provider}/{connection_id}
┌────────────────────────────────────────────────────────┐
│            FastAPI Webhook Receiver Endpoint           │
│                                                        │
│  - Step 1: Verify connection & workspace ownership    │
│  - Step 2: Validate payload size (< 10 MB)             │
│  - Step 3: Verify HMAC signature using raw bytes       │
│  - Step 4: INSERT INTO raw_evidence_inbox (DURABLE)   │
│  - Step 5: Commit DB transaction                       │
│  - Step 6: Return HTTP 202 Accepted (Budget: <100ms)   │
└────────────────────┬───────────────────────────────────┘
                     │ (Durable Ingestion Achieved)
                     ▼
┌────────────────────────────────────────────────────────┐
│               Ingestion Worker Pipeline                │
│                                                        │
│  - Step 7: Poll / Wakeup from raw_evidence_inbox       │
│  - Step 8: Decrypt wire payload & sanitize secrets     │
│  - Step 9: Compute sanitized payload_sha256            │
│  - Step 10: Deduplication check against raw_evidence   │
│  - Step 11: Persist to raw_evidence (inline / S3)      │
│  - Step 12: Parse & persist normalized_events          │
│  - Step 13: Insert outbox record                       │
│  - Step 14: Mark inbox completed & clear raw_payload   │
└────────────────────────────────────────────────────────┘
```

#### Performance Budgets & Reliability Assurances:
- **Design Budget Target**: The FastAPI webhook receiver completes signature verification and single-row durable inbox insertion within a design budget of `< 50–100ms`, well within the provider 3-second SLA.
- **Reliability Assurance**: Guarantees at-least-once ingestion under crash-recovery assumptions (PostgreSQL ACID durability and WAL). If a worker crashes after HTTP 202 is returned, the record remains in `raw_evidence_inbox` with `status = 'pending'` and is picked up upon worker restart.

### 10.2 Pull Ingestion Pipeline (Historical Backfill & Delta Polling)

```text
Scheduled Cron / Worker Trigger (sync_delta)
                     │
                     ▼
┌────────────────────────────────────────────────────────┐
│           Delta Sync Orchestrator Service              │
│                                                        │
│  - Step 1: Load connector_resources cursor state       │
│  - Step 2: Check rate limit budget                     │
│  - Step 3: Invoke BaseConnector.sync_delta(cursor)     │
│  - Step 4: Batch fetch changes from provider API       │
│  - Step 5: Sanitize secrets & compute SHA-256 hashes   │
│  - Step 6: Batch insert into raw_evidence (ON CONFLICT)│
│  - Step 7: Batch insert into normalized_events         │
│  - Step 8: Update resource cursor & last_synced_at     │
│  - Step 9: Insert outbox records & commit transaction  │
└────────────────────────────────────────────────────────┘
```

### 10.3 The Transactional Outbox Pattern: Durability vs. Notification

To guarantee reliable handoff to the asynchronous Knowledge Compiler:
1. **Durable Outbox Insertion**:
   - `raw_evidence`, `normalized_events`, and an entry in `compilation_outbox` are committed in the **exact same ACID database transaction**.
2. **PostgreSQL NOTIFY as Wake-Up Hint Only**:
   - After the transaction commits, the application issues `NOTIFY compiler_events, '{normalized_event_id}'`.
   - **Crucial Invariant**: PostgreSQL `NOTIFY` is treated strictly as an ephemeral latency optimization / wake-up hint. It is **not** durable message storage.
3. **Durable Worker Recovery**:
   - Workers poll `normalized_events WHERE compilation_status = 'pending' ORDER BY occurred_at ASC`.
   - If PostgreSQL restarts, connections drop, or `NOTIFY` events are missed, workers automatically process all pending records without data loss.

---

## 11. Evaluation of Centralized IAM-Like Authorization for Company Brain

### 11.1 Context & Objective

We evaluated whether Company Brain should evolve its existing `AuthorizationService` into a centralized, IAM-like authorization platform (analogous to AWS IAM) across the workspace.

### 11.2 Architectural Evaluation Dimensions

#### 1. Subjects Requiring Authorization
Company Brain governs five classes of subjects:
- **Human Users**: Authenticated individuals carrying `(user_id, workspace_id, workspace_role)`.
- **Subject Sets (Teams)**: Collaborative groups (`Team:<team_id>#member`) granting transitive permissions.
- **AI Agents**: Autonomous assistants operating strictly under delegated user identity (`Agent:<uuidv7>` with caller's `IdentityContext`).
- **Connector Integrations**: External systems executing with scoped sync configurations bound to `connector_resources`.
- **System Background Workers**: Internal pipeline jobs (`SystemContext`) restricted to write-only ingestion and compilation pipelines.

#### 2. Resources and Actions Requiring Protection
- **Workspaces**: `workspace:read`, `workspace:update`, `workspace:manage_members`, `workspace:invite`, `workspace:delete`.
- **Teams**: `team:read`, `team:create`, `team:update`, `team:manage_members`, `team:delete`.
- **Projects**: `project:read`, `project:create`, `project:update`, `project:manage_members`, `project:delete`, `project:archive`.
- **Tasks**: `task:read`, `task:create`, `task:update`, `task:assign`, `task:delete`.
- **Connector Resources**: `connector:manage_connection`, `connector:map_resource`, `connector:trigger_sync`.
- **Evidence & Knowledge**: `knowledge:search_vector`, `knowledge:query_graph`, `knowledge:read_evidence`, `knowledge:view_provenance`.

#### 3. Composition of Roles, Relationships, and Service Permissions
- Rather than maintaining disconnected RBAC, ReBAC, and service-permission silos, Company Brain unifies them under the **hybrid RBAC + ReBAC model** defined in [`authorization.md`](file:///F:/Projects/Fabric/docs/architecture/authorization.md):
  - *Workspace level*: RBAC determines baseline administrative and collaboration rights (`owner`, `admin`, `member`, `guest`).
  - *Project / Team level*: ReBAC dynamically evaluates relationship paths (team ownership of projects, project memberships).
  - *Service / Worker level*: Deterministic execution contexts (`SystemContext`) enforce write-only boundaries without query bypass.

#### 4. Source-Provider Permissions: Sovereign Model vs. Fragile ACL Mirroring
- **The ACL Mirroring Anti-Pattern**: Attempting to mirror real-time permission trees from GitHub (teams, collaborators, repo permissions), Google Drive (user permissions, domain sharing, link sharing), Slack (channel memberships), and Microsoft 365 into Company Brain is an anti-pattern. It causes severe synchronization lag, authorization drift, leaky edge cases, and massive API quota exhaustion.
- **Company Brain's Adopted Sovereign Model**:
  - Company Brain governs organizational memory through its **own sovereign authorization model**.
  - Ingested external containers are mapped authoritatively to Company Brain Projects via `connector_resources.mapped_project_id`.
  - Access to all evidence, vector chunks, and graph nodes derived from that container is governed by the user's Company Brain project and workspace permissions.

#### 5. Unified Enforcement Boundary
The same deterministic policy applies across all interfaces:
- **REST API**: Controllers enforce `AuthorizationService.authorize(identity, action, resource)`.
- **Retrieval Engine**: Compiles `AccessibleScope` to pre-filter vector and graph queries.
- **MCP Server**: MCP tools inject `IdentityContext` and invoke the identical `AuthorizationService` pipeline.
- **AI Agents**: Inherit calling user's `IdentityContext`. Zero backdoor bypass.

#### 6. V1 Scope vs. Deferred Scope
- **V1 Scope (Adopted)**:
  - Centralized application interface: `AuthorizationService`.
  - Native PostgreSQL 16+ indexed CTE repository (`PostgresAuthorizationRepository`).
  - Strict workspace tenancy, project ReBAC, guest confinement, and `connector_resources` scoping.
  - Endpoint-derived graph relationship authorization (Option A).
  - Explicit system contexts for background workers.
- **Deferred (Future Enterprise Scale)**:
  - Custom customer-defined JSON IAM policy documents (AWS IAM-style declarative policies).
  - Attribute-Based Access Control (ABAC) engine for dynamic context (e.g. IP CIDR allowlists, device posture).
  - External Zanzibar microservice deployment (OpenFGA sidecar).

#### 7. Authorization Engine: OpenFGA vs. Native PostgreSQL
- **Decision**: Native PostgreSQL 16+ indexed CTE queries remain the authoritative enforcement engine for V1.
- Deploying an external Zanzibar engine (OpenFGA / SpiceDB) in V1 would introduce dual-write race conditions, network RPC latency hops, and operational overhead.
- Because our domain interfaces and relations strictly mirror the OpenFGA schema specification defined in [`authorization.md` Section 7](file:///F:/Projects/Fabric/docs/architecture/authorization.md), the system can migrate to an external Zanzibar sidecar in future phases without altering domain or application layers.

> [!IMPORTANT]
> **Evaluation Outcome**: Company Brain will **NOT** build a complex clone of AWS IAM or introduce a competing secondary policy engine. The approved `AuthorizationService` serves as the centralized authorization capability for Company Brain, governing relational operations, pre-retrieval filtering, agent execution, and connector resource mapping.

---

## 12. Failure Handling, Retries, and Resilience

### 12.1 Error Classification & Handling Matrix

| Error Type | Examples | Handling Strategy | Connector Impact |
| :--- | :--- | :--- | :--- |
| **Transient Network / HTTP 5xx** | `502 Bad Gateway`, `503 Service Unavailable`, timeouts | Exponential backoff with jitter: `[2s, 4s, 8s, 16s, 32s]`, max 5 retries. | Stays `ACTIVE`. |
| **Rate Limit / HTTP 429** | `429 Too Many Requests`, GitHub secondary rate limits | Parse `Retry-After` or `X-RateLimit-Reset`. Suspend sync tasks until reset epoch. | State transitions to `DEGRADED` until reset. |
| **Auth Expiration** | `401 Unauthorized`, OAuth token expired | Trigger automated OAuth refresh token flow; retry failed request once. | If refresh succeeds: `ACTIVE`. If refresh fails: `DEGRADED`. |
| **Revoked Permissions** | `403 Forbidden`, app uninstalled, user revoked access | Stop polling immediately; log administrative security alert. | State transitions to `REVOKED`. |
| **Payload Malformation / Parsing Failure** | Corrupted JSON, unexpected schema mutation | Move raw payload to `quarantined_events` table (Dead Letter Queue). Do not block queue. | Stays `ACTIVE`. Generates engineering alert. |
| **Webhook Signature Mismatch** | Invalid HMAC, wrong secret | Reject immediately with HTTP 401; log IP and audit record. Do NOT retry. | None (security drop). |

### 12.2 Dead Letter Queue (DLQ) & Quarantine Model

When an event fails normalization or ingestion after 5 consecutive retries:
1. The record is marked `compilation_status = 'failed'`.
2. An entry is written to `quarantined_events` containing `{evidence_id, error_trace, payload_snapshot}`.
3. The worker moves on to subsequent events, ensuring a single bad payload ("poison pill") cannot stall the workspace ingestion pipeline.

---

## 13. Layer Boundaries & Software Architecture

In accordance with [`AGENTS.md` Section 6 (Layer Boundaries & Dependency Rules)](file:///F:/Projects/Fabric/AGENTS.md):

```text
backend/app/
├── domain/connectors/
│   ├── base.py                # Abstract BaseConnector interface
│   ├── resources.py           # ConnectorResource domain model & scoping rules
│   ├── events.py              # NormalizedEventType enums and domain entities
│   └── exceptions.py          # ConnectorAuthException, RateLimitExceededException
│
├── application/connectors/
│   ├── ingestion_service.py   # IngestionService (verifies, persists inbox & raw evidence)
│   ├── resource_service.py    # ConnectorResourceService (manages authoritative project mappings)
│   ├── sync_orchestrator.py   # DeltaSyncOrchestrator (handles polling and cursor states)
│   ├── erasure_service.py     # ErasureOrchestrator (coordinates 9-location deletion lifecycle)
│   └── ports.py               # Abstract RawEvidenceRepository, ResourceRepo, EventStore ports
│
├── infrastructure/connectors/
│   ├── github.py              # GitHubConnector implementation
│   ├── google.py              # GoogleWorkspaceConnector implementation
│   ├── microsoft.py           # Microsoft365Connector implementation
│   ├── slack.py               # SlackConnector implementation
│   └── crypto.py              # Constant-time HMAC signature verifiers
│
├── infrastructure/evidence/
│   ├── postgres_inbox_repo.py    # Concrete PostgreSQL raw_evidence_inbox repository
│   ├── postgres_evidence_repo.py # Concrete PostgreSQL raw_evidence repository
│   ├── postgres_resource_repo.py # Concrete PostgreSQL connector_resources repository
│   ├── postgres_event_repo.py    # Concrete PostgreSQL normalized_events repository
│   ├── postgres_quarantine_repo.py # Concrete PostgreSQL quarantined_events repository
│   └── object_storage.py         # S3/MinIO large payload store adapter
│
├── workers/ingestion/
│   ├── webhook_worker.py      # Async consumer for raw_evidence_inbox payloads
│   ├── poller_worker.py       # Cron-triggered delta sync runner
│   └── erasure_worker.py      # Background worker executing multi-store deletion passes
│
└── api/webhooks/
    ├── github.py              # Fast HTTP POST receiver for GitHub webhooks
    ├── google.py              # Fast HTTP receiver for Google Pub/Sub
    ├── microsoft.py           # Fast HTTP receiver for Microsoft Graph notifications
    └── slack.py               # Fast HTTP receiver for Slack Events API
```

### Dependency Invariants:
1. `domain/connectors/` is pure Python (zero dependencies on FastAPI, SQLAlchemy, or HTTP clients).
2. `application/connectors/` interacts with `application/authorization/service.py` via ports.
3. `infrastructure/connectors/` implements `domain/connectors/base.py`.
4. `api/webhooks/` handlers are ultra-thin: verify signature, persist to inbox table, return HTTP 202.

---

## 14. Architectural Decisions (Decisions Record)

The following architectural decisions are formally established and binding:

| ID | Decision | Rationale |
| :--- | :--- | :--- |
| **AD-INGEST-01** | **Deterministic Ingestion Boundary (Zero LLMs)** | Ingestion is real-time and high-throughput. LLMs introduce latency, cost, and non-determinism. Ingestion is 100% deterministic software; LLMs are isolated in the asynchronous Knowledge Compiler. |
| **AD-INGEST-02** | **Layered Immutability & Tiered Payload Storage** | Original wire payloads are preserved encrypted in restricted cold storage. Sanitized raw evidence (`raw_evidence`) is stored with SHA-256 hashes for deduplication and provenance. Downstream graph state is fully recomputable. |
| **AD-INGEST-03** | **Decoupled Normalized Event Envelope** | Downstream compilation must not depend on brittle third-party provider schemas. The `NormalizedEvent` provides a stable, unified domain model across all external tools. |
| **AD-INGEST-04** | **Hybrid Payload Storage (Inline + Object Store)** | Payloads $\le 64\text{ KB}$ stay in PostgreSQL JSONB for transactional simplicity and speed. Payloads $> 64\text{ KB}$ offload to S3/MinIO to maintain database index locality. |
| **AD-INGEST-05** | **Synchronous Durable Webhook Ingestion Before Ack** | Webhooks are acknowledged with HTTP 202 only after the payload is durably committed to the database inbox (`raw_evidence_inbox`), targeting a `< 50–100ms` budget within the provider 3-second SLA. |
| **AD-INGEST-06** | **Idempotent Deduplication at Database Level** | Unique constraint on `(workspace_id, source_system, external_id, payload_sha256)` guarantees safe handling of duplicate webhook deliveries and overlapping poll runs. |
| **AD-INGEST-07** | **Cursor-Based Delta Polling** | Polling relies on provider-native change tokens (GitHub commit SHAs, Google Drive page tokens, Microsoft Graph delta links), preventing expensive full scans. |
| **AD-INGEST-08** | **Pre-Storage Secret Sanitization & Tier 1 Buffer Isolation** | High-entropy private keys and credentials are deterministically redacted before Tier 2 persistence. Raw wire bodies in Tier 1 (`raw_evidence_inbox`) are AES-256-GCM encrypted, accessible only to workers, and truncated upon completion. |
| **AD-INGEST-09** | **Transactional Outbox / Worker Queue Decoupling** | Raw evidence, normalized events, and outbox records commit in a single ACID transaction. PostgreSQL NOTIFY is used strictly as an ephemeral wake-up hint; workers poll durable state. |
| **AD-INGEST-10** | **Pluggable BaseConnector Architecture** | Every external provider implements a standardized interface (`BaseConnector`), isolating provider-specific HTTP quirks from the core ingestion pipeline. |
| **AD-INGEST-11** | **Four-Layer Permission Model & Resource Scoping** | Ingested resources are not universally readable by workspace members. Access requires passing provider grants, admin allowlists, connector effective access, and Company Brain project authorization. |
| **AD-INGEST-12** | **Persisted `connector_resources` Entity** | External containers (repos, channels, folders) map authoritatively to Company Brain Projects via a persisted relational entity, establishing deterministic access boundaries. |
| **AD-INGEST-13** | **Rejection of Heuristic `project_hint` for Authorization** | Ingested text hints and LLM-proposed project tags are non-authoritative metadata. They must never grant access or set `mapped_project_id`. |
| **AD-INGEST-14** | **Decoupling Connector Revocation from Data Deletion** | Revoking connector access halts syncing but preserves historical evidence as organizational memory. Data deletion requires explicit retention schedule expiry or legal erasure requests. |
| **AD-INGEST-15** | **End-to-End Deletion Lifecycle Across All 9 Storage Locations** | Statutory erasure requests propagate through inbox, wire archives, raw evidence, normalized events, quarantined DLQ records, graph nodes, vector embeddings, and caches, while preserving legally mandated audit records. |
| **AD-INGEST-16** | **Centralized Sovereign Authorization over Fragile ACL Mirroring** | Company Brain does not mirror complex third-party permission trees. It enforces its sovereign ReBAC model via `AuthorizationService`, mapping external resources to internal Projects with explicit administrative redistribution attestation. |
| **AD-INGEST-17** | **Phased Native PostgreSQL Enforcement for Centralized IAM** | Company Brain avoids the operational overhead of external Zanzibar sidecars (OpenFGA) in V1 by utilizing indexed PostgreSQL CTEs, while adhering strictly to OpenFGA tuple schemas for future scale. |

---

## 15. Unresolved Policy Decisions & Decisions Requiring Approval

The following specific policy decisions and trade-offs are flagged for formal user approval before implementation planning begins:

### 1. Decision Requiring Approval: Administrator-Attested Project Mapping vs. Source ACL Tracking
- **Proposal**: Adopt sovereign Administrator-Attested Project Mapping for V1. Ingested external containers map to Company Brain Projects. If a private container (e.g. private Slack channel `#slam-leads`) is mapped to a project with broader membership (`SLAM Core`), the admin explicitly attests to the redistribution (`redistribution_attested = true`). Company Brain does **not** mirror or enforce granular third-party ACLs at retrieval time.
- **Alternative**: Implement hybrid tracking where the connector continuously queries and records external channel membership and evaluates both Company Brain project membership AND external Slack channel membership before returning a snippet.
- **Trade-Off**: Full ACL tracking adds severe synchronization lag, authorization desync, and external API quota exhaustion. The recommended sovereign mapping provides immediate, low-latency, deterministic filtering.

### 2. Decision Requiring Approval: Disconnect Default Behavior (Context Retained vs. Purge)
- **Proposal**: When an administrator disconnects a connector or soft-deletes a `connector_resource`, the default behavior retains existing historical evidence in the mapped Project (`Mode A: Disconnect with Context Retained`). The administrator must explicitly select an optional checkbox (`Mode B: Disconnect and Purge Evidence`) to trigger cascading deletion across the nine stores.
- **Trade-Off**: Preserving historical organizational memory aligns with Company Brain's core purpose, but requires clear UI indication that historical data from disconnected tools remains queryable by project members.

### 3. Open Technical Questions
1. **Granular Attribute-Level Redaction in Graph Nodes**:
   - When a user has read access to a node extracted from a connector (e.g. PR review comments), should sensitive fields (compensation, personal notes) be redacted via field-level policies, or isolated into separate restricted nodes?
   - *Proposed Baseline*: Isolate sensitive information into separate restricted nodes with explicit project/role visibility.
2. **Backfill Depth Limits on Initial Connection**:
   - When connecting an existing GitHub organization with 10 years of history, should initial sync backfill all historical commits or cap at a configurable window (e.g. past 90 days)?
   - *Proposed Baseline*: Default initial sync to the past 90 days with an administrative toggle for full historical backfill.
3. **Multi-Project Resource Mapping (Monorepo Scoping)**:
   - Can a single monolithic repository (monorepo) map to multiple Company Brain Projects based on folder path prefixes (e.g. `/services/billing` -> Project Billing, `/services/slam` -> Project SLAM)?
   - *Proposed Baseline*: For V1, a repository maps to a single Project or workspace-global scope. Monorepo path-based multi-project mapping is deferred to Phase 3.
