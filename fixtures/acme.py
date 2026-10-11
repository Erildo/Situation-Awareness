"""The fake Acme company (30 people). Source of truth for fixtures/{slack,linear,github}.json, which are
shaped exactly like the real API payloads so they go through the real loaders. Run: python3 fixtures/acme.py"""
import json
from pathlib import Path

OUT = Path(__file__).parent

# name, team, github_login, github email source ('org' | 'public:<email>' | None), linear?
P = [
    ("Priya Shah", "INFRA", "priya-s", "org", True),
    ("Dana Kowalski", "INFRA", "dana-k", "org", True),
    ("Marcus Webb", "INFRA", "mwebb", "public:marcus.webb@gmail.com", True),  # personal email: can't auto-link
    ("Yuki Tanaka", "INFRA", "ytanaka", "org", True),
    ("Omar Farouk", "INFRA", "ofarouk", None, True),  # no email on GitHub: needs `sa link`
    ("Lena Fischer", "INFRA", "lfischer", "org", True),
    ("Sam Lee", "APP", "samlee", "org", True),
    ("Samira Haddad", "APP", "shaddad", "org", True),
    ("Rashene Kumar", "APP", "rkumar-dev", "org", True),
    ("Rashid Ali", "APP", "rashid-a", "org", True),
    ("Chloe Martin", "APP", "chloem", "org", True),
    ("Diego Alvarez", "APP", "dalvarez", "public:diego@acme.test", True),
    ("Grace Kim", "APP", "gkim", "org", True),
    ("Tom Becker", "APP", "tbecker", None, True),  # left the company
    ("Aisha Bello", "PAY", "abello", "org", True),
    ("Ben Carter", "PAY", "bcarter", "org", True),
    ("Nina Petrova", "PAY", "npetrova", "org", True),
    ("Leo Rossi", "PAY", "lrossi", "org", True),
    ("Fatima Zahra", "PAY", "fzahra", "org", True),
    ("Jonas Berg", "PAY", "jberg", "org", True),
    ("Mei Chen", "DATA", "meichen", "org", True),
    ("Arjun Nair", "DATA", "anair", "org", True),
    ("Sofia Costa", "DATA", "scosta", "org", True),
    ("Ivan Petrov", "DATA", "ipetrov", "org", True),
    ("Hannah Wright", "LEAD", None, None, False),  # CEO, no Linear/GitHub
    ("Kenji Mori", "LEAD", "kmori", "org", True),  # CTO, in every Linear team
    ("Olivia Brooks", "LEAD", "obrooks", "org", True),
    ("Ravi Menon", "LEAD", "rmenon", "org", True),
    ("Elena Garcia", "LEAD", None, None, True),
    ("Paul Dubois", "LEAD", None, None, False),
]
LEFT = {"Tom Becker"}


def first(n):
    return n.split()[0].lower()


email = {n: f"{first(n)}@acme.test" for n, *_ in P}
slack_id = {n: f"U_{first(n).upper()}" for n, *_ in P}
lin = {n: {"id": f"lin-{first(n)}", "name": n, "email": email[n]} for n, *_, has in P if has}
team_of = {n: t for n, t, *_ in P}
gh = {n: g for n, _, g, *_ in P if g}

# ---- slack ----
def members(*teams, extra=()):
    return [slack_id[n] for n, t, *_ in P if (t in teams or n in extra) and n not in LEFT]

