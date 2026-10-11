"""Sources -> ledger. Every loader takes plain API-shaped data, so real syncs and the fake Acme seed share them.
Order matters: Slack directory, Linear, GitHub, GitHub emails, Slack channels (see __main__.cmd_seed)."""
import re

import httpx

from . import db

LINEAR_KEY = re.compile(r"\b[A-Z][A-Z0-9]{1,9}-\d+\b")


def _person(conn, name, platform_key, platform_id, email=None):
    """Link on email when it agrees; on a conflict keep the account unlinked and say so."""
    try:
        return db.person(conn, name, email=email, **{platform_key: platform_id})
    except db.IdentityConflict as e:
        print(f"identity conflict, not linking {platform_key}={platform_id}: {e}")
        return db.person(conn, name, **{platform_key: platform_id})


# ---- Linear ----

def _linear(api_key, query, path, variables=None):
    after = None
    while True:
        r = httpx.post("https://api.linear.app/graphql", headers={"Authorization": api_key},
                       json={"query": query, "variables": {**(variables or {}), "after": after}}, timeout=30)
        r.raise_for_status()
        page = r.json()["data"][path]
        yield from page["nodes"]
        if not page["pageInfo"]["hasNextPage"]:
            return
        after = page["pageInfo"]["endCursor"]


def fetch_linear_teams(api_key):
    # ponytail: first 250 members per team; paginate members when a team outgrows that
    return _linear(api_key, """query($after: String) { teams(first: 50, after: $after) {
      nodes { key private members(first: 250) { nodes { id name email active } } }
      pageInfo { hasNextPage endCursor } } }""", "teams")


def fetch_linear(api_key):
    return _linear(api_key, """query($after: String) { issues(first: 100, after: $after) {
      nodes { identifier title url updatedAt state { name } team { key private } assignee { id name email } }
      pageInfo { hasNextPage endCursor } } }""", "issues")


def _linear_scope(team):
    # Public Linear teams are visible to everyone in the workspace; private ones only to members.
    return f"linear:{team['key']}" if team["private"] else "linear"


def load_linear_teams(conn, teams):
    n = 0
    for t in teams:
        for m in t["members"]["nodes"]:
            p = _person(conn, m["name"], "linear_id", m["id"], m.get("email"))
            if m.get("active", True):
                db.grant(conn, p, "linear")  # every Linear user sees public teams
                db.grant(conn, p, _linear_scope(t))
            else:
                db.revoke(conn, p, "linear")
                db.revoke(conn, p, _linear_scope(t))
            n += 1
    return n


def load_linear(conn, issues):
    n = 0
    for i in issues:
        scope = _linear_scope(i["team"])
        owner = None
        if a := i["assignee"]:
            owner = _person(conn, a["name"], "linear_id", a["id"], a.get("email"))
        ws = db.workstream(conn, "linear", i["identifier"], i["title"], i["state"]["name"],
                           i["url"], scope, owner, i["updatedAt"])
        db.evidence(conn, i["url"], "linear", scope, i["title"], i["updatedAt"], ws)
        n += 1
    return n


# ---- GitHub ----

def fetch_github(repo, token):
    url = f"https://api.github.com/repos/{repo}/issues?state=all&per_page=100"
    headers = {"Accept": "application/vnd.github+json", **({"Authorization": f"Bearer {token}"} if token else {})}
    while url:
        r = httpx.get(url, headers=headers, timeout=30)
        r.raise_for_status()
        yield from r.json()  # issues and PRs both come from this endpoint
        url = r.links.get("next", {}).get("url")


def fetch_github_emails(org, logins, token):
    """{login: [emails]}: public email (GitHub verifies it) + emails on the org's verified domains."""
    out = {}
    logins = list(logins)
    for i in range(0, len(logins), 50):
        batch = logins[i:i + 50]
        fields = " ".join(f'u{j}: user(login: "{login}") {{ login email organizationVerifiedDomainEmails(login: "{org}") }}'
                          for j, login in enumerate(batch) if re.fullmatch(r"[\w-]+", login))
        r = httpx.post("https://api.github.com/graphql", headers={"Authorization": f"Bearer {token}"},
                       json={"query": f"{{ {fields} }}"}, timeout=30)
        r.raise_for_status()
        for u in (r.json().get("data") or {}).values():
            if u:
                out[u["login"]] = [e for e in [u["email"], *u["organizationVerifiedDomainEmails"]] if e]
    return out


