# Developer Guide — Reproducing the PA Insight Copilot

This guide walks through reproducing the full running state of the PA Insight Copilot from a fresh clone. The pipeline generates synthetic data locally, loads it into Snowflake, deploys Cortex services, and launches the Streamlit app.

## Prerequisites

### Snowflake Account

- A Snowflake account with `ACCOUNTADMIN` role (or a role with `CREATE DATABASE`, `CREATE ROLE`, `CREATE WAREHOUSE` privileges)
- Warehouse `COMPUTE_WH` (or update `config/mart_config.json` to use a different warehouse)
- Cortex AI functions enabled (for `AI_COMPLETE`, Cortex Analyst, Cortex Search)
- Cross-region inference enabled if outside the model's home region

### Local Machine

- **Python 3.11+** (tested on 3.14.7)
- **pip** for dependency installation
- **~500 MB free disk** for cached downloads + generated parquet files
- **Internet access** for fetching open datasets (Synthea, FDA, CMS, NPPES)

### Snowflake Authentication

The pipeline uses non-interactive auth only. Set one of these environment variables:

| Method | Environment Variable | Recommended |
|--------|---------------------|-------------|
| Programmatic Access Token | `SNOWFLAKE_PAT` | Yes |
| Key-pair | `SNOWFLAKE_PRIVATE_KEY_PATH` | Yes |
| Password | `SNOWFLAKE_PASSWORD` | Fallback |

Interactive browser OAuth is deliberately not supported — its token expires mid-run.

Store credentials securely with Cortex Code:

```bash
cortex secret store snowflake_pat --prompt
```

### Snowflake Connection Profile

Create or update `~/.snowflake/connections.toml` with your account details:

```toml
[YourConnectionName]
account = "your-org-your-account"
user = "YOUR_USER"
role = "ACCOUNTADMIN"
```

Then update `config/mart_config.json` to reference your connection:

```json
"snowflake": {
    "connection_name": "YourConnectionName",
    ...
}
```

## Step 1 — Install Dependencies

```bash
pip install -r requirements.txt
```

Key packages: `pandas`, `pyarrow`, `snowflake-connector-python`, `streamlit`, `requests`, `pypdf`, `Pillow`.

## Step 2 — Generate Bronze Layer (Synthetic Data)

This fetches 6 open datasets (~27 MB), builds reference tables, generates synthetic members, PA requests, transactions, and events. Output: 17 parquet files in `dataFilesBronze/`.

```bash
python dataPrepBronze/run_bronze.py
```

**What happens (8 scripts in order):**

| Script | What it does |
|--------|-------------|
| `00_fetch_open_datasets.py` | Downloads Synthea, FDA NDC, CMS ICD-10, HCPCS, SynPUF, NPPES. Computes the date anchor offset. |
| `01_build_reference_tables.py` | Builds `ref_icd10cm`, `ref_hcpcs`, `ref_ndc_product`, `ref_provider_npi`, `ref_place_of_service`, `ref_synpuf_benchmark` |
| `02_build_dim_member.py` | Builds `dim_memberProfile`, `dim_memberPlan`, `dim_memberFinance`, `dim_memberHistory` |
| `03_build_ext_patient.py` | Builds `ext_patientProfile`, `ext_patientClinicalRecords`, `ext_patientEhrEvents`, `ext_patientMedicalHistory` |
| `04_build_fct_transactions.py` | Builds `fct_memberTransactions` |
| `05_build_fct_events.py` | Builds `fct_memberEvents` |
| `06_build_fct_pa.py` | Builds `fct_memberPA` (grounded PA synthesis with criteria) |
| `07_validate_bronze.py` | Validates PKs, FKs, NOT NULLs, enums, banned columns |

**Duration:** ~5 minutes on a modern machine. First run downloads ~27 MB of open data (cached for subsequent runs in `.cache/`).

**Determinism:** Output is byte-identical across runs with the same seed (`master_seed: 20260901` in `mart_config.json`). Only `ingested_at_utc` varies.

## Step 3 — Generate Silver Layer

Builds identity-resolved member profiles, PA case records, criteria evaluation, and provider profiles. Output: 19 parquet files in `dataFilesSilver/`.

```bash
python dataPrepSilver/build_silver_member.py
python dataPrepSilver/build_silver_pa.py
python dataPrepSilver/build_silver_provider.py
```

## Step 4 — Generate Gold Layer

