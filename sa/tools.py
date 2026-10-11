"""Read-only tools the twin answers from. Typed queries over the ledger, no free-form SQL (Q16).

Permission filter first (Q8): every query is restricted to the scopes the asker and the owner share, inside the
SQL, so a row the asker can't see never reaches the model. Every URL a tool returns is recorded in `ctx.seen`;
an answer can only cite those.
"""
import re
from dataclasses import dataclass, field
from datetime import datetime

from . import db, identity

SLACK_URL = re.compile(r"/archives/(\w+)/p(\d{10})(\d{6})")


@dataclass
class Ctx:
    conn: object
    asker_id: int
    owner_id: int
    now: datetime
    chat: object = None  # adapter with fetch_thread(channel, ts); raw chat is fetched live, never stored
    scopes: list = field(default_factory=list)
    seen: set = field(default_factory=set)

    def __post_init__(self):
        self.scopes = sorted(db.shared_scopes(self.conn, self.asker_id, self.owner_id))

    def rows(self, sql, params=()):
        cur = self.conn.execute(sql, params)
        cols = [d.name for d in cur.description]
        out = []
        for r in cur:
            row = {k: v.isoformat() if hasattr(v, "isoformat") else v for k, v in zip(cols, r) if v is not None}
            for k in ("url", "sources"):
                if k in row:
                    self.seen.update(row[k] if isinstance(row[k], list) else [row[k]])
            out.append(row)
        return out


def _person(ctx, ref):
    """Name in a tool call -> person id, or an error dict the model can act on (ask which one)."""
    found = identity.resolve(ctx.conn, ref, ctx.asker_id)
    if len(found) == 1:
        return found[0]["id"], None
    if not found:
        return None, {"error": f"no one called {ref!r}"}
    return None, {"error": "ambiguous, ask the user which one", "candidates": [p["name"] for p in found]}


# Visible = the asker and owner share the workstream's scope, or the scope of a thread the state was confirmed in.
STATE_VISIBLE = """(w.scope = any(%(s)s) or exists (select 1 from evidence_ref e
                    where e.state_id = s.id and e.scope = any(%(s)s)))"""
STATE_COLS = """s.kind, s.text, s.status, s.due, s.valid_from as since, p.name as owner,
                w.anchor_id as workstream, array(select e.url from evidence_ref e
                  where e.state_id = s.id and e.scope = any(%(s)s)) as sources"""
WS_COLS = """w.anchor_id, w.title, w.status, w.url, w.updated_at, p.name as owner,
             array(select e.url from evidence_ref e where (e.workstream_id = w.id or e.scope = w.anchor_id)
                   and e.scope = any(%(s)s)
                   order by e.at desc limit 5) as sources"""


def search(ctx, query):
    """Keyword search over workstreams, confirmed state and the owner's approved facts."""
    q = {"s": ctx.scopes, "q": query, "owner": ctx.owner_id}
    # OR the words together and rank: "speeding up CI" should find "Get CI under 10 minutes".
    tsq = "replace(plainto_tsquery('english', %(q)s)::text, '&', '|')::tsquery"
    return {
        "facts": ctx.rows(f"""select question, answer, created_at as approved_at from fact
            where owner_id = %(owner)s and valid_to is null
              and to_tsvector('english', question || ' ' || answer) @@ {tsq}
            order by ts_rank(to_tsvector('english', question || ' ' || answer), {tsq}) desc limit 5""", q),
        "state": ctx.rows(f"""select {STATE_COLS} from state s join workstream w on w.id = s.workstream_id
            left join person p on p.id = s.owner_id
            where s.status = 'confirmed' and {STATE_VISIBLE} and to_tsvector('english', s.text) @@ {tsq}
            order by ts_rank(to_tsvector('english', s.text), {tsq}) desc limit 8""", q),
        "workstreams": ctx.rows(f"""select {WS_COLS} from workstream w left join person p on p.id = w.owner_id
            where w.scope = any(%(s)s) and to_tsvector('english', w.title || ' ' || w.anchor_id) @@ {tsq}
            order by ts_rank(to_tsvector('english', w.title || ' ' || w.anchor_id), {tsq}) desc limit 8""", q),
    }


def workstreams(ctx, owner=None, status=None, team=None, anchor=None):
    """Tickets, issues and channels. team = Linear key ('PAY') or repo ('acme/payments')."""
    q = {"s": ctx.scopes, "status": status, "anchor": anchor,
         "team": team and (f"{team}#%" if "/" in team else f"{team.upper()}-%")}
    where = ["w.scope = any(%(s)s)", "w.anchor_kind <> 'channel'"]
    if owner:
        q["owner"], err = _person(ctx, owner)
        if err:
            return err
        where.append("w.owner_id = %(owner)s")
    if status:
        where.append("lower(w.status) = lower(%(status)s)")
    if team:
        where.append("w.anchor_id like %(team)s")
    if anchor:
        where.append("w.anchor_id = %(anchor)s")
    return ctx.rows(f"""select {WS_COLS} from workstream w left join person p on p.id = w.owner_id
                        where {' and '.join(where)} order by w.updated_at desc limit 25""", q)


