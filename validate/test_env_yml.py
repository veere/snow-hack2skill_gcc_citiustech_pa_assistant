"""
Prove the environment.yml validator rejects the specs that actually broke the SiS app.

Three deploys were lost to this file, each time discovered only when the app failed to
start in Snowsight. These cases are the real failures, so a regression here means the
same opaque loop starts again.

Run:  cortex secret run --map snowflake_pat=SNOWFLAKE_PAT -- python validate/test_env_yml.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import deploy_sis as D  # noqa: E402
from load import snowflake_loader as L  # noqa: E402

HEADER = "name: sf_env\nchannels:\n  - snowflake\ndependencies:\n"

# (label, yml body, should_raise)
CASES: list[tuple[str, str, bool]] = [
    (
        "python pin - the first failure ('Packages not found: python==3.11')",
        HEADER + "  - python=3.11\n  - streamlit=1.52.1\n",
        True,
    ),
    (
        "PEP 440 '==' - the third failure ('versions must be characters, numbers...')",
        HEADER + "  - streamlit==1.52.1\n  - pandas\n",
        True,
    ),
    (
        "version with a rejected character",
        HEADER + "  - streamlit=1.52.1-rc1\n",
        True,
    ),
    (
        "pin to a build that does not exist in the channel",
        HEADER + "  - streamlit=99.99.99\n",
        True,
    ),
    (
        "package that does not exist at all",
        HEADER + "  - not_a_real_package_xyz\n",
        True,
    ),
    (
        "the spec actually shipped - single '=', no python pin",
        D.ENVIRONMENT_YML,
        False,
    ),
    (
        "unpinned packages only",
        HEADER + "  - streamlit\n  - pandas\n",
        False,
    ),
    (
        "comments and blank lines must not confuse the parser",
        HEADER + "\n  - streamlit=1.52.1  # pinned deliberately\n\n  - pandas\n",
        False,
    ),
]


def main() -> int:
    con = L.connect()
    failures: list[str] = []
    try:
        print("=" * 78)
        print("ENVIRONMENT.YML VALIDATOR")
        print("=" * 78)
        for label, yml, should_raise in CASES:
            try:
                D.validate_environment_yml(con, yml)
                raised, message = False, ""
            except Exception as exc:  # noqa: BLE001
                raised, message = True, str(exc).splitlines()[0]

            ok = raised == should_raise
            verb = "rejected" if raised else "accepted"
            print(f"  {'ok  ' if ok else 'FAIL'} {verb:8s} · {label}")
            if raised and ok:
                print(f"         reason: {message[:120]}")
            if not ok:
                failures.append(
                    f"{label}: expected {'rejection' if should_raise else 'acceptance'}, "
                    f"got {verb}" + (f" ({message[:120]})" if message else "")
                )

        print()
        if failures:
            print(f"{len(failures)} FAILURE(S)")
            for f in failures:
                print(f"  x {f}")
            return 1
        print("ALL PASSED")
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())
