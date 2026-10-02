"""
FROZEN interface between the grounding layer and the UI.

The UI imports these types and never constructs an answer itself; the grounding
layer produces them and never renders. Both tracks can therefore be built in
parallel against this file.

The central rule is encoded in the types: an `Answer` cannot exist without at
least one `Citation`. `Answer.__post_init__` enforces it, so an uncited answer is
a construction error rather than something a reviewer has to spot in the UI.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Route(str, Enum):
    """Which grounding surface answered the question."""

    MEMBER_DATA = "MEMBER_DATA"      # Cortex Analyst over the semantic view
    REGULATORY = "REGULATORY"        # Cortex Search over the regulatory corpus
    BOTH = "BOTH"                    # blended: data + regulation
    REFUSED = "REFUSED"              # asked for a decision, or off-scope
    UNANSWERABLE = "UNANSWERABLE"    # in scope but the mart has no such data


class SourceKind(str, Enum):
    SNOWFLAKE_QUERY = "SNOWFLAKE_QUERY"
    REGULATORY_DOC = "REGULATORY_DOC"
    RADIOLOGY_IMAGE = "RADIOLOGY_IMAGE"


@dataclass(frozen=True)
class Citation:
    """One verifiable source behind part of an answer.

    Every field here exists so an analyst can independently re-derive the claim.
    For a query that means the actual SQL and the views it touched; for a
    regulation it means the citation an analyst would look up plus a live URL.
    """

    kind: SourceKind
    label: str                      # what the analyst sees, e.g. '42 CFR 422.568'
    detail: str = ""                # section heading, or a one-line query summary

    # SNOWFLAKE_QUERY
    sql: str | None = None
    objects: tuple[str, ...] = field(default_factory=tuple)
    row_count: int | None = None
    elapsed_ms: int | None = None
    query_id: str | None = None
    verified_query: str | None = None   # VQR name when Cortex Analyst matched one

    # REGULATORY_DOC
    citation_id: str | None = None
    authority: str | None = None
    source_url: str | None = None
    effective_date: str | None = None
    licence: str | None = None
    chunk_id: str | None = None
    relevance: float | None = None
    excerpt: str | None = None

    # RADIOLOGY_IMAGE
    attribution: str | None = None

    def is_verifiable(self) -> bool:
        """A citation must give the analyst something to actually check."""
        if self.kind is SourceKind.SNOWFLAKE_QUERY:
            return bool(self.sql and self.objects)
        if self.kind is SourceKind.REGULATORY_DOC:
            return bool(self.citation_id and self.source_url)
        if self.kind is SourceKind.RADIOLOGY_IMAGE:
            return bool(self.source_url and self.licence and self.attribution)
        return False


@dataclass
class Answer:
    """A rendered answer plus everything needed to verify and audit it."""

    question: str
    route: Route
    text: str
    citations: list[Citation] = field(default_factory=list)
    data: Any = None                       # optional DataFrame for tabular answers
    warnings: list[str] = field(default_factory=list)
    blocked_reason: str | None = None      # set when the output guard refused
    model: str | None = None
    elapsed_ms: int | None = None

    def __post_init__(self) -> None:
        # REFUSED and UNANSWERABLE legitimately have nothing to cite: they make no
        # factual claim. Every other route must carry at least one citation.
        needs_citation = self.route in (Route.MEMBER_DATA, Route.REGULATORY, Route.BOTH)
        if needs_citation and not self.citations and self.blocked_reason is None:
            raise ValueError(
                f"Answer for route {self.route.value} has no citations. Every factual "
                "answer this copilot renders must be independently verifiable."
            )

    @property
    def is_renderable(self) -> bool:
        return self.blocked_reason is None

    def unverifiable_citations(self) -> list[Citation]:
        return [c for c in self.citations if not c.is_verifiable()]


@dataclass(frozen=True)
class SuggestedQuestion:
    """A context-derived prompt offered to the analyst.

    `reason` explains why it surfaced, which keeps the feature honest: a
    suggestion is a shortcut to evidence the analyst was already entitled to,
    never a nudge toward an outcome.
    """

    text: str
    reason: str
    theme: str
    priority: int = 50   # lower sorts first


@dataclass(frozen=True)
class MemberContext:
    """Everything the UI needs to render the member header and drive suggestions."""

    member_id: str
    found: bool
    name: str | None = None
    age: int | None = None
    gender: str | None = None
    line_of_business: str | None = None
    enrollment_status: str | None = None
    is_eligible_today: bool | None = None
    has_coverage_gap: bool | None = None
    open_pa_count: int = 0
    total_pa_count: int = 0
    breached_pa_count: int = 0
    soonest_deadline_days: float | None = None
    deductible_remaining: float | None = None
    oop_remaining: float | None = None
    active_conditions: int | None = None
    active_medications: int | None = None
    service_categories: tuple[str, ...] = field(default_factory=tuple)
