"""
Unit test for the radiology label validator.

Every case below is a REAL Wikimedia file that the validator once got wrong. They
exist as regression tests because a mislabelled reference image shown to a
non-radiologist analyst is actively harmful - worse than showing nothing.

Run:  python validate/test_radiology_labels.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dataPrepRadiology.fetch_radiology import classify_consistency  # noqa: E402

# (finding_class, body_region, title, description, should_accept, why_it_matters)
CASES: list[tuple[str, str, str, str, bool, str]] = [
    # --- real failures that slipped through earlier runs ---
    ("NORMAL", "LUMBAR_SPINE", "Hernie discale L4 L5.png", "IRM du rachis lombaire",
     False, "French for disc herniation - was accepted as NORMAL by an English-only regex"),
    ("NORMAL", "KNEE", "Radiograph of a child with polymelia.jpg",
     "Radiograph showing a normal knee joint in a child with polymelia",
     False, "paediatric limb malformation - not a normal adult baseline"),
    ("NORMAL", "KNEE", "X-ray of normal right foot by lateral projection.jpg",
     "Normal foot radiograph",
     False, "a FOOT film returned by a knee search - wrong body region"),
    ("NORMAL", "CHEST", "Stuve-wiedemann2.JPG",
     "Chest radiograph of a patient. Bone density appears normal in places.",
     False, "skeletal dysplasia case - 'normal' only appeared in the description"),
    ("NORMAL", "KNEE", "Postoperative X-ray of normal knee prosthesis, AP.jpg",
     "Knee following arthroplasty",
     False, "postoperative with hardware - prosthe/postop stems were disabled by a trailing \\b"),

    # --- inflected forms that the trailing-\\b bug silently disabled ---
    ("ABNORMAL", "LUMBAR_SPINE", "Lumbar disc herniation MRI.jpg", "L5-S1 herniation",
     True, "'herniation' must match the 'herniat' stem"),
    ("NORMAL", "CHEST", "Normal chest radiograph.jpg", "Prosthesis-free normal chest",
     False, "'Prosthesis' must match the 'prosthe' stem and disqualify it"),

    # --- correct accepts that must NOT be over-rejected ---
    ("NORMAL", "CHEST", "Normal posteroanterior (PA) chest radiograph (X-ray).jpg",
     "A normal PA chest radiograph of an adult", True, "canonical good NORMAL case"),
    ("NORMAL", "BRAIN", "CT of a normal brain, sagittal 20.png",
     "Sagittal CT of a normal brain", True, "CC0 normal brain series must pass"),
    ("ABNORMAL", "KNEE", "Proton density MRI of a grade 2 medial meniscal tear.jpg",
     "Grade 2 tear of the medial meniscus", True, "canonical good ABNORMAL case"),
    ("ABNORMAL", "CHEST", "Two-view chest radiograph depicting bilateral pleural effusions.jpg",
     "Bilateral pleural effusions", True, "pathology in title, region in title"),
    ("ABNORMAL", "BRAIN", "Axial DIR MRI of a brain with multiple sclerosis lesions.jpg",
     "Demyelinating lesions", True, "region and pathology both evidenced"),

    # --- region evidence must come from title OR description ---
    ("ABNORMAL", "CHEST", "Severe Pneumonia Caused by Legionella pneumophila.jpg",
     "Chest radiograph showing consolidation", True,
     "region is only in the description - must still be accepted"),

    # --- description prose must NOT veto an explicit NORMAL title ---
    ("NORMAL", "BRAIN", "CT of a normal brain, sagittal 20.png",
     "Sagittal reconstruction from a CT of the head acquired in a trauma workup, "
     "reported as showing a normal brain with no acute intracranial abnormality.",
     True,
     "25 CC0 'CT of a normal brain' files were wrongly rejected because the "
     "description mentions the trauma protocol the scan was acquired in"),
    ("NORMAL", "CHEST", "Normal chest radiograph of an adult.jpg",
     "Taken to exclude pneumonia; no consolidation or effusion seen.",
     True,
     "a normal film whose description names the differential being excluded "
     "must not be vetoed by those words"),
    ("NORMAL", "LUMBAR_SPINE", "Hernie discale L4 L5.png",
     "IRM du rachis lombaire montrant une hernie discale",
     False,
     "pathology in the TITLE must still veto a NORMAL claim"),
]


def main() -> int:
    failures = 0
    for finding, region, title, desc, expected, rationale in CASES:
        got, reason = classify_consistency(finding, region, title, desc)
        ok = got == expected
        if not ok:
            failures += 1
        print(f"  {'ok  ' if ok else 'FAIL'} [{region:13s} {finding:8s}] {title[:52]}")
        print(f"        want={expected!s:5s} got={got!s:5s}  validator said: {reason}")
        if not ok:
            print(f"        WHY THIS MATTERS: {rationale}")

    print()
    print("ALL PASSED" if not failures else f"{failures} FAILURE(S)")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
