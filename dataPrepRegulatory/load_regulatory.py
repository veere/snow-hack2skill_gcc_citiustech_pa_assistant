"""
Load the regulatory corpus into Snowflake and prove retrieval works.

Sequence: DDL -> parquet load -> upload source documents to the stage -> create the
Cortex Search service -> run five real analyst questions through it and print the
citation each one returns.

That last step is the point. A search service that builds without error but returns
irrelevant chunks is worse than no service, because the copilot will cite it
confidently. So retrieval quality is printed for judgement rather than asserted.

Run:
    cortex secret run --map snowflake_pat=SNOWFLAKE_PAT -- python dataPrepRegulatory/load_regulatory.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import app_config as AC  # noqa: E402
from load import snowflake_loader as L  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
SERVING_DIR = REPO / "dataFilesServing"
CACHE = REPO / ".cache" / "regulatory"
SQL_DIR = REPO / "sql"

# Questions drawn from docs/PA_analyst_research.md themes 2 and 7 - the ones a real
# analyst asks under time pressure, with a known correct answer so retrieval can be
# judged rather than admired.
PROBE_QUESTIONS = [
    ("how many days do I have to decide a standard Medicare Advantage prior authorization",
     "expect 42 CFR 422.568 or CMS-0057-F"),
    ("what is the expedited turnaround for a Medicaid managed care authorization",
     "expect 42 CFR 438 Subpart F or 438.210"),
    ("when must a specific denial reason be given to the provider",
     "expect CMS-0057-F or 422 Subpart M"),
    ("what makes a provider exempt from prior authorization in Texas",
     "expect Tex. HB 3459 / 4201.65x"),
    ("what are the ERISA timeframes for a pre-service urgent claim",
     "expect 29 CFR 2560.503-1"),
]


def run_sql_file(con, path: Path) -> None:
    """Deploy a SQL file using the loader's own script splitter.

    An earlier version split naively on ';', which mangled the header comment block
    into a statement and failed with `unexpected 'it'`. L.execute_script already
    strips comments and handles statement boundaries, so use it rather than
    reinventing a parser.
    """
    n = L.execute_script(con, path.read_text(encoding="utf-8"), label=path.name)
    print(f"  deployed {path.name}  ({n} statements)")


def load_parquet(con, table: str, parquet: Path, columns: list[str]) -> int:
    """PUT + COPY with an explicit typed projection, mirroring load/snowflake_loader."""
    stage = AC.fqn(AC.STAGE_REGULATORY)
    fqn = AC.fqn(table)

    L.execute(con, f"REMOVE @{stage}/{parquet.name}")
    posix = parquet.as_posix()
    L.execute(con, f"PUT 'file://{posix}' @{stage} OVERWRITE = TRUE AUTO_COMPRESS = FALSE")

    select_list = ", ".join(f'$1:"{c}"' for c in columns)
    L.execute(con, f"TRUNCATE TABLE {fqn}")
    L.execute(
        con,
        f"COPY INTO {fqn} ({', '.join(columns)}) FROM "
        f"(SELECT {select_list} FROM @{stage}/{parquet.name}) "
        f"FILE_FORMAT = (TYPE = PARQUET) ON_ERROR = ABORT_STATEMENT",
    )
    n = L.execute(con, f"SELECT COUNT(*) FROM {fqn}")[0][0]
    expected = len(pd.read_parquet(parquet))
    status = "ok" if n == expected else "MISMATCH"
    print(f"  {status:8s} {table:24s} {n:,d} rows (parquet {expected:,d})")
    if n != expected:
        raise RuntimeError(f"{table}: loaded {n} rows, parquet has {expected}")
    return n


def main() -> int:
    chunks_pq = SERVING_DIR / "regulatory_chunks.parquet"
    sources_pq = SERVING_DIR / "regulatory_sources.parquet"
    for pq in (chunks_pq, sources_pq):
        if not pq.exists():
            print(f"Missing {pq}. Run build_regulatory_chunks.py first.")
            return 1

    con = L.connect()
    try:
        print("=" * 78)
        print("REGULATORY CORPUS LOAD")
        print("=" * 78)
        run_sql_file(con, SQL_DIR / "60_regulatory_ddl.sql")

        # Sources first: chunks carry an FK to it.
        load_parquet(con, AC.TBL_REGULATORY_SOURCES, sources_pq,
                     list(pd.read_parquet(sources_pq).columns))
        load_parquet(con, AC.TBL_REGULATORY_CHUNKS, chunks_pq,
                     list(pd.read_parquet(chunks_pq).columns))

        # Put the actual source documents on the stage so an analyst can open the
        # exact PDF behind a citation, not just a URL that might rot.
        print()
        print("  uploading source documents to the stage")
        stage = AC.fqn(AC.STAGE_REGULATORY)
        manifest = json.loads((CACHE / "manifest.json").read_text(encoding="utf-8"))
        for key, entry in manifest.items():
            src = CACHE / entry["filename"]
            if not src.exists():
                continue
            L.execute(
                con,
                f"PUT 'file://{src.as_posix()}' @{stage}/docs "
                f"OVERWRITE = TRUE AUTO_COMPRESS = FALSE",
            )
        staged = L.execute(con, f"LIST @{stage}/docs")
        print(f"  staged {len(staged)} source documents")

        print()
        print("  creating the Cortex Search service (indexing may take a minute)")
        run_sql_file(con, SQL_DIR / "61_regulatory_search.sql")

        print()
        print("=" * 78)
        print("RETRIEVAL PROOF - judge these, do not assume them")
        print("=" * 78)
        svc = AC.fqn(AC.SVC_REGULATORY)
        for question, expectation in PROBE_QUESTIONS:
            # L.execute takes no bind parameters, so the JSON payload is inlined.
            # Single quotes are doubled rather than backslash-escaped: that is the
            # SQL literal escape, and the questions themselves contain apostrophes.
            # SEARCH_PREVIEW returns ONLY the columns named here. Omitting `columns`
            # yields hits whose every field is null, which looks like a broken
            # corpus but is really an under-specified request.
            payload = json.dumps({
                "query": question,
                "columns": ["citation_id", "section_label", "heading",
                            "source_url", "chunk_text"],
                "limit": 3,
            }).replace("'", "''")
            sql = (
                "SELECT SNOWFLAKE.CORTEX.SEARCH_PREVIEW("
                f"'{svc}', '{payload}')"
            )
            try:
                raw = L.execute(con, sql)[0][0]
            except Exception as exc:  # noqa: BLE001
                print(f"\n  Q: {question}")
                print(f"     SEARCH FAILED: {str(exc).splitlines()[0][:140]}")
                continue

            parsed = json.loads(raw) if isinstance(raw, str) else raw
            results = parsed.get("results", [])
            print()
            print(f"  Q: {question}")
            print(f"     ({expectation})")
            if not results:
                print("     NO RESULTS")
                continue
            for rank, hit in enumerate(results[:3], 1):
                print(f"     {rank}. {hit.get('citation_id')}  |  {hit.get('section_label')}")
                snippet = " ".join((hit.get("chunk_text") or "")[:150].split())
                print(f"        {snippet}...")
        print()
        print("REGULATORY CORPUS LOADED")
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())