Builds analyst-ready aggregates: worklist, request 360, criteria evidence, SLA tracking, decision audit, precedent, eligibility, clinical summary, financials, timeline, provider network, KPIs. Output: 13 parquet files in `dataFilesGold/`.

```bash
python dataPrepGold/build_gold_member.py
python dataPrepGold/build_gold_pa.py
```

## Step 5 — Generate Regulatory Corpus and Radiology Library

```bash
python dataPrepRegulatory/fetch_regulatory.py
python dataPrepRegulatory/chunk_regulatory.py
python dataPrepRadiology/fetch_radiology.py
```

Output: `dataFilesServing/regulatory_sources.parquet`, `regulatory_chunks.parquet`, `radiology_reference.parquet`.

## Step 6 — Load Data into Snowflake

### 6a — Deploy Database, Schemas, and Table DDL

```bash
cortex secret run --map snowflake_pat=SNOWFLAKE_PAT -- python load/deploy_sql.py --all
```

This executes `sql/00_database_schemas.sql` through `sql/50_rbac.sql`, creating:
- Database `M360MART`
- Schemas: `BRONZE`, `SILVER`, `GOLD`, `SERVING`
- All 49 typed tables (from frozen schema specs)
- 14 SERVING views
- RBAC roles `M360_APP_ROLE` and `M360_ENGINEER_ROLE`

### 6b — Load Parquet Data

```bash
cortex secret run --map snowflake_pat=SNOWFLAKE_PAT -- python load/run_load.py --layer all
```

This PUT-uploads each parquet file to an internal stage and COPY-INTOs it with explicit column projection and type casting. Every table's row count is reconciled against the parquet source — any mismatch fails the load.

**Expected output:**
- BRONZE: 17 tables, 987,484 rows
- SILVER: 19 tables, 131,430 rows
- GOLD: 13 tables, 92,864 rows

### 6c — Deploy Regulatory and Radiology Objects

```bash
cortex secret run --map snowflake_pat=SNOWFLAKE_PAT -- python dataPrepRegulatory/load_regulatory.py
cortex secret run --map snowflake_pat=SNOWFLAKE_PAT -- python dataPrepRadiology/load_radiology.py
```

This creates:
- `SERVING.REGULATORY_SOURCES` (10 rows) and `SERVING.REGULATORY_CHUNKS` (626 rows)
- `SERVING.RADIOLOGY_REFERENCE` (24 rows)
- Stages `STG_REGULATORY` and `STG_RADIOLOGY` with uploaded files

### 6d — Deploy Cortex Search Service

Execute `sql/61_regulatory_search.sql` to create the search service:

```sql
-- Via deploy_sql.py or manually:
CREATE OR REPLACE CORTEX SEARCH SERVICE M360MART.SERVING.SVC_REGULATORY
  ON chunk_text
  ATTRIBUTES chunk_id, doc_key, citation_id, section_label, heading, page_no,
             themes, authority, source_url, effective_date, licence
  WAREHOUSE = COMPUTE_WH
  TARGET_LAG = '1 day'
AS
  SELECT chunk_text, chunk_id, doc_key, citation_id, section_label, heading,
         page_no, themes, authority, source_url, effective_date, licence
  FROM M360MART.SERVING.REGULATORY_CHUNKS;
```

### 6e — Deploy Semantic View

The semantic view YAML is at `semantic/SV_PA_COPILOT.sv.yaml`. Deploy it:

```sql
CALL SYSTEM$CREATE_SEMANTIC_VIEW_FROM_YAML(
  'M360MART.SERVING',
  $$ <contents of semantic/SV_PA_COPILOT.sv.yaml> $$
);
```

Or via Snowsight: navigate to the SERVING schema and create a semantic view from the YAML file.

### 6f — Deploy App-Layer RBAC

Execute `sql/70_app_rbac.sql` to grant the app role access to regulatory tables, stages, Cortex Search, the semantic view, and Cortex AI functions.

## Step 7 — Deploy the Streamlit App

### Option A: Streamlit in Snowflake (standalone)

```bash
cortex secret run --map snowflake_pat=SNOWFLAKE_PAT -- python app/deploy_sis.py
```

This uploads the app bundle to `STG_APP`, validates the `environment.yml` against the Snowflake conda channel, and creates the STREAMLIT object `PA_INSIGHT_COPILOT`. Access it in Snowsight under **Projects > Streamlit**.

### Option B: Self-hosted (embeddable)

