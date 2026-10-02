"""
Cached HTTP fetch for the open datasets.

Design points that matter for replicability:

* Downloads are cached under `.cache/` and reused. Rerunning the pipeline does
  not re-download ~26 MB, and works offline once primed.
* Every dataset declares fallback URLs. CMS reorganises file paths and renames
  quarterly releases, and community mirrors disappear - so a single hardcoded URL
  is a guaranteed future failure. The fetcher walks the list until one responds.
* A SHA-256 is recorded for whatever was actually downloaded and written to
  `.cache/fetch_manifest.json`. That manifest is what `datasets.md` reports, so
  the documentation describes the bytes that were really used rather than what
  was expected.
* Optional datasets that fail are reported and skipped; required datasets that
  fail raise. This keeps the pipeline runnable when a nice-to-have source is
  temporarily unavailable.
"""

from __future__ import annotations

import hashlib
import json
import zipfile
from datetime import datetime
from pathlib import Path

import requests

from config import schemas as S

MANIFEST_FILENAME = "fetch_manifest.json"
USER_AGENT = "member360-datamart-pipeline/1.0 (open-data fetch)"
TIMEOUT = 120
CHUNK = 1 << 16


def cache_dir() -> Path:
    cfg = S.load_config()
    path = Path(cfg["_repo_root"]) / cfg["paths"]["cache"]
    path.mkdir(parents=True, exist_ok=True)
    return path


def _manifest_path() -> Path:
    return cache_dir() / MANIFEST_FILENAME


def read_manifest() -> dict:
    path = _manifest_path()
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def _write_manifest(manifest: dict) -> None:
    _manifest_path().write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def _download(url: str, dest: Path) -> None:
    headers = {"User-Agent": USER_AGENT}
    with requests.get(url, stream=True, timeout=TIMEOUT, headers=headers) as resp:
        resp.raise_for_status()
        tmp = dest.with_suffix(dest.suffix + ".part")
        with tmp.open("wb") as fh:
            for chunk in resp.iter_content(chunk_size=CHUNK):
                if chunk:
                    fh.write(chunk)
        tmp.replace(dest)


def fetch(dataset_key: str, *, force: bool = False) -> dict | None:
    """Fetch one configured dataset. Returns its manifest entry, or None when an
    optional dataset could not be retrieved.

    Raises RuntimeError when a dataset marked `required` cannot be retrieved from
    its primary URL or any fallback.
    """
    cfg = S.load_config()
    spec = cfg["open_datasets"][dataset_key]

    if not spec.get("enabled", True):
        print(f"  [{dataset_key}] disabled in config - skipped")
        return None

    manifest = read_manifest()
    dest = cache_dir() / spec["filename"]

    if dest.exists() and not force and dataset_key in manifest:
        entry = manifest[dataset_key]
        print(f"  [{dataset_key}] cached  {dest.name}  {entry['size_bytes'] / 1e6:.1f} MB")
        return entry

    candidates = [spec["url"], *spec.get("fallback_urls", [])]
    last_error: Exception | None = None

    for attempt, url in enumerate(candidates):
        label = "primary" if attempt == 0 else f"fallback {attempt}"
        try:
            print(f"  [{dataset_key}] downloading ({label}) {url}")
            _download(url, dest)
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            print(f"  [{dataset_key}] {label} failed: {exc}")
            continue

        entry = {
            "dataset_key": dataset_key,
            "url_used": url,
            "url_was_fallback": attempt > 0,
            "filename": spec["filename"],
            "size_bytes": dest.stat().st_size,
            "sha256": sha256_file(dest),
            "fetched_at": datetime.now().isoformat(timespec="seconds"),
            "role": spec.get("role", ""),
        }
        manifest[dataset_key] = entry
        _write_manifest(manifest)
        print(
            f"  [{dataset_key}] ok  {entry['size_bytes'] / 1e6:.1f} MB  "
            f"sha256={entry['sha256'][:16]}..."
        )
        return entry

    if spec.get("required", False):
        raise RuntimeError(
            f"Required dataset {dataset_key!r} could not be downloaded from any of "
            f"{len(candidates)} URL(s). Last error: {last_error}"
        )

    print(f"  [{dataset_key}] OPTIONAL - unavailable, continuing without it")
    return None


def extract_zip(dataset_key: str, *, members: list[str] | None = None) -> Path:
    """Extract a cached zip into `.cache/<dataset_key>/` and return that dir."""
    cfg = S.load_config()
    spec = cfg["open_datasets"][dataset_key]
    archive = cache_dir() / spec["filename"]
    if not archive.exists():
        raise FileNotFoundError(f"{archive} not present - fetch {dataset_key!r} first")

    out_dir = cache_dir() / dataset_key
    out_dir.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(archive) as zf:
        names = members if members is not None else zf.namelist()
        for name in names:
            if name.endswith("/"):
                continue
            target = out_dir / Path(name).name
            if target.exists():
                continue
            with zf.open(name) as src, target.open("wb") as dst:
                dst.write(src.read())

    return out_dir


def list_zip_contents(dataset_key: str) -> list[str]:
    cfg = S.load_config()
    archive = cache_dir() / cfg["open_datasets"][dataset_key]["filename"]
    with zipfile.ZipFile(archive) as zf:
        return zf.namelist()
