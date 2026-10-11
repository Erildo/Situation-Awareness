"""Golden eval over the seeded Acme company. Prints a score per category; returns the number of failures.
`ask` calls Claude (costs money, needs credentials); without credentials it's reported as pending, not failed."""
import json
from datetime import UTC, datetime
from pathlib import Path

import anthropic

from . import db, identity, twin

FIXTURES = Path(__file__).parent.parent / "fixtures"
GOLDEN = FIXTURES / "golden.json"
SLACK_BASE = "https://acme.slack.com/"
NOW = datetime(2026, 10, 12, 9, 0, tzinfo=UTC)  # the Monday after the fixture's last activity
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


class FixtureChat:
    """Stands in for the Slack adapter's live fetch_thread, reading the fixture threads."""
    def __init__(self):
        slack = json.loads((FIXTURES / "slack.json").read_text())
        ids = {u["name"]: u["id"] for u in slack["users"]}
        self.threads = {(t["channel"], t["ts"]): [{"author": ids[a], "text": x} for a, x in t["messages"]]
                        for t in slack["threads"]}

    def fetch_thread(self, channel, ts):
        return self.threads.get((channel, ts), [])


_client = None


def check_ask(conn, c):
    global _client
    _client = _client or anthropic.Anthropic()
    r = twin.respond(conn, _by_email(conn, c["asker"]), _by_email(conn, c["owner"]), c["q"],
                     now=NOW, chat=FixtureChat(), client=_client)
    text = r["text"].lower()
    got = {"text": r["text"], "level": r["level"], "sources": r["sources"], "escalated": bool(r["escalation_id"])}
    kind = c["kind"]
    if kind == "escalate":
        ok = got["escalated"]
    elif kind == "unavailable":
        ok = "left" in text
    elif kind == "denied":  # PCI audit lives in the private PAY team: nothing about it may come back
        ok = "pay-47" not in text and "in progress" not in text and not any("PAY-47" in u for u in r["sources"])
    else:
        ok = (not got["escalated"] and all(e.lower() in text for e in c.get("expect", []))
              and all(any(s in u for u in r["sources"]) for s in c.get("sources", []))
              and (r["level"] == 3) == (kind == "likely_view"))
    return ok, got


CHECKS = {"identity": check_identity, "reference": check_reference, "permission": check_permission, "ask": check_ask}


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
        try:
            bad = [(c, got) for c in cases for ok, got in [check(conn, c)] if not ok]
        except (anthropic.AuthenticationError, TypeError) as e:  # TypeError: SDK found no credentials
            if "auth" not in str(e).lower() and not isinstance(e, anthropic.AuthenticationError):
                raise
            print(f"{kind:<11} {len(cases):>3} cases  pending (no Anthropic credentials: set ANTHROPIC_API_KEY)")
            continue
        print(f"{kind:<11} {len(cases) - len(bad):>3}/{len(cases)} pass")
        for c, got in bad:
            print(f"   FAIL {json.dumps({k: v for k, v in c.items() if k != 'why'})} got={got!r}  ({c.get('why', '')})")
        failures += len(bad)
    return failures