```bash
cortex secret run --map snowflake_pat=SNOWFLAKE_PAT -- python app/serve.py
```

Opens on `http://localhost:8501`. This mode can be embedded in an iframe (SiS cannot).

For iframe embedding, first generate the CSP config:

```bash
python app/embed.py --allow https://your-portal.example.com
```

Then deploy with nginx using the generated `deploy/nginx-pa-copilot.conf`.

## Step 8 — Verify

### Quick Smoke Test

Open the Streamlit app (Snowsight or localhost) and enter member ID `MBR-01925384`. You should see:
- Member banner with name, LOB (COMMERCIAL), and ACTIVE status
- Open PA requests in the Overview tab with SLA states
- The Ask tab accepts questions like "What criteria are unmet on this member's open requests?"

### Automated Test Suites

```bash
# End-to-end (live Cortex services): 8 refusals, 5 regulatory, 6 member Qs, output guard
cortex secret run --map snowflake_pat=SNOWFLAKE_PAT -- python validate/validate_app.py

# Headless UI tests: 6 scenarios, 30 checks
cortex secret run --map snowflake_pat=SNOWFLAKE_PAT -- python validate/test_ui.py

# Offline checks (no Snowflake connection needed)
python validate/check_streamlit_api.py    # API version floor
python validate/test_radiology_labels.py  # 16 image label regressions
```

### Data Quality Checks

```bash
cortex secret run --map snowflake_pat=SNOWFLAKE_PAT -- python validate/check_grain.py
cortex secret run --map snowflake_pat=SNOWFLAKE_PAT -- python validate/check_contract.py
cortex secret run --map snowflake_pat=SNOWFLAKE_PAT -- python validate/check_sla_consistency.py
```

## Configuration Reference

### `config/mart_config.json`

| Key | Purpose | Default |
|-----|---------|---------|
| `scale.max_members` | Member cap | 1000 |
| `determinism.master_seed` | Reproducibility seed | 20260901 |
| `date_anchor.recent_activity_buffer_days` | Days before today for the latest activity | 3 |
| `snowflake.connection_name` | Profile in `connections.toml` | (set to your profile) |
| `snowflake.database` | Target database | M360MART |
| `snowflake.warehouse` | Compute warehouse | COMPUTE_WH |

### `config/app_config.py`

| Key | Purpose | Default |
|-----|---------|---------|
| `PRIMARY_MODEL` | LLM for answer generation | claude-sonnet-4-5 |
| `FALLBACK_MODEL` | Backup model | llama3.1-8b |
| `SEMANTIC_VIEW` | Cortex Analyst grounding | SV_PA_COPILOT |
| `SEARCH_SERVICE` | Regulatory search | SVC_REGULATORY |
| `MAX_SUGGESTED_QUESTIONS` | Contextual suggestions shown | 4 |

## Troubleshooting

| Problem | Cause | Fix |
|---------|-------|-----|
| `No non-interactive Snowflake credential found` | No `SNOWFLAKE_PAT`/key/password set | `cortex secret store snowflake_pat --prompt` |
| `Could not connect to Snowflake backend` | Wrong account in `connections.toml` | Update the `account` field to match your Snowflake account identifier |
| `Packages not found: python==3.11` | Python pinned in `environment.yml` | Never pin Python in conda specs for SiS — the interpreter is not a package |
| `Anaconda dependency versions must be characters...` | PEP 440 `==` in `environment.yml` | Use single `=` (conda syntax), not `==` |
| `FormMixin.form() got unexpected keyword argument 'border'` | Streamlit version too old in SiS | Ensure `environment.yml` pins `streamlit=1.52.1` |
| COPY INTO loads 0 rows silently | Stage filename case mismatch | PUT preserves case; COPY reference must match exactly |
| `unknown model` from Cortex | Model ID transposed | Use `claude-sonnet-4-5`, NOT `claude-4-5-sonnet` |

## Demo Member IDs

| Member ID | Name | LOB | Good for |
|-----------|------|-----|----------|
| `MBR-01925384` | Mariam Bogisich | COMMERCIAL | 13 PAs, 5 breached, $4,677 deductible |
| `MBR-18847811` | Walker Kuhic | MARKETPLACE | 40 PAs, 8 breached — heaviest caseload |
| `MBR-30205841` | Logan Brekke | MEDICARE_ADVANTAGE | 30 PAs, 9 breached, 35 chronic conditions |
