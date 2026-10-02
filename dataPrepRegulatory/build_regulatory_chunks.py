"""
Parse the regulatory corpus into citation-anchored chunks.

The design goal is CITATION QUALITY, not retrieval cleverness. Cortex Search
returns only the columns declared on the service and invents no references, so
whatever citation an analyst eventually reads has to be constructed here.

Per format:
  * ecfr_xml - ONE CHUNK PER SECTION. eCFR marks sections as
    <DIV8 N="438.210" TYPE="SECTION" hierarchy_metadata='{"citation":"42 CFR 438.210"}'>
    so the authoritative citation string is carried in the source itself and does
    not have to be reassembled. `42 CFR 422.568` is precisely what an analyst
    repeats, which is why section granularity beats fixed-size windows here.
  * pdf - page-anchored chunks via pypdf, because the page number is part of a
    usable citation for a 600-page Federal Register rule.
  * html - section-anchored on the Texas statute's `Sec. 4201.xxx` headings.
  * fr_json - Federal Register metadata; also enriches the CMS-0057-F PDF rows
    with authoritative agency and effective-date values.

Run:
    python dataPrepRegulatory/build_regulatory_chunks.py
"""

from __future__ import annotations

import html as html_mod
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common import determinism as det  # noqa: E402
from config import app_config as AC  # noqa: E402
from config.regulatory_sources import REG_DOCS, by_key  # noqa: E402

CACHE = Path(__file__).resolve().parent.parent / ".cache" / "regulatory"
OUT_DIR = Path(__file__).resolve().parent.parent / "dataFilesServing"
MANIFEST = CACHE / "manifest.json"

CHUNK_COLUMNS = [
    "chunk_id", "doc_key", "citation_id", "section_label", "heading",
    "chunk_text", "char_len", "page_no", "themes", "authority",
    "source_url", "effective_date", "licence", "ingested_at_utc",
]

SOURCE_COLUMNS = [
    "doc_key", "title", "citation_id", "authority", "source_url", "fmt",
    "themes", "licence", "effective_date", "size_bytes", "sha256",
    "chunk_count", "fetched_at", "notes",
]


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _clean(text: str) -> str:
    """Collapse whitespace and unescape entities, preserving paragraph markers."""
    text = html_mod.unescape(text)
    # eCFR uses a non-breaking space and an en-dash liberally; normalise both so
    # a search for "7 days" is not defeated by an invisible character.
    text = text.replace("\xa0", " ").replace("\u2013", "-").replace("\u2014", "-")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _split_long(text: str, label: str) -> list[tuple[str, str]]:
    """Split only when a unit exceeds the target, keeping the citation label.

    Returns (label, text) pairs. A split section gets ' (part N)' appended to its
    label so a citation still points at the right section and makes clear it is an
    excerpt rather than the whole thing.
    """
    if len(text) <= AC.CHUNK_TARGET_CHARS:
        return [(label, text)]

    out: list[tuple[str, str]] = []
    step = AC.CHUNK_TARGET_CHARS - AC.CHUNK_OVERLAP_CHARS
    paras = text.split("\n")
    buf: list[str] = []
    size = 0
    part = 1
    for para in paras:
        if size + len(para) > AC.CHUNK_TARGET_CHARS and buf:
            out.append((f"{label} (part {part})", "\n".join(buf).strip()))
            part += 1
            # carry a little context so a paragraph split mid-idea is still readable
            tail = "\n".join(buf)[-AC.CHUNK_OVERLAP_CHARS:]
            buf = [tail, para]
            size = len(tail) + len(para)
        else:
            buf.append(para)
            size += len(para)
    if buf:
        out.append((f"{label} (part {part})", "\n".join(buf).strip()))
    return [(lbl, txt) for lbl, txt in out if len(txt) > 200] or [(label, text[:AC.CHUNK_TARGET_CHARS])]


# ---------------------------------------------------------------------------
# format parsers
# ---------------------------------------------------------------------------

