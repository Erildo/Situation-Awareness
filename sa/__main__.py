"""python -m sa {sync|seed|status|link|eval|slack}"""
import argparse
import difflib
import json
import os
import sys
from pathlib import Path

from . import db, evals, sync

FIXTURES = Path(__file__).parent.parent / "fixtures"


def cmd_sync(conn, args):
    if key := os.environ.get("LINEAR_API_KEY"):
        print("linear members:", sync.load_linear_teams(conn, sync.fetch_linear_teams(key)))
        print("linear issues:", sync.load_linear(conn, sync.fetch_linear(key)))
    token = os.environ.get("GITHUB_TOKEN")
    for repo in args.repos:
        print(f"github {repo}:", sync.load_github(conn, repo, sync.fetch_github(repo, token)))
    if token and args.repos:
        for org in {r.split("/")[0] for r in args.repos}:
            emails = sync.fetch_github_emails(org, sync.unlinked_github_logins(conn), token)
            print(f"github {org} linked by email:", sync.load_github_emails(conn, emails))


def seed(conn):
    """Fake Acme company, through the same loaders as real data, in the same order as a real install."""
    slack = json.loads((FIXTURES / "slack.json").read_text())
    linear = json.loads((FIXTURES / "linear.json").read_text())
    github = json.loads((FIXTURES / "github.json").read_text())
    sync.load_slack_users(conn, slack["users"])
    sync.load_linear_teams(conn, linear["teams"])
    sync.load_linear(conn, linear["issues"])
    for repo, items in github["repos"].items():
        sync.load_github(conn, repo, items)
    sync.load_github_emails(conn, github["emails"])
    sync.load_slack_channels(conn, slack["channels"])


def cmd_seed(conn, args):
    seed(conn)
    print("seeded:", conn.execute("select count(*) from person").fetchone()[0], "people,",
          conn.execute("select count(*) from workstream").fetchone()[0], "workstreams")


def cmd_status(conn, args):
    rows = conn.execute("""select w.anchor_id, w.status, w.title, p.name, count(e.id)
                           from workstream w left join person p on p.id = w.owner_id
                           left join evidence_ref e on e.workstream_id = w.id
                           group by w.id, p.name order by w.updated_at desc""").fetchall()
    for anchor, status, title, owner, n in rows:
        print(f"{anchor:<22} {status or '':<12} {(owner or '-'):<14} {n} refs  {title}")


def cmd_link(conn, args):
    """Human-confirmed account linking, for accounts email can't link (no or personal email on GitHub)."""
    if not args.email:
        names = {}
        for name, email in conn.execute("select name, email from person where email is not null"):
            parts = name.lower().split()
            first, last = parts[0], parts[-1]  # login habits: tbecker, tomb, tom.becker
            for v in (name.lower(), email.split("@")[0], first[:1] + last, first + last[:1], f"{first}.{last}"):
                names[v] = email
        for login in sync.unlinked_github_logins(conn):
            guess = difflib.get_close_matches(login.lower(), names, n=3, cutoff=0.4)
            hint = ", ".join(dict.fromkeys(names[g] for g in guess)) or "no guess"
            print(f"github:{login:<16} maybe {hint}")
        print("confirm with: sa link <email> github:<login>")
        return
    platform, ext = args.account.split(":", 1)
    if not conn.execute("select 1 from person where email = %s", (args.email.lower(),)).fetchone():
        sys.exit(f"no person with email {args.email}")
    pid = db.person(conn, ext, email=args.email, **{evals.COLUMN[platform]: ext})
    print("linked", args.account, "->", args.email, f"(person {pid})")


def cmd_eval(conn, args):
    """Golden eval on a throwaway copy of the seeded company. Point DATABASE_URL at a scratch DB."""
    with conn.transaction(force_rollback=True):
        seed(conn)
        failures = evals.run(conn)
    sys.exit(1 if failures else 0)


def cmd_slack(conn, args):
    from .slack import Slack

    s = Slack(os.environ["SLACK_BOT_TOKEN"], os.environ["SLACK_APP_TOKEN"])

    def pid(user):
        if not conn.execute("select 1 from person where slack_id = %s", (user,)).fetchone():
            name, email = s.profile(user)
            sync.load_slack_users(conn, [{"id": user, "name": name, "email": email, "deleted": False}])
        return conn.execute("select id from person where slack_id = %s", (user,)).fetchone()[0]

    def membership(channel, user, joined):
        (db.grant if joined else db.revoke)(conn, pid(user), f"slack:{channel}")

    sync.load_slack_users(conn, s.users())
    sync.load_slack_channels(conn, [{"id": cid, "name": name, "members": s.members(cid)} for cid, name in s.channels()])
    s.on_membership(membership)
    # ponytail: M1 only keeps membership fresh; M2 plugs the twin responder in here
    s.on_message(lambda m: membership(m["channel"], m["author"], True))
    print("slack: connected")
    s.run()


def main():
    p = argparse.ArgumentParser(prog="sa")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("sync", help="pull Linear (LINEAR_API_KEY) and GitHub repos (GITHUB_TOKEN)").add_argument("repos", nargs="*")
    sub.add_parser("seed", help="load the fake Acme company")
    sub.add_parser("status", help="list workstreams")
    link = sub.add_parser("link", help="no args: suggest links for unlinked accounts; else confirm one")
    link.add_argument("email", nargs="?")
    link.add_argument("account", nargs="?", help="github:<login> | slack:<id> | linear:<id> | teams:<id>")
    sub.add_parser("eval", help="golden eval against the seeded company (use a scratch DATABASE_URL)")
    sub.add_parser("slack", help="run the Slack adapter (SLACK_BOT_TOKEN, SLACK_APP_TOKEN)")
    args = p.parse_args()
    globals()[f"cmd_{args.cmd}"](db.connect(), args)


if __name__ == "__main__":
    main()
