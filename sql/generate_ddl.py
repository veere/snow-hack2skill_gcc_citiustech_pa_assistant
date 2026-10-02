"""Generate SQL DDL files from the frozen schema contract."""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from config import schemas as S

cfg = S.load_config()
db = cfg["snowflake"]["database"]

# 00_database_schemas.sql
sql_dir = Path(cfg["_repo_root"]) / cfg["paths"]["sql"]
sql_dir.mkdir(parents=True, exist_ok=True)

lines = [
    "-- M360MART schema creation",
    "-- Generated from config/schemas.py - do not edit manually.",
    "",
    f"CREATE DATABASE IF NOT EXISTS {db};",
    "",
]
for schema in cfg["snowflake"]["schemas"].values():
    lines.append(f"CREATE SCHEMA IF NOT EXISTS {db}.{schema};")
lines.append("")

(sql_dir / "00_database_schemas.sql").write_text("\n".join(lines), encoding="utf-8")
print(f"Wrote {sql_dir / '00_database_schemas.sql'}")

# 10/20/30 - one DDL file per layer, all generated from the same specs.
#
# Silver and gold DDL were originally hand-written, which let the SQL drift from
# config/schemas_*.py. Generating all three from the registry means a spec change
# can no longer leave a stale CREATE TABLE behind.
LAYER_FILES = [
    ("bronze", "10_bronze_ddl.sql", "config/schemas_bronze.py"),
    ("silver", "20_silver_ddl.sql", "config/schemas_silver.py"),
    ("gold", "30_gold_ddl.sql", "config/schemas_gold.py"),
]

for layer, filename, source_module in LAYER_FILES:
    ddl_lines = [
        f"-- M360MART {layer.upper()} layer DDL",
        f"-- Generated from {source_module} by sql/generate_ddl.py - do not edit manually.",
        "-- All types are explicit (never inferred from data).",
        "",
    ]
    schema = cfg["snowflake"]["schemas"][layer]
    specs = S.layer_tables(layer)
    for spec in specs:
        ddl_lines.append(spec.ddl(db, schema))
        ddl_lines.append("")

    (sql_dir / filename).write_text("\n".join(ddl_lines), encoding="utf-8")
    print(f"Wrote {sql_dir / filename}")
    print(f"  {len(specs)} tables, {sum(len(s.columns) for s in specs)} columns")
