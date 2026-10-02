"""
Fetch all open datasets and compute the date anchor.

Downloads Synthea, FDA NDC, CMS ICD-10-CM, CMS HCPCS, CMS DE-SynPUF
beneficiary summary, and NPPES NPI data. Extracts archives. Discovers the
latest Synthea encounter date and persists the date-anchor offset that every
downstream generator uses.

Idempotent: cached downloads are reused and the date anchor is overwritten
only when it needs updating.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pandas as pd

# Ensure repo root is on sys.path so `config` / `common` resolve.
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from common.fetch import fetch, extract_zip, cache_dir, list_zip_contents  # noqa: E402
from common import dates  # noqa: E402
from config import schemas as S  # noqa: E402


def fetch_all() -> dict[str, dict | None]:
    """Download every configured dataset, respecting enabled/required flags."""
    cfg = S.load_config()
    manifest: dict[str, dict | None] = {}

    print("=== Fetching open datasets ===")

    for key in ("synthea", "fda_ndc", "icd10cm", "hcpcs", "synpuf_beneficiary"):
        manifest[key] = fetch(key)

    # NPPES is an API, not a zip download — handled separately in 01_build_reference_tables.py.
    print("  [nppes] API-based — fetched during reference table build")
    manifest["nppes"] = None

    return manifest


def extract_all() -> dict[str, Path]:
    """Extract cached zips into per-dataset directories under .cache/."""
    dirs: dict[str, Path] = {}
    print("\n=== Extracting archives ===")

    for key in ("synthea", "fda_ndc", "icd10cm", "hcpcs", "synpuf_beneficiary"):
        try:
            d = extract_zip(key)
            n_files = len(list(d.iterdir()))
            print(f"  [{key}] extracted to {d.name}/  ({n_files} files)")
            dirs[key] = d
        except FileNotFoundError:
            print(f"  [{key}] not cached — skipped extraction")
    return dirs


def compute_date_anchor(synthea_dir: Path) -> dict:
    """Find the latest encounter date in Synthea and compute the timeline shift."""
    print("\n=== Computing date anchor ===")

    encounters_path = synthea_dir / "encounters.csv"
    if not encounters_path.exists():
        # Try to find it in a subdirectory (some Synthea zips nest files)
        candidates = list(synthea_dir.glob("**/encounters.csv"))
        if not candidates:
            raise FileNotFoundError(
                f"encounters.csv not found under {synthea_dir}. "
                "Cannot compute date anchor without Synthea encounters."
            )
        encounters_path = candidates[0]

    enc = pd.read_csv(encounters_path, usecols=["START", "STOP"])

    # Parse dates — Synthea uses ISO datetime strings.
    start_dates = pd.to_datetime(enc["START"], errors="coerce").dt.date
    stop_dates = pd.to_datetime(enc["STOP"], errors="coerce").dt.date

    max_encounter = max(start_dates.dropna().max(), stop_dates.dropna().max())
    print(f"  latest Synthea encounter date: {max_encounter}")

    # Also scan patients for the latest death date, conditions, etc.
    patients_path = synthea_dir / "patients.csv"
    if patients_path.exists():
        pat = pd.read_csv(patients_path, usecols=["BIRTHDATE", "DEATHDATE"])
        death_dates = pd.to_datetime(pat["DEATHDATE"], errors="coerce").dt.date
        if death_dates.notna().any():
            max_death = death_dates.dropna().max()
            if max_death > max_encounter:
                max_encounter = max_death
                print(f"  (adjusted to latest death date: {max_encounter})")

    anchor = dates.compute_and_save(
        max_encounter,
        source_note=f"Computed from Synthea encounters.csv + patients.csv; max date = {max_encounter}",
    )
    print(f"  offset: {anchor['offset_days']:+d} days")
    print(f"  shifted latest -> {anchor['target_latest_date']}")
    print(f"  reference date:   {anchor['reference_date']}")
    return anchor


def main() -> int:
    manifest = fetch_all()
    dirs = extract_all()

    if "synthea" not in dirs:
        print("ERROR: Synthea dataset is required but was not fetched.")
        return 1

    compute_date_anchor(dirs["synthea"])

    print("\n=== Fetch complete ===")
    for key, entry in manifest.items():
        status = "ok" if entry else ("API" if key == "nppes" else "skipped")
        print(f"  {key:24s} {status}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
