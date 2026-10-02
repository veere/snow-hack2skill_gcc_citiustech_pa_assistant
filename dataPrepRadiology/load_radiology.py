"""
Build the radiology reference metadata table and load it with the images.

The teaching caption is generated from the source description but constrained hard:
it must describe what is anatomically visible and must never read as clinical advice
or as anything bearing on a determination. Captions are produced with a template
rather than a language model, because a model asked to caption a pathology image
tends to drift into interpretation, which is exactly what this app must not do.

Run:
    cortex secret run --map snowflake_pat=SNOWFLAKE_PAT -- python dataPrepRadiology/load_radiology.py
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common import determinism as det  # noqa: E402
from config import app_config as AC  # noqa: E402
from load import snowflake_loader as L  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
CACHE = REPO / ".cache" / "radiology"
MANIFEST = CACHE / "manifest.json"
OUT_DIR = REPO / "dataFilesServing"
SQL_DIR = REPO / "sql"

COLUMNS = [
    "image_id", "file_name", "body_region", "modality", "finding_class",
    "title", "teaching_caption", "source_page_url", "source_file_url",
    "licence", "attribution", "width", "height", "bytes", "sha256",
    "ingested_at_utc",
]

REGION_LABEL = {
    "LUMBAR_SPINE": "lumbar spine",
    "KNEE": "knee",
    "CHEST": "chest",
    "BRAIN": "brain",
}
MODALITY_LABEL = {"MRI": "MRI", "CT": "CT", "XRAY": "radiograph"}


def teaching_caption(row: dict) -> str:
    """A short, factual, orientation-only caption.

    Deliberately templated. The caption states the modality, the region, whether the
    study is a normal baseline or shows a finding, and defers to the linked source for
    detail. It offers no interpretation and draws no conclusion, so it cannot be read
    as supporting a determination.
    """
    region = REGION_LABEL.get(row["body_region"], row["body_region"].lower())
    modality = MODALITY_LABEL.get(row["modality"], row["modality"])
    subject = str(row.get("title") or "").rsplit(".", 1)[0].replace("_", " ").strip()

    if row["finding_class"] == "NORMAL":
        return (
            f"{modality} of the {region} with no reported abnormality - use as a "
            f"baseline for what unremarkable {region} anatomy looks like on {modality}. "
            f"Source titled: {subject}."
        )
    return (
        f"{modality} of the {region} in which the source reports an abnormal finding. "
        f"Compare against the normal {region} {modality} baseline to see how the "
        f"appearance differs. Source titled: {subject}."
    )


def build_metadata() -> pd.DataFrame:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    accepted = manifest["accepted"]

    rows: list[dict] = []
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    for item in accepted:
        # Re-assert the licence gate at build time. The fetch already filtered, but
        # this table is what the app renders from, so the guarantee is re-checked at
        # the boundary rather than assumed to have held upstream.
        if not AC.licence_allowed(item["licence"]):
            raise RuntimeError(
                f"licence gate violated by {item['title']!r}: {item['licence']!r}"
            )
        if not (CACHE / item["file_name"]).exists():
            print(f"  skip (file missing on disk): {item['file_name']}")
            continue
        rows.append({
            "file_name": item["file_name"],
            "body_region": item["body_region"],
            "modality": item["modality"],
            "finding_class": item["finding_class"],
            "title": item["title"][:300],
            "teaching_caption": teaching_caption(item)[:1000],
            "source_page_url": item["source_page_url"][:600],
            "source_file_url": item["source_file_url"][:600],
            "licence": item["licence"][:100],
            "attribution": item["attribution"][:300],
            "width": int(item["width"]),
            "height": int(item["height"]),
            "bytes": int(item["bytes"]),
            "sha256": item["sha256"],
            "ingested_at_utc": now,
        })

    df = pd.DataFrame(rows)
    if df.empty:
        raise RuntimeError("no accepted images with files on disk")
    df["image_id"] = det.unique_stable_ids("RIMG", df["sha256"], width=12)
    return df[COLUMNS]


def main() -> int:
    if not MANIFEST.exists():
        print("No manifest. Run dataPrepRadiology/fetch_radiology.py first.")
        return 1

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    df = build_metadata()
    parquet = OUT_DIR / "radiology_reference.parquet"
    df.to_parquet(parquet, index=False)

    print("=" * 78)
    print("RADIOLOGY REFERENCE LOAD")
    print("=" * 78)
    print(f"  metadata rows: {len(df)}")
    matrix = df.pivot_table(index="body_region", columns="finding_class",
                            values="image_id", aggfunc="count", fill_value=0)
    print(matrix.to_string())
    print()

    con = L.connect()
    try:
        n = L.execute_script(
            con, (SQL_DIR / "62_radiology_ddl.sql").read_text(encoding="utf-8"),
            label="62_radiology_ddl.sql",
        )
        print(f"  deployed 62_radiology_ddl.sql ({n} statements)")

        stage = AC.fqn(AC.STAGE_RADIOLOGY)
        table = AC.fqn(AC.TBL_RADIOLOGY_REFERENCE)

        print("  uploading images to the stage")
        for name in df["file_name"]:
            src = CACHE / name
            L.execute(
                con,
                f"PUT 'file://{src.as_posix()}' @{stage} "
                f"OVERWRITE = TRUE AUTO_COMPRESS = FALSE",
            )
        staged = L.execute(con, f"LIST @{stage}")
        # Filter out the metadata parquet when counting images.
        image_count = len([r for r in staged if str(r[0]).lower().endswith(".jpg")])
        print(f"  staged {image_count} images")

        L.execute(con, f"PUT 'file://{parquet.as_posix()}' @{stage} "
                       f"OVERWRITE = TRUE AUTO_COMPRESS = FALSE")
        select_list = ", ".join(f'$1:"{c}"' for c in COLUMNS)
        L.execute(con, f"TRUNCATE TABLE {table}")
        L.execute(
            con,
            f"COPY INTO {table} ({', '.join(COLUMNS)}) FROM "
            f"(SELECT {select_list} FROM @{stage}/{parquet.name}) "
            f"FILE_FORMAT = (TYPE = PARQUET) ON_ERROR = ABORT_STATEMENT",
        )
        loaded = L.execute(con, f"SELECT COUNT(*) FROM {table}")[0][0]
        print(f"  loaded {loaded} metadata rows (parquet {len(df)})")
        if loaded != len(df):
            raise RuntimeError(f"row mismatch: loaded {loaded}, parquet {len(df)}")

        # Refresh the directory table so presigned URLs resolve.
        L.execute(con, f"ALTER STAGE {stage} REFRESH")

        print()
        print("  proving three images are actually retrievable:")
        probes = L.execute(
            con,
            f"""SELECT file_name,
                       GET_PRESIGNED_URL(@{stage}, file_name, 3600) AS url
                FROM {table} LIMIT 3""",
        )
        import requests

        for name, url in probes:
            try:
                r = requests.get(url, timeout=60, stream=True)
                ok = r.status_code == 200
                print(f"    {'ok  ' if ok else 'FAIL'} HTTP {r.status_code}  {name[:56]}")
            except Exception as exc:  # noqa: BLE001
                print(f"    FAIL {type(exc).__name__}  {name[:56]}")

        print()
        print("  licence distribution as loaded:")
        for lic, n in L.execute(
            con, f"SELECT licence, COUNT(*) FROM {table} GROUP BY 1 ORDER BY 2 DESC"
        ):
            print(f"    {lic:24s} {n}")

        bad = L.execute(
            con,
            f"""SELECT COUNT(*) FROM {table}
                WHERE licence ILIKE '%-SA%' OR licence ILIKE '%NC%'
                   OR licence ILIKE '%ND%' OR attribution IS NULL""",
        )[0][0]
        print()
        print(f"  rows violating the licence/attribution guarantee: {bad}")
        if bad:
            raise RuntimeError("licence or attribution guarantee violated in the loaded table")

        print()
        print("RADIOLOGY REFERENCE LOADED")
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())
