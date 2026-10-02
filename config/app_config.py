"""
Configuration contract for the PA analyst copilot.

This module is the FROZEN interface between the four build tracks. Workers import
from here rather than hardcoding names, so the regulatory pipeline, the radiology
pipeline, the grounding layer and the Streamlit UI cannot drift apart.

Nothing here reaches Snowflake or the network. It is pure declaration.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# Snowflake object names
# ---------------------------------------------------------------------------

DATABASE = "M360MART"
SERVING_SCHEMA = "SERVING"


def fqn(name: str) -> str:
    """Fully-qualify an object inside the serving schema."""
    return f"{DATABASE}.{SERVING_SCHEMA}.{name}"


# The two "folders" the requirements asked for are internal named stages inside
# SERVING, so the corpora sit under the same RBAC boundary as the views the app
# reads. M360_APP_ROLE gets READ on these and nothing else new.
STAGE_REGULATORY = "STG_REGULATORY"
STAGE_RADIOLOGY = "STG_RADIOLOGY"

# Corpus tables
TBL_REGULATORY_CHUNKS = "REGULATORY_CHUNKS"
TBL_REGULATORY_SOURCES = "REGULATORY_SOURCES"
TBL_RADIOLOGY_REFERENCE = "RADIOLOGY_REFERENCE"

# Cortex Search service over the regulatory corpus
SVC_REGULATORY = "SVC_REGULATORY"

# Semantic view backing Cortex Analyst
SEMANTIC_VIEW = "SV_PA_COPILOT"

# ---------------------------------------------------------------------------
# Model selection
# ---------------------------------------------------------------------------
# Verified against this account on 2026-09-01 (see SHOW CORTEX BASE MODELS).
# Cross-region inference is enabled (CORTEX_ENABLED_CROSS_REGION = ANY_REGION),
# which is what makes the Claude models reachable from Azure East US 2 at all.
#
# IMPORTANT: the model id is 'claude-sonnet-4-5', NOT 'claude-4-5-sonnet'.
# The transposed form returns 'unknown model' and cost us real debugging time.
MODEL_PRIMARY = "claude-sonnet-4-5"

# llama3.1-8b is the only GA chat model native to Azure East US 2. Used as a
# fallback so the app degrades instead of failing if cross-region is ever revoked.
MODEL_FALLBACK = "llama3.1-8b"

EMBED_MODEL = "snowflake-arctic-embed-l-v2.0"

# ---------------------------------------------------------------------------
# Governance: the copilot is an INSIGHT PROVIDER, never a decision maker
# ---------------------------------------------------------------------------

NON_DECISION_SYSTEM_RULE = (
    "You are an insight provider for a prior-authorization review analyst. "
    "You surface evidence and cite sources. You NEVER recommend, suggest, imply "
    "or rank an approval, denial, pend or any other determination, and you never "
    "state whether criteria are 'satisfied overall'. If asked what to decide, "
    "explain that the determination is the analyst's to make and restate the "
    "relevant evidence and its sources instead. Report absent evidence as "
    "INDETERMINATE - never treat missing evidence as a failure or a pass."
)

# Phrases that must never appear in a rendered answer. The output guard checks
# these as regexes; a hit BLOCKS the answer rather than editing it, because silently
# rewriting a model's output hides the failure.
#
# These patterns target the copilot ASSERTING a determination. They deliberately do
# NOT match the copilot REPORTING one, because reporting is the job: "the recorded
# denial reason was 'Does not meet medical necessity criteria'" is a fact from
# denial_reason_desc, and an earlier, blunter version of this list blocked exactly
# that - censoring the member's own history. Quoted regulation hits the same trap,
# since the statutes use this vocabulary verbatim.
#
# The distinguishing signal is FRAMING: first person, imperative, or a forward-looking
# claim about this request. Past-tense reported speech is allowed through.
BANNED_ANSWER_PATTERNS: tuple[str, ...] = (
    # explicit recommendation
    r"\bI\s+(?:recommend|advise|suggest)\b",
    r"\bmy\s+(?:recommendation|determination|decision|advice)\b",
    r"\b(?:recommend|recommending)\s+(?:that\s+)?(?:you|we|the\s+analyst)?\s*"
    r"(?:approve|deny|reject|decline|pend)\b",
    r"\brecommend(?:ed|ation)?\s+(?:decision|outcome|approval|denial)\b",
    # imperative or obligation aimed at this request
    r"\b(?:should|ought\s+to|must)\s+(?:be\s+)?(?:approv|den|reject|declin|pend)",
    r"\b(?:approve|deny|reject|decline|pend)\s+(?:this|the)\s+"
    r"(?:request|case|authorization|authorisation|pa\b)",
    r"\bI\s+would\s+(?:approve|deny|reject|decline|pend)\b",
    # prediction or scoring of an outcome
    r"\b(?:likely|unlikely)\s+to\s+be\s+(?:approv|den)",
    r"\bwill\s+(?:probably|likely)\s+be\s+(?:approv|den)",
    r"\bprobability\s+of\s+(?:approval|denial)\b",
    r"\b(?:approval|denial)\s+(?:probability|likelihood|score)\b",
    r"\bpredict(?:ed|s|ion)?\s+(?:that\s+)?(?:this|the\s+request)\b",
    # collapsing three-valued criteria into an overall verdict
    r"\bcriteria\s+(?:are|is)\s+(?:fully\s+)?(?:met|satisfied)\s+overall\b",
    r"\boverall,?\s+(?:the\s+)?criteria\s+(?:are|is)\s+(?:met|satisfied|not\s+met)\b",
    # asserting necessity in the present tense, as opposed to quoting a past reason.
    # A subject noun is required so reported speech ("the recorded denial reason was
    # 'Does not meet medical necessity'") still passes through. 'member' is in the
    # list because the model's natural phrasing is "this MEMBER meets medical
    # necessity", which an earlier request-only pattern let straight through.
    r"\bthis\s+(?:request|case|service|procedure|member|authorization|authorisation)\s+"
    r"(?:is|does|do|meets?|fails?)\s+(?:not\s+)?"
    r"(?:medically\s+necessary|meet\s+medical\s+necessity|medical\s+necessity)",
    r"\b(?:is|are)\s+medically\s+necessary\s+and\s+should\b",
    r"\b(?:member|request|case)\s+(?:clearly\s+)?(?:meets|satisfies)\s+"
    r"(?:the\s+)?(?:medical\s+necessity|criteria)\b",
)

CRITERION_RESULTS = ("MET", "UNMET", "INDETERMINATE")

# ---------------------------------------------------------------------------
# Licence gate for the radiology reference library
# ---------------------------------------------------------------------------
# Deliberately EXCLUDES CC BY-SA. Share-alike may extend obligations to a
# proprietary payer application that displays the image, which is a legal
# judgement rather than an engineering one - so the safe default is to omit it.
# Flip ALLOW_SHARE_ALIKE to True only on advice of counsel.
ALLOW_SHARE_ALIKE = False

LICENCE_ALLOW_EXACT: tuple[str, ...] = (
    "cc0",
    "public domain",
    "pd",
    "cc pd",
    "cc by 2.0",
    "cc by 3.0",
    "cc by 4.0",
)

LICENCE_ALLOW_SHARE_ALIKE: tuple[str, ...] = (
    "cc by-sa 2.0",
    "cc by-sa 3.0",
    "cc by-sa 4.0",
)

# Never permitted, checked as whole tokens:
#   nc / noncommercial -> forbids use in a commercial payer product
#   nd / noderivatives -> forbids the resize and crop a UI performs
LICENCE_DENY_TOKENS: frozenset[str] = frozenset(
    {"nc", "nd", "noncommercial", "noderivatives", "noderivs"}
)


def _normalise_licence(licence: str) -> tuple[str, set[str]]:
    """Lowercase the licence and split it into comparable tokens.

    'CC BY-NC-SA 4.0' -> ('cc by-nc-sa 4.0', {'cc', 'by', 'nc', 'sa', '4.0'})
    Hyphens are treated as separators so the NC in 'BY-NC-SA' is a distinct
    token and cannot be confused with a substring of another word.
    """
    norm = " ".join(licence.strip().lower().split())
    tokens = {t for t in norm.replace("-", " ").replace("/", " ").split() if t}
    return norm, tokens


def licence_allowed(licence: str | None) -> bool:
    """True when a licence string permits redistribution in a commercial app.

    Allow-list based on purpose: an unrecognised licence is REJECTED rather than
    assumed permissive. For a corpus that ships inside a payer product, a missed
    image is cheap and a licence violation is not.
    """
    if not licence:
        return False

    norm, tokens = _normalise_licence(licence)

    if tokens & LICENCE_DENY_TOKENS:
        return False

    allowed = set(LICENCE_ALLOW_EXACT)
    if ALLOW_SHARE_ALIKE:
        allowed |= set(LICENCE_ALLOW_SHARE_ALIKE)
    return norm in allowed


# ---------------------------------------------------------------------------
# Regulatory corpus taxonomy
# ---------------------------------------------------------------------------
# Themes mirror the 8 analyst question themes in docs/PA_analyst_research.md so a
# retrieved chunk can be tied back to the question class it serves.

THEMES: tuple[str, ...] = (
    "ELIGIBILITY",
    "BENEFITS_COVERAGE",
    "CLINICAL_NECESSITY",
    "PA_HISTORY_PRECEDENT",
    "PROVIDER_NETWORK",
    "FINANCIAL",
    "SLA_COMPLIANCE",
    "SAFETY_DUPLICATES",
)


@dataclass(frozen=True)
class RegDoc:
    """One regulatory source document.

    `citation_id` is what an analyst would actually recognise and repeat, e.g.
    '42 CFR 422.568'. It is the anchor of every citation the copilot renders.
    """

    key: str
    title: str
    citation_id: str
    authority: str
    url: str
    fmt: str  # 'ecfr_xml' | 'pdf' | 'fr_json' | 'html'
    themes: tuple[str, ...]
    licence: str = "Public domain (US government work)"
    effective_date: str = ""
    fallback_urls: tuple[str, ...] = field(default_factory=tuple)
    # eCFR needs hierarchy params; carried here so the fetcher stays generic
    ecfr_params: dict = field(default_factory=dict)
    notes: str = ""


# Chunking. Section-level eCFR XML already arrives at a citable granularity, so
# chunks are kept whole where possible and only split when a section is long.
CHUNK_TARGET_CHARS = 6000
CHUNK_OVERLAP_CHARS = 400

# ---------------------------------------------------------------------------
# Radiology reference taxonomy
# ---------------------------------------------------------------------------

BODY_REGIONS: tuple[str, ...] = ("LUMBAR_SPINE", "KNEE", "CHEST", "BRAIN")
MODALITIES: tuple[str, ...] = ("MRI", "CT", "XRAY")
FINDING_CLASSES: tuple[str, ...] = ("NORMAL", "ABNORMAL")

# Maps a PA service category (from the datamart's controlled vocabulary) to the
# body regions worth showing an analyst. Used to make the reference drawer
# contextual instead of a dump of every image.
SERVICE_CATEGORY_TO_REGION: dict[str, tuple[str, ...]] = {
    "ADVANCED_IMAGING": ("LUMBAR_SPINE", "KNEE", "CHEST", "BRAIN"),
    "ELECTIVE_SURGERY": ("LUMBAR_SPINE", "KNEE"),
    "PAIN_MANAGEMENT": ("LUMBAR_SPINE",),
    "REHABILITATION": ("LUMBAR_SPINE", "KNEE"),
    "INPATIENT_ADMISSION": ("CHEST", "BRAIN"),
    "SLEEP_STUDY": ("CHEST",),
    "RADIATION_THERAPY": ("CHEST", "BRAIN"),
    "DME": ("LUMBAR_SPINE", "KNEE"),
}

RADIOLOGY_DISCLAIMER = (
    "Reference anatomy for orientation only. These are public teaching images, "
    "NOT this member's studies, and they are not a basis for any determination."
)

# ---------------------------------------------------------------------------
# App behaviour
# ---------------------------------------------------------------------------

APP_TITLE = "PA Insight Copilot"
MAX_SUGGESTED_QUESTIONS = 4
QUERY_PARAM_MEMBER = "member_id"
ANSWER_ROW_LIMIT = 200
