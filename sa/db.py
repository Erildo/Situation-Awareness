import os
from pathlib import Path

import psycopg

PERSON_KEYS = ("email", "slack_id", "teams_id", "github_login", "linear_id")
# Every column that points at person, so a merge can move them all.
PERSON_FKS = [("workstream", "owner_id"), ("state", "owner_id"), ("fact", "owner_id"),
              ("escalation", "owner_id"), ("escalation", "asker_id"),
              ("query_log", "owner_id"), ("query_log", "asker_id"),
              ("evidence_ref", "author_id")]


class IdentityConflict(Exception):
    """Two exact IDs disagree (e.g. this email already has a different GitHub login). Never auto-resolved."""


def connect():
    conn = psycopg.connect(os.environ.get("DATABASE_URL", "postgresql:///sa"), autocommit=True)
    conn.execute("set timezone = 'UTC'")
    conn.execute((Path(__file__).parent / "schema.sql").read_text())
    return conn


def person(conn, name, **ids):
    """Account linking. Find the person holding any of these exact IDs, else create one.

    Links only on exact IDs (email, platform IDs), never on names: a false link would leak one person's
    access to another. If the IDs point at two existing people, they are the same human, so merge them.
    """
    ids = {k: v.lower() if k == "email" else v for k, v in ids.items() if v}
    assert ids and ids.keys() <= set(PERSON_KEYS), ids
    where = " or ".join(f"{k} = %({k})s" for k in ids)
    found = [r[0] for r in conn.execute(f"select id from person where {where} order by id", ids)]
    if not found:
        cols = ", ".join(ids)
        vals = ", ".join(f"%({k})s" for k in ids)
        return conn.execute(f"insert into person (name, {cols}) values (%(name)s, {vals}) returning id",
                            {**ids, "name": name}).fetchone()[0]
    keep = found[0]
    for drop in found[1:]:
        merge(conn, keep, drop)
    have = dict(zip(PERSON_KEYS, conn.execute(
        f"select {', '.join(PERSON_KEYS)} from person where id = %s", (keep,)).fetchone()))
    if clash := {k: (have[k], v) for k, v in ids.items() if have[k] and have[k] != v}:
        raise IdentityConflict(f"person {keep}: {clash}")
    sets = ", ".join(f"{k} = %({k})s" for k in ids)
    conn.execute(f"update person set {sets} where id = %(id)s", {**ids, "id": keep})
    _prefer_real_name(conn, keep, name)
    return keep


def merge(conn, keep, drop):
    """Fold person `drop` into `keep`: IDs, scopes and every reference move over, then `drop` is deleted."""
    cols = ", ".join(PERSON_KEYS)
    a, b = (dict(zip(PERSON_KEYS, conn.execute(f"select {cols} from person where id = %s", (p,)).fetchone()))
            for p in (keep, drop))
    if clash := {k: (a[k], b[k]) for k in PERSON_KEYS if a[k] and b[k] and a[k] != b[k]}:
        raise IdentityConflict(f"merge {drop} into {keep}: {clash}")
    conn.execute("insert into person_scope select %s, scope from person_scope where person_id = %s "
                 "on conflict do nothing", (keep, drop))
    for table, col in PERSON_FKS:
        conn.execute(f"update {table} set {col} = %s where {col} = %s", (keep, drop))
    name, opted, active = conn.execute("select name, opted_in, active from person where id = %s", (drop,)).fetchone()
    conn.execute("delete from person where id = %s", (drop,))  # cascades its person_scope rows
    fill = {k: b[k] for k in PERSON_KEYS if b[k] and not a[k]}
    if fill:
        sets = ", ".join(f"{k} = %({k})s" for k in fill)
        conn.execute(f"update person set {sets} where id = %(id)s", {**fill, "id": keep})
    conn.execute("update person set opted_in = opted_in or %s, active = active and %s where id = %s",
                 (opted, active, keep))
    _prefer_real_name(conn, keep, name)


def _prefer_real_name(conn, pid, name):
    # ponytail: "Priya Shah" beats the login "priya-s"; a name with a space is a real name
    if name and " " in name:
        conn.execute("update person set name = %s where id = %s and position(' ' in name) = 0", (name, pid))


def shared_scopes(conn, asker_id, owner_id):
    """Q8: what an answer from owner's twin to asker may draw on. Both must be active."""
    return {r[0] for r in conn.execute(
        """select a.scope from person_scope a join person_scope o on o.scope = a.scope
           join person pa on pa.id = a.person_id join person po on po.id = o.person_id
           where a.person_id = %s and o.person_id = %s and pa.active and po.active""",
        (asker_id, owner_id))}


def grant(conn, person_id, scope):
    conn.execute("insert into person_scope values (%s, %s) on conflict do nothing", (person_id, scope))


def revoke(conn, person_id, scope):
    conn.execute("delete from person_scope where person_id = %s and scope = %s", (person_id, scope))


def workstream(conn, anchor_kind, anchor_id, title, status, url, scope, owner_id, updated_at):
    return conn.execute(
        """insert into workstream (anchor_kind, anchor_id, title, status, url, scope, owner_id, updated_at)
           values (%s, %s, %s, %s, %s, %s, %s, %s)
           on conflict (anchor_kind, anchor_id) do update set
             title = excluded.title, status = excluded.status, url = excluded.url, scope = excluded.scope,
             owner_id = coalesce(excluded.owner_id, workstream.owner_id), updated_at = excluded.updated_at
           returning id""",
        (anchor_kind, anchor_id, title, status, url, scope, owner_id, updated_at),
    ).fetchone()[0]


def evidence(conn, url, source, scope, title, at, workstream_id=None, state_id=None,
             author_id=None, status=None, merged_at=None):
    return conn.execute(
        """insert into evidence_ref (url, source, scope, title, at, workstream_id, state_id, author_id, status, merged_at)
           values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
           on conflict (url) do update set title = excluded.title, at = excluded.at,
             workstream_id = excluded.workstream_id, scope = excluded.scope,
             state_id = coalesce(excluded.state_id, evidence_ref.state_id), author_id = excluded.author_id,
             status = excluded.status, merged_at = excluded.merged_at
           returning id""",
        (url, source, scope, title, at, workstream_id, state_id, author_id, status, merged_at),
    ).fetchone()[0]
