# Company Brain — Architecture Specification

## 1. Product Vision

## 2. Core Architectural Principles

## 3. High-Level Architecture

Data Sources
    ↓
Connectors
    ↓
Ingestion
    ↓
Raw Evidence / Event Store
    ↓
Knowledge Compiler
    ↓
Entity Resolution + Ontology
    ↓
Knowledge Graph
    ↓
Embeddings / Indexes / Graph Analytics
    ↓
Permission-Aware Retrieval
    ↓
LLM / Agents
    ↓
Answer

## 4. Backend Architecture

api
schemas
domain
application
infrastructure
workers
shared

## 5. Domain Model

Organization
User
Team
Project
Task
...

## 6. Knowledge Model

Entities
Relationships
Facts
Events
Evidence
Provenance
Temporal information

## 7. Knowledge Compiler

Extraction
Resolution
Interpretation
Validation
Graph writing
Embedding writing

## 8. Ingestion Architecture

Google Workspace
Microsoft 365 / Teams
GitHub

## 9. Authorization

RBAC
ReBAC
Policy engine
Permission-aware retrieval

## 10. Retrieval

Query understanding
Retrieval planning
Graph retrieval
Vector retrieval
Community retrieval
Source retrieval
Temporal retrieval

## 11. Graph Analytics

Leiden
Communities
Centrality
Dependency paths
Community summaries

## 12. AI / Agents

Knowledge Agent
Project Agent
Employee Assistance
Onboarding
Handover

## 13. MCP

MCP should use the same authorization and retrieval layer.

## 14. Data Ownership

Source systems
Event store
Evidence store
Knowledge graph

## 15. Security & Privacy

Opt-in monitoring
Permissions
Audit
Data visibility
Deletion
...

## 16. Technology Decisions

Python
FastAPI
PostgreSQL
Neo4j candidate
pgvector candidate
Queue
OpenFGA candidate
LangGraph
...

## 17. Non-Goals

Things we explicitly don't want to build yet.