"""
Build the radiology reference library from Wikimedia Commons.

PURPOSE. A PA analyst is usually not a radiologist. When a request involves imaging
it helps to see what a NORMAL study of that region looks like beside a clearly
ABNORMAL one - purely for orientation. These are public teaching images, never the
member's own studies, and they must never support a determination.

LICENCE GATE. config.app_config.licence_allowed() is the hard filter: CC0, public
domain and CC BY only. CC BY-SA is deliberately EXCLUDED because share-alike may
extend obligations to a proprietary payer application - a legal judgement, so the
safe default is to omit it. Radiopaedia (CC BY-NC-SA), CheXpert, MIMIC-CXR and MURA
are all disqualified: non-commercial terms or data use agreements that forbid
redistribution. Every rejection is logged so the gate's work is auditable.

Run:
    python dataPrepRadiology/fetch_radiology.py
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import app_config as AC  # noqa: E402

API = "https://commons.wikimedia.org/w/api.php"
UA = "member360-pa-copilot/1.0 (PA analyst teaching reference; contact: repo maintainer)"
CACHE = Path(__file__).resolve().parent.parent / ".cache" / "radiology"
MANIFEST = CACHE / "manifest.json"
MAX_EDGE = 1600
TIMEOUT = 90

# (body_region, modality, finding_class, search terms)
# Terms are deliberately specific and several per cell: "normal knee MRI" returns
# teaching images, while "knee" returns anatomy diagrams and sports photography.
# Multiple phrasings per cell matter because the strict label validation rejects most
# candidates, so a single query rarely yields a usable hit.
SEARCHES: list[tuple[str, str, str, str]] = [
    ("LUMBAR_SPINE", "MRI",  "NORMAL",   "normal lumbar spine MRI"),
    ("LUMBAR_SPINE", "MRI",  "NORMAL",   "normal lumbar vertebrae magnetic resonance sagittal"),
    ("LUMBAR_SPINE", "XRAY", "NORMAL",   "normal lumbar spine radiograph"),
    # The CC0 "... of a normal ..." naming convention (used by prolific Commons
    # medical uploaders who release under CC0) is what finally yielded usable normal
    # brain studies, so probe the same pattern for the musculoskeletal regions.
    ("LUMBAR_SPINE", "MRI",  "NORMAL",   "MRI of a normal lumbar spine"),
    ("LUMBAR_SPINE", "CT",   "NORMAL",   "CT of a normal lumbar spine"),
    ("LUMBAR_SPINE", "XRAY", "NORMAL",   "radiograph of a normal spine annotated"),
    ("LUMBAR_SPINE", "MRI",  "ABNORMAL", "lumbar disc herniation MRI"),
    ("LUMBAR_SPINE", "MRI",  "ABNORMAL", "lumbar spinal stenosis MRI"),
    ("LUMBAR_SPINE", "XRAY", "ABNORMAL", "lumbar spondylolisthesis radiograph"),

    ("KNEE", "MRI",  "NORMAL",   "normal knee MRI"),
    ("KNEE", "MRI",  "NORMAL",   "normal knee magnetic resonance sagittal"),
    ("KNEE", "XRAY", "NORMAL",   "normal knee joint radiograph"),
    ("KNEE", "MRI",  "NORMAL",   "MRI of a normal knee"),
    ("KNEE", "XRAY", "NORMAL",   "X-ray of a normal knee"),
    ("KNEE", "CT",   "NORMAL",   "CT of a normal knee"),
    ("KNEE", "MRI",  "ABNORMAL", "knee meniscal tear MRI"),
    ("KNEE", "MRI",  "ABNORMAL", "anterior cruciate ligament rupture MRI"),
    ("KNEE", "XRAY", "ABNORMAL", "knee osteoarthritis radiograph"),

    ("CHEST", "XRAY", "NORMAL",   "normal chest radiograph"),
    ("CHEST", "XRAY", "NORMAL",   "normal posteroanterior chest radiograph"),
    ("CHEST", "XRAY", "NORMAL",   "normal lateral chest radiograph"),
    ("CHEST", "CT",   "NORMAL",   "normal chest CT thorax"),
    ("CHEST", "XRAY", "ABNORMAL", "pneumothorax chest radiograph"),
    ("CHEST", "XRAY", "ABNORMAL", "pleural effusion chest radiograph"),
    ("CHEST", "CT",   "ABNORMAL", "pulmonary embolism CT thorax"),

    ("BRAIN", "CT",  "NORMAL",   "normal head CT"),
    ("BRAIN", "CT",  "NORMAL",   "normal brain CT scan axial"),
    ("BRAIN", "CT",  "NORMAL",   "normal cranial computed tomography"),
    ("BRAIN", "MRI", "NORMAL",   "normal brain MRI"),
    ("BRAIN", "MRI", "NORMAL",   "normal brain magnetic resonance axial T2"),
    ("BRAIN", "CT",  "ABNORMAL", "intracranial haemorrhage CT brain"),
    ("BRAIN", "CT",  "ABNORMAL", "subdural hematoma CT brain"),
    ("BRAIN", "MRI", "ABNORMAL", "brain tumour MRI"),
    ("BRAIN", "MRI", "ABNORMAL", "multiple sclerosis brain MRI lesions"),
]

# IMPORTANT REGEX NOTE for everything below: these patterns anchor the START of a
# word with \b but deliberately do NOT anchor the end. They are STEMS meant to match
# inflected forms. An earlier version wrapped them as \b(...)\b, which silently
# disabled most of them: `herniat\b` cannot match "herniation", and `prosthe\b`
# cannot match "prosthesis", so "Postoperative X-ray of normal knee prosthesis" was
# accepted as a NORMAL knee reference.

# Words indicating the file is not a real clinical study. Anatomy drawings are worse
# than useless: an analyst orienting on "a normal knee MRI" must not see an
# illustration.
EXCLUDE_TITLE = re.compile(
    r"\b(diagram|schematic|illustrat|drawing|cartoon|animation|logo|icon|"
    r"chart|graph|plot|map|scheme|3d render|model of)",
    re.I,
)

# Terms that contradict a NORMAL label. Deliberately multilingual: Wikimedia is not
# English-only, and an English-only pattern let "Hernie discale L4 L5" (French for
# disc herniation) through as a NORMAL lumbar spine reference.
ABNORMAL_HINTS = re.compile(
    r"\b(herniat|hernia|hernie|discale|stenos|st[e\u00e9]nos|tear|torn|ruptur|"
    r"lesion|l[e\u00e9]sion|tumou?r|tumeur|cancer|carcinom|metasta|m[e\u00e9]tasta|"
    r"fractur|fractura|effusion|[e\u00e9]panchement|pneumothorax|pneumon|"
    r"consolidat|h[ae]matom|h[e\u00e9]matom|h[ae]morrhag|h[e\u00e9]morragi|"
    r"infarct|oedem|edema|\u0153d[e\u00e8]m|sclerosi|scl[e\u00e9]ros|"
    r"arthriti|arthros|osteophyt|spondylolisthes|spondylarthros|embolis|"
    r"embolie|abscess|abc[e\u00e8]s|nodule|cyst|kyste|degenerat|d[e\u00e9]g[e\u00e9]n[e\u00e9]r|"
    r"protrusion|bulging|malformat|polymeli|anomal|deformit|dysplas|"
    r"osteoporo|scolios|kyphos|listhes|compressi|trauma|infect|inflamm)",
    re.I,
)
NORMAL_HINTS = re.compile(
    r"\b(normal|healthy|sain\b|unremarkable|no abnormalit|without abnormalit)",
    re.I,
)

# The body region must be EVIDENCED in the image's own words. Without this a search
# for "normal knee radiograph" happily returned a normal FOOT x-ray, which is
# useless to an analyst orienting on a knee request.
REGION_TERMS: dict[str, re.Pattern] = {
    "LUMBAR_SPINE": re.compile(
        r"\b(lumbar|lumbaire|lumbosacral|L[1-5]\b|spine|spinal|rachis|vertebr|"
        r"intervertebral|disc|discale)", re.I),
    "KNEE": re.compile(r"\b(knee|genou|patell|menisc|m[e\u00e9]nisq|cruciate|"
                       r"crois[e\u00e9]|tibiofemoral|femorotibial)", re.I),
    "CHEST": re.compile(r"\b(chest|thorax|thoracic|thoracique|lung|poumon|pulmonar|"
                        r"pulmonair|pleural|pl[e\u00e9]vre|mediastin|cardiothoracic)", re.I),
    "BRAIN": re.compile(r"\b(brain|cerebral|c[e\u00e9]r[e\u00e9]bral|cerveau|head|"
                        r"cranial|crani|intracranial|skull|encephal|white matter)", re.I),
}

# A reference image should be an adult, non-post-operative native study. Paediatric or
# post-surgical anatomy is a poor orientation baseline for an adult PA request, and
# hardware obscures the anatomy the analyst is trying to learn to recognise.
UNSUITABLE = re.compile(
    r"\b(child|infant|newborn|neonat|paediatric|pediatric|enfant|nourrisson|"
    r"post-?operativ|post-?op\b|postop|prosthe|proth[e\u00e8]s|implant|screw|"
    r"plate\b|fusion cage|arthroplast|fixation|stent|catheter|surgical)",
    re.I,
)


# Wikimedia asks unauthenticated clients to be gentle and returns 429 when they are
# not. One request per REQUEST_INTERVAL seconds, plus honouring Retry-After, keeps
# the whole run inside their guidance instead of hammering until blocked.
REQUEST_INTERVAL = 3.0
_last_request = 0.0


def _throttle() -> None:
    global _last_request
    wait = REQUEST_INTERVAL - (time.monotonic() - _last_request)
    if wait > 0:
        time.sleep(wait)
    _last_request = time.monotonic()


def _get(params: dict) -> dict:
    params = {**params, "format": "json"}
    for attempt in range(5):
        _throttle()
        try:
            r = requests.get(API, params=params, headers={"User-Agent": UA}, timeout=TIMEOUT)
            if r.status_code == 429:
                delay = float(r.headers.get("Retry-After", 0) or 0) or 5 * (attempt + 1)
                print(f"    rate limited, backing off {delay:.0f}s")
                time.sleep(delay)
                continue
            r.raise_for_status()
            return r.json()
        except requests.HTTPError:
            if attempt == 4:
                raise
            time.sleep(3 * (attempt + 1))
        except Exception:  # noqa: BLE001
            if attempt == 4:
                raise
            time.sleep(2 * (attempt + 1))
    return {}


def search_files(terms: str, limit: int = 25) -> list[str]:
    """Search the File namespace, restricted to raster images.

    `filetype:bitmap` is doing real work: without it 135 of 229 candidates in an
    earlier run were PDFs and OGG files that had to be fetched and rejected one by
    one. Filtering server-side spends the rate-limit budget on plausible candidates
    instead.
    """
    data = _get({
        "action": "query", "list": "search",
        "srsearch": f"{terms} filetype:bitmap",
        "srnamespace": 6, "srlimit": limit,
    })
    return [hit["title"] for hit in data.get("query", {}).get("search", [])]


def file_info(titles: list[str]) -> dict[str, dict]:
    if not titles:
        return {}
    data = _get({
        "action": "query", "titles": "|".join(titles), "prop": "imageinfo",
        "iiprop": "url|size|mime|extmetadata",
    })
    out: dict[str, dict] = {}
    for page in data.get("query", {}).get("pages", {}).values():
        info = (page.get("imageinfo") or [{}])[0]
        if not info:
            continue
        meta = info.get("extmetadata", {})
        out[page["title"]] = {
            "title": page["title"],
            "url": info.get("url", ""),
            "descriptionurl": info.get("descriptionurl", ""),
            "mime": info.get("mime", ""),
            "width": info.get("width", 0),
            "height": info.get("height", 0),
            "size": info.get("size", 0),
            "licence": (meta.get("LicenseShortName", {}) or {}).get("value", ""),
            "artist_html": (meta.get("Artist", {}) or {}).get("value", ""),
            "description_html": (meta.get("ImageDescription", {}) or {}).get("value", ""),
        }
    return out


def strip_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = (text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
                .replace("&quot;", '"').replace("&#039;", "'").replace("&nbsp;", " "))
    return " ".join(text.split())


def classify_consistency(
    finding_class: str, body_region: str, title: str, description: str
) -> tuple[bool, str]:
    """Validate the intended labels against the image's own words.

    Three independent checks, because each caught a real mislabel in an earlier run:

      1. BODY REGION must be evidenced. A search for "normal knee radiograph"
         returned a normal FOOT x-ray, which passed every other check.
      2. A NORMAL label must be supported and must not be contradicted. "Hernie
         discale L4 L5" (French: disc herniation) was accepted as a NORMAL lumbar
         reference because the pathology regex was English-only.
      3. Paediatric and post-operative studies are rejected outright as orientation
         baselines - "Radiograph of a child with polymelia" is not a normal knee.

    An unverifiable label is REJECTED. A mislabelled reference shown to a
    non-radiologist is worse than showing nothing.
    """
    blob = f"{title} {description}"

    if not REGION_TERMS[body_region].search(blob):
        return False, f"body region {body_region} not evidenced in title/description"

    if UNSUITABLE.search(blob):
        hit = UNSUITABLE.search(blob)
        return False, f"unsuitable as a reference baseline ('{hit.group(0)}')"

    if finding_class == "NORMAL":
        # Normality must be asserted in the TITLE. "Stuve-wiedemann2.JPG" (a skeletal
        # dysplasia case) was accepted as a NORMAL chest film because an unrelated
        # sentence in its description contained the word "normal".
        if not NORMAL_HINTS.search(title):
            return False, "labelled NORMAL but the title does not say so"

        # The veto is checked against the TITLE ONLY, not the description. Checking
        # the description rejected 25 CC0 files titled "CT of a normal brain,
        # sagittal N" because their shared description mentions the trauma workup the
        # scan was acquired in - a normal study acquired in a trauma protocol is
        # still a normal study. A title is a deliberate label; a description is
        # prose that mentions clinical context, differentials and incidental notes.
        contradiction = ABNORMAL_HINTS.search(title)
        if contradiction:
            return False, f"title claims NORMAL but also says '{contradiction.group(0)}'"
    else:
        if not ABNORMAL_HINTS.search(blob):
            return False, "labelled ABNORMAL but no pathology term in title/description"

    return True, "labels consistent with source text"


def download_and_resize(url: str, dest: Path) -> tuple[int, int, int, str]:
    from PIL import Image

    # Image fetches must share the throttle with the API calls. Skipping it here
    # caused ALL 37 "download/resize failed" rejections in an earlier run - every
    # one was a 429, not a bad image, so genuinely usable references were discarded.
    _throttle()
    r = requests.get(url, headers={"User-Agent": UA}, timeout=TIMEOUT)
    if r.status_code == 429:
        delay = float(r.headers.get("Retry-After", 0) or 0) or 20
        time.sleep(delay)
        _throttle()
        r = requests.get(url, headers={"User-Agent": UA}, timeout=TIMEOUT)
    r.raise_for_status()
    raw = r.content

    img = Image.open(io.BytesIO(raw))
    img = img.convert("L") if img.mode in ("I;16", "I") else img.convert("RGB")
    w, h = img.size
    if max(w, h) > MAX_EDGE:
        scale = MAX_EDGE / max(w, h)
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
    img.save(dest, "JPEG", quality=88, optimize=True)

    data = dest.read_bytes()
    return img.size[0], img.size[1], len(data), hashlib.sha256(data).hexdigest()


def main() -> int:
    CACHE.mkdir(parents=True, exist_ok=True)
    accepted: list[dict] = []
    rejected: list[dict] = []
    seen_titles: set[str] = set()
    seen_hashes: set[str] = set()

    print("=" * 78)
    print("RADIOLOGY REFERENCE FETCH")
    print(f"licence gate: CC0 / public domain / CC BY   (share-alike allowed: "
          f"{AC.ALLOW_SHARE_ALIKE})")
    print("=" * 78)

    for region, modality, finding, terms in SEARCHES:
        titles = search_files(terms)
        infos = file_info(titles)
        got_for_this_search = 0

        for title in titles:
            if got_for_this_search >= 3:
                break
            info = infos.get(title)
            if not info or title in seen_titles:
                continue
            seen_titles.add(title)

            desc = strip_html(info["description_html"])
            reject = None

            if not info["mime"].startswith("image/"):
                reject = f"not an image ({info['mime']})"
            elif info["mime"] in ("image/svg+xml",):
                reject = "vector graphic, not a real study"
            elif EXCLUDE_TITLE.search(title) or EXCLUDE_TITLE.search(desc[:400]):
                reject = "looks like a diagram or illustration, not a clinical study"
            elif not AC.licence_allowed(info["licence"]):
                reject = f"licence not permitted: {info['licence'] or '(none stated)'}"
            else:
                ok, why = classify_consistency(finding, region, title, desc)
                if not ok:
                    reject = why

            if reject:
                rejected.append({
                    "title": title, "licence": info["licence"],
                    "region": region, "finding": finding, "reason": reject,
                })
                continue

            dest = CACHE / (re.sub(r"[^A-Za-z0-9]+", "_", title.replace("File:", ""))[:80] + ".jpg")
            try:
                w, h, nbytes, sha = download_and_resize(info["url"], dest)
            except Exception as exc:  # noqa: BLE001
                rejected.append({
                    "title": title, "licence": info["licence"], "region": region,
                    "finding": finding,
                    "reason": f"download/resize failed: {type(exc).__name__}: {str(exc)[:90]}",
                })
                continue

            if sha in seen_hashes:
                dest.unlink(missing_ok=True)
                rejected.append({
                    "title": title, "licence": info["licence"], "region": region,
                    "finding": finding, "reason": "duplicate image content",
                })
                continue
            seen_hashes.add(sha)

            accepted.append({
                "file_name": dest.name,
                "body_region": region,
                "modality": modality,
                "finding_class": finding,
                "title": title.replace("File:", ""),
                "description": desc[:1200],
                "source_page_url": info["descriptionurl"],
                "source_file_url": info["url"].split("?")[0],
                "licence": info["licence"],
                "attribution": strip_html(info["artist_html"])[:300] or "Wikimedia Commons contributor",
                "width": w, "height": h, "bytes": nbytes, "sha256": sha,
                "search_terms": terms,
            })
            got_for_this_search += 1
            print(f"  ok    {region:13s} {modality:4s} {finding:8s} "
                  f"{info['licence']:16s} {title[:52]}")

    MANIFEST.write_text(json.dumps({"accepted": accepted, "rejected": rejected}, indent=2),
                        encoding="utf-8")

    print()
    print("=" * 78)
    print(f"ACCEPTED {len(accepted)}   REJECTED {len(rejected)}")
    print("=" * 78)

    print()
    print("  coverage matrix (body_region x finding_class):")
    print(f"    {'region':14s} {'NORMAL':>7s} {'ABNORMAL':>9s}")
    gaps: list[str] = []
    for region in AC.BODY_REGIONS:
        n = sum(1 for a in accepted if a["body_region"] == region and a["finding_class"] == "NORMAL")
        ab = sum(1 for a in accepted if a["body_region"] == region and a["finding_class"] == "ABNORMAL")
        flag = "" if (n and ab) else "   <-- INCOMPLETE PAIR"
        print(f"    {region:14s} {n:7d} {ab:9d}{flag}")
        if not n:
            gaps.append(f"{region} has no NORMAL example")
        if not ab:
            gaps.append(f"{region} has no ABNORMAL example")

    print()
    print("  licence distribution (accepted):")
    dist: dict[str, int] = {}
    for a in accepted:
        dist[a["licence"]] = dist.get(a["licence"], 0) + 1
    for lic, n in sorted(dist.items(), key=lambda kv: -kv[1]):
        print(f"    {lic:24s} {n}")

    print()
    print("  rejection reasons:")
    rdist: dict[str, int] = {}
    for r in rejected:
        key = r["reason"].split(":")[0][:52]
        rdist[key] = rdist.get(key, 0) + 1
    for reason, n in sorted(rdist.items(), key=lambda kv: -kv[1]):
        print(f"    {n:4d}  {reason}")

    print()
    print("  licences seen among REJECTED (proof the gate is doing work):")
    ldist: dict[str, int] = {}
    for r in rejected:
        if r["reason"].startswith("licence not permitted"):
            ldist[r["licence"] or "(none)"] = ldist.get(r["licence"] or "(none)", 0) + 1
    for lic, n in sorted(ldist.items(), key=lambda kv: -kv[1]):
        print(f"    {n:4d}  {lic}")

    print()
    if gaps:
        print(f"  {len(gaps)} COVERAGE GAP(S) - reported, not padded:")
        for g in gaps:
            print(f"    - {g}")
    print(f"  manifest: {MANIFEST}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
