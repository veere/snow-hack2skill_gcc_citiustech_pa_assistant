"""
Snowflake loader: parquet -> internal stage -> typed table.

RUNS LOCALLY. Nothing in this file executes on Snowflake compute beyond DDL,
PUT, COPY INTO and the reconciliation SELECT.

Why an explicit column transform instead of MATCH_BY_COLUMN_NAME
----------------------------------------------------------------
MATCH_BY_COLUMN_NAME is convenient but it cannot express PARSE_JSON, and several
tables carry VARIANT columns (diagnosis arrays). Loading those through column
matching would store the JSON as an opaque string rather than a queryable
structure. Generating an explicit `SELECT $1:"col"::TYPE` list from the frozen
spec instead gives:

  * PARSE_JSON applied exactly to the VARIANT columns
  * every other column cast to its declared type at load time, so a type problem
    surfaces here rather than as a silent coercion
  * immunity to column-order differences between the parquet and the table

Authentication
--------------
Settings come from `config/mart_config.json`, overridable by SNOWFLAKE_*
environment variables. `client_store_temporary_credential` is forced to False on
Windows because the OAuth token blob exceeds the Windows Credential Manager size
limit and CredWrite fails with WinError 1783.

To use a different auth method in another environment, set the relevant
SNOWFLAKE_* environment variables (for example SNOWFLAKE_PASSWORD holding a
programmatic access token, or SNOWFLAKE_PRIVATE_KEY_PATH for key-pair auth). No
credential is ever read from, or written to, a file in this repository.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import snowflake.connector

from common import frames
from config import schemas as S


# ---------------------------------------------------------------------------
# connection
# ---------------------------------------------------------------------------

def _profile_from_toml() -> dict:
    """Read non-secret account/user from ~/.snowflake/connections.toml.

    Only account, user and role are read. No credential is read from this file.
    This lets PAT auth reuse the existing profile's identity without the user
    having to restate it.
    """
    import tomllib

    cfg = S.load_config()["snowflake"]
    path = Path.home() / ".snowflake" / "connections.toml"
    if not path.exists():
        return {}
    try:
        with path.open("rb") as fh:
            data = tomllib.load(fh)
    except Exception:  # noqa: BLE001
        return {}
    profile = data.get(cfg["connection_name"], {})
    return {
        k: profile[k]
        for k in ("account", "user", "role", "warehouse")
        if k in profile
    }


def connect(*, autocommit: bool = True):
    """Open one connection, reused for a whole run.

    NON-INTERACTIVE AUTH ONLY. Interactive browser OAuth is deliberately NOT
    supported: its token expired mid-run repeatedly during this build, leaving
    layers half-loaded, and it cannot work unattended at all. A pipeline that can
    fail halfway because a browser window timed out is not a pipeline.

    Supported, in precedence order:

      1. SNOWFLAKE_PAT                  programmatic access token  (recommended)
      2. SNOWFLAKE_PRIVATE_KEY_PATH     key-pair
      3. SNOWFLAKE_PASSWORD             password

    Account and user are read from ~/.snowflake/connections.toml when not given
    explicitly. Those are not secrets. No credential is ever read from, or written
    to, a file in this repository.

    Supply the credential without it touching disk or the transcript:

        cortex secret store snowflake_pat --prompt
        cortex secret run --map snowflake_pat=SNOWFLAKE_PAT -- \
            python load/run_load.py --layer all
    """
    cfg = S.load_config()["snowflake"]
    profile = _profile_from_toml()

    account = os.environ.get("SNOWFLAKE_ACCOUNT") or profile.get("account")
    user = os.environ.get("SNOWFLAKE_USER") or profile.get("user")
    role = os.environ.get("SNOWFLAKE_ROLE") or profile.get("role")

    params: dict[str, object] = {
        "autocommit": autocommit,
        # Never attempt to cache a token: on Windows the OAuth blob exceeds the
        # Credential Manager size limit and CredWrite fails with WinError 1783.
        "client_store_temporary_credential": False,
    }

    pat = os.environ.get("SNOWFLAKE_PAT")
    key_path = os.environ.get("SNOWFLAKE_PRIVATE_KEY_PATH")
    password = os.environ.get("SNOWFLAKE_PASSWORD")

    if pat:
        # A PAT is used as a DROP-IN REPLACEMENT FOR A PASSWORD with the default
        # authenticator. Setting authenticator='PROGRAMMATIC_ACCESS_TOKEN' makes
        # the server reject a perfectly valid token with
        # "Programmatic access token is invalid", which is badly misleading - it
        # looks like a token problem when it is a wiring problem.
        # Verified against this account by load/probe_pat.py.
        params["password"] = pat
    elif key_path:
        params["private_key_file"] = key_path
        if os.environ.get("SNOWFLAKE_PRIVATE_KEY_PASSPHRASE"):
            params["private_key_file_pwd"] = os.environ["SNOWFLAKE_PRIVATE_KEY_PASSPHRASE"]
    elif password:
        params["password"] = password
        if os.environ.get("SNOWFLAKE_AUTHENTICATOR"):
            params["authenticator"] = os.environ["SNOWFLAKE_AUTHENTICATOR"]
    else:
        raise RuntimeError(
            "No non-interactive Snowflake credential found.\n"
            "\n"
            "Set one of these environment variables:\n"
            "  SNOWFLAKE_PAT                 programmatic access token (recommended)\n"
            "  SNOWFLAKE_PRIVATE_KEY_PATH    path to an RSA private key\n"
            "  SNOWFLAKE_PASSWORD            account password\n"
            "\n"
            "Recommended, keeps the value off disk and out of shell history:\n"
            "  cortex secret store snowflake_pat --prompt\n"
            "  cortex secret run --map snowflake_pat=SNOWFLAKE_PAT -- "
            "python load/run_load.py --layer all\n"
            "\n"
            "Interactive browser OAuth is intentionally not supported here - its "
            "token expires mid-run and cannot be used unattended."
        )

    if not account or not user:
        raise RuntimeError(
            f"Snowflake account/user could not be resolved. Set SNOWFLAKE_ACCOUNT and "
            f"SNOWFLAKE_USER, or ensure profile '{cfg['connection_name']}' exists in "
            f"~/.snowflake/connections.toml."
        )

    params.update(account=account, user=user)
    if role:
        params["role"] = role

    con = snowflake.connector.connect(**params)

    cur = con.cursor()
    try:
        cur.execute(f"USE WAREHOUSE {cfg['warehouse']}")
        cur.execute(f"USE DATABASE {cfg['database']}")
    finally:
        cur.close()
    return con


def auth_mode() -> str:
    """Which auth path connect() will take. Useful in diagnostics."""
    if os.environ.get("SNOWFLAKE_PAT"):
        return "PAT (SNOWFLAKE_PAT)"
    if os.environ.get("SNOWFLAKE_PRIVATE_KEY_PATH"):
        return "key-pair (SNOWFLAKE_PRIVATE_KEY_PATH)"
    if os.environ.get("SNOWFLAKE_PASSWORD"):
        return "password (SNOWFLAKE_PASSWORD)"
    return "NONE - no credential set, connect() will raise"


def execute(con, sql: str, *, label: str = "") -> list[tuple]:
    cur = con.cursor()
    try:
        cur.execute(sql)
        try:
            return cur.fetchall()
        except Exception:  # noqa: BLE001 - DDL returns no result set
            return []
    except Exception as exc:  # noqa: BLE001
        snippet = " ".join(sql.split())[:220]
        raise RuntimeError(
            f"SQL failed{' [' + label + ']' if label else ''}: {exc}\n  sql: {snippet}"
        ) from exc
    finally:
        cur.close()


def split_sql_statements(sql_text: str) -> list[str]:
    """Split a SQL script into statements, honouring comments and string literals.

    Written as a single character scan because two separate naive passes each had a
    real bug:

      * splitting on ';' before stripping comments turned a semicolon INSIDE a
        comment ("...tables; it still cannot see BRONZE...") into a statement
        boundary, leaving comment prose to be compiled as SQL;
      * stripping comments first but still splitting with str.split(';') broke every
        DDL file whose COMMENT = '...' literal contains a semicolon - which is most
        of bronze, silver and gold. That bug was latent only because those files are
        normally deployed from the spec rather than from the .sql artefact.

    Handles: -- line comments, /* block comments */, '...' with '' escaping, and
    "..." quoted identifiers.
    """
    statements: list[str] = []
    buf: list[str] = []
    i = 0
    n = len(sql_text)
    in_single = in_double = False
    in_line_comment = in_block_comment = False

    while i < n:
        ch = sql_text[i]
        pair = sql_text[i : i + 2]

        if in_line_comment:
            if ch == "\n":
                in_line_comment = False
                buf.append(ch)
            i += 1
            continue

        if in_block_comment:
            if pair == "*/":
                in_block_comment = False
                i += 2
                continue
            i += 1
            continue

        if not in_single and not in_double:
            if pair == "--":
                in_line_comment = True
                i += 2
                continue
            if pair == "/*":
                in_block_comment = True
                i += 2
                continue
            if ch == ";":
                stmt = "".join(buf).strip()
                if stmt:
                    statements.append(stmt)
                buf = []
                i += 1
                continue

        if ch == "'" and not in_double:
            # '' inside a literal is an escaped quote, not a close-then-open
            if in_single and sql_text[i : i + 2] == "''":
                buf.append("''")
                i += 2
                continue
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double

        buf.append(ch)
        i += 1

    tail = "".join(buf).strip()
    if tail:
        statements.append(tail)
    return statements


def execute_script(con, sql_text: str, *, label: str = "") -> int:
    """Run a multi-statement script, skipping blanks and comment-only chunks."""
    count = 0
    for stmt in split_sql_statements(sql_text):
        execute(con, stmt, label=label)
        count += 1
    return count


# ---------------------------------------------------------------------------
# DDL
# ---------------------------------------------------------------------------

def ensure_schemas(con) -> None:
    cfg = S.load_config()["snowflake"]
    db = cfg["database"]
    execute(con, f"CREATE DATABASE IF NOT EXISTS {db}", label="create database")
    for schema in cfg["schemas"].values():
        execute(con, f"CREATE SCHEMA IF NOT EXISTS {db}.{schema}", label=f"create schema {schema}")


def stage_name(layer: str) -> str:
    cfg = S.load_config()["snowflake"]
    return f"{cfg['database']}.{cfg['schemas'][layer]}.{cfg['stage_prefix']}{layer.upper()}"


def ensure_stage(con, layer: str) -> str:
    stage = stage_name(layer)
    execute(
        con,
        f"CREATE STAGE IF NOT EXISTS {stage} "
        f"FILE_FORMAT = (TYPE = PARQUET) "
        f"COMMENT = 'Parquet landing stage for the {layer} layer'",
        label=f"create stage {layer}",
    )
    return stage


def create_table(con, spec: S.TableSpec) -> None:
    cfg = S.load_config()["snowflake"]
    schema = cfg["schemas"][spec.layer]
    execute(con, spec.ddl(cfg["database"], schema), label=f"ddl {spec.name}")


# ---------------------------------------------------------------------------
# load
# ---------------------------------------------------------------------------

def _select_list(spec: S.TableSpec) -> str:
    """Build the explicit `SELECT ... FROM @stage` projection from the spec."""
    variant = set(frames.variant_columns(spec))
    parts = []
    for col in spec.columns:
        ref = f'$1:"{col.name}"'
        if col.name in variant:
            # Parquet holds JSON text; PARSE_JSON makes it a queryable VARIANT.
            parts.append(f"TRY_PARSE_JSON({ref}::VARCHAR)")
        else:
            parts.append(f"{ref}::{col.sf_type}")
    return ",\n        ".join(parts)


def load_table(con, spec: S.TableSpec, *, recreate: bool = True) -> dict:
    """PUT the parquet and COPY it into the typed table. Returns a recon record."""
    cfg = S.load_config()["snowflake"]
    db, schema = cfg["database"], cfg["schemas"][spec.layer]
    fqn = f"{db}.{schema}.{spec.name}"

    path = frames.table_path(spec.layer, spec.name)
    if not path.exists():
        raise FileNotFoundError(f"{path} not found - generate {spec.name} before loading")

    parquet_rows = frames.row_count(spec.layer, spec.name)
    stage = ensure_stage(con, spec.layer)

    if recreate:
        create_table(con, spec)
    else:
        execute(con, f"TRUNCATE TABLE IF EXISTS {fqn}", label=f"truncate {spec.name}")

    # PUT preserves the on-disk filename exactly, including case. Lowercasing it
    # here would make the COPY reference a path that does not exist in the stage -
    # and COPY INTO treats "no matching file" as success, loading 0 rows silently
    # rather than raising. Table names like dim_memberProfile are camelCase, so
    # this must use the real filename.
    staged_name = path.name
    execute(con, f"REMOVE @{stage}/{staged_name}", label=f"clear stage {spec.name}")

    # Forward slashes and a file:// URI keep PUT portable across platforms.
    uri = path.resolve().as_posix()
    execute(
        con,
        f"PUT 'file://{uri}' @{stage} AUTO_COMPRESS = FALSE OVERWRITE = TRUE",
        label=f"put {spec.name}",
    )

    execute(
        con,
        f"""
        COPY INTO {fqn} ({', '.join(spec.col_names())})
        FROM (
          SELECT
        {_select_list(spec)}
          FROM @{stage}/{staged_name}
        )
        FILE_FORMAT = (TYPE = PARQUET)
        ON_ERROR = ABORT_STATEMENT
        PURGE = FALSE
        """,
        label=f"copy {spec.name}",
    )

    table_rows = execute(con, f"SELECT COUNT(*) FROM {fqn}", label=f"count {spec.name}")[0][0]

    # Fail loudly on a zero-row load. COPY INTO reports success when it matches no
    # file, so without this a staging-path mistake looks like a clean run and the
    # empty table only surfaces much later, in silver or in the app.
    if parquet_rows > 0 and int(table_rows) == 0:
        raise RuntimeError(
            f"{fqn} loaded 0 rows from a parquet containing {parquet_rows:,d}. "
            f"The COPY matched no file in @{stage}. Check that the staged file name "
            f"({staged_name}) matches what PUT actually uploaded - PUT preserves "
            f"filename case."
        )

    rec = {
        "layer": spec.layer,
        "table": spec.name,
        "parquet_rows": parquet_rows,
        "table_rows": int(table_rows),
        "reconciled": int(table_rows) == parquet_rows,
    }
    flag = "ok " if rec["reconciled"] else "MISMATCH"
    print(
        f"    {flag} {spec.name:34s} parquet={parquet_rows:8,d}  "
        f"snowflake={int(table_rows):8,d}"
    )
    return rec


def load_layer(con, layer: str, *, recreate: bool = True) -> list[dict]:
    """Load every registered table in a layer, in registration order.

    Registration order is dependency order, so FK parents land before children.
    """
    print(f"\n  loading {layer.upper()} -> Snowflake")
    ensure_schemas(con)
    records = []
    for spec in S.layer_tables(layer):
        records.append(load_table(con, spec, recreate=recreate))
    return records


def print_recon(records: list[dict]) -> bool:
    ok = all(r["reconciled"] for r in records)
    total_p = sum(r["parquet_rows"] for r in records)
    total_t = sum(r["table_rows"] for r in records)
    print()
    print(f"  reconciliation: {len(records)} tables, {total_p:,d} parquet rows, {total_t:,d} loaded")
    if not ok:
        print("  ROW COUNT MISMATCHES:")
        for r in records:
            if not r["reconciled"]:
                print(f"    x {r['layer']}.{r['table']}: {r['parquet_rows']} -> {r['table_rows']}")
    return ok
