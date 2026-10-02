"""
Connection diagnostic.

    cortex secret run --map snowflake_pat=SNOWFLAKE_PAT -- python load/check_auth.py

Confirms the credential works, reports the effective identity, and checks that the
role can actually see the mart. Prints nothing sensitive.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from load import snowflake_loader as L  # noqa: E402
from config import schemas as S  # noqa: E402


def _inspect_token() -> None:
    """Report the token's shape and expiry claim. Prints no secret material.

    A PAT is a JWT, so the expiry and issuer claims are readable without the
    signing key. Checking them locally distinguishes 'expired or malformed token'
    from 'server rejected a valid token', which are very different problems.
    """
    import base64
    import datetime
    import json as _json
    import os

    v = os.environ.get("SNOWFLAKE_PAT", "")
    if not v:
        return

    parts = v.split(".")
    print(f"  token shape : {len(v)} chars, {len(parts)} segments "
          f"({'valid JWT shape' if len(parts) == 3 else 'MALFORMED - expected 3'})")
    if len(parts) != 3:
        print("  -> a PAT is a JWT with exactly 3 segments. Re-store it, pasting once.")
        return

    try:
        pad = parts[1] + "=" * (-len(parts[1]) % 4)
        payload = _json.loads(base64.urlsafe_b64decode(pad))
    except Exception:  # noqa: BLE001
        print("  token claims: could not decode payload")
        return

    exp = payload.get("exp")
    print(f"  issuer      : {payload.get('iss')}")
    if exp:
        dt = datetime.datetime.fromtimestamp(exp, datetime.timezone.utc)
        now = datetime.datetime.now(datetime.timezone.utc)
        print(f"  expires at  : {dt.isoformat()}")
        print(f"  expired     : {dt < now}")
    print()


def main() -> int:
    cfg = S.load_config()["snowflake"]
    print("=" * 70)
    print("SNOWFLAKE AUTH CHECK")
    print("=" * 70)
    print(f"auth mode : {L.auth_mode()}")
    print(f"target    : {cfg['database']} / {cfg['warehouse']}")
    print()
    _inspect_token()

    try:
        con = L.connect()
    except Exception as exc:  # noqa: BLE001
        print("CONNECT FAILED")
        print()
        print(str(exc)[:1500])
        return 1

    try:
        user, role, wh, db = L.execute(
            con,
            "SELECT current_user(), current_role(), current_warehouse(), current_database()",
        )[0]
        print(f"  user      : {user}")
        print(f"  role      : {role}")
        print(f"  warehouse : {wh}")
        print(f"  database  : {db}")
        print()

        rows = L.execute(
            con,
            f"SELECT table_schema, COUNT(*) FROM {cfg['database']}.INFORMATION_SCHEMA.TABLES "
            f"WHERE table_schema IN ('BRONZE','SILVER','GOLD') GROUP BY 1 ORDER BY 1",
        )
        for schema, n in rows:
            print(f"  {schema:8s} {n} tables visible")

        views = L.execute(
            con,
            f"SELECT COUNT(*) FROM {cfg['database']}.INFORMATION_SCHEMA.VIEWS "
            f"WHERE table_schema = 'SERVING'",
        )[0][0]
        print(f"  SERVING  {views} views visible")
        print()
        print("AUTH OK - credential works and the mart is reachable")
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())
