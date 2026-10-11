"""Golden eval over the seeded Acme company. Prints a score per category; returns the number of failures.
Categories without an implementation yet (ask -> M2) are reported as pending, not failed."""
import json
from pathlib import Path

from . import db, identity

GOLDEN = Path(__file__).parent.parent / "fixtures" / "golden.json"
COLUMN = {"github": "github_login", "linear": "linear_id", "slack": "slack_id", "teams": "teams_id"}


def _by_email(conn, email):
    return conn.execute("select id from person where email = %s", (email,)).fetchone()[0]


def check_identity(conn, c):
    platform, ext = c["account"].split(":", 1)
    row = conn.execute(f"select email from person where {COLUMN[platform]} = %s", (ext,)).fetchone()
    got = row[0] if row else None
    return got == c["expect"], got


def check_reference(conn, c):
    got = sorted(p["email"] for p in identity.resolve(conn, c["ref"], _by_email(conn, c["asker"])))
    return got == sorted(c["expect"]), got


def check_permission(conn, c):
    scope = conn.execute("select scope from evidence_ref where url = %s", (c["evidence"],)).fetchone()[0]
    got = scope in db.shared_scopes(conn, _by_email(conn, c["asker"]), _by_email(conn, c["owner"]))
    return got == c["expect"], got


CHECKS = {"identity": check_identity, "reference": check_reference, "permission": check_permission}


def run(conn):
    golden = json.loads(GOLDEN.read_text())
    failures = 0
    for kind, cases in golden.items():
        if kind.startswith("_"):
            continue
        check = CHECKS.get(kind)
        if not check:
            print(f"{kind:<11} {len(cases):>3} cases  pending")
            continue
        bad = [(c, got) for c in cases for ok, got in [check(conn, c)] if not ok]
        print(f"{kind:<11} {len(cases) - len(bad):>3}/{len(cases)} pass")
        for c, got in bad:
            print(f"   FAIL {json.dumps({k: v for k, v in c.items() if k != 'why'})} got={got!r}  ({c.get('why', '')})")
        failures += len(bad)
    return failures
