"""
Deploy the PA copilot to Streamlit in Snowflake.

SiS is the STANDALONE surface. It cannot be iframed - the Snowflake proxy injects
`X-Frame-Options: DENY` and the SiS docs state the CSP "is not configurable at this
time" - so embedding uses the self-hosted mode instead (see app/embed.py). Both run
the same code.

Only the modules SiS actually needs are uploaded. The PAT connector path in
app/snow.py and the REST transport in app/answering.py both import
load.snowflake_loader lazily, inside the self-hosted branch, so that module and its
dependencies stay out of the bundle.

Run:
    cortex secret run --map snowflake_pat=SNOWFLAKE_PAT -- python app/deploy_sis.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import app_config as AC  # noqa: E402
from load import snowflake_loader as L  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
STAGE = "STG_APP"
STREAMLIT_NAME = "PA_INSIGHT_COPILOT"
WAREHOUSE = "COMPUTE_WH"

# environment.yml IS uploaded, but it pins ONLY streamlit.
#
# Two failures shaped this file:
#
# 1. The first version contained `python=3.11` and the app died at load with
#      'Packages not found: python==3.11'
#    environment.yml resolves against the Snowflake conda channel, where the
#    interpreter is NOT a selectable package. The Python version is a property of the
#    STREAMLIT object's runtime, never a dependency.
#
# 2. With no environment.yml at all, SiS fell back to its default Streamlit, which is
#    older than 1.29 and raised
#      TypeError: FormMixin.form() got an unexpected keyword argument 'border'
#    Local validation could not catch this: the dev machine runs 1.51.0, so every
#    modern API resolved fine there and failed only once deployed.
#
# 3. The pin was first written PEP 440 style as `streamlit==1.52.1` and the app failed
#    with
#      Anaconda dependency versions must be characters, numbers of one of [.+!] .
#      =1.52.1 does not match this spec.
#    environment.yml is a CONDA spec, so the separator is a SINGLE '='. The doubled form
#    left a stray '=' inside the version string. Use `streamlit=1.52.1`.
#
# Pinning streamlit is load-bearing, not cosmetic. STREAMLIT_PIN must stay in step with
# the APIs app/ui.py and app/main.py actually use - validate/check_streamlit_api.py
# enforces that floor offline, before a deploy.
STREAMLIT_PIN = "1.52.1"

# The SHIPPED file is deliberately minimal - no comments at all.
#
# The rules above are documented here, in the source, rather than inside the YAML,
# because the comment text would otherwise contain the literal strings `python==3.11`
# and `==`. This file has already been rejected three times for reasons that were
# opaque from the outside, and there is no upside in betting that Snowflake's spec
# parser handles comments robustly. Nothing reads the staged file but Snowflake.
ENVIRONMENT_YML = f"""name: sf_env
channels:
  - snowflake
dependencies:
  - streamlit={STREAMLIT_PIN}
  - pandas