def load_github(conn, repo, items):
    scope = f"github:{repo}"
    n = 0
    for i in items:
        is_pr = "pull_request" in i
        people = [i["user"], *(i.get("assignees") or [])]
        ids = [db.person(conn, u["login"], github_login=u["login"]) for u in people if u]
        for pid in ids:
            # ponytail: authors/assignees get repo scope; repo collaborator list when ACL needs more
            db.grant(conn, pid, scope)
        keys = LINEAR_KEY.findall(f"{i['title']} {i.get('body') or ''}")
        linked = conn.execute(
            "select id from workstream where anchor_kind = 'linear' and anchor_id = any(%s) order by id",
            (keys,)).fetchone()
        merged_at = i["pull_request"].get("merged_at") if is_pr else None
        status = "merged" if merged_at else i["state"]
        if linked:  # PR/issue that references a Linear issue is evidence for it, not a new workstream
            ws = linked[0]
        else:
            owner = ids[1] if len(ids) > 1 else ids[0] if ids else None  # assignee over author
            ws = db.workstream(conn, "github", f"{repo}#{i['number']}", i["title"], status,
                               i["html_url"], scope, owner, i["updated_at"])
        db.evidence(conn, i["html_url"], "github", scope, i["title"], i["updated_at"], ws,
                    author_id=ids[0] if i["user"] else None, status=status, merged_at=merged_at)
        n += 1
    return n


def fetch_github_collaborators(repo, token):
    """Logins that can read the repo. Needs a token with push access to it."""
    r = httpx.get(f"https://api.github.com/repos/{repo}/collaborators?per_page=100",
                  headers={"Accept": "application/vnd.github+json", "Authorization": f"Bearer {token}"}, timeout=30)
    r.raise_for_status()  # ponytail: first 100; paginate like fetch_github when a repo has more
    return [u["login"] for u in r.json()]


def load_github_collaborators(conn, repo, logins):
    for login in logins:
        db.grant(conn, db.person(conn, login, github_login=login), f"github:{repo}")
    return len(logins)


def unlinked_github_logins(conn):
    return [r[0] for r in conn.execute("select github_login from person where github_login is not null and email is null")]


def load_github_emails(conn, emails):
    """Link GitHub accounts to people by exact email. A login whose emails match nobody stays unlinked."""
    n = 0
    for login, addrs in emails.items():
        for e in addrs:
            if conn.execute("select 1 from person where email = %s", (e.lower(),)).fetchone():
                _person(conn, login, "github_login", login, e)
                n += 1
                break
    return n


# ---- Slack directory (the live adapter calls these too) ----

def load_slack_users(conn, users):
    """users: [{id, name, email, deleted}]. People who left lose all access."""
    n = 0
    for u in users:
        p = _person(conn, u["name"], "slack_id", u["id"], u.get("email"))
        if u["deleted"]:
            conn.execute("update person set active = false where id = %s", (p,))
            conn.execute("delete from person_scope where person_id = %s", (p,))
        n += 1
    return n


def slack_url(base, channel, ts):
    """Slack permalink. base = workspace URL from auth.test, e.g. https://acme.slack.com/"""
    return f"{base}archives/{channel}/p{ts.replace('.', '')}"


def link_thread(conn, url, channel, at, text):
    """Pointer from a Slack thread to the workstream it talks about, found deterministically by ticket key.
    Stores the permalink and who can see it, never the text. Returns the evidence id, or None if nothing matched.
    ponytail: first key wins; a thread about two tickets links to the first one mentioned."""
    keys = LINEAR_KEY.findall(text)
    row = conn.execute("select id from workstream where anchor_kind = 'linear' and anchor_id = any(%s) "
                       "order by array_position(%s, anchor_id) limit 1", (keys, keys)).fetchone()
    return row and db.evidence(conn, url, "slack", f"slack:{channel}", None, at, row[0])


def load_slack_channels(conn, channels):
    """channels: [{id, name, members}]. Public channels are workstreams too (anchor_kind 'channel')."""
    for c in channels:
        scope = f"slack:{c['id']}"
        db.workstream(conn, "channel", scope, f"#{c['name']}", None, None, scope, None, "now")
        for u in c["members"]:
            if row := conn.execute("select id from person where slack_id = %s and active", (u,)).fetchone():
                db.grant(conn, row[0], scope)
    return len(channels)
