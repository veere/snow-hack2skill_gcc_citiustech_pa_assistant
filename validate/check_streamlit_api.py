"""
Guard against using Streamlit APIs newer than the deployed runtime supports.

WHY THIS EXISTS. The app ran perfectly on the dev machine (Streamlit 1.51.0) and then
died on Streamlit in Snowflake with:

    TypeError: FormMixin.form() got an unexpected keyword argument 'border'

`border=` landed in Streamlit 1.29. Nothing local could catch it, because locally the
API existed. A version-floor check is the only thing that catches this class of bug
BEFORE a deploy, which is why it runs offline and needs no Snowflake connection.

Whenever you use a newer Streamlit API, either raise STREAMLIT_PIN in app/deploy_sis.py
or stop using the API. Do not silence this check.

Run:  python validate/check_streamlit_api.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
APP_DIR = REPO / "app"

# (regex, minimum Streamlit version, what it is)
# Versions are from the Streamlit changelog. Only APIs this app actually uses, or
# might plausibly reach for, are listed - an exhaustive table would rot.
API_FLOORS: list[tuple[str, tuple[int, int], str]] = [
    (r"\bst\.form\([^)]*\bborder\s*=", (1, 29), "st.form(border=...)"),
    (r"\bst\.columns\([^)]*\bvertical_alignment\s*=", (1, 36), "st.columns(vertical_alignment=...)"),
    (r"\bst\.columns\([^)]*\bborder\s*=", (1, 41), "st.columns(border=...)"),
    (r"\bst\.container\([^)]*\bborder\s*=", (1, 29), "st.container(border=...)"),
    (r"\bst\.container\([^)]*\bheight\s*=", (1, 31), "st.container(height=...)"),
    (r":material/", (1, 37), "material icon shorthand"),
    (r"\bst\.link_button\b", (1, 28), "st.link_button"),
    (r"\bst\.status\b", (1, 28), "st.status"),
    (r"\bst\.toast\b", (1, 27), "st.toast"),
    (r"\bst\.dialog\b", (1, 37), "st.dialog"),
    (r"\bst\.fragment\b", (1, 37), "st.fragment"),
    (r"\bst\.popover\b", (1, 32), "st.popover"),
    (r"\bst\.query_params\b", (1, 30), "st.query_params"),
    (r"\bst\.column_config\b", (1, 23), "st.column_config"),
    (r"\bst\.data_editor\b", (1, 23), "st.data_editor"),
    (r"\bst\.chat_input\b", (1, 24), "st.chat_input"),
    (r"\bst\.chat_message\b", (1, 24), "st.chat_message"),
    (r"\bst\.divider\b", (1, 18), "st.divider"),
    (r"\bst\.tabs\b", (1, 15), "st.tabs"),
    (r"\buse_container_width\s*=", (1, 19), "use_container_width="),
    (r"\bwidth\s*=\s*[\"']stretch[\"']", (1, 49), "width='stretch'"),
    (r"\bst\.badge\b", (1, 45), "st.badge"),
    (r"\bst\.segmented_control\b", (1, 40), "st.segmented_control"),
    (r"\bst\.pills\b", (1, 40), "st.pills"),
    (r"\bst\.metric\([^)]*\bborder\s*=", (1, 40), "st.metric(border=...)"),
]

PIN_RE = re.compile(r'STREAMLIT_PIN\s*=\s*["\']([0-9]+)\.([0-9]+)')


def read_pin() -> tuple[int, int]:
    text = (APP_DIR / "deploy_sis.py").read_text(encoding="utf-8")
    m = PIN_RE.search(text)
    if not m:
        raise RuntimeError("STREAMLIT_PIN not found in app/deploy_sis.py")
    return int(m.group(1)), int(m.group(2))


def strip_comments_and_strings(source: str) -> str:
    """Crude but adequate: drop # comments and triple-quoted blocks.

    Without this, the explanatory comments in deploy_sis.py that MENTION
    `st.form(border=...)` would be reported as usages.
    """
    source = re.sub(r'"""(?:.|\n)*?"""', "", source)
    source = re.sub(r"'''(?:.|\n)*?'''", "", source)
    return "\n".join(line.split("#", 1)[0] for line in source.splitlines())


def main() -> int:
    pin = read_pin()
    print("=" * 78)
    print(f"STREAMLIT API FLOOR CHECK  ·  deployed pin {pin[0]}.{pin[1]}")
    print("=" * 78)

    violations: list[str] = []
    used: list[tuple[str, tuple[int, int], str]] = []

    for path in sorted(APP_DIR.glob("*.py")):
        source = strip_comments_and_strings(path.read_text(encoding="utf-8"))
        for pattern, floor, label in API_FLOORS:
            for m in re.finditer(pattern, source):
                line_no = source[: m.start()].count("\n") + 1
                used.append((label, floor, f"{path.name}:{line_no}"))
                if floor > pin:
                    violations.append(
                        f"{path.name}:{line_no} uses {label}, which needs Streamlit "
                        f"{floor[0]}.{floor[1]} but the deploy pins {pin[0]}.{pin[1]}"
                    )

    seen: set[str] = set()
    highest = (0, 0)
    for label, floor, where in used:
        if label in seen:
            continue
        seen.add(label)
        highest = max(highest, floor)
        flag = "ok  " if floor <= pin else "FAIL"
        print(f"  {flag} needs {floor[0]:>2}.{floor[1]:<2}  {label:38s} {where}")

    print()
    print(f"  highest API requirement in app/: {highest[0]}.{highest[1]}")
    print(f"  deployed pin                   : {pin[0]}.{pin[1]}")

    print()
    if violations:
        print(f"{len(violations)} VIOLATION(S):")
        for v in violations:
            print(f"  x {v}")
        print()
        print("Either raise STREAMLIT_PIN in app/deploy_sis.py or stop using the API.")
        return 1
    print("API FLOOR CHECK PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