def state(ctx, kind=None, owner=None, workstream=None, due_before=None, since=None):
    """Confirmed decisions, commitments and blockers that are still current."""
    q = {"s": ctx.scopes, "kind": kind, "ws": workstream, "due": due_before, "since": since}
    where = ["s.status = 'confirmed'", "s.valid_to is null", STATE_VISIBLE]
    if owner:
        q["owner"], err = _person(ctx, owner)
        if err:
            return err
        where.append("s.owner_id = %(owner)s")
    for v, cond in [(kind, "s.kind = %(kind)s"), (workstream, "w.anchor_id = %(ws)s"),
                    (due_before, "s.due <= %(due)s"), (since, "s.valid_from >= %(since)s")]:
        if v:
            where.append(cond)
    return ctx.rows(f"""select {STATE_COLS} from state s join workstream w on w.id = s.workstream_id
                        left join person p on p.id = s.owner_id
                        where {' and '.join(where)} order by s.valid_from desc limit 25""", q)


def prs(ctx, author=None, repo=None, status=None, since=None, until=None):
    """GitHub pull requests. since/until filter on merged_at when status='merged', else on last update."""
    q = {"s": ctx.scopes, "repo": repo and f"github:{repo}", "status": status, "since": since, "until": until}
    at = "e.merged_at" if status == "merged" else "e.at"
    where = ["e.scope = any(%(s)s)", "e.source = 'github'", "e.url like '%%/pull/%%'"]
    if author:
        q["author"], err = _person(ctx, author)
        if err:
            return err
        where.append("e.author_id = %(author)s")
    for v, cond in [(repo, "e.scope = %(repo)s"), (status, "e.status = %(status)s"),
                    (since, f"{at} >= %(since)s"), (until, f"{at} < %(until)s")]:
        if v:
            where.append(cond)
    return ctx.rows(f"""select e.url, e.title, e.status, e.merged_at, e.at as updated_at, p.name as author,
                               w.anchor_id as workstream
                        from evidence_ref e left join person p on p.id = e.author_id
                        left join workstream w on w.id = e.workstream_id
                        where {' and '.join(where)} order by {at} desc nulls last limit 50""", q)


def read_thread(ctx, url):
    """Fetch a Slack thread live. Only threads the ledger points at, in a scope both sides share."""
    row = ctx.conn.execute("select 1 from evidence_ref where url = %s and source = 'slack' and scope = any(%s)",
                           (url, ctx.scopes)).fetchone()
    m = SLACK_URL.search(url)
    if not (row and m and ctx.chat):
        return {"error": "not available"}
    ctx.seen.add(url)
    names = dict(ctx.conn.execute("select slack_id, name from person where slack_id is not null"))
    return [{"author": names.get(msg["author"], msg["author"]), "text": msg["text"]}
            for msg in ctx.chat.fetch_thread(m[1], f"{m[2]}.{m[3]}")]


def teams(ctx):
    """Linear teams and repos visible here, so the model can map 'payments' to PAY."""
    keys = {r[0] for r in ctx.conn.execute(
        "select distinct split_part(anchor_id, '-', 1) from workstream where anchor_kind = 'linear' and scope = any(%s)",
        (ctx.scopes,))}
    repos = [s.removeprefix("github:") for s in ctx.scopes if s.startswith("github:")]
    return sorted(keys), repos


TOOLS = {"search": search, "workstreams": workstreams, "state": state, "prs": prs, "read_thread": read_thread}

_str = {"type": "string"}
SCHEMAS = [
    {"name": "search", "description": "Keyword search over tickets/issues, confirmed decisions/commitments/blockers "
     "and the owner's approved answers. Use for how/why questions or when unsure where something lives.",
     "input_schema": {"type": "object", "properties": {"query": _str}, "required": ["query"]}},
    {"name": "workstreams", "description": "List tickets and issues with status and owner. All filters optional. "
     "team is a Linear key like PAY or a repo like acme/payments; status is e.g. 'Blocked', 'In Review', 'open'; "
     "anchor is an exact id like INFRA-123.",
     "input_schema": {"type": "object", "properties": {"owner": _str, "status": _str, "team": _str, "anchor": _str}}},
    {"name": "state", "description": "Current confirmed decisions, commitments (with due dates) and blockers. "
     "kind: decision | commitment | blocker. owner is a person's name. Dates are YYYY-MM-DD.",
     "input_schema": {"type": "object", "properties": {
         "kind": {"type": "string", "enum": ["decision", "commitment", "blocker"]},
         "owner": _str, "workstream": _str, "due_before": _str, "since": _str}}},
    {"name": "prs", "description": "GitHub pull requests. status: open | closed | merged. repo like acme/platform. "
     "since/until are ISO dates; with status=merged they filter on the merge date.",
     "input_schema": {"type": "object", "properties": {
         "author": _str, "repo": _str, "status": {"type": "string", "enum": ["open", "closed", "merged"]},
         "since": _str, "until": _str}}},
    {"name": "read_thread", "description": "Read a Slack thread by a URL another tool returned, for details "
     "like root causes that aren't in the ticket.",
     "input_schema": {"type": "object", "properties": {"url": _str}, "required": ["url"]}},
]
