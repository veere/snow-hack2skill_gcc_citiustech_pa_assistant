"""
Snowflake access for the PA copilot, in either deployment mode.

The app has to run in two places from ONE codebase:

  * Streamlit in Snowflake (SiS) - a Snowpark session already exists, so we attach to
    it. This is the standalone analyst surface.
  * Self-hosted (docker / local) - no session exists, so we connect with a PAT. This
    is the mode that can be embedded in an iframe, because we control the response
    headers there and Snowflake's own hosting sets X-Frame-Options: DENY on every
    endpoint with no way to override it.

Everything above this layer is written against `run_query()` and never needs to know
which mode it is in.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import app_config as AC  # noqa: E402


@dataclass(frozen=True)
class QueryResult:
    """A dataframe plus the provenance needed to cite it."""

    df: pd.DataFrame
    sql: str
    elapsed_ms: int
    query_id: str | None = None

    @property
    def row_count(self) -> int:
        return len(self.df)


def in_snowflake() -> bool:
    """True when running inside Streamlit in Snowflake.

    get_active_session() raises rather than returning None outside SiS, so the
    exception IS the signal. Import is local because snowflake.snowpark may be
    absent in a slim self-hosted image.
    """
    try:
        from snowflake.snowpark.context import get_active_session

        get_active_session()
        return True
    except Exception:  # noqa: BLE001
        return False


def mode() -> str:
    return "SiS" if in_snowflake() else "self-hosted"


@st.cache_resource(show_spinner=False)
def _connection() -> Any:
    """One connection per server process, reused across reruns.

    Streamlit reruns the whole script on every interaction, so without
    cache_resource each click would open a new Snowflake session.
    """
    if in_snowflake():
        from snowflake.snowpark.context import get_active_session

        return ("session", get_active_session())

    from load import snowflake_loader as L

    if not os.environ.get("SNOWFLAKE_PAT"):
        raise RuntimeError(
            "SNOWFLAKE_PAT is not set.\n\n"
            "Self-hosted mode authenticates with a programmatic access token. Store it\n"
            "once with:\n"
            "    cortex secret store snowflake_pat --prompt\n"
            "then start the app with:\n"
            "    cortex secret run --map snowflake_pat=SNOWFLAKE_PAT -- "
            "streamlit run app/main.py\n\n"
            "Interactive browser auth is deliberately unsupported: its token expires "
            "mid-session and cannot work unattended."
        )
    return ("connector", L.connect())


def run_query(sql: str, *, params: dict | None = None) -> QueryResult:
    """Execute SQL and return rows plus provenance.

    `params` are substituted as quoted literals rather than bind variables because
    the same call has to work through both the Snowpark session and the connector,
    which expose different paramstyles. Values are escaped by doubling single
    quotes; identifiers are never taken from params.
    """
    import time

    if params:
        for key, value in params.items():
            if value is None:
                literal = "NULL"
            elif isinstance(value, bool):
                literal = "TRUE" if value else "FALSE"
            elif isinstance(value, (int, float)):
                literal = str(value)
            else:
                literal = "'" + str(value).replace("'", "''") + "'"
            sql = sql.replace(f":{key}", literal)

    kind, handle = _connection()
    started = time.perf_counter()

    if kind == "session":
        rows = handle.sql(sql).collect()
        df = pd.DataFrame([r.as_dict() for r in rows]) if rows else pd.DataFrame()
        query_id = None
    else:
        cur = handle.cursor()
        try:
            cur.execute(sql)
            cols = [c[0] for c in cur.description] if cur.description else []
            data = cur.fetchall() if cols else []
            df = pd.DataFrame(data, columns=cols)
            query_id = cur.sfqid
        finally:
            cur.close()

    elapsed = int((time.perf_counter() - started) * 1000)
    return QueryResult(df=df, sql=sql.strip(), elapsed_ms=elapsed, query_id=query_id)


@st.cache_data(show_spinner=False, ttl=3600, max_entries=64)
def read_stage_image(stage_fqn: str, file_name: str) -> bytes | None:
    """Return the bytes of a staged image.

    NOT a presigned URL. `GET_PRESIGNED_URL` returns a working link - HTTP 200 from
    both a plain and a browser-shaped request - but Snowflake serves staged files as
    `Content-Type: application/octet-stream`, and a browser <img> will not render that.
    Every one of the 24 reference images failed in the browser for this reason while
    passing a server-side probe.

    Reading the bytes and handing them to st.image() lets Streamlit serve them with a
    correct content type. It also avoids link expiry and any cross-origin surface,
    and works identically in both deployment modes.
    """
    kind, handle = _connection()

    if kind == "session":
        # Snowpark exposes a stream straight off the stage - no egress, no temp file.
        try:
            with handle.file.get_stream(f"@{stage_fqn}/{file_name}") as fh:
                return fh.read()
        except Exception:  # noqa: BLE001
            return None

    # Self-hosted: GET the file down through the existing connection.
    import tempfile

    try:
        with tempfile.TemporaryDirectory() as tmp:
            posix = Path(tmp).as_posix()
            cur = handle.cursor()
            try:
                cur.execute(f"GET @{stage_fqn}/{file_name} 'file://{posix}'")
            finally:
                cur.close()
            for path in Path(tmp).iterdir():
                if path.is_file():
                    return path.read_bytes()
    except Exception:  # noqa: BLE001
        return None
    return None


def cortex_complete(prompt: str, *, model: str | None = None, system: str = "") -> str:
    """Call AI_COMPLETE, falling back to the in-region model if the primary fails.

    The primary model reaches this Azure East US 2 account only via cross-region
    inference. If that is ever disabled the app should degrade to llama3.1-8b rather
    than break, so the fallback is a real code path, not decoration.
    """
    full = f"{system}\n\n{prompt}".strip() if system else prompt
    escaped = full.replace("\\", "\\\\").replace("'", "''")
    for candidate in (model or AC.MODEL_PRIMARY, AC.MODEL_FALLBACK):
        try:
            res = run_query(
                f"SELECT AI_COMPLETE('{candidate}', '{escaped}') AS completion"
            )
            if not res.df.empty:
                return str(res.df.iloc[0, 0])
        except Exception:  # noqa: BLE001
            continue
    return ""


def search_regulatory(query: str, *, limit: int = 5, doc_keys: list[str] | None = None) -> pd.DataFrame:
    """Query the regulatory Cortex Search service.

    `columns` MUST be requested explicitly: omitting it returns hits whose every
    attribute is null, which looks like an empty corpus but is an under-specified
    request.

    `doc_keys` scopes retrieval to particular documents. This matters because
    CMS-0057-F contributes 418 of 626 chunks (67%) and its preamble commentary
    otherwise crowds out the shorter, more operative CFR sections - e.g. a Medicaid
    turnaround question returns CMS-0057-F discussion instead of 42 CFR 438.
    """
    import json

    payload: dict[str, Any] = {
        "query": query,
        "columns": [
            "chunk_id", "doc_key", "citation_id", "section_label", "heading",
            "page_no", "authority", "source_url", "effective_date", "licence",
            "chunk_text",
        ],
        "limit": limit,
    }
    if doc_keys:
        payload["filter"] = {"@or": [{"@eq": {"doc_key": k}} for k in doc_keys]}

    literal = json.dumps(payload).replace("'", "''")
    res = run_query(
        f"SELECT SNOWFLAKE.CORTEX.SEARCH_PREVIEW('{AC.fqn(AC.SVC_REGULATORY)}', "
        f"'{literal}') AS payload"
    )
    if res.df.empty:
        return pd.DataFrame()
    raw = res.df.iloc[0, 0]
    parsed = json.loads(raw) if isinstance(raw, str) else raw
    return pd.DataFrame(parsed.get("results", []))
