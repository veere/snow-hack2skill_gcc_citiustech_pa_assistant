"""
Confirm server-side whether the deployed Streamlit app actually STARTED.

Every SiS failure so far was visible only as a banner in Snowsight, so each diagnosis
depended on the error text being relayed back. This closes that loop: when the app
starts, it queries the SERVING views, and those queries land in query history attributed
to the Streamlit object. Queries present means the Python actually ran - which is exactly
what a package-resolution failure prevents.

Open the app in Snowsight first, then run this.

    cortex secret run --map snowflake_pat=SNOWFLAKE_PAT -- python validate/check_sis_started.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import app_config as AC  # noqa: E402
from load import snowflake_loader as L  # noqa: E402

# Clients that are definitely NOT the deployed app, so a session from one of these says
# nothing about whether SiS started:
#   PythonConnector  - this repo's scripts and the self-hosted app (via PAT)
#   SQLAPI           - the SQL REST endpoint used by tooling
#   JavaScript       - the Snowsight web UI itself
#   Go               - the cortex CLI
# An earlier version treated "not PythonConnector" as "must be SiS", which reported
# Snowsight and the CLI as evidence the app had started. It had not.
KNOWN_NON_APP_CLIENTS = ("PythonConnector", "SQLAPI", "JavaScript", "Go")

APP = "PA_INSIGHT_COPILOT"
LOOKBACK_HOURS = 6


def is_non_app(client: str) -> bool:
    return any(client.startswith(prefix) for prefix in KNOWN_NON_APP_CLIENTS)


def main() -> int:
    con = L.connect()
    try:
        print("=" * 78)
        print(f"DID {APP} START IN SNOWFLAKE?")
        print("=" * 78)
        print()
        print("  A SiS app runs Python INSIDE Snowflake, so a successful start creates a")
        print("  session from a client that is none of: this repo's scripts, the SQL REST")
        print("  API, the Snowsight UI, or the cortex CLI.")
        print()
        print("  NOTE: ACCOUNT_USAGE.SESSIONS lags by up to ~3 hours. Presence is strong")
        print("  evidence; absence shortly after opening the app is inconclusive.")
        print()

        rows = L.execute(
            con,
            f"""SELECT client_application_id,
                       authentication_method,
                       COUNT(*) AS sessions,
                       MAX(created_on) AS latest
                FROM SNOWFLAKE.ACCOUNT_USAGE.SESSIONS
                WHERE created_on >= DATEADD('hour', -{LOOKBACK_HOURS}, CURRENT_TIMESTAMP())
                GROUP BY 1, 2
                ORDER BY latest DESC""",
        )

        print(f"  Sessions in the last {LOOKBACK_HOURS}h:")
        candidates: list[tuple[str, str, int, str]] = []
        for client, auth, n, latest in rows:
            client_s = str(client or "(none)")
            known = is_non_app(client_s)
            tag = "known, not the app" if known else "UNRECOGNISED -> possibly the app"
            print(f"    {client_s:28s} {str(auth):26s} n={n:<4d} "
                  f"{str(latest)[:19]}  {tag}")
            if not known:
                candidates.append((client_s, str(auth), n, str(latest)))

        print()
        if not candidates:
            print("  No unrecognised client session found.")
            print()
            print("  Every session in the window came from local tooling, the Snowsight")
            print("  UI or the CLI. Nothing indicates the deployed app has executed")
            print("  Python yet - which is exactly what a package-resolution failure")
            print("  produces, since it fails before any code runs.")
            print()
            print("  Open the app in Snowsight, give it a few minutes, then re-run.")
            return 1

        print("  Unrecognised client session(s) - likely the deployed app:")
        for client, auth, n, latest in candidates:
            print(f"    {client} · {auth} · {n} session(s) · latest {latest}")
        print()
        print("  Cross-check these against the app in Snowsight. If they line up with")
        print("  when you opened it, Python ran inside Snowflake and the package spec")
        print("  and bundle are sound.")
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())
