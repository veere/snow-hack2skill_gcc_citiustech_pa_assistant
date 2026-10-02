"""
Parquet read/write that conforms a DataFrame to its frozen spec.

`write_table` is the ONLY sanctioned way to emit a mart table. It enforces the
contract at the moment of writing rather than discovering drift three layers
later:

  * every spec column is present (missing nullable columns are filled with NULL;
    a missing NOT NULL column is an error)
  * unexpected extra columns are an error, not silently dropped - a stray column
    usually means a typo against the contract
  * columns are reordered to the declared physical order
  * dtypes are coerced from the Snowflake type, so DATE really is a date and
    BOOLEAN really is a bool before it ever reaches Snowflake
  * VARIANT columns are serialised to JSON text, which is what the loader's
    PARSE_JSON transform expects
  * the audit trailer is populated, including `record_hash`

Doing all of this in one place is what lets four generator tracks run in parallel
and still produce mutually loadable output.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from common import determinism as det
from config import schemas as S


def layer_dir(layer: str) -> Path:
    cfg = S.load_config()
    key = {"bronze": "bronze_out", "silver": "silver_out", "gold": "gold_out"}[layer]
    path = Path(cfg["_repo_root"]) / cfg["paths"][key]
    path.mkdir(parents=True, exist_ok=True)
    return path


def table_path(layer: str, name: str) -> Path:
    return layer_dir(layer) / f"{name}.parquet"


# ---------------------------------------------------------------------------
# dtype coercion
# ---------------------------------------------------------------------------

def _is_variant(sf_type: str) -> bool:
    return sf_type.upper().startswith(("VARIANT", "ARRAY", "OBJECT"))


def _coerce(series: pd.Series, col: S.Col) -> pd.Series:
    t = col.sf_type.upper()

    if _is_variant(t):
        # Serialise lists/dicts to JSON text; the loader wraps these in PARSE_JSON.
        def _dump(v: object) -> str | None:
            if v is None:
                return None
            if isinstance(v, str):
                return v
            if isinstance(v, np.ndarray):
                v = v.tolist()
            if isinstance(v, (list, tuple, dict)):
                return json.dumps(v, separators=(",", ":"), default=str)
            if pd.isna(v):
                return None
            return json.dumps(v, default=str)

        return series.map(_dump).astype("object")

    if t == "DATE":
        return pd.to_datetime(series, errors="coerce").dt.date

    if t.startswith("TIMESTAMP"):
        out = pd.to_datetime(series, errors="coerce")
        # Snowflake TIMESTAMP_NTZ is wall-clock with no zone; strip any tz so the
        # value written is exactly the value loaded.
        if isinstance(out.dtype, pd.DatetimeTZDtype):
            out = out.dt.tz_convert("UTC").dt.tz_localize(None)
        return out

    if t == "BOOLEAN":
        mapped = series.map(
            lambda v: None if v is None or (not isinstance(v, str) and pd.isna(v))
            else bool(v) if not isinstance(v, str)
            else v.strip().lower() in {"true", "t", "y", "yes", "1"}
        )
        return mapped.astype("boolean")

    if t.startswith("NUMBER"):
        inner = t[t.find("(") + 1: t.find(")")] if "(" in t else "38,0"
        parts = [p.strip() for p in inner.split(",")]
        scale = int(parts[1]) if len(parts) > 1 else 0
        numeric = pd.to_numeric(series, errors="coerce")
        if scale == 0:
            return numeric.round(0).astype("Int64")
        return numeric.astype("Float64").round(scale)

    # VARCHAR and anything else -> nullable string
    out = series.astype("object").where(series.notna(), None)
    return out.map(lambda v: None if v is None else str(v))


# ---------------------------------------------------------------------------
# write / read
# ---------------------------------------------------------------------------

def write_table(
    df: pd.DataFrame,
    layer: str,
    name: str,
    *,
    source_system: str,
    quiet: bool = False,
) -> Path:
    """Conform `df` to its frozen spec, populate the audit trailer, write parquet."""
    spec = S.get(layer, name)
    expected = spec.col_names()
    audit = {c.name for c in S.AUDIT_COLS}

    out = df.copy()

    # Reject unexpected columns - almost always a typo against the contract.
    extra = [c for c in out.columns if c not in expected]
    if extra:
        raise ValueError(
            f"{layer}.{name}: columns not in the frozen spec: {extra}\n"
            f"Either remove them or update the spec (bronze specs are frozen)."
        )

    # Fill absent columns. Absent NOT NULL business columns are a hard error.
    for col in spec.columns:
        if col.name in out.columns or col.name in audit:
            continue
        if not col.nullable:
            raise ValueError(
                f"{layer}.{name}: required NOT NULL column {col.name!r} was not "
                f"produced by the generator"
            )
        out[col.name] = None

    # Audit trailer. record_hash is computed after coercion so the hash is taken
    # over canonical values rather than whatever the generator happened to pass.
    out["source_system"] = source_system
    out["ingested_at_utc"] = det.ingested_at()
    if "record_hash" not in out.columns:
        out["record_hash"] = None

    out = out[expected]

    for col in spec.columns:
        if col.name in ("record_hash", "ingested_at_utc"):
            continue
        out[col.name] = _coerce(out[col.name], col)

    out["record_hash"] = det.row_hashes(out)
    out["ingested_at_utc"] = pd.to_datetime(out["ingested_at_utc"])

    # Primary key uniqueness is enforced HERE, at write time.
    #
    # Snowflake accepts a PRIMARY KEY constraint but does not enforce it, so a
    # duplicate surrogate key loads without complaint and then silently fans out
    # every downstream join - a member with a duplicated key would have its
    # claims counted twice. Surrogate keys are truncated hashes, which collide at
    # surprisingly small volumes (~50% chance across 1,200 keys at 6 digits), so
    # this has to be checked rather than assumed.
    if spec.primary_key:
        pk = list(spec.primary_key)
        dup_count = int(out.duplicated(subset=pk).sum())
        if dup_count:
            sample = (
                out.loc[out.duplicated(subset=pk, keep=False), pk]
                .head(5)
                .to_dict("records")
            )
            raise ValueError(
                f"{layer}.{name}: {dup_count} duplicate primary key rows on "
                f"{pk}. Snowflake will not catch this. Generate the key with "
                f"common.determinism.unique_stable_ids (or widen it) rather than "
                f"a bare truncated hash.\n  sample: {sample}"
            )

    path = table_path(layer, name)
    out.to_parquet(path, index=False, engine="pyarrow", compression="snappy")

    if not quiet:
        size_mb = path.stat().st_size / 1e6
        print(f"    wrote {layer}.{name:34s} {len(out):8,d} rows  {size_mb:7.2f} MB")

    return path


def read_table(layer: str, name: str) -> pd.DataFrame:
    path = table_path(layer, name)
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found. The step that produces {layer}.{name} has not run yet."
        )
    return pd.read_parquet(path, engine="pyarrow")


def table_exists(layer: str, name: str) -> bool:
    return table_path(layer, name).exists()


def row_count(layer: str, name: str) -> int:
    import pyarrow.parquet as pq

    return pq.ParquetFile(table_path(layer, name)).metadata.num_rows


def variant_columns(spec: S.TableSpec) -> list[str]:
    """Columns the loader must wrap in PARSE_JSON."""
    return [c.name for c in spec.columns if _is_variant(c.sf_type)]