slack = {
    "users": [{"id": slack_id[n], "name": n, "email": email[n], "deleted": n in LEFT} for n, *_ in P],
    "channels": [
        {"id": "C_GENERAL", "name": "general", "members": members("INFRA", "APP", "PAY", "DATA", "LEAD")},
        {"id": "C_INFRA", "name": "infra", "members": members("INFRA", extra=("Kenji Mori", "Olivia Brooks", "Aisha Bello"))},
        {"id": "C_APP", "name": "app", "members": members("APP", extra=("Kenji Mori", "Ravi Menon", "Elena Garcia"))},
        {"id": "C_PAYMENTS", "name": "payments", "members": members("PAY", extra=("Kenji Mori", "Hannah Wright"))},
        {"id": "C_DATA", "name": "data", "members": members("DATA", extra=("Hannah Wright", "Ravi Menon"))},
        {"id": "C_INCIDENTS", "name": "incidents", "members": members("INFRA", extra=("Kenji Mori", "Paul Dubois", "Sam Lee"))},
    ],
    # Fixture only: used to seed a dev workspace (M2) and as proposer input (M3). Never loaded into the ledger.
    "threads": [
        {"channel": "C_INFRA", "ts": "1791536400.000100", "messages": [
            ["Priya Shah", "PG16 cutover plan for INFRA-123: dry run went clean on staging, PR #812 is in review"],
            ["Kenji Mori", "nice. when do we cut over prod?"],
            ["Priya Shah", "Decided: prod cutover Friday Oct 16, 22:00 UTC, 30 min read-only window"],
            ["Kenji Mori", "approved :white_check_mark:"]]},
        {"channel": "C_INFRA", "ts": "1791626400.000200", "messages": [
            ["Ben Carter", "PCI audit needs proof the DB creds rotate after the migration"],
            ["Priya Shah", "I'll rotate the database credentials (INFRA-130) by Oct 19, right after cutover"]]},
        {"channel": "C_PAYMENTS", "ts": "1791630000.000300", "messages": [
            ["Aisha Bello", "Stripe upgrade PAY-40 is blocked on INFRA-123: the new webhook store needs PG16 json_table"],
            ["Jonas Berg", "so nothing ships on payments until the cutover?"],
            ["Aisha Bello", "correct, PAY-40 waits for Priya's cutover on the 16th"]]},
        {"channel": "C_APP", "ts": "1791633600.000400", "messages": [
            ["Rashene Kumar", "passkeys (APP-97) PR #821 is ready for review"],
            ["Ravi Menon", "rollout plan?"],
            ["Rashene Kumar", "Decided with Ravi: behind a feature flag, 10% of users first, full rollout after 2 weeks with no auth errors"]]},
        {"channel": "C_APP", "ts": "1791637200.000500", "messages": [
            ["Sam Lee", "APP-88 root cause: N+1 queries in price lookup. PR #790 caches them, merged."],
            ["Sam Lee", "checkout p95 back to 180ms from 950ms"],
            ["Grace Kim", "sounds good, thanks!"]]},
        {"channel": "C_INCIDENTS", "ts": "1791453600.000600", "messages": [
            ["Lena Fischer", "Postmortem for the Oct 8 staging outage is in INFRA-145. Root cause: CI ran migrations against staging."],
            ["Kenji Mori", "action items?"],
            ["Lena Fischer", "Omar will get CI under 10 minutes and off shared staging (INFRA-142) by Oct 31"]]},
        {"channel": "C_DATA", "ts": "1791640800.000700", "messages": [
            ["Hannah Wright", "weekly revenue dashboard looks a day off?"],
            ["Mei Chen", "yes, UTC vs local timezone bug (DATA-12). Fix by Wednesday Oct 14, PR acme/data#31"]]},
        {"channel": "C_INFRA", "ts": "1791644400.000800", "messages": [
            ["Marcus Webb", "Redis 7.4 upgrade (INFRA-131) is blocked: waiting on maintenance window approval from Olivia"],
            ["Olivia Brooks", "will look this week"]]},
        {"channel": "C_GENERAL", "ts": "1791648000.000900", "messages": [
            ["Hannah Wright", "Q4 planning offsite is Nov 3 in Lisbon. Agenda to follow."]]},
    ],
}

# What each thread establishes. Hand-seeded into the ledger until M3's proposer extracts it; then these are the
# proposer's golden answers. (kind, anchor, text, owner, due)
STATE = {
    "1791536400.000100": [("decision", "INFRA-123", "Prod cutover to Postgres 16 is Friday Oct 16, 22:00 UTC, with a 30 min read-only window", "Priya Shah", None)],
    "1791626400.000200": [("commitment", "INFRA-130", "Rotate the database credentials right after the cutover (needed for the PCI audit)", "Priya Shah", "2026-10-19")],
    "1791630000.000300": [("blocker", "PAY-40", "Stripe upgrade is blocked on INFRA-123: the new webhook store needs Postgres 16 json_table", "Aisha Bello", None)],
    "1791633600.000400": [("decision", "APP-97", "Passkeys roll out behind a feature flag: 10% of users first, full rollout after 2 weeks with no auth errors", "Rashene Kumar", None)],
    "1791453600.000600": [("commitment", "INFRA-142", "Get CI under 10 minutes and off shared staging", "Omar Farouk", "2026-10-31")],
    "1791640800.000700": [("commitment", "DATA-12", "Fix the revenue dashboard timezone bug (UTC vs local) by Wednesday Oct 14", "Mei Chen", "2026-10-14")],
    "1791644400.000800": [("blocker", "INFRA-131", "Redis 7.4 upgrade waits on maintenance window approval from Olivia", "Marcus Webb", None)],
    "1791648000.000900": [("decision", "slack:C_GENERAL", "Q4 planning offsite is Nov 3 in Lisbon", "Hannah Wright", None)],
}
for t in slack["threads"]:
    t["state"] = [{"kind": k, "anchor": a, "text": x, "owner": email[o], "due": d} for k, a, x, o, d in STATE.get(t["ts"], [])]

