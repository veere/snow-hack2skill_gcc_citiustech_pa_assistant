"""
Build fct_memberEvents from Synthea encounters and synthetic member interactions.

Generates member-facing contact history: calls, portal logins, letters, faxes,
appeals and grievances. Events are grounded in real Synthea encounters so the
timeline is consistent with the clinical record.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from common import dates, determinism as det  # noqa: E402
from common import identity
from common.frames import write_table  # noqa: E402
from config import schemas as S  # noqa: E402

SYNTHEA = Path(S.load_config()["_repo_root"]) / S.load_config()["paths"]["cache"] / "synthea"

TABLE = "fct_memberEvents"
RNG_BASE = TABLE


def _load_synthea(name: str) -> pd.DataFrame:
    return pd.read_csv(SYNTHEA / f"{name}.csv", dtype=str)


def _member_id(patient_uuid: str) -> str:
    return identity.member_id(patient_uuid)


# ---------------------------------------------------------------------------
# Event categories, channels, dispositions
# ---------------------------------------------------------------------------

EVENT_TEMPLATES: list[dict] = [
    {
        "category": "CALL_INBOUND",
        "channel": "PHONE",
        "direction": "INBOUND",
        "topics": ["Benefits inquiry", "Claim status", "PA status", "Eligibility question",
                   "Provider search", "ID card request", "Billing question"],
        "queues": ["MEMBER_SERVICES", "BENEFITS", "CLAIMS", "PA_INQUIRY"],
        "duration_range": (60, 900),
    },
    {
        "category": "CALL_OUTBOUND",
        "channel": "PHONE",
        "direction": "OUTBOUND",
        "topics": ["Care coordination", "Appointment reminder", "PA follow-up",
                   "Premium reminder", "Wellness outreach"],
        "queues": ["CARE_MGMT", "PA_TEAM", "MEMBER_SERVICES"],
        "duration_range": (30, 600),
    },
    {
        "category": "PORTAL_LOGIN",
        "channel": "PORTAL",
        "direction": "INBOUND",
        "topics": ["View claims", "View benefits", "Check PA status", "Update profile",
                   "Download EOB", "Message provider"],
        "queues": [None],
        "duration_range": (10, 300),
    },
    {
        "category": "SECURE_MESSAGE",
        "channel": "PORTAL",
        "direction": "INBOUND",
        "topics": ["Claim question", "PA inquiry", "Prescription question",
                   "Referral request", "Document upload"],
        "queues": ["PORTAL_SUPPORT", "PA_TEAM", "CLAIMS"],
        "duration_range": None,
    },
    {
        "category": "LETTER_SENT",
        "channel": "MAIL",
        "direction": "OUTBOUND",
        "topics": ["EOB", "PA determination notice", "Pend letter", "Appeal rights notice",
                   "Coverage change notice", "Welcome packet", "Renewal notice"],
        "queues": ["CORRESPONDENCE"],
        "duration_range": None,
    },
    {
        "category": "FAX_RECEIVED",
        "channel": "FAX",
        "direction": "INBOUND",
        "topics": ["Clinical records", "PA request", "Lab results", "Operative report",
                   "Letter of medical necessity", "Appeal documentation"],
        "queues": ["INTAKE", "PA_TEAM", "CLAIMS"],
        "duration_range": None,
    },
    {
        "category": "SMS_SENT",
        "channel": "SMS",
        "direction": "OUTBOUND",
        "topics": ["Appointment reminder", "PA status update", "Rx refill reminder"],
        "queues": [None],
        "duration_range": None,
    },
    {
        "category": "EMAIL_SENT",
        "channel": "EMAIL",
        "direction": "OUTBOUND",
        "topics": ["PA determination", "Welcome email", "EOB available", "Portal alert"],
        "queues": ["CORRESPONDENCE"],
        "duration_range": None,
    },
    {
        "category": "APPEAL_FILED",
        "channel": "MAIL",
        "direction": "INBOUND",
        "topics": ["Level 1 appeal", "Level 2 appeal", "External review request"],
        "queues": ["APPEALS"],
        "duration_range": None,
    },
    {
        "category": "GRIEVANCE_FILED",
        "channel": "PHONE",
        "direction": "INBOUND",
        "topics": ["Service complaint", "Access complaint", "Quality of care",
                   "Billing dispute", "Provider complaint"],
        "queues": ["GRIEVANCES"],
        "duration_range": (120, 1200),
    },
    {
        "category": "CASE_MGMT_OUTREACH",
        "channel": "PHONE",
        "direction": "OUTBOUND",
        "topics": ["Chronic care check-in", "Discharge follow-up", "Transition of care",
                   "Medication reconciliation", "High-risk assessment"],
        "queues": ["CARE_MGMT", "COMPLEX_CARE"],
        "duration_range": (180, 1800),
    },
]

DISPOSITION_CODES = [
    ("RES", "Resolved on contact"),
    ("ESC", "Escalated to supervisor"),
    ("TRF", "Transferred to specialist queue"),
    ("CB", "Callback scheduled"),
    ("INF", "Information provided"),
    ("ACK", "Acknowledged - no action needed"),
    ("DOC", "Documentation received"),
    ("DEN", "Request denied"),
    ("PND", "Pending further review"),
]


def _generate_encounter_events(encounters: pd.DataFrame, rng: np.random.Generator) -> list[dict]:
    """Generate events correlated with encounter dates."""
    rows = []
    agent_pool = [f"AGT{i:04d}" for i in range(100)]

    for _, enc in encounters.iterrows():
        member_id = _member_id(enc["PATIENT"])
        enc_date = dates.shift_scalar(enc["START"])
        if enc_date is None:
            continue
        # Ensure tz-naive for consistent typing with standalone events
        if hasattr(enc_date, 'tzinfo') and enc_date.tzinfo:
            enc_date = enc_date.tz_localize(None)

        n_events = int(rng.integers(0, 5))
        for j in range(n_events):
            tmpl = EVENT_TEMPLATES[int(rng.integers(0, len(EVENT_TEMPLATES)))]
            offset_days = int(rng.integers(-5, 30))
            event_dt = enc_date + pd.Timedelta(days=offset_days, hours=int(rng.integers(8, 18)),
                                                minutes=int(rng.integers(0, 60)))

            event_id = det.stable_id("EVT", enc["PATIENT"], enc["Id"], str(j), width=12)
            topic = rng.choice(tmpl["topics"])
            queue = rng.choice(tmpl["queues"])
            agent = rng.choice(agent_pool) if queue else None

            dur_range = tmpl.get("duration_range")
            duration = int(rng.integers(dur_range[0], dur_range[1])) if dur_range else None

            disp = DISPOSITION_CODES[int(rng.integers(0, len(DISPOSITION_CODES)))]
            resolved = disp[0] in ("RES", "INF", "ACK", "DOC")
            escalated = disp[0] in ("ESC", "TRF")
            sentiment = round(float(rng.normal(0.2, 0.4)), 3)
            sentiment = max(-1.0, min(1.0, sentiment))

            rows.append({
                "event_id": event_id,
                "member_id": member_id,
                "event_datetime_utc": event_dt,
                "event_category": tmpl["category"],
                "event_subtype": topic.replace(" ", "_").upper(),
                "channel": tmpl["channel"],
                "direction": tmpl["direction"],
                "related_pa_id": None,
                "related_claim_id": None,
                "handled_by_queue": queue,
                "agent_id": agent,
                "duration_seconds": duration,
                "disposition_code": disp[0],
                "disposition_desc": disp[1],
                "topic": topic,
                "resolved_flag": resolved,
                "escalated_flag": escalated,
                "sentiment_score": sentiment,
                "notes_text": f"Contact regarding {topic.lower()} for encounter",
            })

    return rows


def _generate_standalone_events(patients: pd.DataFrame, rng: np.random.Generator) -> list[dict]:
    """Generate events not tied to a specific encounter."""
    rows = []
    agent_pool = [f"AGT{i:04d}" for i in range(100)]

    for _, pat in patients.iterrows():
        member_id = _member_id(pat["Id"])
        n_standalone = int(rng.integers(0, 8))

        for j in range(n_standalone):
            tmpl = EVENT_TEMPLATES[int(rng.integers(0, len(EVENT_TEMPLATES)))]
            days_back = int(rng.integers(0, 365 * 3))
            base_date = dates.reference_date()
            event_dt = pd.Timestamp(base_date) - pd.Timedelta(
                days=days_back,
                hours=int(rng.integers(0, 10)),
                minutes=int(rng.integers(0, 60)),
            )
            event_dt = event_dt.replace(hour=int(rng.integers(8, 18)))

            event_id = det.stable_id("EVT", pat["Id"], "SA", str(j), width=12)
            topic = rng.choice(tmpl["topics"])
            queue = rng.choice(tmpl["queues"])
            agent = rng.choice(agent_pool) if queue else None

            dur_range = tmpl.get("duration_range")
            duration = int(rng.integers(dur_range[0], dur_range[1])) if dur_range else None

            disp = DISPOSITION_CODES[int(rng.integers(0, len(DISPOSITION_CODES)))]
            resolved = disp[0] in ("RES", "INF", "ACK", "DOC")
            escalated = disp[0] in ("ESC", "TRF")
            sentiment = round(float(rng.normal(0.1, 0.5)), 3)
            sentiment = max(-1.0, min(1.0, sentiment))

            rows.append({
                "event_id": event_id,
                "member_id": member_id,
                "event_datetime_utc": event_dt,
                "event_category": tmpl["category"],
                "event_subtype": topic.replace(" ", "_").upper(),
                "channel": tmpl["channel"],
                "direction": tmpl["direction"],
                "related_pa_id": None,
                "related_claim_id": None,
                "handled_by_queue": queue,
                "agent_id": agent,
                "duration_seconds": duration,
                "disposition_code": disp[0],
                "disposition_desc": disp[1],
                "topic": topic,
                "resolved_flag": resolved,
                "escalated_flag": escalated,
                "sentiment_score": sentiment,
                "notes_text": f"Member contact: {topic.lower()}",
            })

    return rows


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    print("=" * 78)
    print("Building fct_memberEvents")
    print("=" * 78)
    print(f"  date anchor: {dates.describe()}")

    rng = det.rng(RNG_BASE)

    print("\n  [1/2] Encounter-correlated events ...")
    encounters = _load_synthea("encounters")
    encounter_events = _generate_encounter_events(encounters, det.rng(RNG_BASE, "encounter"))
    print(f"         {len(encounter_events):,d} rows")

    print("  [2/2] Standalone member events ...")
    patients = _load_synthea("patients")
    standalone_events = _generate_standalone_events(patients, det.rng(RNG_BASE, "standalone"))
    print(f"         {len(standalone_events):,d} rows")

    all_events = encounter_events + standalone_events
    df = pd.DataFrame(all_events)
    print(f"\n  Total events: {len(df):,d}")

    write_table(df, "bronze", TABLE, source_system="SYNTHETIC")

    print("\nfct_memberEvents complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