def parse_ecfr(doc, path: Path) -> list[dict]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    rows: list[dict] = []

    # Grab each DIV8 SECTION with its attributes and body.
    pattern = re.compile(
        r'<DIV8\s+N="(?P<num>[^"]+)"\s+TYPE="SECTION"(?P<attrs>[^>]*)>(?P<body>.*?)</DIV8>',
        re.S,
    )
    for m in pattern.finditer(raw):
        num = m.group("num")
        attrs = m.group("attrs")
        body = m.group("body")

        # Prefer the citation the source itself asserts.
        cite = None
        meta = re.search(r'hierarchy_metadata="(.*?)"', attrs, re.S)
        if meta:
            try:
                cite = json.loads(html_mod.unescape(meta.group(1))).get("citation")
            except Exception:  # noqa: BLE001
                cite = None
        citation_id = cite or f"{doc.citation_id} {num}"

        head_m = re.search(r"<HEAD>(.*?)</HEAD>", body, re.S)
        heading = _clean(re.sub(r"<[^>]+>", " ", head_m.group(1))) if head_m else num
        heading = re.sub(r"^[^\w]*", "", heading)

        # Keep paragraph boundaries: each <P> becomes its own line so the (a)(1)(i)
        # structure a regulation depends on survives into the chunk text.
        paras = re.findall(r"<P[^>]*>(.*?)</P>", body, re.S)
        text = "\n".join(_clean(re.sub(r"<[^>]+>", "", p)) for p in paras)
        text = _clean(text)
        if len(text) < 200:
            continue

        for label, piece in _split_long(text, citation_id):
            rows.append({
                "citation_id": citation_id,
                "section_label": label,
                "heading": heading,
                "chunk_text": f"{heading}\n\n{piece}",
                "page_no": None,
            })
    return rows


def parse_pdf(doc, path: Path) -> list[dict]:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    n_pages = len(reader.pages)
    rows: list[dict] = []

    # Accumulate pages until the target size is reached, then emit with a page
    # RANGE. Single-page chunks fragment sentences across the boundary; whole-doc
    # chunks lose the page number that makes the citation checkable.
    buf: list[str] = []
    first_page = 1
    size = 0

    def flush(last_page: int) -> None:
        nonlocal buf, size, first_page
        text = _clean("\n".join(buf))
        if len(text) >= 300:
            label = (
                f"{doc.citation_id}, p. {first_page}"
                if first_page == last_page
                else f"{doc.citation_id}, pp. {first_page}-{last_page}"
            )
            # Accumulating whole pages overshoots the target by up to a page, so
            # split the flushed text rather than emitting an oversized chunk.
            for piece_label, piece in _split_long(text, label):
                rows.append({
                    "citation_id": doc.citation_id,
                    "section_label": piece_label,
                    "heading": doc.title[:300],
                    "chunk_text": piece,
                    "page_no": first_page,
                })
        buf = []
        size = 0

    for idx in range(n_pages):
        try:
            page_text = reader.pages[idx].extract_text() or ""
        except Exception:  # noqa: BLE001
            page_text = ""
        page_text = _clean(page_text)
        if not page_text:
            continue
        if not buf:
            first_page = idx + 1
        buf.append(page_text)
        size += len(page_text)
        if size >= AC.CHUNK_TARGET_CHARS:
            flush(idx + 1)
    if buf:
        flush(n_pages)
    return rows


def parse_html(doc, path: Path) -> list[dict]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    # Drop script/style before stripping tags, else their contents become "text".
    raw = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", raw, flags=re.S | re.I)
    text = _clean(re.sub(r"<[^>]+>", "\n", raw))

    # Texas statutes are organised as "Sec. 4201.651. HEADING. body"
    parts = re.split(r"\n(?=Sec\.\s*\d+\.\d+)", text)
    rows: list[dict] = []
    for part in parts:
        part = part.strip()
        if len(part) < 250:
            continue
        m = re.match(r"(Sec\.\s*(\d+\.\d+)\.?)\s*(.{0,140}?)\.\s", part)
        if m:
            section_no = m.group(2)
            heading = _clean(m.group(3))
            citation_id = f"Tex. Ins. Code {section_no}"
        else:
            section_no = ""
            heading = doc.title[:200]
            citation_id = doc.citation_id
        for label, piece in _split_long(part, citation_id):
            rows.append({
                "citation_id": citation_id,
                "section_label": label,
                "heading": heading or doc.title[:200],
                "chunk_text": piece,
                "page_no": None,
            })
    return rows