# ---- linear ----
TEAMS = {"INFRA": False, "APP": False, "PAY": True, "DATA": False}  # PAY is a private team


def team_members(key):
    return [lin[n] | {"active": n not in LEFT} for n in lin if team_of[n] == key or n == "Kenji Mori"]


I = [  # identifier, title, state, assignee, updatedAt
    ("INFRA-123", "Migrate primary DB to Postgres 16", "In Review", "Priya Shah", "2026-10-10T14:00:00Z"),
    ("INFRA-130", "Rotate database credentials after cutover", "Todo", "Priya Shah", "2026-10-09T09:00:00Z"),
    ("INFRA-131", "Upgrade Redis to 7.4", "Blocked", "Marcus Webb", "2026-10-10T15:00:00Z"),
    ("INFRA-140", "Split Terraform state per environment", "Backlog", "Yuki Tanaka", "2026-10-02T10:00:00Z"),
    ("INFRA-142", "Get CI under 10 minutes and off shared staging", "In Progress", "Omar Farouk", "2026-10-09T12:00:00Z"),
    ("INFRA-145", "Postmortem: Oct 8 staging outage", "Done", "Lena Fischer", "2026-10-08T20:00:00Z"),
    ("APP-88", "Checkout latency regression", "Done", "Sam Lee", "2026-10-10T16:30:00Z"),
    ("APP-91", "Dark mode for settings", "Backlog", None, "2026-10-01T08:00:00Z"),
    ("APP-95", "New onboarding flow", "In Progress", "Chloe Martin", "2026-10-07T11:00:00Z"),
    ("APP-97", "Migrate auth to passkeys", "In Review", "Rashene Kumar", "2026-10-10T13:00:00Z"),
    ("APP-99", "Fix auth token refresh race", "Todo", "Rashid Ali", "2026-10-06T09:30:00Z"),
    ("APP-102", "iOS crash on launch (v3.2)", "In Progress", "Grace Kim", "2026-10-10T09:00:00Z"),
    ("PAY-40", "Upgrade Stripe API to 2026-09", "Blocked", "Aisha Bello", "2026-10-10T17:00:00Z"),
    ("PAY-44", "Refunds for partial orders", "Todo", "Nina Petrova", "2026-10-05T10:00:00Z"),
    ("PAY-47", "PCI audit evidence collection", "In Progress", "Ben Carter", "2026-10-09T16:00:00Z"),
    ("DATA-12", "Weekly revenue dashboard off by one day", "In Progress", "Mei Chen", "2026-10-10T18:00:00Z"),
    ("DATA-15", "Move warehouse jobs to dbt", "Backlog", "Arjun Nair", "2026-10-03T10:00:00Z"),
]
linear = {
    "teams": [{"key": k, "private": p, "members": {"nodes": team_members(k)}} for k, p in TEAMS.items()],
    "issues": [{
        "identifier": i, "title": t, "url": f"https://linear.app/acme/issue/{i}", "updatedAt": u,
        "state": {"name": s}, "team": {"key": i.split("-")[0], "private": TEAMS[i.split("-")[0]]},
        "assignee": {"id": lin[a]["id"], "name": a, "email": email[a]} if a else None,
    } for i, t, s, a, u in I],
}

