"""
Bronze layer validation.

Checks run BEFORE loading to Snowflake, catching problems when they're cheapest
to fix. Checks: PK uniqueness, FK integrity, enum domains, date sanity,
amount invariants, null rules.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from common import dates, frames  # noqa: E402
from config import schemas as S  # noqa: E402


class ValidationResult:
    def __init__(self):
        self.passes: list[str] = []
        self.failures: list[str] = []

    def ok(self, msg: str):
        self.passes.append(msg)

    def fail(self, msg: str):
        self.failures.append(msg)
        print(f"    FAIL  {msg}")

    @property
    def passed(self) -> bool:
        return len(self.failures) == 0


def validate_pk(df: pd.DataFrame, spec: S.TableSpec, result: ValidationResult):
    pk_cols = list(spec.primary_key)
    if not pk_cols:
        return
    dupes = df.duplicated(subset=pk_cols, keep=False).sum()
    if dupes > 0:
        result.fail(f"{spec.name}: PK {pk_cols} has {dupes} duplicate rows")
    else:
        result.ok(f"{spec.name}: PK unique")


def validate_fk(df: pd.DataFrame, spec: S.TableSpec, result: ValidationResult):
    for fk in spec.foreign_keys:
        if fk.column not in df.columns:
            continue
        target_spec = S.get(spec.layer, fk.target_table)
        target_path = frames.table_path(spec.layer, fk.target_table)
        if not target_path.exists():
            result.fail(f"{spec.name}.{fk.column} -> {fk.target_table}.{fk.target_column}: target not built")
            continue
        target_df = pd.read_parquet(target_path)
        if fk.target_column not in target_df.columns:
            result.fail(f"{spec.name}.{fk.column} -> {fk.target_table}.{fk.target_column}: target column missing")
            continue
        source_vals = df[fk.column].dropna().unique()
        target_vals = set(target_df[fk.target_column].dropna().unique())
        orphans = [v for v in source_vals if v not in target_vals]
        if orphans:
            pct = len(orphans) / len(source_vals) * 100 if len(source_vals) > 0 else 0
            if pct > 50:
                result.fail(f"{spec.name}.{fk.column} -> {fk.target_table}: {len(orphans)} orphans ({pct:.1f}%)")
            else:
                result.ok(f"{spec.name}.{fk.column} -> {fk.target_table}: {len(orphans)} orphans ({pct:.1f}%, acceptable)")
        else:
            result.ok(f"{spec.name}.{fk.column} FK valid")


def validate_enums(df: pd.DataFrame, spec: S.TableSpec, result: ValidationResult):
    for col in spec.enum_cols():
        if col.name not in df.columns:
            continue
        valid = set(col.enum)
        actual = set(df[col.name].dropna().unique())
        invalid = actual - valid
        if invalid:
            result.fail(f"{spec.name}.{col.name}: invalid enum values: {invalid}")
        else:
            result.ok(f"{spec.name}.{col.name} enum valid")


def validate_not_null(df: pd.DataFrame, spec: S.TableSpec, result: ValidationResult):
    for col_name in spec.not_null_cols():
        if col_name not in df.columns:
            continue
        nulls = df[col_name].isna().sum()
        if nulls > 0:
            result.fail(f"{spec.name}.{col_name}: {nulls} nulls in NOT NULL column")
        else:
            result.ok(f"{spec.name}.{col_name} not-null valid")


def validate_amounts(df: pd.DataFrame, spec: S.TableSpec, result: ValidationResult):
    if spec.name != "fct_memberTransactions":
        return
    claims = df[df["transaction_type"].str.startswith("CLAIM", na=False)]
    if claims.empty:
        return
    b = claims["billed_amt"].fillna(0)
    a = claims["allowed_amt"].fillna(0)
    p = claims["plan_paid_amt"].fillna(0)
    violations = ((b < a - 0.01) | (a < p - 0.01)).sum()
    if violations > 0:
        result.fail(f"fct_memberTransactions: {violations} rows violate billed >= allowed >= plan_paid")
    else:
        result.ok("fct_memberTransactions: amount invariant holds")


def validate_dates(df: pd.DataFrame, spec: S.TableSpec, result: ValidationResult):
    ref = pd.Timestamp(dates.reference_date()) + pd.Timedelta(days=365)
    for col in spec.columns:
        if col.sf_type != "DATE" or col.name not in df.columns:
            continue
        s = pd.to_datetime(df[col.name], errors="coerce").dropna()
        if s.empty:
            continue
        future = s[s > ref]
        if len(future) > 0:
            result.fail(f"{spec.name}.{col.name}: {len(future)} dates more than 1 year in the future")
        else:
            result.ok(f"{spec.name}.{col.name} dates reasonable")


def validate_record_hash(df: pd.DataFrame, spec: S.TableSpec, result: ValidationResult):
    if "record_hash" not in df.columns:
        result.fail(f"{spec.name}: record_hash column missing")
        return
    nulls = df["record_hash"].isna().sum()
    if nulls > 0:
        result.fail(f"{spec.name}: {nulls} null record_hash values")
    else:
        result.ok(f"{spec.name}: record_hash populated")


def main() -> int:
    print("=" * 78)
    print("Bronze Validation")
    print("=" * 78)

    result = ValidationResult()
    specs = S.layer_tables("bronze")

    for spec in specs:
        path = frames.table_path("bronze", spec.name)
        if not path.exists():
            result.fail(f"{spec.name}: parquet file not found at {path}")
            continue

        print(f"\n  {spec.name}")
        df = pd.read_parquet(path)

        validate_pk(df, spec, result)
        validate_fk(df, spec, result)
        validate_enums(df, spec, result)
        validate_not_null(df, spec, result)
        validate_amounts(df, spec, result)
        validate_dates(df, spec, result)
        validate_record_hash(df, spec, result)

    # Governance guardrail
    print("\n  Governance guardrail ...")
    try:
        S.assert_no_banned_columns()
        result.ok("No banned column names found")
    except AssertionError as e:
        result.fail(str(e))

    print("\n" + "=" * 78)
    print(f"  {len(result.passes)} passed, {len(result.failures)} failed")
    if result.failures:
        print("\n  FAILURES:")
        for f in result.failures:
            print(f"    x {f}")
        return 1
    else:
        print("  All bronze validations passed.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
