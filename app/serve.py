"""
Launcher for the self-hosted PA copilot.

Exists because nesting two CLIs - `cortex secret run -- python -m streamlit run ...`
- has the inner `run` argument swallowed during arg parsing, so streamlit reports
`No such command 'app/main.py'`. Invoking Streamlit's own entry point in-process
avoids the ambiguity entirely and gives one canonical way to start the app.

    cortex secret run --map snowflake_pat=SNOWFLAKE_PAT -- python app/serve.py
    cortex secret run --map snowflake_pat=SNOWFLAKE_PAT -- python app/serve.py --port 8600
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MAIN = REPO / "app" / "main.py"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8501)
    ap.add_argument("--headless", action="store_true",
                    help="do not try to open a browser (use for servers and probes)")
    args = ap.parse_args()

    if not os.environ.get("SNOWFLAKE_PAT"):
        print(
            "SNOWFLAKE_PAT is not set.\n\n"
            "Start the app with the token injected:\n"
            "    cortex secret run --map snowflake_pat=SNOWFLAKE_PAT -- python app/serve.py\n\n"
            "Store the token first if you have not:\n"
            "    cortex secret store snowflake_pat --prompt",
            file=sys.stderr,
        )
        return 1

    from streamlit.web import cli as stcli

    sys.argv = [
        "streamlit", "run", str(MAIN),
        "--server.port", str(args.port),
        "--server.headless", "true" if args.headless else "false",
        "--browser.gatherUsageStats", "false",
    ]
    return stcli.main()


if __name__ == "__main__":
    raise SystemExit(main())
