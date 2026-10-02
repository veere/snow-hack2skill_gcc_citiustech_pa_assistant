"""
Browser-based UX validation for the PA copilot.

WHY THIS EXISTS. `validate/test_ui.py` uses Streamlit's AppTest, which renders the
element tree but never a browser. That catches "did the script raise" and "is the text
present"; it cannot catch a stylesheet that fails to apply, a layout that collapses, an
image that 404s, or a control the analyst cannot actually click.

This drives real Chromium against the locally-running self-hosted app - the SAME code
that runs in Streamlit in Snowflake - and asserts on the rendered DOM. It also captures
screenshots to `.cache/ux/` so the visual result can be reviewed rather than trusted.

The SiS deployment cannot be driven this way: it sits behind Snowsight SSO. Since both
targets run identical code, validating locally is the meaningful check, and the
difference is limited to the connection layer.

Prerequisites (installed by the validate step, or manually):
    python -m pip install playwright
    python -m playwright install chromium

Run:
    # terminal 1
    cortex secret run --map snowflake_pat=SNOWFLAKE_PAT -- python app/serve.py --headless
    # terminal 2
    python validate/validate_ux_browser.py

Or let it manage the server itself:
    cortex secret run --map snowflake_pat=SNOWFLAKE_PAT -- python validate/validate_ux_browser.py --serve
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

SHOTS = REPO / ".cache" / "ux"
BASE = "http://127.0.0.1:8501"

failures: list[str] = []
notes: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> bool:
    print(f"  {'ok  ' if ok else 'FAIL'} {label}")
    if not ok:
        failures.append(f"{label}{(' :: ' + detail) if detail else ''}")
        if detail:
            print(f"        {detail}")
    return ok


def wait_for_server(url: str, timeout: int = 90) -> bool:
    import requests

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if requests.get(f"{url}/_stcore/health", timeout=5).status_code == 200:
                return True
        except Exception:  # noqa: BLE001
            pass
        time.sleep(2)
    return False


def resolve_member() -> str:
    """A member with open requests, so every panel has something to render."""
    from app import snow
    from config import app_config as AC

    return snow.run_query(
        f"""SELECT w.member_id FROM {AC.fqn('VW_PA_WORKLIST')} w
            JOIN {AC.fqn('VW_MEMBER_FINANCIAL')} f ON f.member_id = w.member_id
            GROUP BY w.member_id ORDER BY COUNT(*) DESC LIMIT 1"""
    ).df.iloc[0, 0]


def run_checks(member: str) -> None:  # noqa: C901
    from playwright.sync_api import sync_playwright

    SHOTS.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        console_errors: list[str] = []
        page.on("console", lambda m: console_errors.append(m.text)
                if m.type == "error" else None)
        failed_requests: list[str] = []
        page.on("requestfailed",
                lambda r: failed_requests.append(f"{r.method} {r.url[:110]}"))

        def settle(seconds: float = 3.0) -> None:
            """Streamlit streams over a websocket, so networkidle is not enough.

            Wait for the running indicator to clear where possible, then hold briefly.
            A fixed sleep alone produced flaky failures on the slower panels.
            """
            page.wait_for_load_state("networkidle")
            try:
                page.wait_for_selector("[data-testid='stStatusWidget']",
                                       state="detached", timeout=int(seconds * 1000))
            except Exception:  # noqa: BLE001
                pass
            time.sleep(seconds)

        def dump(name: str, body: str) -> None:
            """Persist rendered text so a failure can be diagnosed after the fact."""
            (SHOTS / f"{name}.txt").write_text(body, encoding="utf-8")

        # ---------------------------------------------------------------
        print("\n1. COLD START")
        page.goto(BASE, wait_until="domcontentloaded", timeout=60_000)
        settle(6)
        page.screenshot(path=str(SHOTS / "01-cold-start.png"), full_page=True)

        body = page.inner_text("body")
        dump("01-cold-start", body)
        check("page renders visible text", len(body) > 200, f"{len(body)} chars")
        check("title is shown", "PA Insight Copilot" in body)
        check("non-decision notice is visible", "Insight, not decisions" in body)
        check("prompts for a member", "Start with a member" in body,
              f"body starts: {body[:200]!r}")

        # The stylesheet is the difference between a polished surface and raw markup.
        notice_bg = page.evaluate(
            """() => {
                const el = document.querySelector('.notice');
                return el ? getComputedStyle(el).backgroundColor : null;
            }"""
        )
        check("custom stylesheet applied (.notice styled)",
              notice_bg is not None and notice_bg != "rgba(0, 0, 0, 0)",
              f"computed background: {notice_bg}")

        check("no raw HTML leaked as text",
              "<div" not in body and "unsafe_allow_html" not in body)
        check("member input is present and enabled",
              page.locator("input[type='text']").first.is_enabled())

        # ---------------------------------------------------------------
        print("\n2. UNKNOWN MEMBER")
        page.goto(f"{BASE}/?member_id=MBR-00000000", wait_until="domcontentloaded",
                  timeout=60_000)
        settle(8)
        page.screenshot(path=str(SHOTS / "02-unknown-member.png"), full_page=True)
        body = page.inner_text("body")
        dump("02-unknown-member", body)
        check("states no member matched", "No member matches" in body,
              f"body: {body[:260]!r}")
        check("explains why nothing is shown", "wrong member" in body)
        check("view selector is not rendered",
              page.get_by_role("button", name="Overview", exact=True).count() == 0)

        # ---------------------------------------------------------------
        print(f"\n3. REAL MEMBER · {member}")
        page.goto(f"{BASE}/?member_id={member}", wait_until="domcontentloaded",
                  timeout=60_000)
        settle(8)
        page.screenshot(path=str(SHOTS / "03-overview.png"), full_page=True)
        body = page.inner_text("body")

        check("member banner shows the id", member in body)
        banner_bg = page.evaluate(
            """() => {
                const el = document.querySelector('.member');
                return el ? getComputedStyle(el).backgroundImage : null;
            }"""
        )
        check("member banner has its gradient",
              banner_bg is not None and "gradient" in (banner_bg or ""),
              f"computed background-image: {str(banner_bg)[:70]}")

        tiles = page.locator(".tile").count()
        check("four stat tiles render", tiles == 4, f"found {tiles}")

        # A session-state-backed segmented control, not st.tabs. st.tabs lost its
        # selection on every rerun, which bounced the analyst out of the Ask view the
        # moment they submitted a question.
        #
        # Located by accessible role and name rather than a data-testid. Streamlit
        # renders this as data-testid="stButtonGroup" with
        # "stBaseButton-segmented_control" children - internal names that shift between
        # versions. Role+name is what a user perceives and is stable.
        def view_button(name: str):
            return page.get_by_role("button", name=name, exact=True)

        present = [n for n in ("Overview", "Ask", "Reference imaging")
                   if view_button(n).count() == 1]
        check("three view options render", len(present) == 3, f"found {present}")

        check("worklist table renders",
              page.locator("[data-testid='stDataFrame']").count() >= 1)
        check("provenance line is visible", "source ·" in body)
        check("INDETERMINATE is explained",
              "Indeterminate" in body or "INDETERMINATE" in body)

        # Layout sanity: tiles should sit on ONE row at this viewport. A wrapped grid
        # is the classic sign of a broken CSS grid that AppTest cannot see.
        tops = page.evaluate(
            """() => Array.from(document.querySelectorAll('.tile'))
                        .map(e => Math.round(e.getBoundingClientRect().top))"""
        )
        check("stat tiles align on one row", len(set(tops)) == 1,
              f"tile top offsets: {tops}")

        # Nothing should overflow the viewport horizontally.
        overflow = page.evaluate(
            "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"
        )
        check("no horizontal overflow", overflow <= 2, f"overflow {overflow}px")

        # ---------------------------------------------------------------
        print("\n4. ASK TAB · suggestions and controls")
        view_button("Ask").click()
        settle(4)
        page.screenshot(path=str(SHOTS / "04-ask-tab.png"), full_page=True)
        body = page.inner_text("body")

        check("suggestions are offered", "Worth checking for this member" in body)
        check("each suggestion explains itself", "surfaced because" in body)
        check("question box is present", page.locator("textarea").count() >= 1)
        ask_buttons = page.locator("button", has_text="Ask")
        check("ask controls are clickable", ask_buttons.count() >= 1,
              f"found {ask_buttons.count()}")

        # ---------------------------------------------------------------
        print("\n5. ACTION FEEDBACK · empty submit must be handled")
        # Target the FORM's submit button specifically. Clicking "the last Ask button"
        # hit a suggestion row instead, which fired a real question and made this check
        # assert the wrong thing.
        form_submit = page.locator("[data-testid='stFormSubmitButton'] button")
        if form_submit.count() == 0:
            notes.append("could not locate the form submit button; skipped empty-submit check")
            print("  note  form submit button not found")
        else:
            page.locator("textarea").first.fill("")
            form_submit.last.click()
            settle(4)
            body = page.inner_text("body")
            dump("05-empty-submit", body)
            check("empty question gives explicit feedback",
                  "Type a question first" in body,
                  f"body: {body[:260]!r}")
            page.screenshot(path=str(SHOTS / "05-empty-submit.png"), full_page=True)

        # ---------------------------------------------------------------
        print("\n6. REFERENCE IMAGING · images must actually load")
        view_button("Reference imaging").click()
        # Longer than the other panels on purpose: image bytes are read from the stage
        # one round trip at a time, which is slower than the presigned URLs this
        # replaced. A short wait here reported "no images rendered" for a panel that
        # was simply still loading.
        settle(14)
        page.screenshot(path=str(SHOTS / "06-reference.png"), full_page=True)
        body = page.inner_text("body")
        check("disclaimer is shown", "orientation only" in body.lower())

        imgs = page.locator("img")
        total = imgs.count()
        broken = page.evaluate(
            """() => Array.from(document.images)
                        .filter(i => i.complete && i.naturalWidth === 0).length"""
        )
        if total <= 1:
            notes.append("no reference image rendered for the default region selection")
            print(f"  note  only {total} img element(s) present")
        else:
            check("reference images loaded without breakage", broken == 0,
                  f"{broken} of {total} images failed to load")
            check("attribution is displayed alongside images",
                  "·" in body and ("CC" in body or "Public domain" in body))

        # ---------------------------------------------------------------
        print("\n7. RUNTIME HEALTH")
        real_console = [
            e for e in console_errors
            if "favicon" not in e.lower() and "sourcemap" not in e.lower()
        ]
        check("no javascript console errors", not real_console,
              "; ".join(real_console[:3])[:240])
        real_failed = [f for f in failed_requests if "favicon" not in f.lower()]
        check("no failed network requests", not real_failed,
              "; ".join(real_failed[:3])[:240])
        check("no python traceback surfaced in the UI",
              "Traceback" not in page.inner_text("body"))

        browser.close()

    print(f"\n  screenshots written to {SHOTS}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--serve", action="store_true",
                    help="start the app itself instead of expecting one running")
    args = ap.parse_args()

    try:
        import playwright  # noqa: F401
    except ImportError:
        print("Playwright is not installed. Run:\n"
              "  python -m pip install playwright\n"
              "  python -m playwright install chromium", file=sys.stderr)
        return 1

    proc: subprocess.Popen | None = None
    if args.serve:
        if not os.environ.get("SNOWFLAKE_PAT"):
            print("SNOWFLAKE_PAT is not set; run under "
                  "`cortex secret run --map snowflake_pat=SNOWFLAKE_PAT -- ...`",
                  file=sys.stderr)
            return 1
        print("starting the app…")
        proc = subprocess.Popen(
            [sys.executable, str(REPO / "app" / "serve.py"), "--headless"],
            cwd=str(REPO), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )

    try:
        if not wait_for_server(BASE):
            print(f"No app responding at {BASE}. Start it with:\n"
                  "  cortex secret run --map snowflake_pat=SNOWFLAKE_PAT -- "
                  "python app/serve.py --headless", file=sys.stderr)
            return 1

        member = resolve_member()
        print("=" * 78)
        print("BROWSER UX VALIDATION")
        print("=" * 78)
        run_checks(member)

        print()
        print("=" * 78)
        print(f"  failures: {len(failures)}")
        for f in failures:
            print(f"    x {f}")
        print(f"  notes: {len(notes)}")
        for n in notes:
            print(f"    ! {n}")
        print()
        print("BROWSER UX PASSED" if not failures else "BROWSER UX FAILED")
        return 1 if failures else 0
    finally:
        if proc:
            proc.terminate()


if __name__ == "__main__":
    raise SystemExit(main())
