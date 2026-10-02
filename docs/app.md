# PA Insight Copilot

A member-anchored copilot for prior-authorization review analysts, built on the
`M360MART.SERVING` layer. It answers questions about a member with citations, and it
does not make decisions.

---

## 1. What it is, and what it deliberately is not

| It does | It does not |
|---|---|
| Surface a member's eligibility, criteria evidence, SLA position, history and financials | Recommend, imply or rank an approval, denial or pend |
| Cite the exact SQL or the exact regulation behind every statement | Present an answer without a verifiable source |
| Report absent evidence as `INDETERMINATE` | Collapse criteria into an overall pass/fail |
| Retrieve and quote public-domain regulation | Redistribute proprietary criteria (InterQual, MCG) |
| Show reference anatomy for orientation | Interpret the member's own imaging |

The non-decision rule is enforced **three times**, because one layer is not enough:

1. **System prompt** — `NON_DECISION_SYSTEM_RULE` in `config/app_config.py`.
2. **Input refusal** — `grounding.DECISION_SEEKING` catches decision-seeking questions
   *before* any model call, so no determination is ever generated.
3. **Output guard** — `grounding.check_output()` scans the draft. A hit **blocks** the
   answer rather than editing it, because silently rewriting output hides the failure.

The guard is deliberately precise about *framing*. It blocks the copilot **asserting**
a determination but permits it **reporting** one: `"the recorded denial reason was
'Does not meet medical necessity'"` is a fact from `denial_reason_desc`. An earlier,
blunter version blocked exactly that — censoring the member's own history and quoted
regulation. Both behaviours are locked in by tests.

---

## 2. Architecture

```
member_id  (typed, or ?member_id=… from a host application)
     │
     ├── Overview ─── direct SQL on SERVING views
     │                 tiles · open worklist · criteria evidence
     │
     ├── Ask ──────── router (rule-based, not a model)
     │                 ├─ decision-seeking ──► REFUSED, no model call
     │                 ├─ member data ───────► Cortex Analyst → SV_PA_COPILOT
     │                 │                        └─ cite: generated SQL, views, rows, ms
     │                 ├─ regulatory ────────► Cortex Search → SVC_REGULATORY
     │                 │                        └─ cite: citation_id, section, URL, licence
     │                 └─ both ──────────────► blended, citations from both
     │
     └── Reference ─── licence-gated teaching images, scoped to open service categories
```

Routing is rule-based on purpose. An LLM classifier would add a second failure mode
and a second latency hit for a decision a handful of keywords settles reliably.

### Files

| File | Role |
|---|---|
| `app/main.py` | Page flow and state. No markup, no queries inline. |
| `app/ui.py` | Presentation kit — design tokens, banner, tiles, source cards. |
| `app/snow.py` | Dual-mode connection (SiS session vs PAT connector) + Cortex helpers. |
| `app/grounding.py` | Router, refusal, output guard, member context, suggestions. |
| `app/answering.py` | Cortex Analyst + Cortex Search calls and citation assembly. |
| `app/contracts.py` | **Frozen** types. `Answer` refuses to exist without a `Citation`. |
| `app/serve.py` | Launcher for self-hosted mode. |
| `app/embed.py` | Writes the iframe CSP config and an nginx fragment. |
| `app/deploy_sis.py` | Deploys to Streamlit in Snowflake. |

`Answer.__post_init__` raises when a factual route carries no citation. An uncited
answer is therefore a **construction error**, not something a reviewer must notice.

---

## 3. Deployment: two targets, one codebase

**The iframe requirement cannot be met by Snowflake-hosted Streamlit.** Streamlit in
Snowflake, SPCS and App Runtime all sit behind a proxy that injects
`X-Frame-Options: DENY` and `Cross-Origin-Resource-Policy: same-origin`, and the SiS
documentation states the CSP "is not configurable at this time". There is no flag or
URL parameter that relaxes it. So:

### Standalone (Streamlit in Snowflake)

```
cortex secret run --map snowflake_pat=SNOWFLAKE_PAT -- python app/deploy_sis.py
```

