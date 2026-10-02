"""
Unit test for the SQL comment stripper.

It sits in front of every SQL deploy, so a bug here breaks the whole SQL layer.
The two cases that matter are a semicolon inside a comment (the bug that motivated
it) and a `--` inside a string literal (the bug a naive fix would introduce).

Run:  python validate/test_sql_split.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from load.snowflake_loader import split_sql_statements  # noqa: E402


def statements(sql: str) -> list[str]:
    return [" ".join(s.split()) for s in split_sql_statements(sql)]


CASES: list[tuple[str, str, list[str]]] = [
    (
        "semicolon inside a line comment must not split",
        "-- grants READ; it cannot see BRONZE.\nCREATE SCHEMA S;",
        ["CREATE SCHEMA S"],
    ),
    (
        "semicolon inside a string literal must not split",
        "CREATE TABLE T (c INT COMMENT 'reconciled; not inferred');",
        ["CREATE TABLE T (c INT COMMENT 'reconciled; not inferred')"],
    ),
    (
        "double dash inside a string literal must survive",
        "CREATE TABLE T (c INT COMMENT 'see -- note; here');",
        ["CREATE TABLE T (c INT COMMENT 'see -- note; here')"],
    ),
    (
        "escaped '' inside a literal does not end the literal",
        "CREATE TABLE T (c INT COMMENT 'row''s hash; stable');",
        ["CREATE TABLE T (c INT COMMENT 'row''s hash; stable')"],
    ),
    (
        "trailing comment after a statement is removed",
        "SELECT 1; -- pick one\nSELECT 2;",
        ["SELECT 1", "SELECT 2"],
    ),
    (
        "comment-only script yields nothing",
        "-- just a note\n-- and another;\n",
        [],
    ),
    (
        "block comment with a semicolon is ignored",
        "/* note; here */ SELECT 1;",
        ["SELECT 1"],
    ),
    (
        "multi-line statement with interior comments",
        "CREATE VIEW V AS\n-- explain; why\nSELECT a FROM t;",
        ["CREATE VIEW V AS SELECT a FROM t"],
    ),
    (
        "double-quoted identifier containing a dash pair",
        'SELECT "we--ird" FROM t;',
        ['SELECT "we--ird" FROM t'],
    ),
    (
        "final statement without a trailing semicolon is kept",
        "SELECT 1;\nSELECT 2",
        ["SELECT 1", "SELECT 2"],
    ),
]


def main() -> int:
    failures = 0
    for name, sql, expected in CASES:
        got = statements(sql)
        ok = got == expected
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
        if not ok:
            failures += 1
            print(f"       expected: {expected}")
            print(f"       got     : {got}")

    # Every real SQL file must split into statements that each START with a SQL
    # verb. A fragment beginning with lowercase prose is the signature of the
    # comment/literal splitting bug this module exists to prevent.
    VERBS = {
        "CREATE", "DROP", "ALTER", "GRANT", "REVOKE", "USE", "TRUNCATE",
        "INSERT", "SELECT", "COPY", "PUT", "REMOVE", "COMMENT", "SET", "LIST",
        "DESCRIBE", "SHOW", "CALL", "MERGE", "DELETE", "UPDATE",
    }
    print()
    sql_dir = Path(__file__).resolve().parent.parent / "sql"
    for path in sorted(sql_dir.glob("*.sql")):
        stmts = split_sql_statements(path.read_text(encoding="utf-8"))
        suspect = [s for s in stmts if s.split()[0].upper() not in VERBS]
        flag = "ok  " if not suspect else "FAIL"
        print(f"  {flag} {path.name:28s} {len(stmts):3d} statements")
        if suspect:
            failures += 1
            for s in suspect[:3]:
                print(f"       suspect fragment: {' '.join(s.split())[:90]}")

    print()
    print("ALL PASSED" if not failures else f"{failures} FAILURE(S)")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
