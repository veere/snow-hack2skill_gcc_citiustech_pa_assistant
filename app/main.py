"""
PA Insight Copilot - the analyst-facing surface.

UX priorities, in order:

  1. ONE task at a time. The context is a single member; everything on screen relates
     to that member. No cross-member browsing.
  2. Status is always visible. Answering runs several steps (route, query, retrieve,
     summarise, guard) and each is named as it happens, so a 15-second answer never
     looks like a hang.
  3. Every click acknowledges itself, and every outcome - answered, empty, refused,
     blocked, failed - gets a visually distinct treatment.
  4. The copilot never decides, and says so where the analyst is looking.

One codebase, two deployments (see app/snow.py):

    cortex secret run --map snowflake_pat=SNOWFLAKE_PAT -- python app/serve.py
    # or deploy to Streamlit in Snowflake for the standalone surface
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import answering, grounding, snow, ui  # noqa: E402
from app.contracts import Answer, MemberContext  # noqa: E402
from config import app_config as AC  # noqa: E402

st.set_page_config(
    page_title=AC.APP_TITLE,
    page_icon=":material/clinical_notes:",
    layout="wide",
    initial_sidebar_state="collapsed",
)
ui.inject_styles()

HISTORY_LIMIT = 6


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

def state_defaults() -> None:
    st.session_state.setdefault("member_id", "")
    st.session_state.setdefault("ctx", None)
    st.session_state.setdefault("loaded_at", None)
    st.session_state.setdefault("history", [])
    st.session_state.setdefault("pending_question", "")
    st.session_state.setdefault("load_error", "")


def member_from_url() -> str:
    """Read the member from the URL so a host application can set the context.

    st.query_params is GA in Streamlit in Snowflake; SiS prefixes the key with
    'streamlit-' in the address bar and strips it again here, so the same
    ?member_id=... contract works in both deployments.
    """
    try:
        return (st.query_params.get(AC.QUERY_PARAM_MEMBER) or "").strip().upper()
    except Exception:  # noqa: BLE001
        return ""


def load_member(member_id: str) -> None:
    """Load a member with explicit progress, and record the outcome in state."""
    st.session_state["load_error"] = ""
    with st.status(f"Loading {member_id}…", expanded=False) as status:
        try:
            status.write("Checking eligibility and plan…")
            ctx = grounding.load_member_context(member_id)
        except Exception as exc:  # noqa: BLE001
            status.update(label="Could not reach Snowflake", state="error")
            st.session_state["load_error"] = str(exc).splitlines()[0][:400]
            st.session_state["ctx"] = None
            return

        if not ctx.found:
            status.update(label=f"No member matches {member_id}", state="error")
            st.session_state["ctx"] = ctx
            st.session_state["loaded_at"] = datetime.now(timezone.utc)
            return

        status.write(f"Found {ctx.name} · {ctx.line_of_business}")
        status.write(f"{ctx.open_pa_count} open of {ctx.total_pa_count} PA requests")
        status.update(label=f"Loaded {ctx.name}", state="complete")

    st.session_state["ctx"] = ctx
    st.session_state["loaded_at"] = datetime.now(timezone.utc)
    st.session_state["history"] = []
    st.toast(f"Loaded {ctx.name}", icon=":material/person_check:")


def ask(question: str, ctx: MemberContext) -> None:
    """Answer a question, narrating each grounding step as it runs.

    The step labels are not decoration: answering takes 5-20 seconds because it may
    call Cortex Analyst, execute generated SQL, search the regulatory corpus and then
    summarise. Naming the current step is the difference between "working" and "hung".
    """
    route = grounding.classify(question)
    plan = {
        "REFUSED": ["Checking the request against the non-decision guardrail"],
        "MEMBER_DATA": ["Interpreting the question against the semantic view",
                        "Running the generated SQL on the SERVING layer",
                        "Summarising only what the rows show"],
        "REGULATORY": ["Selecting the relevant regulatory documents",
                       "Retrieving matching sections",
                       "Answering strictly from the retrieved text"],
        "BOTH": ["Interpreting the question against the semantic view",
                 "Running the generated SQL on the SERVING layer",
                 "Retrieving the applicable regulation",
                 "Assembling the answer and its citations"],
    }.get(route.value, ["Working"])

    with st.status("Gathering evidence…", expanded=True) as status:
        for step in plan:
            status.write(step)
        status.write("Screening the draft for decision-making language")
        try:
            ans = answering.answer(question, ctx)
        except Exception as exc:  # noqa: BLE001
            status.update(label="Answering failed", state="error")
            st.error(
                f"Something went wrong while answering: "
                f"{str(exc).splitlines()[0][:300]}",
                icon=":material/error:",
            )
            return

        if ans.blocked_reason:
            status.update(label="Draft withheld by the guardrail", state="error")
        elif ans.route.value == "REFUSED":
            status.update(label="Outside what this copilot will answer", state="complete")
        elif ans.route.value == "UNANSWERABLE":
            status.update(label="Could not ground an answer", state="error")
        else:
            status.update(
                label=f"Answered with {len(ans.citations)} source(s) "
                      f"in {(ans.elapsed_ms or 0) / 1000:.1f}s",
                state="complete",
            )

    st.session_state["history"].insert(0, ans)
    del st.session_state["history"][HISTORY_LIMIT:]


# ---------------------------------------------------------------------------
# Panels
# ---------------------------------------------------------------------------

def panel_overview(ctx: MemberContext) -> None:
    ui.stat_tiles(ctx)

    st.markdown("##### Open requests")
    if not ctx.open_pa_count:
        ui.empty_state(
            "No open prior authorization requests",
            "Nothing is awaiting a determination for this member right now.",
        )
    else:
        with st.spinner("Loading the worklist…"):
            res = snow.run_query(
                f"""SELECT pa_id AS "PA id",
                           service_category AS "Service",
                           urgency_flag AS "Urgency",
                           pa_status AS "Status",
                           sla_state AS "SLA",
                           ROUND(days_until_deadline, 1) AS "Days left",
                           criteria_met_count || ' of ' || criteria_total_count AS "Criteria with evidence",
                           requested_procedure_desc AS "Requested",
                           ordering_provider_name AS "Ordering provider"
                    FROM {AC.fqn('VW_PA_WORKLIST')}
                    WHERE member_id = :mid
                    ORDER BY days_until_deadline""",
                params={"mid": ctx.member_id},
            )
        st.dataframe(res.df, use_container_width=True, hide_index=True)
        st.markdown(
            f'<div class="prov">source · {AC.fqn("VW_PA_WORKLIST")}  ·  '
            f'{res.row_count} rows in {res.elapsed_ms} ms. '
            f'"Criteria with evidence" counts what was found in the record — it is '
            f'not a score and implies no outcome.</div>',
            unsafe_allow_html=True,
        )

    st.markdown("##### Criteria evidence")
    with st.spinner("Aggregating criteria…"):
        crit = snow.run_query(
            f"""SELECT criterion_type AS "Criterion",
                       COUNT_IF(criterion_result = 'MET') AS "Met",
                       COUNT_IF(criterion_result = 'UNMET') AS "Unmet",
                       COUNT_IF(criterion_result = 'INDETERMINATE') AS "Indeterminate"
                FROM {AC.fqn('VW_PA_CRITERIA_EVIDENCE')}
                WHERE member_id = :mid
                GROUP BY criterion_type
                ORDER BY "Unmet" DESC, "Indeterminate" DESC""",
            params={"mid": ctx.member_id},
        )
    if crit.df.empty:
        ui.empty_state("No criteria evaluated",
                       "This member has no prior authorization criteria on record.")
    else:
        st.dataframe(
            crit.df, use_container_width=True, hide_index=True,
            column_config={
                "Met": st.column_config.NumberColumn(width="small"),
                "Unmet": st.column_config.NumberColumn(width="small"),
                "Indeterminate": st.column_config.NumberColumn(width="small"),
            },
        )
        st.markdown(
            f'<div class="prov">source · {AC.fqn("VW_PA_CRITERIA_EVIDENCE")}  ·  '
            f'{crit.row_count} criterion types in {crit.elapsed_ms} ms. '
            f'<b>Indeterminate</b> means the evidence is absent from the record — '
            f'neither a pass nor a fail.</div>',
            unsafe_allow_html=True,
        )


def panel_ask(ctx: MemberContext) -> None:
    suggestions = grounding.suggest_questions(ctx)
    if suggestions:
        st.markdown("##### Worth checking for this member")
        st.caption(
            "Surfaced from this member's current state. Each one asks for evidence; "
            "none of them points toward an outcome."
        )
        for i, sug in enumerate(suggestions):
            cols = st.columns([6, 1], vertical_alignment="center")
            cols[0].markdown(
                f'<div class="sug">{sug.text}</div>'
                f'<div class="sug-why">surfaced because {sug.reason}</div>',
                unsafe_allow_html=True,
            )
            if cols[1].button("Ask", key=f"sug{i}", use_container_width=True):
                st.session_state["pending_question"] = sug.text
                st.rerun()
        st.divider()

    with st.form("ask_form", clear_on_submit=False, border=False):
        question = st.text_area(
            "Ask about this member",
            value=st.session_state.get("pending_question", ""),
            placeholder="e.g. which criteria are indeterminate on the open imaging request, "
                        "and what evidence is on file?",
            height=88,
            label_visibility="collapsed",
        )
        cols = st.columns([1, 4])
        submitted = cols[0].form_submit_button(
            "Ask", type="primary", use_container_width=True,
        )
        cols[1].caption(
            "Answers cite the SQL or the regulation behind them. "
            "Requests for a decision will be declined."
        )

    if submitted:
        if not question.strip():
            st.warning("Type a question first.", icon=":material/edit:")
        else:
            st.session_state["pending_question"] = ""
            ask(question.strip(), ctx)

    history: list[Answer] = st.session_state.get("history", [])
    if not history:
        return

    st.divider()
    for n, ans in enumerate(history):
        if n == 0:
            st.markdown(f"**Q · {ans.question}**")
            ui.answer_block(ans)
        else:
            with st.expander(f"Earlier · {ans.question[:96]}"):
                ui.answer_block(ans)


def panel_reference(ctx: MemberContext) -> None:
    st.caption(AC.RADIOLOGY_DISCLAIMER)

    regions: list[str] = []
    for cat in ctx.service_categories:
        regions += list(AC.SERVICE_CATEGORY_TO_REGION.get(cat, ()))
    regions = sorted(set(regions))

    if not regions:
        ui.empty_state(
            "No imaging-related open requests",
            "Reference anatomy appears here when this member has an open request "
            "for imaging, surgery, pain management or rehabilitation.",
        )
        return

    picked = st.multiselect(
        "Body region", options=regions, default=regions[:1],
        help="Scoped to the regions relevant to this member's open service categories.",
    )
    if not picked:
        st.caption("Pick a body region to see paired normal and abnormal examples.")
        return

    in_list = ", ".join(f"'{r}'" for r in picked)
    try:
        with st.spinner("Fetching reference images…"):
            # file_name only. Image bytes are read through the session by
            # snow.read_stage_image, because a presigned URL is served as
            # application/octet-stream and a browser <img> will not render it.
            refs = snow.run_query(
                f"""SELECT body_region, modality, finding_class, teaching_caption,
                           source_page_url, licence, attribution, file_name
                    FROM {AC.fqn(AC.TBL_RADIOLOGY_REFERENCE)}
                    WHERE body_region IN ({in_list})
                    ORDER BY body_region, finding_class, modality"""
            )
    except Exception as exc:  # noqa: BLE001
        st.error(f"Reference library unavailable: {str(exc).splitlines()[0][:220]}",
                 icon=":material/broken_image:")
        return

    if refs.df.empty:
        ui.empty_state("No reference images for that selection",
                       "Try a different body region.")
        return

    stage_fqn = AC.fqn(AC.STAGE_RADIOLOGY)

    # Pre-fetch every image BEFORE rendering, under one visible progress indicator.
    #
    # Reading bytes from the stage costs a round trip per image. Doing that inside the
    # render loop meant the page built row by row with no indication anything was
    # happening - the spinner had already closed. Fetching up front with a progress bar
    # keeps the status honest and makes the wait legible. Results are cached, so this is
    # only slow the first time a region is opened.
    names = list(refs.df["FILE_NAME"])
    images: dict[str, bytes | None] = {}
    progress = st.progress(0.0, text=f"Loading {len(names)} reference image(s)…")
    for i, name in enumerate(names, 1):
        images[name] = snow.read_stage_image(stage_fqn, name)
        progress.progress(i / len(names),
                          text=f"Loading reference images… {i} of {len(names)}")
    progress.empty()

    failed = sum(1 for v in images.values() if not v)
    if failed:
        st.warning(
            f"{failed} of {len(names)} image(s) could not be read from the stage.",
            icon=":material/broken_image:",
        )

    for region in picked:
        block = refs.df[refs.df["BODY_REGION"] == region]
        if block.empty:
            continue
        normal = block[block["FINDING_CLASS"] == "NORMAL"]
        abnormal = block[block["FINDING_CLASS"] == "ABNORMAL"]
        st.markdown(f"##### {region.replace('_', ' ').title()}")

        if normal.empty:
            st.info(
                "No openly-licensed NORMAL example is available for this region. "
                "Most normal studies on Wikimedia Commons carry a share-alike licence, "
                "which is excluded here. Abnormal examples are shown alone rather than "
                "paired with a substitute from another region.",
                icon=":material/info:",
            )

        cols = st.columns(2)
        for col, frame, label in ((cols[0], normal, "Normal baseline"),
                                 (cols[1], abnormal, "Abnormal comparison")):
            with col:
                st.markdown(f"**{label}**")
                if frame.empty:
                    st.caption("none available")
                    continue
                for _, row in frame.iterrows():
                    data = images.get(row["FILE_NAME"])
                    if data:
                        st.image(data, use_container_width=True)
                    else:
                        st.caption(":material/broken_image: image could not be read "
                                   "from the stage")
                    st.caption(
                        f"**{row['MODALITY']}** — {row.get('TEACHING_CAPTION') or ''}"
                    )
                    st.caption(
                        f"{row.get('ATTRIBUTION') or ''} · {row.get('LICENCE') or ''} · "
                        f"[source]({row.get('SOURCE_PAGE_URL') or ''})"
                    )
        st.divider()


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------

def main() -> None:
    state_defaults()
    ui.app_bar(snow.mode())
    ui.non_decision_notice()

    url_member = member_from_url()
    if url_member and url_member != st.session_state["member_id"]:
        st.session_state["member_id"] = url_member
        load_member(url_member)

    with st.form("member_form", clear_on_submit=False, border=False):
        cols = st.columns([4, 1, 1], vertical_alignment="bottom")
        entered = cols[0].text_input(
            "Member ID", value=st.session_state["member_id"],
            placeholder="MBR-12345678", label_visibility="collapsed",
        )
        load = cols[1].form_submit_button("Load member", type="primary",
                                         use_container_width=True)
        refresh = cols[2].form_submit_button("Refresh", use_container_width=True,
                                            help="Re-read this member from Snowflake")

    if load and entered.strip():
        st.session_state["member_id"] = entered.strip().upper()
        load_member(st.session_state["member_id"])
    elif refresh and st.session_state["member_id"]:
        load_member(st.session_state["member_id"])

    if st.session_state["load_error"]:
        st.error(
            f"**Could not reach Snowflake.** {st.session_state['load_error']}\n\n"
            "In self-hosted mode the app needs a programmatic access token:\n"
            "`cortex secret run --map snowflake_pat=SNOWFLAKE_PAT -- python app/serve.py`",
            icon=":material/cloud_off:",
        )
        ui.footer()
        return

    ctx: MemberContext | None = st.session_state.get("ctx")

    if ctx is None:
        ui.skeleton_tiles()
        ui.empty_state(
            "Start with a member",
            "Enter a member ID above, or open this app with "
            "<code>?member_id=MBR-…</code> to have the host application set the context.",
        )
        ui.footer()
        return

    if not ctx.found:
        st.warning(
            f"**No member matches {ctx.member_id}.** Nothing is shown rather than a "
            "near match, so you are never looking at the wrong member. "
            "Check the identifier and try again.",
            icon=":material/person_off:",
        )
        ui.footer()
        return

    ui.member_banner(ctx, st.session_state.get("loaded_at"))

    # A session-state-backed selector, NOT st.tabs.
    #
    # st.tabs loses its selection on a rerun, and every form submit triggers one - so
    # asking a question bounced the analyst back to Overview and hid the answer they
    # had just requested. Persisting the view means it survives reruns deterministically.
    views = ["Overview", "Ask", "Reference imaging"]
    current = st.session_state.get("view", views[0])
    chosen = st.segmented_control(
        "View", options=views,
        default=current if current in views else views[0],
        label_visibility="collapsed", key="view_selector",
    )
    view = chosen or current
    st.session_state["view"] = view

    if view == "Overview":
        panel_overview(ctx)
    elif view == "Ask":
        panel_ask(ctx)
    else:
        panel_reference(ctx)

    ui.footer()


main()
