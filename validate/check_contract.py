"""
Schema contract self-check.

Run this before and after touching any spec module:

    python validate/check_contract.py

It verifies the contract is internally consistent and prints the table
inventory. Implementors adding silver/gold specs should run it to confirm their
registrations are well-formed before writing any generator code.

Checks performed:
  1. Registry loads (no import or duplicate-name errors)
  2. Non-decision-making guardrail - no banned column names
  3. Every PK column exists and is NOT NULL
  4. Every FK column exists locally, and the target table/column resolves
  5. FK targets are registered BEFORE the referencing table (load-order safety)
  6. No duplicate column names within a table
  7. Every table carries the audit trailer
  8. Enum columns are VARCHAR and wide enough for their longest value
  9. DDL renders without error for every table
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import schemas as S  # noqa: E402

FAIL: list[str] = []
WARN: list[str] = []


def fail(msg: str) -> None:
    FAIL.append(msg)


def warn(msg: str) -> None:
    WARN.append(msg)


def main() -> int:
    # ---- 2. governance guardrail -----------------------------------------
    try:
        S.assert_no_banned_columns()
    except AssertionError as exc:
        fail(str(exc))

    # Build a global position index so we can verify FK load ordering.
    order: dict[str, int] = {}
    pos = 0
    for layer in ("bronze", "silver", "gold"):
        for spec in S.layer_tables(layer):
            order[spec.name] = pos
            pos += 1

    all_specs = S.all_specs()
    audit_names = {c.name for c in S.AUDIT_COLS}

    for spec in all_specs:
        tag = f"{spec.layer}.{spec.name}"
        names = spec.col_names()

        # ---- 6. duplicate columns ----------------------------------------
        dupes = {n for n in names if names.count(n) > 1}
        if dupes:
            fail(f"{tag}: duplicate column names {sorted(dupes)}")

        # ---- 7. audit trailer --------------------------------------------
        missing_audit = audit_names - set(names)
        if missing_audit:
            fail(f"{tag}: missing audit columns {sorted(missing_audit)} - use with_audit()")

        # ---- 3. primary key ----------------------------------------------
        if not spec.primary_key:
            warn(f"{tag}: no primary key declared")
        for pk in spec.primary_key:
            if pk not in names:
                fail(f"{tag}: PK column {pk!r} is not defined")
            elif spec.col(pk).nullable:
                fail(f"{tag}: PK column {pk!r} must be NOT NULL")

        # ---- 4 & 5. foreign keys -----------------------------------------
        for fk in spec.foreign_keys:
            if fk.column not in names:
                fail(f"{tag}: FK column {fk.column!r} is not defined")
            if fk.target_table not in order:
                fail(f"{tag}: FK {fk.column} -> unknown table {fk.target_table!r}")
                continue
            target = None
            for layer in ("bronze", "silver", "gold"):
                if fk.target_table in S.REGISTRY[layer]:
                    target = S.REGISTRY[layer][fk.target_table]
                    break
            if target is not None and fk.target_column not in target.col_names():
                fail(
                    f"{tag}: FK {fk.column} -> {fk.target_table}.{fk.target_column} "
                    f"but that column does not exist"
                )
            if order[fk.target_table] > order[spec.name]:
                fail(
                    f"{tag}: FK target {fk.target_table} is registered AFTER "
                    f"{spec.name}. Load order follows registration order, so the "
                    f"target must come first."
                )

        # ---- 8. enum sizing ----------------------------------------------
        for col in spec.enum_cols():
            if not col.sf_type.upper().startswith("VARCHAR"):
                fail(f"{tag}.{col.name}: enum column must be VARCHAR, got {col.sf_type}")
                continue
            try:
                width = int(col.sf_type.upper().removeprefix("VARCHAR").strip("() "))
            except ValueError:
                continue
            longest = max(len(v) for v in col.enum)
            if longest > width:
                fail(
                    f"{tag}.{col.name}: VARCHAR({width}) too narrow for longest "
                    f"enum value ({longest} chars)"
                )

        # ---- 9. DDL renders ----------------------------------------------
        try:
            spec.ddl("M360MART", spec.layer.upper())
        except Exception as exc:  # noqa: BLE001
            fail(f"{tag}: DDL render failed - {exc}")

    # ---- report ----------------------------------------------------------
    cfg = S.load_config()
    print("=" * 74)
    print("SCHEMA CONTRACT CHECK")
    print("=" * 74)
    print(f"config      : {S.CONFIG_PATH}")
    print(f"seed        : {cfg['determinism']['master_seed']}")
    print(f"target      : {cfg['snowflake']['database']} @ {cfg['snowflake']['connection_name']}")
    print()

    total_cols = 0
    for layer in ("bronze", "silver", "gold"):
        specs = S.layer_tables(layer)
        n_cols = sum(len(s.columns) for s in specs)
        total_cols += n_cols
        print(f"{layer.upper():6s}  {len(specs):2d} tables  {n_cols:4d} columns")
        for spec in specs:
            fk_note = f"  fk->{len(spec.foreign_keys)}" if spec.foreign_keys else ""
            print(
                f"        {spec.name:34s} {len(spec.columns):3d} cols"
                f"  pk=({','.join(spec.primary_key)}){fk_note}"
            )
        if not specs:
            print("        (none registered yet)")
        print()

    print(f"TOTAL: {len(S.all_specs())} tables, {total_cols} columns")
    print()

    if WARN:
        print(f"WARNINGS ({len(WARN)}):")
        for w in WARN:
            print(f"  ! {w}")
        print()

    if FAIL:
        print(f"FAILURES ({len(FAIL)}):")
        for f in FAIL:
            print(f"  x {f}")
        print()
        print("CONTRACT CHECK FAILED")
        return 1

    print("CONTRACT CHECK PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
