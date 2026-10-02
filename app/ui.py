"""
Presentation layer for the PA copilot.

Kept separate from main.py so the page flow reads as flow, not markup. Everything
here is pure rendering: no queries, no branching on data availability beyond what it
takes to draw an honest empty state.

Two UX principles drive the design:

  STATUS REFLECTION - the analyst should never wonder whether the app is working.
  Long operations name the step they are on, results state where they came from and
  how long they took, and stale data says so.

  ACTION FEEDBACK - every click acknowledges itself immediately, and every outcome
  (success, empty, blocked, failed) has a distinct, unmistakable treatment.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.contracts import Answer, Citation, MemberContext, Route, SourceKind  # noqa: E402
from config import app_config as AC  # noqa: E402

# ---------------------------------------------------------------------------
# Design tokens. One place, so spacing and colour stay consistent.
# ---------------------------------------------------------------------------

INK = "#10243a"
INK_SOFT = "#5c6b7f"
LINE = "#dfe5ee"
CANVAS = "#f7f9fc"
OK = "#12703f"
WARN = "#8a5d00"
STOP = "#9c1f2b"
ACCENT = "#1f5f9e"

STYLES = f"""
<style>
  #MainMenu, footer {{visibility:hidden;}}
  .block-container {{padding-top:1rem; padding-bottom:3rem; max-width:1160px;}}

  h1,h2,h3,h4 {{color:{INK}; letter-spacing:-.01em;}}
  .stTabs [data-baseweb="tab-list"] {{gap:2px; border-bottom:1px solid {LINE};}}
  .stTabs [data-baseweb="tab"] {{
    padding:9px 18px; font-weight:600; font-size:.9rem; color:{INK_SOFT};
  }}
  .stTabs [aria-selected="true"] {{color:{INK}; border-bottom:2px solid {ACCENT};}}

  /* ---- app bar ---- */
  .appbar {{display:flex; align-items:baseline; justify-content:space-between;
           margin-bottom:.35rem;}}
  .appbar .title {{font-size:1.32rem; font-weight:700; color:{INK};}}
  .appbar .env {{font-size:.7rem; color:{INK_SOFT}; text-transform:uppercase;
                letter-spacing:.07em;}}

  /* ---- non-decision notice: prominent, never dismissible ---- */
  .notice {{background:#fffaf0; border:1px solid #f0dcb4; border-left:4px solid {WARN};
           border-radius:7px; padding:11px 15px; font-size:.83rem; color:#5b4a24;
           margin:.5rem 0 1.1rem;}}
  .notice b {{color:#4a3a18;}}

  /* ---- member banner ---- */
  .member {{background:linear-gradient(101deg,{INK} 0%,#1c3d5f 100%); color:#fff;
           border-radius:11px; padding:16px 20px; margin-bottom:.9rem;
           box-shadow:0 2px 10px rgba(16,36,58,.16);}}
  .member .who {{font-size:1.28rem; font-weight:650; line-height:1.25;}}
  .member .ids {{font-size:.79rem; opacity:.8; margin-top:3px; font-variant-numeric:tabular-nums;}}
  .member .tags {{margin-top:11px; display:flex; flex-wrap:wrap; gap:6px;}}

  .tag {{display:inline-flex; align-items:center; gap:5px; padding:3px 10px;
        border-radius:12px; font-size:.71rem; font-weight:700; letter-spacing:.03em;
        text-transform:uppercase;}}
  .tag-ok {{background:rgba(255,255,255,.16); color:#c8f2d9; border:1px solid #2f8f5b;}}
  .tag-warn {{background:rgba(255,255,255,.16); color:#ffe2ab; border:1px solid #b7862a;}}
  .tag-stop {{background:rgba(255,255,255,.2); color:#ffd2d6; border:1px solid #c14653;}}
  .tag-flat {{background:rgba(255,255,255,.12); color:#dbe6f2; border:1px solid #3c5f85;}}

  /* ---- stat tiles ---- */
  .tiles {{display:grid; grid-template-columns:repeat(4,1fr); gap:11px; margin:.2rem 0 1rem;}}
  .tile {{background:#fff; border:1px solid {LINE}; border-radius:9px; padding:12px 14px;}}
  .tile .k {{font-size:.68rem; text-transform:uppercase; letter-spacing:.06em;
            color:{INK_SOFT}; font-weight:700;}}
  .tile .v {{font-size:1.44rem; font-weight:700; color:{INK}; line-height:1.25;
            margin-top:3px; font-variant-numeric:tabular-nums;}}
  .tile .n {{font-size:.71rem; color:{INK_SOFT}; margin-top:1px;}}
  .tile.alarm {{border-color:#e8b6bb; background:#fdf6f7;}}
  .tile.alarm .v {{color:{STOP};}}
  .tile.caution {{border-color:#efdcb0; background:#fffcf4;}}
  .tile.caution .v {{color:{WARN};}}
  .tile.absent .v {{color:{INK_SOFT}; font-size:1rem; font-weight:600;}}

  /* ---- source card ---- */
  .src {{background:{CANVAS}; border:1px solid {LINE}; border-left:3px solid {ACCENT};
        border-radius:7px; padding:10px 13px; margin:6px 0; font-size:.81rem;}}
  .src .h {{font-weight:700; color:{INK}; display:flex; gap:7px; align-items:baseline;}}
  .src .n {{margin-top:3px; color:{INK_SOFT}; font-size:.75rem; line-height:1.55;}}
  .src .idx {{background:{ACCENT}; color:#fff; border-radius:4px; padding:0 6px;
             font-size:.68rem; font-weight:700;}}

  /* ---- suggestion row ---- */
  .sug {{font-size:.88rem; color:{INK}; font-weight:520; line-height:1.4;}}
  .sug-why {{font-size:.72rem; color:{INK_SOFT}; font-style:italic; margin-top:2px;}}

  /* ---- misc ---- */
  .prov {{font-size:.72rem; color:{INK_SOFT}; margin-top:5px;
         border-top:1px dashed {LINE}; padding-top:5px;}}
  .foot {{font-size:.72rem; color:{INK_SOFT}; border-top:1px solid {LINE};
         margin-top:2rem; padding-top:9px; line-height:1.6;}}
  .empty {{background:{CANVAS}; border:1px dashed #ccd6e3; border-radius:9px;
          padding:22px; text-align:center; color:{INK_SOFT}; font-size:.87rem;}}
  div[data-testid="stMetricValue"] {{font-variant-numeric:tabular-nums;}}
</style>
"""


def inject_styles() -> None:
    st.markdown(STYLES, unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def money(value: float | None) -> tuple[str, bool]:
    """Format a currency figure. Returns (text, is_absent).

    None means no accumulator is on record - a terminated member has no current-year
    row. Rendering that as $0 would assert a zero balance the data does not support,
    so absence is shown as absence.
    """
    if value is None:
        return "not on record", True
    return f"${value:,.0f}", False


def relative_time(then: datetime) -> str:
    secs = (datetime.now(timezone.utc) - then).total_seconds()
    if secs < 60:
        return "just now"
    if secs < 3600:
        return f"{int(secs // 60)} min ago"
    return f"{int(secs // 3600)} h ago"


# ---------------------------------------------------------------------------
# App bar and standing notice
# ---------------------------------------------------------------------------

def app_bar(mode: str) -> None:
    st.markdown(
        f'<div class="appbar"><span class="title">{AC.APP_TITLE}</span>'
        f'<span class="env">{mode}</span></div>',
        unsafe_allow_html=True,
    )


def non_decision_notice() -> None:
    st.markdown(
        '<div class="notice"><b>Insight, not decisions.</b> '
        'Every answer arrives with the query or citation behind it so you can verify '
        'it yourself. This copilot will not recommend or imply an approval, denial or '
        'pend — that determination is yours. Where evidence is missing it is reported '
        'as <b>INDETERMINATE</b>, never as a pass or a fail.</div>',
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Member banner
# ---------------------------------------------------------------------------

def member_banner(ctx: MemberContext, loaded_at: datetime | None) -> None:
    tags: list[str] = []
    if ctx.is_eligible_today:
        tags.append('<span class="tag tag-ok">eligible today</span>')
    else:
        tags.append(f'<span class="tag tag-stop">{(ctx.enrollment_status or "not eligible").lower()}</span>')
    if ctx.line_of_business:
        tags.append(f'<span class="tag tag-flat">{ctx.line_of_business.replace("_", " ").lower()}</span>')

    if ctx.breached_pa_count:
        tags.append(f'<span class="tag tag-stop">{ctx.breached_pa_count} past deadline</span>')
    elif ctx.soonest_deadline_days is not None:
        if ctx.soonest_deadline_days < 0:
            tags.append(f'<span class="tag tag-stop">overdue by {abs(ctx.soonest_deadline_days):.1f}d</span>')
        elif ctx.soonest_deadline_days < 2:
            tags.append(f'<span class="tag tag-warn">due in {ctx.soonest_deadline_days:.1f}d</span>')

    if ctx.has_coverage_gap:
        tags.append('<span class="tag tag-warn">coverage gap</span>')
    if ctx.open_pa_count:
        tags.append(f'<span class="tag tag-flat">{ctx.open_pa_count} open</span>')

    facts = "  ·  ".join(
        p for p in [
            f"{ctx.age} yrs" if ctx.age else "",
            ctx.gender or "",
            f"{ctx.total_pa_count} PA requests on record",
            f"loaded {relative_time(loaded_at)}" if loaded_at else "",
        ] if p
    )
    st.markdown(
        f'<div class="member"><div class="who">{ctx.name or ctx.member_id}</div>'
        f'<div class="ids">{ctx.member_id}  ·  {facts}</div>'
        f'<div class="tags">{"".join(tags)}</div></div>',
        unsafe_allow_html=True,
    )


def stat_tiles(ctx: MemberContext) -> None:
    ded, ded_absent = money(ctx.deductible_remaining)
    oop, oop_absent = money(ctx.oop_remaining)

    open_cls = "alarm" if ctx.breached_pa_count else ("caution" if ctx.open_pa_count else "")
    breach_cls = "alarm" if ctx.breached_pa_count else ""

    if ctx.soonest_deadline_days is None:
        soonest, soon_cls, soon_note = "—", "", "no open requests"
    elif ctx.soonest_deadline_days < 0:
        soonest = f"{abs(ctx.soonest_deadline_days):.1f}d"
        soon_cls, soon_note = "alarm", "past the deadline"
    else:
        soonest = f"{ctx.soonest_deadline_days:.1f}d"
        soon_cls = "caution" if ctx.soonest_deadline_days < 2 else ""
        soon_note = "until the earliest deadline"

    st.markdown(
        f'<div class="tiles">'
        f'<div class="tile {open_cls}"><div class="k">open requests</div>'
        f'<div class="v">{ctx.open_pa_count}</div>'
        f'<div class="n">awaiting a determination</div></div>'
        f'<div class="tile {breach_cls}"><div class="k">past deadline</div>'
        f'<div class="v">{ctx.breached_pa_count}</div>'
        f'<div class="n">a count, not a judgement</div></div>'
        f'<div class="tile {soon_cls}"><div class="k">soonest deadline</div>'
        f'<div class="v">{soonest}</div><div class="n">{soon_note}</div></div>'
        f'<div class="tile {"absent" if ded_absent else ""}"><div class="k">deductible left</div>'
        f'<div class="v">{ded}</div>'
        f'<div class="n">out-of-pocket left {oop}</div></div>'
        f'</div>',
        unsafe_allow_html=True,
    )


def skeleton_tiles() -> None:
    """Placeholder shown while the member loads, so the layout never jumps."""
    cells = "".join(
        '<div class="tile"><div class="k">loading</div>'
        '<div class="v" style="color:#c9d3e0">···</div>'
        '<div class="n">&nbsp;</div></div>' for _ in range(4)
    )
    st.markdown(f'<div class="tiles">{cells}</div>', unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Citations
# ---------------------------------------------------------------------------

def citation_card(cite: Citation, idx: int) -> None:
    if cite.kind is SourceKind.SNOWFLAKE_QUERY:
        bits = [
            f"{cite.row_count:,d} rows" if cite.row_count is not None else "",
            f"{cite.elapsed_ms} ms" if cite.elapsed_ms else "",
            f"verified query · {cite.verified_query}" if cite.verified_query else "",
        ]
        st.markdown(
            f'<div class="src"><div class="h"><span class="idx">{idx}</span>'
            f'Live query · governed {AC.SERVING_SCHEMA} layer</div>'
            f'<div class="n">{cite.detail}<br>'
            f'objects · {", ".join(cite.objects)}<br>'
            f'{"  ·  ".join(b for b in bits if b)}</div></div>',
            unsafe_allow_html=True,
        )
        with st.expander(f"[{idx}] Inspect the exact SQL"):
            st.code(cite.sql or "", language="sql")
            if cite.query_id:
                st.caption(f"Snowflake query id · {cite.query_id}")

    elif cite.kind is SourceKind.REGULATORY_DOC:
        bits = [cite.authority or "", cite.detail or "",
                f"effective {cite.effective_date}" if cite.effective_date else "",
                cite.licence or ""]
        st.markdown(
            f'<div class="src"><div class="h"><span class="idx">{idx}</span>'
            f'{cite.citation_id}</div>'
            f'<div class="n">{"  ·  ".join(b for b in bits if b)}</div></div>',
            unsafe_allow_html=True,
        )
        cols = st.columns([1, 4])
        if cite.source_url:
            cols[0].link_button("Open source", cite.source_url, use_container_width=True)
        if cite.excerpt:
            with cols[1].expander(f"[{idx}] Read the quoted passage"):
                st.markdown(f"> {cite.excerpt}")

    else:
        st.markdown(
            f'<div class="src"><div class="h"><span class="idx">{idx}</span>{cite.label}</div>'
            f'<div class="n">{cite.attribution or ""}  ·  {cite.licence or ""}</div></div>',
            unsafe_allow_html=True,
        )


def answer_block(ans: Answer) -> None:
    """Render an answer with a treatment specific to its outcome."""
    if ans.blocked_reason:
        st.error(f"**Answer withheld.** {ans.blocked_reason}", icon=":material/block:")
        return

    if ans.route is Route.REFUSED:
        st.info(ans.text, icon=":material/gavel:")
        return

    if ans.route is Route.UNANSWERABLE:
        st.warning(ans.text, icon=":material/help:")
        for w in ans.warnings:
            st.caption(w)
        return

    st.markdown(ans.text)

    if isinstance(ans.data, pd.DataFrame) and not ans.data.empty:
        with st.expander(f"Inspect the {len(ans.data):,d} row(s) behind this answer"):
            st.dataframe(
                ans.data.head(AC.ANSWER_ROW_LIMIT),
                use_container_width=True, hide_index=True,
            )
            if len(ans.data) > AC.ANSWER_ROW_LIMIT:
                st.caption(f"Showing the first {AC.ANSWER_ROW_LIMIT:,d} of {len(ans.data):,d} rows.")

    st.markdown("###### Sources — every statement above traces to one of these")
    for i, cite in enumerate(ans.citations, 1):
        citation_card(cite, i)

    unverifiable = ans.unverifiable_citations()
    if unverifiable:
        st.warning(
            f"{len(unverifiable)} source(s) lack full verification detail. "
            "Treat those points as unconfirmed.",
            icon=":material/warning:",
        )
    for w in ans.warnings:
        st.caption(f":material/info: {w}")

    st.markdown(
        f'<div class="prov">answered in {(ans.elapsed_ms or 0) / 1000:.1f}s  ·  '
        f'route {ans.route.value.lower().replace("_", " ")}  ·  '
        f'{len(ans.citations)} source(s)  ·  model {ans.model or "n/a"}</div>',
        unsafe_allow_html=True,
    )


def empty_state(title: str, detail: str) -> None:
    st.markdown(
        f'<div class="empty"><b>{title}</b><br>{detail}</div>',
        unsafe_allow_html=True,
    )


def footer() -> None:
    st.markdown(
        f'<div class="foot">'
        f'Reads only the governed <code>{AC.DATABASE}.{AC.SERVING_SCHEMA}</code> layer — '
        f'no access to raw bronze, silver or gold tables. No SSN, address, email or '
        f'phone is exposed. Criteria are reported as MET / UNMET / INDETERMINATE and '
        f'are never collapsed into an overall verdict.</div>',
        unsafe_allow_html=True,
    )
