# PA Insight Copilot

A member-anchored copilot for prior-authorization (PA) review analysts built on Snowflake Cortex. An analyst looks up a health plan member, sees their open PA requests, criteria evidence, financials, and clinical summary, then asks natural-language questions grounded in either member data or a regulatory document corpus. The copilot surfaces cited, verifiable evidence and **never makes or implies a determination** (approve/deny/pend).

## Problem

Prior-authorization review is time-pressured, documentation-heavy, and spread across disconnected systems. Analysts juggle member eligibility, clinical records, regulatory deadlines, benefit rules, and precedent lookups while meeting SLA clocks measured in hours. Missing a deadline is a regulatory breach; missing a criterion is a quality defect. Today this context-gathering is largely manual.

## Value Add

- **Single pane of glass** for all PA-relevant data: eligibility, clinical summary, financials, criteria evidence, SLA tracking, decision history, provider network status, and duplicate/safety flags.
- **Grounded answers with citations** — every response traces back to a specific Snowflake query (with SQL, row count, and query ID) or a specific regulatory document section (with citation ID, page number, and live source URL).
- **Non-decision by design** — the copilot reports evidence; the analyst makes the call. Enforced at three independent layers: system prompt, input refusal (regex-based routing), and output guard (14+ banned-pattern regexes). No single-point-of-failure path can produce a determination.
- **Regulatory retrieval over public-domain law** — 626 chunks from 10 US federal and state government works (CMS-0057-F, 42 CFR 422/438, 29 CFR 2560, Texas HB 3459, OIG). Proprietary criteria (InterQual, MCG) are deliberately excluded.
- **Governed data layer** — the app reads only the 14 non-PII SERVING views through `M360_APP_ROLE`. It structurally cannot see Bronze, Silver, or Gold tables.

## Features

| Feature | Powered By |
|---------|-----------|
| Member data Q&A (eligibility, PAs, criteria, financials, timeline) | Cortex Analyst + `SV_PA_COPILOT` semantic view (12 tables, 118 columns, 12 verified queries) |
| Regulatory Q&A (TAT deadlines, ERISA, Texas exemptions) | Cortex Search + `SVC_REGULATORY` (626 chunks, 52 citations) |
| Decision-seeking refusal | Rule-based router + `DECISION_SEEKING` regex |
| Output guard (blocks decision language in responses) | 14+ regex patterns, distinguishes determinations from factual reporting |
| SLA countdown with breach/at-risk/on-track states | Gold layer SLA engine (EMERGENT 24h, EXPEDITED 72h, STANDARD 7/15 days by LOB) |
| Criteria evidence (MET/UNMET/INDETERMINATE per criterion) | Silver criteria engine (63 versioned rules, 32,501 evaluations) |
| Historical precedent (approval rates by service code + diagnosis) | Gold aggregates over decided requests |
| Provider network + gold-card eligibility | Gold provider summary |
| Duplicate detection + safety flags | Gold duplicate/safety analysis |
| Radiology reference images (teaching orientation only) | 24 CC0/PD/CC BY Wikimedia images, licence-gated |
| Suggested questions (contextual, never directive) | Grounding module, scoped to member's open PAs |

## Architecture

```
Open Datasets (Synthea, FDA NDC, CMS ICD-10/HCPCS, NPPES, SynPUF)
    |
    v
BRONZE (17 tables, 987K rows) -- reference + dimension + fact + external
    |
    v
SILVER (19 tables, 131K rows) -- identity resolution, criteria engine
    |
    v
GOLD (13 tables, 93K rows) -- analyst-ready aggregates
    |
    v
SERVING (14 views, non-PII) + Cortex Search + Semantic View
    |
    v
Streamlit Copilot (SiS or self-hosted)
```

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Language | Python 3.14 |
| UI | Streamlit 1.51 |
| Database | Snowflake (M360MART, 4 schemas, 52 tables) |
| NL-to-SQL | Cortex Analyst via `SV_PA_COPILOT` semantic view |
| Regulatory search | Cortex Search via `SVC_REGULATORY` |
| LLM | claude-sonnet-4-5 (primary), llama3.1-8b (fallback) |
| Data pipeline | pandas, pyarrow (runs locally, loads to Snowflake) |

## Data

All data is synthetic or sourced from public-domain open datasets. No real patient data exists anywhere in this project.

- **1,171 synthetic patients** from Synthea, time-shifted to current dates
- **6,099 PA requests** with deterministic criteria evaluation
- **10 regulatory documents** (US federal/state government works)
- **24 radiology teaching images** (Creative Commons)
- **Deterministic pipeline** — same seed (`20260901`) produces identical output

## Project Structure

```
app/            Streamlit copilot (main.py, grounding, answering, UI, snow connector)
config/         Frozen schema contracts, app config, mart config
dataPrepBronze/ Bronze layer generators (8 scripts, fetch + build + validate)
dataPrepSilver/ Silver layer builders (member, PA, provider)
dataPrepGold/   Gold layer builders (member, PA aggregates)
dataPrepRegulatory/ Regulatory corpus fetcher + chunker
dataPrepRadiology/  Radiology reference image pipeline
load/           Snowflake loader (PUT + COPY INTO with reconciliation)
sql/            DDL scripts (generated + hand-authored)
semantic/       Semantic view YAML (SV_PA_COPILOT)
validate/       Test suites (E2E, UI, grain, contract, SLA, browser, API)
common/         Shared utilities (frames, dates, determinism, identity)
docs/           PA analyst domain research
```

## Deployment Modes

| Mode | Command | Use Case |
|------|---------|----------|
| **Streamlit in Snowflake** | `python app/deploy_sis.py` | Standalone analyst surface in Snowsight |
| **Self-hosted** | `python app/serve.py` | Embeddable in iframe (SiS cannot be iframed) |

## Verification

28 automated checks across 5 test suites, all passing:

- **Cortex Search**: 5/5 regulatory questions with correct citations
- **Member data**: 14 views populated, member lookup across all views, edge cases
- **Decision refusal**: 8/8 decision-seeking questions refused
- **Output guard (block)**: 6/6 decision statements caught
- **Output guard (allow)**: 5/5 factual statements passed

## Quick Start

See [devGuide.md](docs/devGuide.md) for full reproduction steps from a fresh clone to a running deployment.

## Existing Documentation

- [datamart.md](docs/datamart.md) — Medallion architecture, table inventory, governance, RBAC
- [app.md](docs/app.md) — Copilot architecture, deployment, citations, UX, defects
- [datasets.md](docs/datasets.md) — Data sourcing, synthesis methodology, crosswalks, defects
- [PA_analyst_research.md](docs/PA_analyst_research.md) — PA workflow, criteria frameworks, regulatory TAT rules
