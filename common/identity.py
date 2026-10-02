"""
Canonical member identity.

Builders 02 (dim_member*), 03 (ext_patient*) and 06 (fct_memberPA) each need the
payer member id for a Synthea patient. Deriving it independently in three places
is how they drifted apart before: 03 emitted the raw Synthea UUID while 02 emitted
MBR-######, so the payer/provider linkage matched zero rows.

The mapping is therefore computed ONCE here, cached to
`.cache/member_id_map.json`, and reused by every builder and every rerun.

Uniqueness matters as much as consistency. `det.stable_id` is a truncated hash,
so at 6 digits a collision across ~1,200 members is roughly a coin flip - and one
did occur, giving two different patients the same member_id. Snowflake does not
enforce PRIMARY KEY constraints, so that loaded silently and then fanned out
joins downstream. `det.unique_stable_ids` resolves collisions deterministically,
and the width here is 8 to make them rare in the first place.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from common import determinism as det
from config import schemas as S

MAP_FILENAME = "member_id_map.json"
MEMBER_ID_WIDTH = 8


def _map_path() -> Path:
    cfg = S.load_config()
    cache = Path(cfg["_repo_root"]) / cfg["paths"]["cache"]
    cache.mkdir(parents=True, exist_ok=True)
    return cache / MAP_FILENAME


def _synthea_patients_path() -> Path:
    cfg = S.load_config()
    return Path(cfg["_repo_root"]) / cfg["paths"]["cache"] / "synthea" / "patients.csv"


def build_map(*, force: bool = False) -> dict[str, str]:
    """Build (or load) the canonical Synthea patient UUID -> member_id mapping.

    Patients are sorted by UUID before ids are assigned, so the mapping - including
    how any collision is disambiguated - is identical on every run regardless of
    file ordering.
    """
    path = _map_path()
    if path.exists() and not force:
        return json.loads(path.read_text(encoding="utf-8"))

    patients = pd.read_csv(_synthea_patients_path(), dtype=str)
    uuids = patients["Id"].dropna().astype(str).sort_values().reset_index(drop=True)

    ids = det.unique_stable_ids("MBR-", uuids, width=MEMBER_ID_WIDTH)
    mapping = dict(zip(uuids.tolist(), ids.tolist()))

    if len(set(mapping.values())) != len(mapping):
        raise AssertionError("member_id mapping is not unique - this must never happen")

    path.write_text(json.dumps(mapping, indent=2, sort_keys=True), encoding="utf-8")
    return mapping


def member_id_map() -> dict[str, str]:
    return build_map()


def member_id(patient_uuid: str | None) -> str | None:
    if not patient_uuid:
        return None
    return member_id_map().get(str(patient_uuid))


def map_series(s: pd.Series) -> pd.Series:
    """Vectorised UUID -> member_id lookup."""
    return s.astype(str).map(member_id_map())
