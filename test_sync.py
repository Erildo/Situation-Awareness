"""Loaders + account linking edge cases. Run: DATABASE_URL=postgresql:///sa_test uv run python test_sync.py
(Golden behaviour lives in `sa eval`; this covers what golden cases can't: idempotency, conflicts, merges.)"""
from sa import db
from sa.__main__ import seed

conn = db.connect()
with conn.transaction(force_rollback=True):
    seed(conn)
    counts = conn.execute("select (select count(*) from person), (select count(*) from workstream), "
                          "(select count(*) from evidence_ref), (select count(*) from person_scope)").fetchone()
    seed(conn)  # second pass proves every loader is idempotent
    assert counts == conn.execute("select (select count(*) from person), (select count(*) from workstream), "
                                  "(select count(*) from evidence_ref), (select count(*) from person_scope)").fetchone()

    ws = {a: (s, o) for a, s, o in conn.execute(
        "select anchor_id, status, p.name from workstream w left join person p on p.id = owner_id")}
    assert "acme/platform#812" not in ws  # PR mentioning INFRA-123 is evidence for it, not a workstream
    assert ws["INFRA-123"] == ("In Review", "Priya Shah")  # real name won over the login after linking
    assert ws["acme/platform#820"] == ("open", "Dana Kowalski")  # assignee beats author
    assert ws["acme/platform#805"][0] == "merged"
    assert ws["slack:C_INFRA"][0] is None and "slack:C_GENERAL" in ws  # channels are workstreams
    # 30 people, minus Tom (left), plus 3 GitHub accounts email could not link: mwebb, ofarouk, tbecker
    assert conn.execute("select count(*) from person where active").fetchone()[0] == 32
    priya = conn.execute("select id from person where email = 'priya@acme.test'").fetchone()[0]
    assert conn.execute("select slack_id, linear_id, github_login from person where id = %s",
                        (priya,)).fetchone() == ("U_PRIYA", "lin-priya", "priya-s")

    # conflicting exact IDs are refused, not overwritten
    try:
        db.person(conn, "x", email="priya@acme.test", github_login="someone-else")
        raise AssertionError("conflict not raised")
    except db.IdentityConflict:
        pass

    # confirming a link merges the GitHub-only person: ownership and scopes move over
    omar_gh = conn.execute("select id from person where github_login = 'ofarouk'").fetchone()[0]
    omar = db.person(conn, "ofarouk", email="omar@acme.test", github_login="ofarouk")
    assert omar != omar_gh and not conn.execute("select 1 from person where id = %s", (omar_gh,)).fetchone()
    assert "github:acme/platform" in db.shared_scopes(conn, omar, priya)
    assert conn.execute("select name from person where id = %s", (omar,)).fetchone()[0] == "Omar Farouk"

    # people who left can't see anything, and can't be the other side of a share either
    tom = conn.execute("select id from person where email = 'tom@acme.test'").fetchone()[0]
    assert not db.shared_scopes(conn, tom, priya) and not db.shared_scopes(conn, priya, tom)
print("ok")
