"""
Grounding layer: turn an analyst's question into a CITED answer, or refuse.

Three rules shape this module:

1. EVERY factual answer carries at least one verifiable Citation. `Answer` enforces
   this in its constructor, so an uncited answer is a construction error rather than
   something a reviewer must notice.
2. The copilot NEVER makes or implies a determination. That is enforced three times
   over: in the system prompt, by refusing decision-seeking questions before they
   reach a model, and by scanning the generated text afterwards. A hit BLOCKS the
   answer rather than editing it, because silently rewriting a model's output hides
   the failure.
3. Absent evidence is INDETERMINATE, never a pass or a fail.

Routing is deliberately rule-based rather than model-based. An LLM classifier adds a
second failure mode and a second latency hit for a decision that a handful of
keywords settles reliably.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import snow  # noqa: E402
from app.contracts import (  # noqa: E402
    Answer,
    Citation,
    MemberContext,
    Route,
    SourceKind,
    SuggestedQuestion,
)
from config import app_config as AC  # noqa: E402

# ---------------------------------------------------------------------------
# Guardrail: refuse decision-seeking questions up front
# ---------------------------------------------------------------------------

# Matched against the QUESTION. Catching these before any model call means the
# copilot never generates a determination it then has to suppress.
#
# Note the `(?:\w+\s+){0,3}` gaps: an earlier version required "this be approved" and
# so missed "Will this REQUEST be approved?" - the noun between the determiner and the
# verb defeated it. Analysts write naturally, so the pattern has to tolerate that.
DECISION_SEEKING = re.compile(
    r"\b(should\s+(?:i|we)\s+(?:\w+\s+){0,2}?(?:approve|deny|reject|decline|authorise|authorize)"
    r"|can\s+(?:i|we)\s+(?:\w+\s+){0,2}?(?:approve|deny|reject|decline)"
    r"|(?:approve|deny|reject|decline)\s+(?:this|the|it)\b"
    r"|what\s+(?:should|would)\s+(?:i|we|you)\s+(?:do|decide|recommend)"
    r"|(?:is|does)\s+(?:this|it|the)\s+(?:\w+\s+){0,3}?"
    r"(?:medically\s+necessary|meet\s+criteria|meet\s+medical\s+necessity)"
    r"|do\s+the\s+criteria\s+(?:pass|hold|stack\s+up)"
    r"|recommend\s+(?:a\s+)?(?:decision|outcome|approval|denial)"
    r"|will\s+(?:this|it|the)\s+(?:\w+\s+){0,3}?be\s+(?:approved|denied|rejected|declined)"
    r"|(?:likelihood|probability|chance|odds)\s+of\s+(?:approval|denial|it\s+being)"
    r"|predict\s+(?:the\s+)?(?:decision|outcome|result)"
    r"|how\s+(?:should|would)\s+(?:i|we)\s+decide"
    r"|what(?:'s|\s+is)\s+your\s+(?:recommendation|call|verdict|decision))",
    re.I,
)

REFUSAL_TEXT = (
    "I can't help with that one - the determination is yours to make, and I'm built "
    "to provide evidence rather than conclusions.\n\n"
    "What I can do is lay out the evidence you'd weigh. Ask me things like:\n"
    "- Which criteria are UNMET or INDETERMINATE on this request, and what evidence exists?\n"
    "- What has this member already tried, and when?\n"
    "- What is the regulatory deadline for this request, and which rule sets it?\n"
    "- How were comparable requests decided historically?"
)

# Regulatory questions: route to the corpus rather than the mart.
#
# `denial reason` and the notice/obligation phrasings are here because "When must a
# specific denial reason be given to the provider?" is purely a question about
# CMS-0057-F, yet an earlier version routed it to Cortex Analyst - which dutifully
# queried this member's denial reasons and answered a different question entirely.
REGULATORY_HINTS = re.compile(
    r"\b(regulat|statut|law\b|legal|cfr|cms|rule\b|rules\b|mandate|require[sd]?\b|"
    r"deadline|turnaround|turn-around|tat\b|timeframe|time\s*frame|"
    r"how\s+(?:many|long)\s+(?:days|hours|business\s+days)|"
    r"erisa|medicare\s+advantage\s+rule|medicaid\s+rule|gold[- ]?card|"
    r"exempt|appeal\s+rights|notice\s+requirement|denial\s+reason|"
    r"must\s+(?:be\s+)?(?:given|provided|communicated|notified|disclosed|sent)|"
    r"when\s+must|obligated|obligation|"
    r"compliance|0057|422\.|438\.|2560\.|texas|hb\s*3459)",
    re.I,
)

# Scoping map. The corpus is 67% CMS-0057-F by chunk count, so an unscoped query on
# a Medicaid turnaround question returns CMS-0057-F preamble commentary instead of
# the operative 42 CFR 438 text. Narrowing by topic fixes retrieval precision far
# more cheaply than rebalancing the corpus.
DOC_SCOPE: list[tuple[re.Pattern, list[str]]] = [
    (re.compile(r"\bmedicaid|chip\b", re.I),
     ["cfr_42_438_210", "cfr_42_438_subpartF", "cms_0057_f_pdf"]),
    (re.compile(r"\bmedicare\s+advantage|\bMA\b|part\s*c", re.I),
     ["cfr_42_422_subpartM", "cms_mcm_ch4", "cms_0057_f_pdf"]),
    (re.compile(r"\berisa|commercial|self[- ]funded|dol\b", re.I),
     ["cfr_29_2560_503_1"]),
    (re.compile(r"\btexas|gold[- ]?card|exempt", re.I),
     ["tx_ins_4201"]),
    (re.compile(r"\bexclusion|not\s+covered|benefit\s+design", re.I),
     ["cms_bpm_ch16", "cms_mcm_ch4"]),
    (re.compile(r"\bdenial\s+rate|oig|overturn|inappropriate\s+denial", re.I),
     ["oig_oei_09_18_00260"]),
]


def scope_documents(question: str) -> list[str] | None:
    for pattern, keys in DOC_SCOPE:
        if pattern.search(question):
            return keys
    return None


def classify(question: str) -> Route:
    if DECISION_SEEKING.search(question):
        return Route.REFUSED
    if REGULATORY_HINTS.search(question):
        # Member-specific wording alongside a regulatory term means both are needed:
        # "what is the deadline for THIS member's MRI request" needs the case data
        # and the rule that sets the clock.
        if re.search(r"\b(this\s+member|their|his|her|MBR-|this\s+request|pa-\d)", question, re.I):
            return Route.BOTH
        return Route.REGULATORY
    return Route.MEMBER_DATA


# ---------------------------------------------------------------------------
# Output guard
# ---------------------------------------------------------------------------

_BANNED = [re.compile(p, re.I) for p in AC.BANNED_ANSWER_PATTERNS]


def check_output(text: str) -> str | None:
    """Return a reason to block, or None when the text is acceptable."""
    for pattern in _BANNED:
        hit = pattern.search(text or "")
        if hit:
            return (
                f"The generated answer contained decision-making language "
                f"({hit.group(0)!r}) and was withheld. This copilot reports evidence; "
                f"the determination belongs to the analyst."
            )
    return None


# ---------------------------------------------------------------------------
# Member context
# ---------------------------------------------------------------------------

def load_member_context(member_id: str) -> MemberContext:
    """Everything the header and the suggestion engine need, in one round trip."""
    mid = (member_id or "").strip().upper()
    if not mid:
        return MemberContext(member_id="", found=False)

    elig = snow.run_query(
        f"""SELECT member_name, age_years, gender, line_of_business, enrollment_status,
                   is_eligible_today, has_coverage_gap
            FROM {AC.fqn('VW_MEMBER_ELIGIBILITY')} WHERE member_id = :mid""",
        params={"mid": mid},
    ).df
    if elig.empty:
        return MemberContext(member_id=mid, found=False)
    e = elig.iloc[0]

    pa = snow.run_query(
        f"""SELECT
              (SELECT COUNT(*) FROM {AC.fqn('VW_PA_REQUEST_360')} WHERE member_id = :mid) AS total_pa,
              (SELECT COUNT(*) FROM {AC.fqn('VW_PA_WORKLIST')} WHERE member_id = :mid) AS open_pa,
              (SELECT COUNT(*) FROM {AC.fqn('VW_PA_WORKLIST')}
                WHERE member_id = :mid AND sla_state = 'BREACHED') AS breached,
              (SELECT MIN(days_until_deadline) FROM {AC.fqn('VW_PA_WORKLIST')}
                WHERE member_id = :mid) AS soonest""",
        params={"mid": mid},
    ).df
    p = pa.iloc[0] if not pa.empty else {}

    fin = snow.run_query(
        f"""SELECT deductible_remaining_amt, oop_remaining_amt
            FROM {AC.fqn('VW_MEMBER_FINANCIAL')} WHERE member_id = :mid""",
        params={"mid": mid},
    ).df

    clin = snow.run_query(
        f"""SELECT active_condition_count, active_medication_count
            FROM {AC.fqn('VW_MEMBER_CLINICAL_SUMMARY')} WHERE member_id = :mid""",
        params={"mid": mid},
    ).df

    cats = snow.run_query(
        f"""SELECT DISTINCT service_category FROM {AC.fqn('VW_PA_WORKLIST')}
            WHERE member_id = :mid""",
        params={"mid": mid},
    ).df

    def num(frame: pd.DataFrame, col: str):
        if frame.empty or col not in frame.columns:
            return None
        val = frame.iloc[0][col]
        return None if pd.isna(val) else float(val)

    return MemberContext(
        member_id=mid,
        found=True,
        name=e.get("MEMBER_NAME"),
        age=int(e["AGE_YEARS"]) if pd.notna(e.get("AGE_YEARS")) else None,
        gender=e.get("GENDER"),
        line_of_business=e.get("LINE_OF_BUSINESS"),
        enrollment_status=e.get("ENROLLMENT_STATUS"),
        is_eligible_today=bool(e["IS_ELIGIBLE_TODAY"]) if pd.notna(e.get("IS_ELIGIBLE_TODAY")) else None,
        has_coverage_gap=bool(e["HAS_COVERAGE_GAP"]) if pd.notna(e.get("HAS_COVERAGE_GAP")) else None,
        total_pa_count=int(p.get("TOTAL_PA", 0) or 0),
        open_pa_count=int(p.get("OPEN_PA", 0) or 0),
        breached_pa_count=int(p.get("BREACHED", 0) or 0),
        soonest_deadline_days=None if p.get("SOONEST") is None or pd.isna(p.get("SOONEST"))
        else float(p["SOONEST"]),
        # NOTE: a terminated member legitimately has no current-year accumulator, so
        # None here means "not on record" and must never be rendered as a zero balance.
        deductible_remaining=num(fin, "DEDUCTIBLE_REMAINING_AMT"),
        oop_remaining=num(fin, "OOP_REMAINING_AMT"),
        active_conditions=int(num(clin, "ACTIVE_CONDITION_COUNT") or 0) if not clin.empty else None,
        active_medications=int(num(clin, "ACTIVE_MEDICATION_COUNT") or 0) if not clin.empty else None,
        service_categories=tuple(cats["SERVICE_CATEGORY"].dropna().tolist()) if not cats.empty else (),
    )


# ---------------------------------------------------------------------------
# Suggested questions, derived from this member's actual state
# ---------------------------------------------------------------------------

def suggest_questions(ctx: MemberContext) -> list[SuggestedQuestion]:
    """Offer shortcuts to evidence the analyst is already entitled to.

    Every suggestion is a request for FACTS. None of them nudges toward an outcome,
    and each carries a `reason` naming the state that surfaced it so the analyst can
    see why it appeared rather than trusting a black box.
    """
    out: list[SuggestedQuestion] = []
    if not ctx.found:
        return out

    if ctx.breached_pa_count:
        out.append(SuggestedQuestion(
            f"Which of this member's requests have passed their regulatory deadline?",
            reason=f"{ctx.breached_pa_count} request(s) already show SLA_STATE = BREACHED",
            theme="SLA_COMPLIANCE", priority=1,
        ))
    if ctx.soonest_deadline_days is not None and ctx.soonest_deadline_days < 2:
        out.append(SuggestedQuestion(
            "Which open request is closest to its regulatory deadline, and what rule sets it?",
            reason=f"soonest deadline is {ctx.soonest_deadline_days:.1f} days away",
            theme="SLA_COMPLIANCE", priority=2,
        ))
    if ctx.open_pa_count:
        out.append(SuggestedQuestion(
            "Which criteria are UNMET or INDETERMINATE on this member's open requests?",
            reason=f"{ctx.open_pa_count} open request(s) awaiting review",
            theme="CLINICAL_NECESSITY", priority=3,
        ))
    if ctx.is_eligible_today is False:
        out.append(SuggestedQuestion(
            "What is this member's coverage history and when did it terminate?",
            reason=f"enrollment status is {ctx.enrollment_status}",
            theme="ELIGIBILITY", priority=1,
        ))
    if ctx.has_coverage_gap:
        out.append(SuggestedQuestion(
            "Where are the gaps in this member's coverage history?",
            reason="member has at least one gap in coverage",
            theme="ELIGIBILITY", priority=4,
        ))
    if ctx.line_of_business:
        out.append(SuggestedQuestion(
            f"What are the regulatory turnaround requirements for a {ctx.line_of_business} plan?",
            reason=f"line of business is {ctx.line_of_business}, which selects the applicable rule",
            theme="SLA_COMPLIANCE", priority=5,
        ))
    if "SPECIALTY_DRUG" in ctx.service_categories:
        out.append(SuggestedQuestion(
            "What medications has this member tried, and is step therapy documented?",
            reason="an open request is for a specialty drug",
            theme="CLINICAL_NECESSITY", priority=3,
        ))
    if any(c in ctx.service_categories for c in ("ADVANCED_IMAGING", "ELECTIVE_SURGERY")):
        out.append(SuggestedQuestion(
            "What conservative care and prior imaging are on record for this member?",
            reason="an open request is for imaging or elective surgery",
            theme="CLINICAL_NECESSITY", priority=3,
        ))
    if ctx.total_pa_count > ctx.open_pa_count:
        out.append(SuggestedQuestion(
            "What is this member's prior authorization history and what were the outcomes?",
            reason=f"{ctx.total_pa_count - ctx.open_pa_count} previously decided request(s)",
            theme="PA_HISTORY_PRECEDENT", priority=6,
        ))

    out.sort(key=lambda s: s.priority)
    return out[: AC.MAX_SUGGESTED_QUESTIONS]