def parse_fr_json(doc, path: Path) -> tuple[list[dict], dict]:
    """Return (chunks, enrichment) - enrichment feeds the CMS-0057-F PDF rows."""
    data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    agencies = ", ".join(a.get("name", "") for a in data.get("agencies", []) if a.get("name"))
    cfr_refs = ", ".join(
        f"{r.get('title')} CFR {r.get('part')}" for r in (data.get("cfr_references") or [])
    )
    fields = [
        ("Title", data.get("title")),
        ("Document number", data.get("document_number")),
        ("Publication date", data.get("publication_date")),
        ("Effective date", data.get("effective_on")),
        ("Agencies", agencies),
        ("CFR references", cfr_refs),
        ("Type", data.get("type")),
        ("Abstract", data.get("abstract")),
    ]
    text = "\n".join(f"{k}: {v}" for k, v in fields if v)
    chunks = [{
        "citation_id": doc.citation_id,
        "section_label": f"{doc.citation_id} (metadata)",
        "heading": "Federal Register document metadata",
        "chunk_text": _clean(text),
        "page_no": None,
    }]
    enrichment = {
        "effective_date": data.get("effective_on") or data.get("publication_date") or "",
        "authority": agencies or doc.authority,
    }
    return chunks, enrichment


# ---------------------------------------------------------------------------
# driver
# ---------------------------------------------------------------------------