# ---- github ----
G = {  # repo -> [(number, title, body, state, merged_at, author, assignees, updated_at, is_pr)]
    "acme/platform": [
        (812, "Postgres 16 cutover script", "Fixes INFRA-123", "open", None, "Priya Shah", [], "2026-10-10T14:00:00Z", True),
        (790, "Cache price lookups", "Fixes APP-88", "closed", "2026-10-10T16:00:00Z", "Sam Lee", [], "2026-10-10T16:00:00Z", True),
        (820, "Flaky test in billing suite", None, "open", None, "Sam Lee", ["Dana Kowalski"], "2026-10-10T18:00:00Z", False),
        (805, "Bump ruff", "", "closed", "2026-10-08T10:00:00Z", "Dana Kowalski", [], "2026-10-08T10:00:00Z", True),
        (815, "Redis 7.4 config", "Part of INFRA-131", "open", None, "Marcus Webb", [], "2026-10-10T15:00:00Z", True),
        (818, "Parallelize test shards", "INFRA-142", "open", None, "Omar Farouk", [], "2026-10-09T12:00:00Z", True),
        (821, "Passkeys behind feature flag", "Implements APP-97", "open", None, "Rashene Kumar", [], "2026-10-10T13:00:00Z", True),
        (823, "Mutex around token refresh", "Fixes APP-99", "open", None, "Rashid Ali", [], "2026-10-06T09:30:00Z", True),
        (824, "Onboarding step 2", "APP-95", "closed", "2026-10-07T11:00:00Z", "Chloe Martin", [], "2026-10-07T11:00:00Z", True),
        (826, "Fix iOS launch crash", "Fixes APP-102", "open", None, "Grace Kim", [], "2026-10-10T09:00:00Z", True),
        (800, "Remove old login page", "", "closed", "2026-10-02T15:00:00Z", "Tom Becker", [], "2026-10-02T15:00:00Z", True),
        (827, "Add OpenTelemetry tracing", "", "open", None, "Lena Fischer", [], "2026-10-09T17:00:00Z", True),
        (828, "Local setup broken on M-series Macs", "make dev fails at the arm64 postgres image", "open", None, "Diego Alvarez", [], "2026-10-09T08:00:00Z", False),
    ],
    "acme/payments": [
        (210, "Stripe API 2026-09 upgrade", "PAY-40", "open", None, "Aisha Bello", [], "2026-10-10T17:00:00Z", True),
        (212, "Partial refunds", "PAY-44", "open", None, "Nina Petrova", [], "2026-10-05T10:00:00Z", True),
        (205, "Audit log export", "For PAY-47", "closed", "2026-10-09T16:00:00Z", "Ben Carter", [], "2026-10-09T16:00:00Z", True),
        (214, "Webhook retries create duplicate charges", None, "open", None, "Leo Rossi", ["Jonas Berg"], "2026-10-08T12:00:00Z", False),
    ],
    "acme/data": [
        (31, "Fix revenue dashboard timezone", "Fixes DATA-12", "open", None, "Mei Chen", [], "2026-10-10T18:00:00Z", True),
    ],
}


def gh_item(repo, num, title, body, state, merged, author, assignees, updated, is_pr):
    kind = "pull" if is_pr else "issues"
    item = {"number": num, "title": title, "body": body, "state": state,
            "html_url": f"https://github.com/{repo}/{kind}/{num}", "updated_at": updated,
            "user": {"login": gh[author]}, "assignees": [{"login": gh[a]} for a in assignees]}
    if is_pr:
        item["pull_request"] = {"merged_at": merged}
    return item


emails = {}
for n, _, login, src, _ in P:
    if login and src:
        emails[login] = [email[n]] if src == "org" else [src.split(":", 1)[1]]

# Repo collaborators (GET /repos/{repo}/collaborators): who can read each repo, beyond its authors.
REPO_TEAMS = {"acme/platform": ("INFRA", "APP", "LEAD"), "acme/payments": ("PAY",), "acme/data": ("DATA", "LEAD")}
collaborators = {r: [gh[n] for n, t, *_ in P if n in gh and (t in teams or n == "Kenji Mori")] for r, teams in REPO_TEAMS.items()}

github = {"repos": {r: [gh_item(r, *x) for x in items] for r, items in G.items()}, "emails": emails,
          "collaborators": collaborators}

for name, data in [("slack", slack), ("linear", linear), ("github", github)]:
    (OUT / f"{name}.json").write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n")
print("wrote", OUT)