Deployed as `M360MART.SERVING.PA_INSIGHT_COPILOT`. Auth is the Snowsight session.
A host application can still set context by deep-linking:

```
…#/streamlit-apps/M360MART.SERVING.PA_INSIGHT_COPILOT?streamlit-member_id=MBR-12345678
```

(SiS prefixes query-param keys with `streamlit-` in the URL and strips them again, so
`st.query_params["member_id"]` works identically in both modes.)

### Embedded (self-hosted)

```
python app/embed.py --allow https://portal.payer.example.com
cortex secret run --map snowflake_pat=SNOWFLAKE_PAT -- python app/serve.py
```

```html
<iframe src="https://your-host/pa-copilot/?member_id=MBR-12345678"
        style="width:100%;height:900px;border:0"
        referrerpolicy="strict-origin"></iframe>
```

`app/embed.py` writes `.streamlit/config.toml` and `deploy/nginx-pa-copilot.conf`.
Two things about it are deliberate:

- **A wildcard origin is refused.** `frame-ancestors *` would let any site frame a
  surface showing member health data. Hosts must be named.
- **XSRF protection is disabled** so a cross-origin iframe can function. That is
  precisely why **the reverse proxy must authenticate the analyst** — the app cannot.

---

## 4. Citations

Neither Cortex service produces a citation on its own, so they are constructed.

**Cortex Analyst** returns the SQL it generated and a `verified_query_used` name when
a VQR matched. It returns **no confidence score** — the only confidence signal is
whether a verified query matched. So a data citation carries: the SQL, the views
touched, row count, elapsed ms, the Snowflake query id, and the VQR name if any.

**Cortex Search** returns *only* the attribute columns declared on the service.
`SVC_REGULATORY` therefore declares every field a citation needs — `citation_id`,
`section_label`, `heading`, `page_no`, `authority`, `source_url`, `effective_date`,
`licence`. Omitting `columns` from a query returns hits whose every field is null,
which looks like an empty corpus but is an under-specified request.

`Citation.is_verifiable()` encodes the standard: a query needs SQL and objects; a
regulation needs a citation id and a live URL; an image needs a URL, licence and
attribution. The UI warns when any citation falls short.

---

## 5. UX principles

**Status reflection.** Answering takes 5–20 seconds because it may call Analyst,
execute generated SQL, search the corpus and summarise. `st.status` names the current
step, so a slow answer never looks like a hang. Results state their provenance and
timing.

**Action feedback.** Every button acknowledges immediately; every outcome —
answered, empty, refused, blocked, failed — has a visually distinct treatment
(`st.error` / `st.info` / `st.warning` with distinct icons). Loading a member emits a
toast. Skeleton tiles hold the layout so it never jumps.

**Honest absence.** A terminated member has no current-year accumulator, so the tile
reads **"not on record"**, never `$0`. Rendering zero would assert a balance the data
does not support. This is asserted by a UI test.

**Suggested questions** are derived from the member's live state, and each shows *why*
it appeared ("surfaced because 4 requests already show SLA_STATE = BREACHED"). Every
suggestion asks for facts; none points toward an outcome, and the validator asserts
that none trips the decision guard.

---

## 6. Verification

| Suite | Covers |
|---|---|
| `validate/validate_app.py` | Live end-to-end: objects, citation URLs, licence gate, member edge cases, 8 refusals, 5 regulatory questions, 6 member questions, output guard both ways. **0 failures.** |
| `validate/test_ui.py` | Headless render via `AppTest` across 6 scenarios incl. unknown member, no-history member, terminated member. **30 checks, 0 failures.** |
| `validate/test_sql_split.py` | Quote/comment-aware SQL splitting. |
| `validate/test_radiology_labels.py` | 16 regression cases, every one a real mislabel. |
| `validate/check_grain.py` | View grain, required NOT-NULLs, approval-rate honesty. |

```
cortex secret run --map snowflake_pat=SNOWFLAKE_PAT -- python validate/validate_app.py
cortex secret run --map snowflake_pat=SNOWFLAKE_PAT -- python validate/test_ui.py
```

---