def main() -> int:
    if not MANIFEST.exists():
        print("No manifest. Run dataPrepRegulatory/fetch_regulatory.py first.")
        return 1
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    print("=" * 78)
    print("REGULATORY CHUNKING")
    print("=" * 78)

    # Parse the FR metadata first so it can enrich the matching PDF.
    enrichment: dict[str, dict] = {}
    for doc in REG_DOCS:
        if doc.fmt == "fr_json" and doc.key in manifest:
            _, enr = parse_fr_json(doc, CACHE / manifest[doc.key]["filename"])
            enrichment["cms_0057_f_pdf"] = enr

    all_rows: list[dict] = []
    source_rows: list[dict] = []
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    for doc in REG_DOCS:
        entry = manifest.get(doc.key)
        if not entry:
            print(f"  SKIP {doc.key} - not in manifest")
            continue
        path = CACHE / entry["filename"]

        if doc.fmt == "ecfr_xml":
            parsed = parse_ecfr(doc, path)
        elif doc.fmt == "pdf":
            parsed = parse_pdf(doc, path)
        elif doc.fmt == "html":
            parsed = parse_html(doc, path)
        elif doc.fmt == "fr_json":
            parsed, _ = parse_fr_json(doc, path)
        else:
            print(f"  SKIP {doc.key} - unknown fmt {doc.fmt}")
            continue

        enr = enrichment.get(doc.key, {})
        effective = enr.get("effective_date") or doc.effective_date
        authority = enr.get("authority") or doc.authority

        for row in parsed:
            row.update({
                "doc_key": doc.key,
                "themes": "|".join(doc.themes),
                "authority": authority,
                "source_url": doc.url,
                "effective_date": effective,
                "licence": doc.licence,
                "char_len": len(row["chunk_text"]),
                "ingested_at_utc": now,
            })
        all_rows.extend(parsed)

        flag = "" if parsed else "   <-- PRODUCED NO CHUNKS"
        print(f"  {doc.key:26s} {len(parsed):5,d} chunks  {doc.citation_id}{flag}")

        source_rows.append({
            "doc_key": doc.key,
            "title": doc.title,
            "citation_id": doc.citation_id,
            "authority": authority,
            "source_url": doc.url,
            "fmt": doc.fmt,
            "themes": "|".join(doc.themes),
            "licence": doc.licence,
            "effective_date": effective,
            "size_bytes": entry["size_bytes"],
            "sha256": entry["sha256"],
            "chunk_count": len(parsed),
            "fetched_at": entry["fetched_at"],
            "notes": doc.notes,
        })

    if not all_rows:
        print("No chunks produced at all - aborting rather than writing an empty corpus.")
        return 1

    chunks = pd.DataFrame(all_rows)
    chunks["chunk_id"] = det.unique_stable_ids(
        "RC",
        chunks["doc_key"] + "|" + chunks["section_label"] + "|" + chunks["char_len"].astype(str),
        width=14,
    )
    chunks = chunks[CHUNK_COLUMNS]
    sources = pd.DataFrame(source_rows)[SOURCE_COLUMNS]

    # Integrity gates. A chunk without a resolvable citation is worse than absent:
    # it looks authoritative and cannot be checked.
    problems = []
    if chunks["chunk_text"].str.strip().eq("").any():
        problems.append("empty chunk_text present")
    for col in ("citation_id", "source_url", "section_label", "doc_key"):
        if chunks[col].isna().any() or chunks[col].astype(str).str.strip().eq("").any():
            problems.append(f"{col} has blank/NULL values")
    if not chunks["chunk_id"].is_unique:
        problems.append("chunk_id is not unique")
    empty_docs = sources.loc[sources["chunk_count"] == 0, "doc_key"].tolist()
    if empty_docs:
        problems.append(f"documents contributing zero chunks: {empty_docs}")

    # Content-free source guard. statutes.capitol.texas.gov returned 250 KB of
    # JavaScript shell containing no statute text, which yielded ONE 1.5 KB chunk of
    # navigation chrome and passed every other check. A document that fetches large
    # but parses tiny is almost always a JS-rendered page, so treat a low
    # text-to-bytes yield as a failure rather than trusting the byte count.
    yields = sources.assign(
        parsed_chars=[
            int(chunks.loc[chunks["doc_key"] == k, "char_len"].sum())
            for k in sources["doc_key"]
        ]
    )
    yields["yield_pct"] = 100.0 * yields["parsed_chars"] / yields["size_bytes"].clip(lower=1)
    starved = yields[(yields["size_bytes"] > 50_000) & (yields["parsed_chars"] < 5_000)]
    for _, row in starved.iterrows():
        problems.append(
            f"{row['doc_key']} fetched {row['size_bytes']:,d} bytes but parsed only "
            f"{row['parsed_chars']:,d} chars - likely a JavaScript shell, not real content"
        )

    # Chunk size sanity: a chunk far over target dilutes retrieval precision.
    oversize = int((chunks["char_len"] > AC.CHUNK_TARGET_CHARS * 1.5).sum())
    if oversize:
        problems.append(
            f"{oversize} chunk(s) exceed 1.5x the {AC.CHUNK_TARGET_CHARS} char target"
        )

    chunks.to_parquet(OUT_DIR / "regulatory_chunks.parquet", index=False)
    sources.to_parquet(OUT_DIR / "regulatory_sources.parquet", index=False)

    print()
    print(f"  chunks  {len(chunks):,d} rows -> {OUT_DIR / 'regulatory_chunks.parquet'}")
    print(f"  sources {len(sources):,d} rows -> {OUT_DIR / 'regulatory_sources.parquet'}")
    print(f"  chunk chars: median {int(chunks['char_len'].median()):,d}  "
          f"max {int(chunks['char_len'].max()):,d}  "
          f"over target {int((chunks['char_len'] > AC.CHUNK_TARGET_CHARS).sum())}")
    print(f"  distinct citations: {chunks['citation_id'].nunique():,d}")
    print()
    print("  text yield per document (parsed chars vs fetched bytes):")
    for _, row in yields.sort_values("yield_pct").iterrows():
        print(f"    {row['doc_key']:26s} {row['parsed_chars']:9,d} chars / "
              f"{row['size_bytes']:10,d} bytes = {row['yield_pct']:5.1f}%")
    print()
    if problems:
        print(f"{len(problems)} INTEGRITY PROBLEM(S):")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("Integrity checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
