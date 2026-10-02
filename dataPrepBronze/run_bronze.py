"""Bronze layer orchestrator. Runs all generators then validates."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


SCRIPTS = [
    ("00_fetch_open_datasets", "Fetch open datasets"),
    ("01_build_reference_tables", "Reference tables"),
    ("02_build_dim_member", "Member dimensions"),
    ("03_build_ext_patient", "External patient data"),
    ("04_build_fct_transactions", "Transactions"),
    ("05_build_fct_events", "Events"),
    ("06_build_fct_pa", "Prior authorizations"),
    ("07_validate_bronze", "Validation"),
]


def main() -> int:
    print("=" * 78)
    print("Bronze Layer - Full Pipeline")
    print("=" * 78)

    for module_name, label in SCRIPTS:
        print(f"\n{'='*78}")
        print(f"  Running: {label} ({module_name})")
        print(f"{'='*78}")
        try:
            mod = importlib.import_module(f"dataPrepBronze.{module_name}")
            rc = mod.main()
            if rc != 0:
                print(f"\n  FAILED: {label} returned {rc}")
                return rc
        except Exception as exc:
            print(f"\n  ERROR in {label}: {exc}")
            return 1

    print("\n" + "=" * 78)
    print("Bronze layer complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
