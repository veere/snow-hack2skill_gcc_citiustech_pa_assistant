"""
Load parquet files into Snowflake via batched INSERT statements.

Fallback loader for when the Python Snowflake connector cannot reach the
Snowflake account directly (e.g. network restrictions). Reads parquet files
and writes batched INSERT SQL to stdout (JSON-encoded), one batch per line.

Usage:
    python load/insert_loader.py --layer bronze --table ref_place_of_service --batch-size 500
    python load/insert_loader.py --layer gold --all --batch-size 500
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from config import schemas as S


def _sql_literal(val, sf_type: str) -> str:
    """Convert a Python value to a SQL literal string."""
    if val is None or (isinstance(val, float) and math.isnan(val)):
        return "NULL"
    if pd.isna(val):
        return "NULL"

    sf_upper = sf_type.upper()

    if "BOOLEAN" in sf_upper:
        return "TRUE" if val else "FALSE"

    if "VARIANT" in sf_upper:
        # VARIANT columns hold JSON text
        s = str(val).replace("'", "''")
        return f"PARSE_JSON('{s}')"

    if "TIMESTAMP" in sf_upper:
        return f"'{val}'"

    if "DATE" == sf_upper or sf_upper.startswith("DATE"):
        return f"'{val}'"

    if any(k in sf_upper for k in ("NUMBER", "INT", "FLOAT", "DECIMAL", "NUMERIC")):
        return str(val)

    # String types
    s = str(val).replace("'", "''")
    return f"'{s}'"


def generate_inserts(layer: str, table_name: str, batch_size: int = 500):
    """Yield (batch_index, sql) tuples for INSERT statements."""
    spec = S.get(layer, table_name)
    cfg = S.load_config()["snowflake"]
    schema = cfg["schemas"][layer]
    db = cfg["database"]
    fqn = f"{db}.{schema}.{table_name}"

    parquet_path = REPO_ROOT / cfg_paths(layer) / f"{table_name}.parquet"
    if not parquet_path.exists():
        raise FileNotFoundError(f"{parquet_path} not found")

    df = pd.read_parquet(parquet_path)
    col_names = spec.col_names()
    col_types = {c.name: c.sf_type for c in spec.columns}

    total_rows = len(df)
    if total_rows == 0:
        return

    variant_cols = {c.name for c in spec.columns if "VARIANT" in c.sf_type.upper()}
    has_variant = bool(variant_cols)

    for batch_start in range(0, total_rows, batch_size):
        batch_end = min(batch_start + batch_size, total_rows)
        batch_df = df.iloc[batch_start:batch_end]

        values_clauses = []
        for _, row in batch_df.iterrows():
            vals = []
            for col_name in col_names:
                val = row.get(col_name)
                if has_variant and col_name in variant_cols:
                    vals.append(_sql_literal(val, "VARCHAR"))
                else:
                    vals.append(_sql_literal(val, col_types[col_name]))
            values_clauses.append(f"({', '.join(vals)})")

        col_list = ", ".join(col_names)
        values_str = ",\n".join(values_clauses)

        if has_variant:
            aliases = [f"c{i}" for i in range(len(col_names))]
            select_parts = []
            for i, col_name in enumerate(col_names):
                if col_name in variant_cols:
                    select_parts.append(f"PARSE_JSON({aliases[i]})")
                else:
                    select_parts.append(aliases[i])
            sql = (
                f"INSERT INTO {fqn} ({col_list})\n"
                f"SELECT {', '.join(select_parts)}\n"
                f"FROM VALUES\n{values_str}\n"
                f"AS t({', '.join(aliases)})"
            )
        else:
            sql = f"INSERT INTO {fqn} ({col_list})\nVALUES\n{values_str}"

        yield batch_start // batch_size, sql


def cfg_paths(layer: str) -> str:
    cfg = S.load_config()
    if layer == "serving":
        return "dataFilesServing"
    return cfg["paths"][f"{layer}_out"]


def write_sql_files(layer: str, tables: list[str], batch_size: int, out_dir: Path):
    """Write one .sql file per batch to out_dir. Returns summary."""
    out_dir.mkdir(parents=True, exist_ok=True)
    summary = []
    for table_name in tables:
        parquet_path = REPO_ROOT / cfg_paths(layer) / f"{table_name}.parquet"
        meta = pq.read_metadata(parquet_path)
        total_batches = math.ceil(meta.num_rows / batch_size)
        files_written = []
        for batch_idx, sql in generate_inserts(layer, table_name, batch_size):
            fname = f"{layer}__{table_name}__b{batch_idx:04d}.sql"
            fpath = out_dir / fname
            fpath.write_text(sql, encoding="utf-8")
            files_written.append(fname)
            print(f"  wrote {fname} ({len(sql):,d} chars)", flush=True)
        summary.append({
            "table": table_name,
            "rows": meta.num_rows,
            "batches": len(files_written),
            "files": files_written,
        })
    return summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--layer", required=True)
    parser.add_argument("--table", default=None)
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--info-only", action="store_true",
                        help="Just print table info, no SQL")
    parser.add_argument("--out-dir", default=None,
                        help="Write .sql files to this directory instead of stdout JSONL")
    args = parser.parse_args()

    if args.all:
        if args.layer == "serving":
            tables = ["regulatory_sources", "regulatory_chunks", "radiology_reference"]
        else:
            tables = [s.name for s in S.layer_tables(args.layer)]
    elif args.table:
        tables = [args.table]
    else:
        parser.error("--table or --all required")
        return

    if args.info_only:
        for table_name in tables:
            parquet_path = REPO_ROOT / cfg_paths(args.layer) / f"{table_name}.parquet"
            meta = pq.read_metadata(parquet_path)
            print(json.dumps({
                "table": table_name,
                "layer": args.layer,
                "rows": meta.num_rows,
                "batches": math.ceil(meta.num_rows / args.batch_size),
            }))
        return

    if args.out_dir:
        summary = write_sql_files(args.layer, tables, args.batch_size, Path(args.out_dir))
        total_files = sum(s["batches"] for s in summary)
        total_rows = sum(s["rows"] for s in summary)
        print(f"\n{len(summary)} tables, {total_rows:,d} rows, {total_files} SQL files written to {args.out_dir}")
        print(json.dumps(summary, indent=2))
        return

    for table_name in tables:
        for batch_idx, sql in generate_inserts(args.layer, table_name, args.batch_size):
            print(json.dumps({
                "table": table_name,
                "batch": batch_idx,
                "sql": sql,
            }))


if __name__ == "__main__":
    main()
