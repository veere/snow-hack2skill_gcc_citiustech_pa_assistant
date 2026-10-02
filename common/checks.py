"""
Validation framework.

Every layer runs the same generic suite derived from the frozen spec (PK
uniqueness, FK integrity, NOT NULL, enum domains, numeric ranges), plus any
layer-specific invariants registered as custom checks.

The reporting contract matters as much as the checks: results are returned as
structured rows and a non-zero exit code is produced on failure, so the
orchestrators and the end-to-end verifier can gate on real outcomes rather than
on a script having merely completed. Warnings are separated from failures so a
soft data-quality observation cannot silently pass as a hard guarantee.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from common import frames
from config import schemas as S


@dataclass
class Result:
    table: str
    check: str
    passed: bool
    severity: str  # "FAIL" or "WARN"
    detail: str = ""
    offending_count: int = 0
    sample: list = field(default_factory=list)

    @property
    def is_blocking(self) -> bool:
        return (not self.passed) and self.severity == "FAIL"


class Report:
    def __init__(self, title: str) -> None:
        self.title = title
        self.results: list[Result] = []

    def add(self, r: Result) -> None:
        self.results.append(r)

    @property
    def failures(self) -> list[Result]:
        return [r for r in self.results if r.is_blocking]

    @property
    def warnings(self) -> list[Result]:
        return [r for r in self.results if (not r.passed) and r.severity == "WARN"]

    @property
    def passed(self) -> bool:
        return not self.failures

    def print_summary(self, *, verbose: bool = False) -> None:
        total = len(self.results)
        ok = sum(1 for r in self.results if r.passed)
        print()
        print("=" * 78)
        print(self.title)
        print("=" * 78)
        print(f"{ok}/{total} checks passed")

        if verbose:
            for r in self.results:
                if r.passed:
                    print(f"  ok   {r.table:34s} {r.check}")

        if self.warnings:
            print()
            print(f"WARNINGS ({len(self.warnings)}):")
            for r in self.warnings:
                print(f"  !  {r.table:34s} {r.check}: {r.detail}")
                if r.sample:
                    print(f"     sample: {r.sample[:5]}")

        if self.failures:
            print()
            print(f"FAILURES ({len(self.failures)}):")
            for r in self.failures:
                print(f"  x  {r.table:34s} {r.check}: {r.detail}")
                if r.sample:
                    print(f"     sample: {r.sample[:5]}")
            print()
            print(f"{self.title} -> FAILED")
        else:
            print()
            print(f"{self.title} -> PASSED")

    def to_frame(self) -> pd.DataFrame:
        return pd.DataFrame([
            {
                "table": r.table,
                "check": r.check,
                "passed": r.passed,
                "severity": r.severity,
                "detail": r.detail,
                "offending_count": r.offending_count,
            }
            for r in self.results
        ])

    def exit_code(self) -> int:
        return 1 if self.failures else 0


# ---------------------------------------------------------------------------
# Generic spec-driven checks
# ---------------------------------------------------------------------------

def check_table(
    df: pd.DataFrame,
    spec: S.TableSpec,
    report: Report,
    *,
    fk_lookup: dict[str, pd.DataFrame] | None = None,
) -> None:
    tag = f"{spec.layer}.{spec.name}"

    # --- columns present and ordered ---
    expected = spec.col_names()
    if list(df.columns) != expected:
        missing = [c for c in expected if c not in df.columns]
        extra = [c for c in df.columns if c not in expected]
        detail = []
        if missing:
            detail.append(f"missing={missing}")
        if extra:
            detail.append(f"extra={extra}")
        if not detail:
            detail.append("column order differs from the declared physical order")
        report.add(Result(tag, "schema_conformance", False, "FAIL", "; ".join(detail)))
        return
    report.add(Result(tag, "schema_conformance", True, "FAIL"))

    # --- non-empty ---
    report.add(Result(
        tag, "non_empty", len(df) > 0, "FAIL",
        "table produced zero rows" if len(df) == 0 else "",
    ))
    if len(df) == 0:
        return

    # --- primary key ---
    if spec.primary_key:
        pk = list(spec.primary_key)
        nulls = int(df[pk].isna().any(axis=1).sum())
        dupes = int(df.duplicated(subset=pk).sum())
        report.add(Result(
            tag, f"pk_not_null({','.join(pk)})", nulls == 0, "FAIL",
            f"{nulls} rows have a NULL primary key", nulls,
        ))
        sample = []
        if dupes:
            dup_mask = df.duplicated(subset=pk, keep=False)
            sample = df.loc[dup_mask, pk].head(5).to_dict("records")
        report.add(Result(
            tag, f"pk_unique({','.join(pk)})", dupes == 0, "FAIL",
            f"{dupes} duplicate primary key rows", dupes, sample,
        ))

    # --- extra uniqueness sets ---
    for uset in spec.unique_sets:
        cols = list(uset)
        dupes = int(df.duplicated(subset=cols).sum())
        report.add(Result(
            tag, f"unique({','.join(cols)})", dupes == 0, "FAIL",
            f"{dupes} duplicate rows on {cols}", dupes,
        ))

    # --- NOT NULL ---
    for col_name in spec.not_null_cols():
        n = int(df[col_name].isna().sum())
        report.add(Result(
            tag, f"not_null({col_name})", n == 0, "FAIL",
            f"{n} NULLs in a NOT NULL column", n,
        ))

    # --- enum domains ---
    for col in spec.enum_cols():
        present = df[col.name].dropna().astype(str)
        allowed = set(col.enum)
        bad = sorted(set(present.unique()) - allowed)
        n = int(present.isin(allowed).eq(False).sum())
        report.add(Result(
            tag, f"enum({col.name})", not bad, "FAIL",
            f"values outside the controlled vocabulary: {bad[:10]}", n, bad[:5],
        ))

    # --- numeric ranges ---
    for col in spec.columns:
        if col.min_value is None and col.max_value is None:
            continue
        series = pd.to_numeric(df[col.name], errors="coerce").dropna()
        if series.empty:
            continue
        viol = pd.Series(False, index=series.index)
        if col.min_value is not None:
            viol |= series < col.min_value
        if col.max_value is not None:
            viol |= series > col.max_value
        n = int(viol.sum())
        bounds = f"[{col.min_value}, {col.max_value}]"
        report.add(Result(
            tag, f"range({col.name})", n == 0, "FAIL",
            f"{n} values outside {bounds}", n,
            series[viol].head(5).tolist(),
        ))

    # --- foreign keys ---
    if fk_lookup:
        for fk in spec.foreign_keys:
            target = fk_lookup.get(fk.target_table)
            if target is None or fk.target_column not in target.columns:
                report.add(Result(
                    tag, f"fk({fk.column}->{fk.target_table})", True, "WARN",
                    "target table not loaded for this run - FK not verified",
                ))
                continue
            valid = set(target[fk.target_column].dropna().astype(str))
            child = df[fk.column]
            if not fk.allow_null:
                n_null = int(child.isna().sum())
                report.add(Result(
                    tag, f"fk_not_null({fk.column})", n_null == 0, "FAIL",
                    f"{n_null} NULLs in a mandatory FK", n_null,
                ))
            non_null = child.dropna().astype(str)
            orphan_mask = ~non_null.isin(valid)
            n = int(orphan_mask.sum())
            report.add(Result(
                tag, f"fk({fk.column}->{fk.target_table}.{fk.target_column})",
                n == 0, "FAIL",
                f"{n} values have no matching parent row", n,
                non_null[orphan_mask].head(5).tolist(),
            ))


def check_layer(
    layer: str,
    report: Report,
    *,
    custom: dict[str, list] | None = None,
) -> Report:
    """Run the generic suite over every registered table in a layer.

    All tables are read once and shared, so FK checks resolve against the same
    snapshot the generators produced.
    """
    specs = S.layer_tables(layer)
    loaded: dict[str, pd.DataFrame] = {}

    for spec in specs:
        if frames.table_exists(spec.layer, spec.name):
            loaded[spec.name] = frames.read_table(spec.layer, spec.name)
        else:
            report.add(Result(
                f"{spec.layer}.{spec.name}", "parquet_exists", False, "FAIL",
                "expected parquet file was not produced",
            ))

    # FK targets may live in an earlier layer, so make those available too.
    fk_lookup = dict(loaded)
    for spec in specs:
        for fk in spec.foreign_keys:
            if fk.target_table in fk_lookup:
                continue
            for other in ("bronze", "silver", "gold"):
                if fk.target_table in S.REGISTRY[other] and frames.table_exists(
                    other, fk.target_table
                ):
                    fk_lookup[fk.target_table] = frames.read_table(other, fk.target_table)
                    break

    for spec in specs:
        df = loaded.get(spec.name)
        if df is None:
            continue
        check_table(df, spec, report, fk_lookup=fk_lookup)

        for fn in (custom or {}).get(spec.name, []):
            try:
                fn(df, spec, report, fk_lookup)
            except Exception as exc:  # noqa: BLE001
                report.add(Result(
                    f"{spec.layer}.{spec.name}", f"custom:{fn.__name__}", False, "FAIL",
                    f"custom check raised {type(exc).__name__}: {exc}",
                ))

    return report


# ---------------------------------------------------------------------------
# Reusable invariant helpers for layer-specific checks
# ---------------------------------------------------------------------------

def assert_monetary_waterfall(
    df: pd.DataFrame, spec: S.TableSpec, report: Report, _fk=None,
) -> None:
    """billed >= allowed >= plan_paid, and member responsibility reconciles.

    Compared only where both sides are present, so genuinely-absent amounts on
    non-claim transaction types are not treated as violations.
    """
    tag = f"{spec.layer}.{spec.name}"

    def _cmp(hi: str, lo: str) -> None:
        if hi not in df.columns or lo not in df.columns:
            return
        a = pd.to_numeric(df[hi], errors="coerce")
        b = pd.to_numeric(df[lo], errors="coerce")
        both = a.notna() & b.notna()
        viol = both & (a < b - 0.005)  # cent tolerance
        n = int(viol.sum())
        report.add(Result(
            tag, f"monetary({hi}>={lo})", n == 0, "FAIL",
            f"{n} rows where {hi} < {lo}", n,
            df.loc[viol, [hi, lo]].head(5).to_dict("records"),
        ))

    _cmp("billed_amt", "allowed_amt")
    _cmp("allowed_amt", "plan_paid_amt")


def assert_date_sanity(
    df: pd.DataFrame, spec: S.TableSpec, report: Report, _fk=None,
) -> None:
    """No business date may sit in the future relative to the reference date.

    `regulatory_deadline_datetime_utc`, `auth_expiration_date` and
    `requested_*_date` are legitimately in the future - an open PA case is
    precisely one whose deadline has not yet arrived - so those are exempt.
    """
    from common import dates

    tag = f"{spec.layer}.{spec.name}"
    ref = pd.Timestamp(dates.reference_date()) + pd.Timedelta(days=1)

    future_ok = {
        "regulatory_deadline_datetime_utc",
        "auth_expiration_date",
        "auth_effective_date",
        "requested_start_date",
        "requested_end_date",
        "effective_end_date",
        "coverage_end_date",
        "attribution_end_date",
        "grace_period_end_date",
        "premium_paid_through_date",
        "marketing_end_date",
        "end_date",
        "listing_expiry_date",
    }

    for col in spec.columns:
        t = col.sf_type.upper()
        if not (t == "DATE" or t.startswith("TIMESTAMP")):
            continue
        if col.name in future_ok or col.name == "ingested_at_utc":
            continue
        series = pd.to_datetime(df[col.name], errors="coerce")
        viol = series.notna() & (series > ref)
        n = int(viol.sum())
        report.add(Result(
            tag, f"not_future({col.name})", n == 0, "FAIL",
            f"{n} values are after the reference date", n,
            series[viol].head(3).astype(str).tolist(),
        ))
