"""
Loader smoke test.

Proves the full mechanical chain works before any real generator depends on it:

    spec -> DataFrame -> conformed parquet -> PUT -> COPY INTO -> row recon
                                                  -> VARIANT round-trip
                                                  -> type round-trip

It registers a throwaway spec exercising every type family used in the mart
(VARCHAR, NUMBER with and without scale, BOOLEAN, DATE, TIMESTAMP_NTZ, VARIANT),
loads it into the BRONZE schema, verifies what came back, then drops the table
and clears the stage.

Run this after any change to common/frames.py or load/snowflake_loader.py:

    python validate/smoke_test_loader.py
"""

from __future__ import annotations

import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from common import frames
from config import schemas as S
from load import snowflake_loader as L

SMOKE = "_smoke_loader_probe"


def build_spec() -> S.TableSpec:
    return S.register(S.TableSpec(
        layer="bronze", name=SMOKE,
        grain="One throwaway probe row",
        desc="Temporary loader smoke-test table. Dropped at the end of the run.",
        primary_key=("probe_id",),
        columns=S.with_audit(
            S.Col("probe_id", "VARCHAR(20)", False, "Probe key"),
            S.Col("label", "VARCHAR(100)", True, "Free text with an apostrophe and unicode"),
            S.Col("int_value", "NUMBER(10,0)", True, "Integer measure"),
            S.Col("dec_value", "NUMBER(12,2)", True, "Scaled decimal measure"),
            S.Col("flag", "BOOLEAN", True, "Boolean round-trip"),
            S.Col("the_date", "DATE", True, "Date round-trip"),
            S.Col("the_ts", "TIMESTAMP_NTZ", True, "Timestamp round-trip"),
            S.Col("code_list", "VARIANT", True, "Array round-trip via PARSE_JSON"),
            S.Col("status", "VARCHAR(10)", True, "Enum round-trip", enum=("NEW", "DONE")),
        ),
    ))


def build_frame() -> pd.DataFrame:
    return pd.DataFrame([
        {
            "probe_id": "P001",
            "label": "O'Brien - café",
            "int_value": 42,
            "dec_value": 1234.56,
            "flag": True,
            "the_date": date(2026, 8, 15),
            "the_ts": datetime(2026, 8, 15, 13, 45, 30),
            "code_list": ["M5416", "M5126"],
            "status": "NEW",
        },
        {
            "probe_id": "P002",
            "label": None,
            "int_value": None,
            "dec_value": -0.05,
            "flag": False,
            "the_date": None,
            "the_ts": None,
            "code_list": [],
            "status": "DONE",
        },
        {
            "probe_id": "P003",
            "label": "third row",
            "int_value": 0,
            "dec_value": 0.00,
            "flag": None,
            "the_date": date(2026, 1, 1),
            "the_ts": datetime(2026, 1, 1, 0, 0, 0),
            "code_list": None,
            "status": "NEW",
        },
    ])


def main() -> int:
    failures: list[str] = []
    spec = build_spec()
    df = build_frame()

    print("=" * 74)
    print("LOADER SMOKE TEST")
    print("=" * 74)

    # ---- 1. conform + write parquet -------------------------------------
    path = frames.write_table(df, "bronze", SMOKE, source_system="SYNTHETIC")
    back = frames.read_table("bronze", SMOKE)

    if list(back.columns) != spec.col_names():
        failures.append("parquet column order does not match the spec")
    if len(back) != 3:
        failures.append(f"expected 3 rows in parquet, got {len(back)}")
    if back["record_hash"].isna().any():
        failures.append("record_hash was not populated")
    if back["record_hash"].nunique() != 3:
        failures.append("record_hash is not unique per row")
    print(f"  parquet written: {path.name}  ({len(back)} rows, {len(back.columns)} cols)")

    # ---- 2. determinism: same input -> same hashes ------------------------
    hashes_a = set(back["record_hash"])
    frames.write_table(df, "bronze", SMOKE, source_system="SYNTHETIC", quiet=True)
    hashes_b = set(frames.read_table("bronze", SMOKE)["record_hash"])
    if hashes_a != hashes_b:
        failures.append("record_hash changed across two identical writes - determinism broken")
    else:
        print("  determinism: identical input reproduced identical record_hash values")

    # ---- 3. load to Snowflake -------------------------------------------
    cfg = S.load_config()["snowflake"]
    fqn = f"{cfg['database']}.{cfg['schemas']['bronze']}.{SMOKE}"
    con = L.connect()
    try:
        rec = L.load_table(con, spec)
        if not rec["reconciled"]:
            failures.append(
                f"row count mismatch: parquet={rec['parquet_rows']} table={rec['table_rows']}"
            )

        # ---- 4. verify what actually landed -----------------------------
        rows = L.execute(con, f"""
            SELECT probe_id, label, int_value, dec_value, flag,
                   the_date, the_ts,
                   TYPEOF(code_list)            AS variant_type,
                   ARRAY_SIZE(code_list)        AS variant_len,
                   status
            FROM {fqn}
            ORDER BY probe_id
        """)

        by_id = {r[0]: r for r in rows}

        r1 = by_id.get("P001")
        if r1 is None:
            failures.append("P001 did not land")
        else:
            if r1[1] != "O'Brien - café":
                failures.append(f"text/unicode round-trip failed: {r1[1]!r}")
            if int(r1[2]) != 42:
                failures.append(f"integer round-trip failed: {r1[2]!r}")
            if abs(float(r1[3]) - 1234.56) > 0.001:
                failures.append(f"decimal round-trip failed: {r1[3]!r}")
            if r1[4] is not True:
                failures.append(f"boolean round-trip failed: {r1[4]!r}")
            if str(r1[5]) != "2026-08-15":
                failures.append(f"date round-trip failed: {r1[5]!r}")
            if str(r1[6]) != "2026-08-15 13:45:30":
                failures.append(f"timestamp round-trip failed: {r1[6]!r}")
            if r1[7] != "ARRAY":
                failures.append(f"VARIANT did not parse to ARRAY, got {r1[7]!r}")
            if r1[8] is None or int(r1[8]) != 2:
                failures.append(f"VARIANT array length wrong: {r1[8]!r}")
            else:
                print("  VARIANT round-trip: parsed to a queryable ARRAY of length 2")

        r2 = by_id.get("P002")
        if r2 is not None:
            if r2[1] is not None or r2[2] is not None:
                failures.append("NULLs were not preserved through the load")
            if abs(float(r2[3]) + 0.05) > 0.001:
                failures.append(f"negative decimal round-trip failed: {r2[3]!r}")
            else:
                print("  NULL and negative-value handling verified")

        r3 = by_id.get("P003")
        if r3 is not None and r3[4] is not None:
            failures.append("NULL boolean was not preserved")

    finally:
        try:
            L.execute(con, f"DROP TABLE IF EXISTS {fqn}")
            L.execute(con, f"REMOVE @{L.stage_name('bronze')}/{SMOKE.lower()}.parquet")
            print("  cleaned up: probe table dropped, stage cleared")
        finally:
            con.close()

    if path.exists():
        path.unlink()

    print()
    if failures:
        print(f"FAILURES ({len(failures)}):")
        for f in failures:
            print(f"  x {f}")
        print()
        print("SMOKE TEST FAILED")
        return 1

    print("SMOKE TEST PASSED - the spec -> parquet -> Snowflake chain is sound")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
