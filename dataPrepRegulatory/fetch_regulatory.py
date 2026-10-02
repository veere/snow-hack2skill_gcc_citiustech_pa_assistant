"""
Cached fetch of the regulatory corpus.

A sibling of common/fetch.py rather than an extension of it: that module is driven
by `config["open_datasets"]`, whose shape (single filename, role, required flag)
does not carry the citation metadata a RegDoc needs, and contorting it would make
both harder to read.

Traps this encodes, each found by actually trying it:
  * eCFR /full/ 404s on the `current` snapshot - a DATED snapshot works. The date is
    pinned in config/regulatory_sources.py so re-running months later reproduces the
    same corpus instead of silently drifting.
  * eCFR section retrieval needs the whole hierarchy (chapter/subchapter/part/
    subpart), not just part+section.
  * www.oig.hhs.gov serves the OIG PDF; the bare host refuses the connection.
  * A descriptive User-Agent is required or several .gov hosts reject the request.

Run:
    python dataPrepRegulatory/fetch_regulatory.py [--force]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.regulatory_sources import REG_DOCS  # noqa: E402

CACHE_DIR = Path(__file__).resolve().parent.parent / ".cache" / "regulatory"
MANIFEST = CACHE_DIR / "manifest.json"
USER_AGENT = "member360-pa-copilot/1.0 (regulatory corpus; public-domain sources)"
TIMEOUT = 180
CHUNK = 1 << 16

EXT = {"ecfr_xml": ".xml", "pdf": ".pdf", "fr_json": ".json", "html": ".html"}


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(CHUNK), b""):
            h.update(block)
    return h.hexdigest()


def _read_manifest() -> dict:
    if MANIFEST.exists():
        return json.loads(MANIFEST.read_text(encoding="utf-8"))
    return {}


def _download(url: str, params: dict | None, dest: Path) -> None:
    headers = {"User-Agent": USER_AGENT}
    with requests.get(url, params=params or None, headers=headers,
                      stream=True, timeout=TIMEOUT) as resp:
        resp.raise_for_status()
        tmp = dest.with_suffix(dest.suffix + ".part")
        with tmp.open("wb") as fh:
            for block in resp.iter_content(chunk_size=CHUNK):
                if block:
                    fh.write(block)
        tmp.replace(dest)


def fetch_all(*, force: bool = False) -> dict:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    manifest = _read_manifest()

    print("=" * 78)
    print(f"REGULATORY CORPUS FETCH - {len(REG_DOCS)} documents")
    print("=" * 78)

    failures: list[str] = []
    for doc in REG_DOCS:
        dest = CACHE_DIR / f"{doc.key}{EXT[doc.fmt]}"

        if dest.exists() and not force and doc.key in manifest:
            size = manifest[doc.key]["size_bytes"]
            print(f"  cached   {doc.key:26s} {size / 1e6:7.2f} MB  {doc.citation_id}")
            continue

        urls = [doc.url, *doc.fallback_urls]
        got = False
        for attempt, url in enumerate(urls):
            try:
                _download(url, doc.ecfr_params, dest)
            except Exception as exc:  # noqa: BLE001
                label = "primary" if attempt == 0 else f"fallback {attempt}"
                print(f"  {label} failed for {doc.key}: {str(exc)[:90]}")
                continue

            size = dest.stat().st_size
            # A 200 that returns a stub is a silent failure; eCFR in particular
            # answers 404-as-XML in ~119 bytes. Treat anything tiny as a failure.
            if size < 2000:
                print(f"  SUSPECT  {doc.key}: only {size} bytes from {url} - treating as failure")
                continue

            manifest[doc.key] = {
                "doc_key": doc.key,
                "citation_id": doc.citation_id,
                "title": doc.title,
                "authority": doc.authority,
                "url_used": url,
                "url_was_fallback": attempt > 0,
                "params": doc.ecfr_params,
                "fmt": doc.fmt,
                "filename": dest.name,
                "size_bytes": size,
                "sha256": _sha256(dest),
                "licence": doc.licence,
                "effective_date": doc.effective_date,
                "themes": list(doc.themes),
                "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
            print(f"  ok       {doc.key:26s} {size / 1e6:7.2f} MB  "
                  f"sha={manifest[doc.key]['sha256'][:12]}  {doc.citation_id}")
            got = True
            break

        if not got:
            failures.append(doc.key)

    MANIFEST.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    print()
    print(f"  fetched/cached {len(manifest)} / {len(REG_DOCS)}")
    print(f"  manifest: {MANIFEST}")
    if failures:
        # Loud, not swallowed: a missing source silently narrows what the copilot
        # can cite, and that is exactly the kind of gap that should not be quiet.
        print()
        print(f"  {len(failures)} DOCUMENT(S) FAILED: {', '.join(failures)}")
    return manifest


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="re-download even if cached")
    args = ap.parse_args()
    manifest = fetch_all(force=args.force)
    return 0 if len(manifest) == len(REG_DOCS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
