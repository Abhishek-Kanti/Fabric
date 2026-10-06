# Connectors & Ingestion Low-Level Design (LLD)

## 1. Document Overview & Context

This document is the **Low-Level Design (LLD)** for the **Connectors, Webhooks, Ingestion Pipeline, Raw Evidence Store, and Normalized Events** subsystem of **Company Brain**. It builds directly upon the High-Level Design in [`docs/architecture/overview.md`](file:///F:/Projects/Fabric/docs/architecture/overview.md), the repository operating manual in [`AGENTS.md`](file:///F:/Projects/Fabric/AGENTS.md), the relational persistence layer in [`docs/architecture/database.md`](file:///F:/Projects/Fabric/docs/architecture/database.md), the identity architecture in [`docs/architecture/identity.md`](file:///F:/Projects/Fabric/docs/architecture/identity.md), and the authorization model in [`docs/architecture/authorization.md`](file:///F:/Projects/Fabric/docs/architecture/authorization.md).

Company Brain is explicitly **team-first rather than enterprise-only**. It continuously captures collaborative activity from external source systems:
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
│ 2. Ingestion Receivers   Fast webhook receivers (< 3s SLA) &           │
│                          cursor-based delta polling workers            │
├────────────────────────────────────────────────────────────────────────┤
│ 3. Raw Evidence Store    Immutable, append-only payload store with     │
│                          cryptographic SHA-256 hash deduplication      │
├────────────────────────────────────────────────────────────────────────┤
│ 4. Normalized Events     Standardized domain activity records with     │
│                          actor metadata ready for compilation queue    │
└────────────────────────────────────────────────────────────────────────┘
```

> [!IMPORTANT]
> **Design-First Invariant**: This document is an architectural design and specification. **No connector code, webhook endpoints, database models, or migration scripts will be written in this phase.**

---

## 2. Core Invariants & Architectural Boundaries

Every component in the Ingestion and Connector pipeline must strictly adhere to these invariants:

1. **Evidence First (Permanent Immutability)**:
   - Raw source evidence is captured unaltered and preserved permanently.
   - The Knowledge Graph and Vector Store are derived state that can be recompiled or re-indexed from raw evidence at any time.
   - Raw evidence records are **never mutated** after insertion.
2. **Full Provenance & Cryptographic Content Hashing**:
   - Every ingested piece of evidence is assigned an immutable `evidence_id` (UUIDv7) and a SHA-256 payload hash (`payload_sha256`).
   - Downstream graph nodes, facts, and embeddings must maintain a direct foreign key link back to the originating `evidence_id`.
3. **Deterministic Ingestion Boundary (Zero LLMs in the Ingestion Path)**:
   - Ingestion is **100% deterministic software**: signature validation, payload parsing, deduplication, raw persistence, event normalization, and queue dispatch.
   - LLMs and heuristic extraction belong strictly to the downstream **Knowledge Compiler** (Flow B), which runs asynchronously. No LLM calls occur during the real-time webhook or ingestion path.
4. **Strict Workspace Tenancy**:
   - Every external connection, webhook endpoint, evidence record, and normalized event is strictly bound to a `workspace_id`.
   - Cross-workspace data sharing or cross-tenant event routing is physically prohibited.
5. **Strict Idempotency & Deduplication**:
   - External providers routinely retry webhooks (at-least-once delivery).
   - Ingestion enforces idempotency at the database level using composite uniqueness on `(workspace_id, source_system, external_id, payload_sha256)`. Duplicate deliveries produce identical state without duplicate records.
6. **Authoritative Source Systems**:
   - External platforms remain the authoritative system of record for source-specific artifacts (e.g., full git trees, compiled binaries, large video recordings).
   - Company Brain ingests textual evidence, metadata, diffs, comments, and references necessary for organizational memory, rather than acting as a redundant file mirror.
7. **Privacy & Work-Related Context Guardrails**:
   - Ingestion is strictly **opt-in and explicit**.
   - **Privacy Prohibitions**: In accordance with [`AGENTS.md` Section 10](file:///F:/Projects/Fabric/AGENTS.md), Company Brain strictly prohibits keystroke logging, screen capture, personal browsing tracking, and covert activity monitoring.
   - Ingestion is scoped strictly to designated project repositories, team channels, and shared workspace folders.
8. **System Worker Isolation**:
   - Ingestion workers operate under an internal `SystemContext` (write-only pipeline). They cannot invoke user-facing retrieval or query endpoints.

---

## 3. Connector Lifecycle & Unified Architecture (`BaseConnector`)

### 3.1 The Pluggable Connector Model

All external system integrations implement a standard abstract interface: `BaseConnector`. This decouples the core ingestion engine from provider-specific APIs:

```mermaid
classDiagram
    class BaseConnector {
        <<interface>>
        +provider_name: str
        +authenticate(connection_config) AuthCredentials
        +refresh_credentials(connection) AuthCredentials
        +verify_webhook_signature(headers, body, secret) bool
        +discover_resources(connection) List[ResourceDescriptor]
        +sync_initial(connection, resource_id, cursor) SyncBatchResult
        +sync_delta(connection, resource_id, cursor) SyncBatchResult
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
    REVOKED --> [*] : Connection Archived (Evidence Retained Permanently)
```

| Lifecycle State | Description | Webhook Processing | Polling / Sync Allowed |
| :--- | :--- | :---: | :---: |
| **`PENDING_AUTH`** | Connection initiated; awaiting OAuth completion or admin consent | NO | NO |
| **`ACTIVE`** | Credentials healthy; receiving webhooks and scheduling delta syncs | **YES** | **YES** |
| **`SYNCING`** | Background sync actively paging through provider history | **YES** | **YES** |
| **`DEGRADED`** | Backing off due to 429 rate limit or waiting for automated token refresh | **YES** (Queue buffer) | Paused temporarily |
| **`REVOKED`** | OAuth token revoked or app uninstalled by customer admin | NO | NO |

### 3.3 Proposed Entity Specification: `connector_connections`

To manage external platform integrations securely within the Phase 1 multi-tenant architecture:

```text
connector_connections
├── id (UUIDv7, PK)
├── workspace_id (UUIDv7, FK -> workspaces.id ON DELETE RESTRICT)
├── provider (VARCHAR(50), e.g. 'github', 'google', 'microsoft', 'slack')
├── name (VARCHAR(255), e.g. "Acme Corp GitHub Org")
├── status (VARCHAR(30): 'pending_auth', 'active', 'syncing', 'degraded', 'revoked')
├── external_account_id (VARCHAR(255), External Org/Tenant ID, e.g. GitHub Org ID or M365 Tenant ID)
├── auth_type (VARCHAR(30): 'oauth2_app', 'github_app', 'service_account', 'webhook_only')
├── encrypted_credentials (BYTEA / JSONB, Encrypted tokens/keys via AES-256-GCM)
├── webhook_secret (BYTEA, Encrypted webhook signing secret)
├── sync_cursor (JSONB, Tracks high-water marks and pagination tokens per resource)
├── config (JSONB, Opt-in resource filters, selected repos, channel allowlists)
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

## 4. Target Connector Specifications

### 4.1 GitHub Connector

#### A. Ingested Artifacts & Granularity
- **Commits**: Commit message, author string, committer string, SHA, parent SHAs, file change diffs (patches), GPG verification status.
- **Pull Requests**: Number, title, description body, creator, assignees, reviewers, milestone, branch names, merged status, merge commit SHA.
- **Pull Request Reviews & Comments**: Review state (`APPROVED`, `CHANGES_REQUESTED`), review body, inline diff comments (file path, line number, diff hunk).
- **Issues & Discussions**: Title, description, creator, labels, assignees, state, comments, thread replies.

#### B. Webhook Verification Protocol
- Header: `X-Hub-Signature-256: sha256=<hex_hmac>`
- Algorithm: HMAC-SHA256 computed over raw request body using the connection's `webhook_secret`.
- Delivery Event Header: `X-GitHub-Event` (e.g., `push`, `pull_request`, `pull_request_review`, `issues`, `issue_comment`).
- Delivery GUID: `X-GitHub-Delivery` (used for request-level deduplication).

#### C. Polling & Delta Synchronization
- Protocol: GitHub REST API v3 / GraphQL v4.
- High-Water Mark: Stored as `last_commit_sha` and `since` ISO timestamp per repository.
- Commits Sync: `GET /repos/{owner}/{repo}/commits?since={timestamp}` with cursor pagination (`Link` header).
- PR Sync: `GET /repos/{owner}/{repo}/pulls?state=all&sort=updated&direction=desc`.

#### D. Rate Limit Management
- Response Headers: `X-RateLimit-Remaining`, `X-RateLimit-Reset` (Unix epoch).
- Strategy: When `X-RateLimit-Remaining < 100`, delay polling tasks until `X-RateLimit-Reset`.
- Secondary Rate Limits (HTTP 403/429 with `Retry-After`): Pause all polling workers for the connection for the duration of `Retry-After` (with exponential backoff).

---

### 4.2 Google Workspace Connector

#### A. Ingested Artifacts & Granularity
- **Google Drive Docs / Markdown**: Document ID, title, MIME type, revision ID, plain text / markdown export body, author/editors, comments, suggestions.
- **Gmail (Opt-in Project Labels Only)**: Thread ID, message ID, subject, participants (From, To, CC), body text (HTML stripped), attachments metadata (no binary mirroring).
- **Google Calendar**: Event ID, summary, description, start/end time, organizer, attendees.

#### B. Webhook / Push Notification Protocol
- Inbound Protocol: Google Cloud Pub/Sub push subscription or Google Drive Webhooks (`watch` API).
- Verification Headers:
  - `X-Goog-Channel-ID`: Unique channel UUID generated by Company Brain upon registration.
  - `X-Goog-Channel-Token`: Cryptographically signed HMAC verifying workspace identity.
  - `X-Goog-Resource-State`: Notification state (`sync`, `add`, `update`, `trash`).

#### C. Polling & Delta Synchronization
- Drive Delta Protocol: `GET https://www.googleapis.com/drive/v3/changes?pageToken={startPageToken}`.
- Text Export Protocol: For Google Docs, invoke `GET https://www.googleapis.com/drive/v3/files/{fileId}/export?mimeType=text/markdown` (or `text/plain`).
- Checkpoint Token: Save `newStartPageToken` in `connector_connections.sync_cursor` upon completing each change batch.

#### D. Quota & Backoff Management
- Quota: Google Drive enforces per-user and per-project queries-per-minute (QPM).
- Strategy: Standard exponential backoff with jitter on HTTP 429 and 503 (`Backoff = min(60, 2^attempt + rand(0, 1))`).

---

### 4.3 Microsoft 365 / Teams Connector

#### A. Ingested Artifacts & Granularity
- **Teams Channel Messages**: Channel ID, team ID, message ID, subject, content text, author UPN/OID, thread replies, reactions.
- **SharePoint / OneDrive Documents**: Drive item ID, name, web URL, last modified by, text extract.
- **Outlook (Opt-in Shared/Project Mailbox)**: Conversation ID, message ID, subject, sender, recipients, body text.

#### B. Webhook Verification Protocol
- Verification Challenge: Microsoft Graph issues an initial validation handshake:
  - Sends query param `?validationToken={token}`. Receiver must echo `{token}` as `text/plain` within 10 seconds.
- Notification Payload: JSON envelope containing resource URI, tenant ID, and `clientState`.
- Verification: Verify `clientState` matches the connection's secret token.
- Rich Notifications (Decryption): When notifications carry encrypted resource data, decrypt payload using RSA private key matching the public certificate registered with Microsoft Graph.

#### C. Polling & Delta Synchronization
- Protocol: Microsoft Graph Delta Queries.
- Teams Channel Delta: `GET https://graph.microsoft.com/v1.0/teams/{team-id}/channels/{channel-id}/messages/delta`.
- Drive Items Delta: `GET https://graph.microsoft.com/v1.0/drives/{drive-id}/root/delta`.
- Checkpoint: Persist `@odata.deltaLink` into `connector_connections.sync_cursor`. When polling, requesting the delta link returns only changes since the last poll.

#### D. Throttling Management
- Status Code: HTTP 429.
- Header: `Retry-After` (in seconds).
- Strategy: Ingestion dispatcher intercepts HTTP 429, parses `Retry-After`, and schedules the task retry strictly after the requested delay.

---

### 4.4 Slack Connector

#### A. Ingested Artifacts & Granularity
- **Messages & Threads**: Channel ID, message timestamp (`ts`), thread timestamp (`thread_ts`), user ID, text, message blocks, reactions, edit timestamps.
- **Canvases**: Document ID, title, text content.

#### B. Webhook Verification Protocol
- Endpoint: Slack Events API.
- Verification Headers:
  - `X-Slack-Request-Timestamp`: Unix timestamp. Reject if `abs(now - timestamp) > 300` (replay attack mitigation).
  - `X-Slack-Signature`: `v0=<hex_hmac>`.
- Algorithm: Compute HMAC-SHA256 over `v0:{timestamp}:{raw_body}` using connection `slack_signing_secret`. Compare with constant-time equality.
- URL Verification Handshake: Handle `{"type": "url_verification", "challenge": "..."}` by returning `{"challenge": "..."}`.

#### C. Polling & Backfill Protocol
- Conversations History: `conversations.history?channel={channel}&oldest={cursor}&limit=100`.
- Thread Replies: `conversations.replies?channel={channel}&ts={thread_ts}`.
- Pagination: Cursor-based via `response_metadata.next_cursor`.

#### D. Rate Limit Management
- Slack Tier Limits (Tier 2: 20 req/min, Tier 3: 50 req/min).
- Response on limit: HTTP 429 with `Retry-After` header.

---

## 5. Raw Evidence Store Specification

### 5.1 Storage Model: Relational Metadata + Payload Decoupling

In accordance with [`AGENTS.md` Invariant 1 (Evidence First)](file:///F:/Projects/Fabric/AGENTS.md), raw evidence is permanently stored with cryptographic integrity.

Payloads vary in size from small chat messages (1 KB) to large git diffs or document exports (several MBs). To maintain PostgreSQL index locality and table compact size, we implement a **hybrid storage model**:
- **Inline Storage (`inline`)**: Payloads $\le 64\text{ KB}$ are stored directly in PostgreSQL `raw_evidence.raw_payload` as `JSONB` or `TEXT`.
- **Object Storage (`object_store`)**: Payloads $> 64\text{ KB}$ are written to S3-compatible immutable object storage (MinIO / S3 / GCS), with `raw_evidence.payload_uri` storing the content addressable object key.

### 5.2 Proposed Entity Specification: `raw_evidence`

```text
raw_evidence
├── id (UUIDv7, PK)
├── workspace_id (UUIDv7, FK -> workspaces.id ON DELETE RESTRICT)
├── connection_id (UUIDv7, FK -> connector_connections.id ON DELETE RESTRICT)
├── source_system (VARCHAR(50): 'github', 'google', 'microsoft', 'slack')
├── source_resource_type (VARCHAR(50): 'commit', 'pull_request', 'document', 'message')
├── external_id (VARCHAR(255), External identifier, e.g. commit SHA, PR number, message ts)
├── external_url (VARCHAR(1024), Canonical link to evidence on source platform)
├── payload_sha256 (CHAR(64), Cryptographic SHA-256 hash of payload content)
├── content_type (VARCHAR(100), e.g. 'application/json', 'text/markdown', 'text/plain')
├── storage_tier (VARCHAR(20): 'inline', 'object_store')
├── raw_payload (JSONB, Inline payload; NULL if stored in object storage)
├── payload_uri (VARCHAR(512), Object store URI e.g. 's3://fabric-evidence/ws1/...'; NULL if inline)
├── byte_size (INTEGER, Size of raw payload in bytes)
├── external_occurred_at (TIMESTAMPTZ, Timestamp asserted by external platform)
├── ingested_at (TIMESTAMPTZ, Timestamp when Company Brain recorded the evidence)
├── created_at (TIMESTAMPTZ)
└── deleted_at (TIMESTAMPTZ, NULL; Soft-deletion reserved for compliance/GDPR requests)
```

#### Relational Constraints & Invariants:
1. **Tenant Integrity Composite Foreign Key**:
   - `FOREIGN KEY (connection_id, workspace_id) REFERENCES connector_connections(id, workspace_id) ON DELETE RESTRICT`
2. **Deduplication Unique Constraint**:
   - `UNIQUE (workspace_id, source_system, external_id, payload_sha256) WHERE deleted_at IS NULL`
   - Guarantees identical event re-deliveries do not insert duplicate evidence rows.
3. **Lookup Indexes**:
   - `INDEX idx_raw_evidence_lookup ON raw_evidence(workspace_id, source_system, source_resource_type, external_occurred_at DESC)`
   - `INDEX idx_raw_evidence_hash ON raw_evidence(workspace_id, payload_sha256)`

---

## 6. Normalized Event Store Specification

### 6.1 Why Separate Raw Evidence from Normalized Events?

A critical anti-pattern in organizational memory systems is tightly coupling external provider payload schemas to downstream reasoning.
- Provider schemas mutate constantly (e.g., GitHub changes webhook JSON formats; Microsoft Graph updates OData shapes).
- The **Normalized Event** translates heterogeneous platform activities into a standardized, clean event envelope representing human collaboration over time.
- The downstream Knowledge Compiler reads **only** `NormalizedEvent` records, remaining completely agnostic to whether an event originated from GitHub, Slack, or Google Workspace.

### 6.2 The `NormalizedEvent` Structure

```text
┌────────────────────────────────────────────────────────────────────────┐
│                        NormalizedEvent Envelope                        │
├────────────────────────────────────────────────────────────────────────┤
│ id: UUIDv7                                                             │
│ workspace_id: UUIDv7                                                   │
│ evidence_id: UUIDv7 (FK -> raw_evidence.id)                            │
│ event_type: NormalizedEventType                                        │
│             (e.g., 'code.commit.created', 'document.updated')          │
│ occurred_at: ISO-8601 Timestamp                                        │
├────────────────────────────────────────────────────────────────────────┤
│ ACTOR                                                                  │
│   provider: "github"                                                   │
│   provider_subject_id: "4182931"                                       │
│   username: "alexvance"                                                │
│   email: "alex@acme.com"                                               │
│   display_name: "Dr. Alex Vance"                                       │
├────────────────────────────────────────────────────────────────────────┤
│ ACTION                                                                 │
│   verb: "created" | "merged" | "commented" | "edited" | "assigned"     │
├────────────────────────────────────────────────────────────────────────┤
│ RESOURCE                                                               │
│   type: "pull_request" | "commit" | "document" | "message"             │
│   external_id: "142"                                                   │
│   title: "SLAM Engine Optimization"                                    │
│   url: "https://github.com/org/repo/pull/142"                          │
├────────────────────────────────────────────────────────────────────────┤
│ CONTEXT (Relational Scoping Hints)                                     │
│   project_hint: "SLAM Engine"                                          │
│   repository: "stanford-robotics/slam-core"                            │
│   channel_name: "#slam-discussions"                                    │
│   parent_external_id: "pull_request:142"                               │
└────────────────────────────────────────────────────────────────────────┘
```

### 6.3 Proposed Entity Specification: `normalized_events`

```text
normalized_events
├── id (UUIDv7, PK)
├── workspace_id (UUIDv7, FK -> workspaces.id ON DELETE RESTRICT)
├── evidence_id (UUIDv7, FK -> raw_evidence.id ON DELETE RESTRICT)
├── event_type (VARCHAR(100), e.g. 'code.pr.opened', 'communication.message.posted')
├── action (VARCHAR(50), e.g. 'created', 'updated', 'deleted', 'merged', 'commented')
├── actor (JSONB, Provider subject ID, username, email, display name)
├── resource (JSONB, External ID, title, resource type, URL)
├── context (JSONB, Container hints, repository name, channel name, thread parent ID)
├── occurred_at (TIMESTAMPTZ, Source event timestamp)
├── ingested_at (TIMESTAMPTZ, Company Brain ingestion timestamp)
├── compilation_status (VARCHAR(30): 'pending', 'processing', 'compiled', 'failed', 'ignored')
├── compilation_attempts (INTEGER, Default 0)
├── last_error (TEXT, Error trace if compilation failed)
├── compiled_at (TIMESTAMPTZ, Timestamp when Knowledge Compiler finished processing)
└── created_at (TIMESTAMPTZ)
```

#### Relational Constraints & Invariants:
- `FOREIGN KEY (evidence_id, workspace_id) REFERENCES raw_evidence(id, workspace_id) ON DELETE RESTRICT`
- `INDEX idx_normalized_events_queue ON normalized_events(workspace_id, compilation_status, occurred_at ASC)`
  - Powers the high-performance worker queue fetch query.

---

## 7. The Ingestion Pipeline & Execution Architecture

### 7.1 Push Ingestion Pipeline (Real-Time Webhooks)

Webhooks must meet a strict provider SLA: **respond with HTTP 200/202 within 3 seconds** or external platforms will flag the webhook as failing and disconnect.

```text
External Provider (GitHub / Slack / MSFT / Google)
                     │
                     ▼ 1. POST /api/v1/webhooks/{provider}/{connection_id}
┌────────────────────────────────────────────────────────┐
│            FastAPI Webhook Receiver Endpoint           │
│                                                        │
│  - Step 1: Validate payload size (< 10 MB)             │
│  - Step 2: Verify HMAC cryptographic signature (< 2ms) │
│  - Step 3: Enqueue raw request into Ingestion Buffer   │
│  - Step 4: Return HTTP 202 Accepted immediately        │
└────────────────────┬───────────────────────────────────┘
                     │ (Elapsed: < 15ms)
                     ▼
┌────────────────────────────────────────────────────────┐
│               Ingestion Worker Pipeline                │
│                                                        │
│  - Step 5: Compute SHA-256 payload hash                │
│  - Step 6: Deduplication check against raw_evidence    │
│            (If hash exists -> discard duplicate)       │
│  - Step 7: Persist to raw_evidence (inline / S3)       │
│  - Step 8: Parse & persist normalized_events           │
│  - Step 9: Enqueue compilation task for Compiler       │
└────────────────────────────────────────────────────────┘
```

### 7.2 Pull Ingestion Pipeline (Historical Backfill & Delta Polling)

For historical synchronization and platforms with periodic delta APIs:

```text
Scheduled Cron / Worker Trigger (sync_delta)
                     │
                     ▼
┌────────────────────────────────────────────────────────┐
│           Delta Sync Orchestrator Service              │
│                                                        │
│  - Step 1: Load connector_connections cursor state     │
│  - Step 2: Check rate limit budget                     │
│  - Step 3: Invoke BaseConnector.sync_delta(cursor)     │
│  - Step 4: Batch fetch changes from provider API       │
│  - Step 5: Batch insert into raw_evidence (ON CONFLICT)│
│  - Step 6: Batch insert into normalized_events         │
│  - Step 7: Update connection cursor & last_synced_at   │
│  - Step 8: Enqueue compilation batch                   │
└────────────────────────────────────────────────────────┘
```

### 7.3 Transactional Outbox Pattern for Queue Reliability

To guarantee that an event is never lost between the database and the compilation queue worker:
1. `raw_evidence` and `normalized_events` are committed to PostgreSQL in a single ACID transaction.
2. An **Outbox Event** is recorded in the same transaction or dispatched via PostgreSQL `NOTIFY compiler_events, '{normalized_event_id}'`.
3. If the worker crashes immediately after insertion, the compilation queue query (`compilation_status = 'pending' ORDER BY occurred_at ASC`) automatically picks up uncompiled events on the next poll cycle.

---

## 8. Failure Handling, Retries, and Backoff Architecture

### 8.1 Error Classification & Handling Matrix

| Error Type | Examples | Handling Strategy | Connector Impact |
| :--- | :--- | :--- | :--- |
| **Transient Network / HTTP 5xx** | `502 Bad Gateway`, `503 Service Unavailable`, connection timeouts | Exponential backoff with jitter: `[2s, 4s, 8s, 16s, 32s]`, max 5 retries. | Stays `ACTIVE`. |
| **Rate Limit / HTTP 429** | `429 Too Many Requests`, GitHub secondary rate limits | Parse `Retry-After` or `X-RateLimit-Reset`. Suspend sync tasks until reset epoch. | State transitions to `DEGRADED` until reset. |
| **Auth Expiration** | `401 Unauthorized`, OAuth token expired | Trigger automated OAuth refresh token flow; retry failed request once. | If refresh succeeds: `ACTIVE`. If refresh fails: `DEGRADED`. |
| **Revoked Permissions** | `403 Forbidden`, app uninstalled, user revoked access | Stop polling immediately; log administrative security alert. | State transitions to `REVOKED`. |
| **Payload Malformation / Parsing Failure** | Corrupted JSON, unexpected schema mutation | Move raw payload to `quarantined_events` table (Dead Letter Queue). Do not block queue. | Stays `ACTIVE`. Generates engineering alert. |
| **Webhook Signature Mismatch** | Invalid HMAC, wrong secret | Reject immediately with HTTP 401; log IP and audit record. Do NOT retry. | None (security drop). |

### 8.2 Dead Letter Queue (DLQ) & Quarantine Model

When an event fails normalization or ingestion after 5 consecutive retries:
1. The record is marked `compilation_status = 'failed'`.
2. An entry is written to `quarantined_events` containing `{evidence_id, error_trace, payload_snapshot}`.
3. The worker moves on to subsequent events, ensuring a single bad payload ("poison pill") cannot stall the workspace ingestion pipeline.

---

## 9. Security, Privacy, and Secret Management

1. **Encrypted Token & Secret Storage**:
   - OAuth access tokens, refresh tokens, and webhook signing secrets must **NEVER** be stored in plaintext.
   - Credentials in `connector_connections.encrypted_credentials` are encrypted using **AES-256-GCM** with envelope encryption (workspace-keyed data keys protected by a master KMS key).
2. **Pre-Ingestion Secret & Key Redaction**:
   - To prevent secret sprawl, connectors apply deterministic regex filters *before* raw evidence is stored:
     - High-entropy strings matching private keys (`-----BEGIN RSA PRIVATE KEY-----`)
     - AWS access keys (`AKIA[0-9A-Z]{16}`)
     - GitHub Personal Access Tokens (`ghp_[0-9a-zA-Z]{36}`)
     - Generic API secrets (`sk_live_[0-9a-zA-Z]{24}`)
   - Detected secrets are replaced with `[REDACTED_SECRET]` before persistence.
3. **Repository & Channel Allowlisting**:
   - Workspace administrators configure an explicit allowlist in `connector_connections.config`:
     ```json
     {
       "github": { "repositories": ["org/repo-a", "org/repo-b"] },
       "slack": { "channels": ["C0123456", "C0987654"] }
     }
     ```
   - Inbound webhooks for unlisted repositories or channels are discarded immediately at Step 2 with an HTTP 200 response, preventing unrequested data capture.

---

## 10. Layer Boundaries & Software Architecture

In accordance with [`AGENTS.md` Section 6 (Layer Boundaries & Dependency Rules)](file:///F:/Projects/Fabric/AGENTS.md):

```text
backend/app/
├── domain/connectors/
│   ├── base.py                # Abstract BaseConnector interface
│   ├── events.py              # NormalizedEventType enums and domain entities
│   └── exceptions.py          # ConnectorAuthException, RateLimitExceededException
│
├── application/connectors/
│   ├── ingestion_service.py   # IngestionService (verifies, hashes, persists raw evidence)
│   ├── sync_orchestrator.py   # DeltaSyncOrchestrator (handles polling and cursor states)
│   └── ports.py               # Abstract RawEvidenceRepository & EventStore ports
│
├── infrastructure/connectors/
│   ├── github.py              # GitHubConnector implementation
│   ├── google.py              # GoogleWorkspaceConnector implementation
│   ├── microsoft.py           # Microsoft365Connector implementation
│   ├── slack.py               # SlackConnector implementation
│   └── crypto.py              # Constant-time HMAC signature verifiers
│
├── infrastructure/evidence/
│   ├── postgres_evidence_repo.py # Concrete PostgreSQL raw_evidence repository
│   ├── postgres_event_repo.py    # Concrete PostgreSQL normalized_events repository
│   └── object_storage.py         # S3/MinIO large payload store adapter
│
├── workers/ingestion/
│   ├── webhook_worker.py      # Async consumer for buffered webhook payloads
│   └── poller_worker.py       # Cron-triggered delta sync runner
│
└── api/webhooks/
    ├── github.py              # Fast HTTP POST receiver for GitHub webhooks
    ├── google.py              # Fast HTTP receiver for Google Pub/Sub
    ├── microsoft.py           # Fast HTTP receiver for Microsoft Graph notifications
    └── slack.py               # Fast HTTP receiver for Slack Events API
```

### Dependency Invariants:
1. `domain/connectors/` is pure Python (zero dependencies on FastAPI, SQLAlchemy, or HTTP clients).
2. `infrastructure/connectors/` implements `domain/connectors/base.py`.
3. `api/webhooks/` handlers are ultra-thin: verify signature, buffer payload, return HTTP 202.

---

## 11. Architectural Decisions (Decisions Record)

| ID | Decision | Rationale |
| :--- | :--- | :--- |
| **AD-INGEST-01** | **Deterministic Ingestion Boundary (Zero LLMs)** | Ingestion is real-time and high-throughput. LLMs introduce latency, cost, and non-determinism. Ingestion is 100% deterministic software; LLMs are isolated in the asynchronous Knowledge Compiler. |
| **AD-INGEST-02** | **Immutable Raw Evidence Store** | Raw evidence is preserved permanently with SHA-256 hashes. The derived Knowledge Graph and Vector Store can be wiped and recompiled from raw evidence at any time. |
| **AD-INGEST-03** | **Decoupled Normalized Event Envelope** | Downstream compilation must not depend on brittle third-party provider schemas. The `NormalizedEvent` provides a stable, unified domain model across all external tools. |
| **AD-INGEST-04** | **Hybrid Payload Storage (Inline + Object Store)** | Small payloads ($\le 64\text{ KB}$) stay in PostgreSQL JSONB for transactional simplicity and speed. Payloads $> 64\text{ KB}$ offload to S3/MinIO to maintain database index locality. |
| **AD-INGEST-05** | **Ultra-Fast Webhook Handshake (< 3s SLA)** | Webhook endpoints verify signatures, buffer payloads to workers, and return HTTP 202 in `< 15ms`, preventing external providers from dropping webhooks due to timeouts. |
| **AD-INGEST-06** | **Idempotent Deduplication at Database Level** | Unique constraint on `(workspace_id, source_system, external_id, payload_sha256)` guarantees safe handling of duplicate webhook deliveries and overlapping poll runs. |
| **AD-INGEST-07** | **Cursor-Based Delta Polling** | Polling relies on provider-native change tokens (GitHub commit SHAs, Google Drive page tokens, Microsoft Graph delta links), preventing expensive full scans. |
| **AD-INGEST-08** | **Pre-Storage Secret Redaction** | High-entropy private keys and API credentials are stripped before raw evidence is stored, preventing secret leakage into organizational memory. |
| **AD-INGEST-09** | **Transactional Outbox / Worker Queue Decoupling** | Raw evidence and normalized events commit in a single ACID transaction before enqueueing, guaranteeing zero event loss during worker restarts. |
| **AD-INGEST-10** | **Pluggable BaseConnector Architecture** | Every external provider implements a standardized interface (`BaseConnector`), isolating provider-specific HTTP quirks from the core ingestion pipeline. |

---

## 12. Open Questions for Review

The following questions are identified for review before implementation begins:

1. **Large Binary Attachment Handling**:
   - When a GitHub commit attaches large build assets or a Slack message includes a 50MB PDF, should Company Brain ingest the full file into object storage for text extraction, or ingest metadata only and fetch content on-demand via the provider API?
   - *Proposed Baseline*: Ingest text, diffs, and document markdown directly. For binary files, store metadata and fetch content on demand only when targeted for compilation.
2. **Backfill Depth Limits on Initial Connection**:
   - When a workspace connects an existing GitHub organization with 10 years of history, should initial sync backfill all historical commits or cap at a configurable window (e.g. past 90 days)?
   - *Proposed Baseline*: Default initial sync to the past 90 days with an optional administrative toggle for full historical backfill.
3. **Queue Technology for Compilation Handoff**:
   - For Phase 2/3 worker dispatch between Ingestion and the Knowledge Compiler, should we use PostgreSQL LISTEN/NOTIFY with an Outbox table, or Redis Streams?
   - *Proposed Baseline*: Start with PostgreSQL transactional outbox + worker polling for minimal operational overhead, with Redis Streams as the candidate for high-throughput scaling.