"""

BUNDLE: list[tuple[Path, str]] = [
    (REPO / "app" / "main.py", ""),
    (REPO / "app" / "__init__.py", "app"),
    (REPO / "app" / "ui.py", "app"),
    (REPO / "app" / "snow.py", "app"),
    (REPO / "app" / "grounding.py", "app"),
    (REPO / "app" / "answering.py", "app"),
    (REPO / "app" / "contracts.py", "app"),
    (REPO / "config" / "__init__.py", "config"),
    (REPO / "config" / "app_config.py", "config"),
]


# Snowflake's Anaconda spec parser accepts only these characters in a version.
# Reproduced from the error it raises:
#   "Anaconda dependency versions must be characters, numbers of one of [.+!]"
CONDA_VERSION_RE = re.compile(r"^[A-Za-z0-9.+!]+$")


def validate_environment_yml(con, yml: str) -> None:
    """Fail before deploying if environment.yml would be rejected by Snowflake.

    Three separate deploys were burned on this one file - a python pin, a missing pin,
    then a PEP 440 '=='. Each time the only feedback was the app failing to start in
    Snowsight, which is a slow and opaque way to learn a spec rule. This checks the two
    rules that actually bite, plus whether the pinned build genuinely exists in the
    channel, so a bad spec is caught here rather than by the user.
    """
    problems: list[str] = []
    deps: list[tuple[str, str | None]] = []

    in_deps = False
    for raw in yml.splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        if line.strip() == "dependencies:":
            in_deps = True
            continue
        if in_deps and not line.startswith((" ", "\t", "-")):
            in_deps = False
        if not in_deps or not line.strip().startswith("- "):
            continue

        spec = line.strip()[2:].strip()
        if spec.lower().startswith("python"):
            problems.append(
                f"'{spec}': do not pin python. The interpreter is not a package in the "
                f"Snowflake channel; pinning it fails with 'Packages not found'."
            )
            continue
        if "==" in spec:
            problems.append(
                f"'{spec}': environment.yml is a CONDA spec - use a single '=', not "
                f"'=='. The doubled form leaves a stray '=' in the version and fails "
                f"with 'Anaconda dependency versions must be characters, numbers of "
                f"one of [.+!]'."
            )
            continue
        if "=" in spec:
            name, _, version = spec.partition("=")
            if not CONDA_VERSION_RE.match(version):
                problems.append(
                    f"'{spec}': version '{version}' has characters Snowflake rejects; "
                    f"only letters, digits and [.+!] are allowed."
                )
                continue
            deps.append((name.strip(), version.strip()))
        else:
            deps.append((spec, None))

    if problems:
        raise RuntimeError(
            "environment.yml would be rejected by Snowflake:\n  - "
            + "\n  - ".join(problems)
        )

    # Server-side existence check. A syntactically valid pin to a build that is not in
    # the channel still breaks the app at load time.
    for name, version in deps:
        if version is None:
            rows = L.execute(
                con,
                f"""SELECT COUNT(*) FROM {AC.DATABASE}.INFORMATION_SCHEMA.PACKAGES
                    WHERE language = 'python' AND package_name = '{name}'""",
            )
            if not rows[0][0]:
                raise RuntimeError(
                    f"package '{name}' is not available in the Snowflake conda channel"
                )
            print(f"  spec ok · {name} (unpinned, available)")
            continue

        rows = L.execute(
            con,
            f"""SELECT COUNT(*) FROM {AC.DATABASE}.INFORMATION_SCHEMA.PACKAGES
                WHERE language = 'python' AND package_name = '{name}'
                  AND version = '{version}'""",
        )
        if not rows[0][0]:
            available = L.execute(
                con,
                f"""SELECT DISTINCT version
                    FROM {AC.DATABASE}.INFORMATION_SCHEMA.PACKAGES
                    WHERE language = 'python' AND package_name = '{name}'
                    ORDER BY version DESC LIMIT 8""",
            )
            raise RuntimeError(
                f"{name}=={version} is not in the Snowflake channel. Recent versions: "
                f"{[r[0] for r in available]}"
            )
        print(f"  spec ok · {name}={version} exists in the channel")


def main() -> int:
    con = L.connect()
    try:
        db, schema = AC.DATABASE, AC.SERVING_SCHEMA
        stage_fqn = f"{db}.{schema}.{STAGE}"

        print("=" * 78)
        print("DEPLOY TO STREAMLIT IN SNOWFLAKE")
        print("=" * 78)

        L.execute(
            con,
            f"CREATE STAGE IF NOT EXISTS {stage_fqn} "
            f"DIRECTORY = (ENABLE = TRUE) "
            f"COMMENT = 'Source for the PA Insight Copilot Streamlit app'",
        )
        print(f"  stage ready · {stage_fqn}")

        # Rewrite environment.yml every deploy so the pin on the stage always matches
        # STREAMLIT_PIN in this file. A stale file here is how the app silently ran on
        # the wrong Streamlit version.
        env_path = REPO / ".cache" / "environment.yml"
        env_path.parent.mkdir(parents=True, exist_ok=True)
        env_path.write_text(ENVIRONMENT_YML, encoding="utf-8")

        # Validate the spec against Snowflake BEFORE uploading anything.
        validate_environment_yml(con, ENVIRONMENT_YML)

        for local, subdir in BUNDLE + [(env_path, "")]:
            if not local.exists():
                raise FileNotFoundError(f"bundle file missing: {local}")
            target = f"@{stage_fqn}/{subdir}" if subdir else f"@{stage_fqn}"
            L.execute(
                con,
                f"PUT 'file://{local.as_posix()}' {target} "
                f"OVERWRITE = TRUE AUTO_COMPRESS = FALSE",
            )
            print(f"  uploaded {subdir + '/' if subdir else ''}{local.name}")

        staged = L.execute(con, f"LIST @{stage_fqn}")
        names = [str(r[0]).split("/")[-1] for r in staged]
        missing = [
            local.name for local, _ in BUNDLE + [(env_path, "")]
            if local.name not in names
        ]
        if missing:
            raise RuntimeError(f"files did not reach the stage: {missing}")
        print(f"  stage holds {len(names)} files, streamlit pinned to {STREAMLIT_PIN}")

        L.execute(
            con,
            f"""CREATE OR REPLACE STREAMLIT {db}.{schema}.{STREAMLIT_NAME}
                ROOT_LOCATION = '@{stage_fqn}'
                MAIN_FILE = 'main.py'
                QUERY_WAREHOUSE = {WAREHOUSE}
                TITLE = '{AC.APP_TITLE}'
                COMMENT = 'Prior-authorization insight copilot. Reads only the governed SERVING layer. Provides cited evidence and never recommends a determination.'""",
        )
        print(f"  created STREAMLIT {db}.{schema}.{STREAMLIT_NAME}")

        L.execute(
            con,
            f"GRANT USAGE ON STREAMLIT {db}.{schema}.{STREAMLIT_NAME} "
            f"TO ROLE M360_APP_ROLE",
        )
        L.execute(con, f"GRANT READ ON STAGE {stage_fqn} TO ROLE M360_APP_ROLE")
        print("  granted USAGE to M360_APP_ROLE")

        rows = L.execute(con, f"SHOW STREAMLITS LIKE '{STREAMLIT_NAME}' IN SCHEMA {db}.{schema}")
        if not rows:
            raise RuntimeError("the STREAMLIT object was not created")

        url_id = None
        try:
            desc = L.execute(con, f"DESCRIBE STREAMLIT {db}.{schema}.{STREAMLIT_NAME}")
            for row in desc:
                if str(row[0]).lower() == "url_id":
                    url_id = row[1]
        except Exception:  # noqa: BLE001
            pass

        print()
        print("DEPLOYED")
        print(f"  Open it in Snowsight under Projects » Streamlit » {AC.APP_TITLE}")
        if url_id:
            print(f"  url_id · {url_id}")
        print()
        print("  Pass a member from a host application with:")
        print(f"    ...#/streamlit-apps/{db}.{schema}.{STREAMLIT_NAME}"
              f"?streamlit-member_id=MBR-12345678")
        print()
        print("  NOTE: this surface cannot be iframed. Snowflake sets")
        print("  X-Frame-Options: DENY on every hosted endpoint and the CSP is not")
        print("  configurable. For embedding, run the self-hosted mode:")
        print("    python app/embed.py --allow https://your-portal.example.com")
        print("    cortex secret run --map snowflake_pat=SNOWFLAKE_PAT -- python app/serve.py")
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())
