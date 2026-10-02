"""
Headless UI tests for the PA copilot.

Uses streamlit.testing.v1.AppTest, which executes the real script and exposes the
rendered element tree. That catches what an HTTP probe cannot: Streamlit renders
client-side, so a 200 response only proves the shell was served, not that the page
built without raising.

Covers the states an analyst will actually hit, including the unhappy ones - no
member, unknown member, a member with no PA history, and a refused question.

Run:
    cortex secret run --map snowflake_pat=SNOWFLAKE_PAT -- python validate/test_ui.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from streamlit.testing.v1 import AppTest  # noqa: E402

APP = Path(__file__).resolve().parent.parent / "app" / "main.py"
TIMEOUT = 180

failures: list[str] = []
notes: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> bool:
    print(f"  {'ok  ' if condition else 'FAIL'} {label}")
    if not condition:
        failures.append(f"{label}{(' :: ' + detail) if detail else ''}")
        if detail:
            print(f"        {detail}")
    return condition


def texts(at: AppTest) -> str:
    """All rendered text, flattened, for substring assertions."""
    parts: list[str] = []
    for coll in (at.markdown, at.caption, at.info, at.warning, at.error, at.success):
        for el in coll:
            parts.append(str(getattr(el, "value", "")))
    for el in at.title:
        parts.append(str(getattr(el, "value", "")))
    return "\n".join(parts)


def no_exception(at: AppTest, label: str) -> bool:
    ok = not at.exception
    return check(
        label, ok,
        "; ".join(str(getattr(e, "value", e))[:220] for e in at.exception) if not ok else "",
    )


def run(query: dict | None = None) -> AppTest:
    at = AppTest.from_file(str(APP), default_timeout=TIMEOUT)
    if query:
        at.query_params.update(query)
    return at.run()


def main() -> int:  # noqa: C901
    print("=" * 78)
    print("1. COLD START - no member selected")
    print("=" * 78)
    at = run()
    no_exception(at, "renders without raising")
    body = texts(at)
    check("shows the non-decision notice", "Insight, not decisions" in body)
    check("prompts for a member", "Start with a member" in body)
    check("documents the URL parameter", "member_id" in body)
    check("has a member input", len(at.text_input) >= 1)
    check("has a load button", any("Load" in str(b.label) for b in at.button))

    print()
    print("=" * 78)
    print("2. UNKNOWN MEMBER - must refuse to guess")
    print("=" * 78)
    at = run({"member_id": "MBR-00000000"})
    no_exception(at, "renders without raising")
    body = texts(at)
    check("states no member matched", "No member matches" in body)
    check("explains why nothing is shown", "never looking at the wrong member" in body)
    # The unknown-member path must render NO member content at all. Asserting on the
    # view selector would be vacuous here (AppTest cannot see it), so assert on the
    # panel content that would only appear for a member who actually resolved.
    check("renders no member panels",
          "Open requests" not in body and "Criteria evidence" not in body)

    print()
    print("=" * 78)
    print("3. REAL MEMBER - the main surface")
    print("=" * 78)
    # Resolve a member with open requests so every panel has something to draw.
    from app import grounding, snow
    from config import app_config as AC

    member = snow.run_query(
        f"""SELECT w.member_id FROM {AC.fqn('VW_PA_WORKLIST')} w
            JOIN {AC.fqn('VW_MEMBER_FINANCIAL')} f ON f.member_id = w.member_id
            GROUP BY w.member_id ORDER BY COUNT(*) DESC LIMIT 1"""
    ).df.iloc[0, 0]
    print(f"  using member {member}")

    at = run({"member_id": member})
    no_exception(at, "renders without raising")
    body = texts(at)
    check("renders the member banner", member in body)
    # The view selector is deliberately NOT asserted here. AppTest has no accessor for
    # st.segmented_control and its option labels never reach the element tree, so any
    # assertion would be vacuous. validate_ux_browser.py covers it properly, by
    # accessible role and name, and also verifies that clicking each option works -
    # which is the part that actually matters and which AppTest cannot test at all.
    check("shows open requests section", "Open requests" in body)
    check("shows criteria evidence section", "Criteria evidence" in body)
    check("explains INDETERMINATE in the UI", "Indeterminate" in body or "INDETERMINATE" in body)
    check("shows data provenance", "source ·" in body or "source&nbsp;·" in body)
    check("renders at least one dataframe", len(at.dataframe) >= 1,
          f"found {len(at.dataframe)}")
    check("footer states the governance boundary",
          "governed" in body and "SERVING" in body)

    ctx = grounding.load_member_context(member)
    suggestions = grounding.suggest_questions(ctx)
    check("suggestions were generated", bool(suggestions),
          f"{len(suggestions)} suggestions")
    # Suggestions live in the Ask view. The default view is Overview, so they are not
    # in the rendered body on first load - assert on the generated objects instead of
    # the page text, which is what they are actually a contract about.
    if suggestions:
        check("each suggestion explains why it surfaced",
              all(s.reason for s in suggestions),
              f"missing reasons: {[s.text for s in suggestions if not s.reason]}")
        check("no suggestion contains decision language",
              not any(grounding.check_output(s.text) for s in suggestions))

    print()
    print("=" * 78)
    print("4. MEMBER WITH NO PA HISTORY - empty state must be graceful")
    print("=" * 78)
    empty = snow.run_query(
        f"""SELECT e.member_id FROM {AC.fqn('VW_MEMBER_ELIGIBILITY')} e
            LEFT JOIN {AC.fqn('VW_PA_REQUEST_360')} r ON r.member_id = e.member_id
            WHERE r.pa_id IS NULL LIMIT 1"""
    ).df
    if empty.empty:
        notes.append("no member without PA history exists to test the empty state")
        print("  skipped - every member has PA history")
    else:
        at = run({"member_id": empty.iloc[0, 0]})
        no_exception(at, "renders without raising")
        body = texts(at)
        check("shows an explicit empty state",
              "No open prior authorization requests" in body
              or "No criteria evaluated" in body)
        check("does not crash on absent financials", not at.exception)

    print()
    print("=" * 78)
    print("5. TERMINATED MEMBER - absent accumulator must not read as zero")
    print("=" * 78)
    termed = snow.run_query(
        f"""SELECT e.member_id FROM {AC.fqn('VW_MEMBER_ELIGIBILITY')} e
            LEFT JOIN {AC.fqn('VW_MEMBER_FINANCIAL')} f ON f.member_id = e.member_id
            WHERE NOT e.is_eligible_today AND f.member_id IS NULL LIMIT 1"""
    ).df
    if termed.empty:
        notes.append("no terminated member without a financial row to test")
        print("  skipped")
    else:
        at = run({"member_id": termed.iloc[0, 0]})
        no_exception(at, "renders without raising")
        body = texts(at)
        check("absent accumulator shown as 'not on record', not $0",
              "not on record" in body,
              "a terminated member has no current-year accumulator; rendering $0 "
              "would assert a balance the data does not support")
        check("eligibility status is visible", "termed" in body.lower()
              or "not eligible" in body.lower())

    print()
    print("=" * 78)
    print("6. STYLE AND STATUS AFFORDANCES")
    print("=" * 78)
    at = run({"member_id": member})
    body = texts(at)
    check("styles are injected once", body.count("<style>") <= 1,
          f"found {body.count('<style>')} style blocks")
    check("deployment mode is surfaced", "self-hosted" in body or "SiS" in body)
    check("tiles carry explanatory subtext", "a count, not a judgement" in body)
    check("no raw python repr leaked into the page",
          "MemberContext(" not in body and "dtype:" not in body)

    print()
    print("=" * 78)
    print("SUMMARY")
    print("=" * 78)
    print(f"  failures: {len(failures)}")
    for f in failures:
        print(f"    x {f}")
    print(f"  notes: {len(notes)}")
    for n in notes:
        print(f"    ! {n}")
    print()
    print("UI TESTS PASSED" if not failures else "UI TESTS FAILED")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
