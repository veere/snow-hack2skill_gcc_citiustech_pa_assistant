"""
Determinism helpers.

The whole pipeline must reproduce byte-identical business data on a rerun with
the same seed. That is asserted by the end-to-end determinism test, which
re-runs generation and compares `record_hash` values.

Two rules make this work:

1. Never call an unseeded RNG and never use wall-clock time or `id()` in any
   business value. Derive every generator from `rng(...)` below, which mixes the
   master seed with a stable string path.
2. `ingested_at_utc` is the ONLY column allowed to vary between runs, and it is
   therefore excluded from `record_hash`.

Deriving a child seed from a name rather than sharing one global RNG also means
generator scripts can run in any order, or in parallel, and still produce the
same output - which is what allows the bronze tracks to be built concurrently.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from config import schemas as S

# Columns excluded from record_hash because they are not business data.
HASH_EXCLUDE: frozenset[str] = frozenset({"record_hash", "ingested_at_utc"})


def master_seed() -> int:
    return int(S.load_config()["determinism"]["master_seed"])


def child_seed(*parts: str | int) -> int:
    """Derive a stable 63-bit seed from the master seed and a name path.

    `child_seed("fct_memberPA", "urgency")` always returns the same value, so a
    generator's random stream is reproducible regardless of what else ran.
    """
    payload = "|".join([str(master_seed()), *(str(p) for p in parts)])
    digest = hashlib.sha256(payload.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") & ((1 << 63) - 1)


def rng(*parts: str | int) -> np.random.Generator:
    """A NumPy Generator seeded from the named path. Always use this."""
    return np.random.default_rng(child_seed(*parts))


def _normalize(value: object) -> str:
    """Render a value to a canonical string for hashing.

    Normalisation matters more than it looks: NaN vs None, 1 vs 1.0, and
    timezone-aware vs naive timestamps would otherwise produce different hashes
    for identical business content and cause false determinism failures.
    """
    if value is None:
        return "\x00"
    if isinstance(value, float):
        if np.isnan(value):
            return "\x00"
        # Render integral floats without a trailing .0 so 1 and 1.0 agree.
        if value == int(value) and abs(value) < 1e15:
            return str(int(value))
        return repr(round(value, 6))
    if isinstance(value, (np.integer,)):
        return str(int(value))
    if isinstance(value, (np.floating,)):
        return _normalize(float(value))
    if isinstance(value, (np.bool_, bool)):
        return "1" if bool(value) else "0"
    if isinstance(value, pd.Timestamp):
        if value is pd.NaT:
            return "\x00"
        return value.tz_localize(None).isoformat() if value.tzinfo else value.isoformat()
    if value is pd.NaT:
        return "\x00"
    if isinstance(value, (list, tuple, np.ndarray)):
        return "[" + ",".join(_normalize(v) for v in value) + "]"
    return str(value)


def row_hashes(df: pd.DataFrame, *, exclude: frozenset[str] = HASH_EXCLUDE) -> pd.Series:
    """SHA-256 per row over business columns, in declared column order."""
    cols = [c for c in df.columns if c not in exclude]
    if not cols:
        raise ValueError("no business columns available to hash")

    parts = [df[c].map(_normalize) for c in cols]
    joined = parts[0].astype(str)
    for p in parts[1:]:
        joined = joined + "\x1f" + p.astype(str)

    return joined.map(lambda s: hashlib.sha256(s.encode("utf-8")).hexdigest())


def frame_fingerprint(df: pd.DataFrame, *, exclude: frozenset[str] = HASH_EXCLUDE) -> str:
    """One hash for a whole table - order-independent.

    Order independence is deliberate: a generator is allowed to emit rows in a
    different order between runs as long as the row content is identical.
    """
    hashes = row_hashes(df, exclude=exclude)
    combined = hashlib.sha256()
    for h in sorted(hashes.tolist()):
        combined.update(h.encode("ascii"))
    return combined.hexdigest()


def ingested_at() -> datetime:
    """Wall-clock stamp for the audit trailer. Excluded from all hashes."""
    return datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)


def stable_id(prefix: str, *parts: str | int, width: int = 6) -> str:
    """A deterministic surrogate key derived from business content.

    Content-derived rather than sequential, so a key does not change just because
    an upstream row count changed.

    COLLISIONS: this is a truncated hash, so it is NOT collision-free. At
    `width=6` there are only 10**6 values and the birthday bound gives roughly a
    50% chance of at least one collision across just 1,200 keys. For any column
    used as a primary key, use `unique_stable_ids` instead, which detects and
    deterministically resolves collisions.
    """
    payload = "|".join(str(p) for p in parts)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    numeric = int(digest[:16], 16) % (10 ** width)
    return f"{prefix}{numeric:0{width}d}"


def unique_stable_ids(
    prefix: str,
    keys: "pd.Series | list",
    *,
    width: int = 8,
) -> "pd.Series":
    """Deterministic, GUARANTEED-UNIQUE surrogate keys for a set of business keys.

    Generates `stable_id` for each key, then resolves any collision by rehashing
    with an incrementing disambiguator until the value is free. Because the input
    order is stable and the disambiguation is deterministic, the same input always
    produces the same output - so uniqueness does not cost reproducibility.

    Use this for every primary key. Snowflake does not enforce PRIMARY KEY
    constraints, so a duplicate surrogate key will load without complaint and then
    silently fan out every downstream join.
    """
    import pandas as pd

    series = keys if isinstance(keys, pd.Series) else pd.Series(list(keys))
    seen: set[str] = set()
    out: list[str] = []

    for key in series.astype(str).tolist():
        candidate = stable_id(prefix, key, width=width)
        if candidate in seen:
            attempt = 1
            while candidate in seen:
                candidate = stable_id(prefix, key, f"#{attempt}", width=width)
                attempt += 1
        seen.add(candidate)
        out.append(candidate)

    return pd.Series(out, index=series.index)


def luhn_check_digit(partial: str) -> int:
    """Check digit for a 9-digit NPI body, per the NPI Luhn variant.

    Real NPIs are Luhn-valid with the constant 80840 prefix applied. Generating
    valid check digits means synthetic NPIs pass the same format validation a
    real system would apply, so downstream code cannot accidentally depend on
    them being malformed.
    """
    payload = "80840" + partial
    total = 0
    for idx, ch in enumerate(reversed(payload)):
        digit = int(ch)
        if idx % 2 == 0:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return (10 - (total % 10)) % 10


def synthetic_npi(*parts: str | int) -> str:
    """A deterministic, Luhn-valid 10-digit NPI derived from the given parts."""
    payload = "|".join(str(p) for p in parts)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    body = f"{int(digest[:12], 16) % 10**9:09d}"
    return body + str(luhn_check_digit(body))
