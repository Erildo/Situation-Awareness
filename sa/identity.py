"""Reference resolution: a name in a question ("Priya", "Rash", "me") -> people.

Separate from account linking (db.person) on purpose. Fuzzy matching is fine here because a wrong guess
only changes which question is answered, never who can see what. Ambiguity is returned, not guessed:
the caller asks "did you mean Rashene Kumar or Rashid Ali?".
"""
import difflib
import re

from .db import PERSON_KEYS

SELF = {"me", "my", "mine", "myself", "i"}


def _people(conn):
    cols = ", ".join(PERSON_KEYS)
    for row in conn.execute(f"select id, name, active, {cols} from person"):
        pid, name, active, *ids = row
        yield {"id": pid, "name": name, "active": active, **dict(zip(PERSON_KEYS, ids))}


def aliases(p):
    """Every handle this one person goes by. Handed to the model as an exhaustive set, not suggestions."""
    return {k: p[k] for k in PERSON_KEYS if p[k]}


def resolve(conn, ref, asker_id=None):
    """Best tier wins: exact > prefix > typo. Returns [] (unknown), [one] or [several] (ambiguous)."""
    r = ref.strip().lower().lstrip("@").removesuffix("'s")
    people = list(_people(conn))
    if r in SELF:
        return [p for p in people if p["id"] == asker_id]

    def words(p):
        return set(re.split(r"[\s._-]+", p["name"].lower())) | {(p["email"] or "").split("@")[0]}

    def handles(p):
        return {v.lower() for k, v in aliases(p).items()} | {p["name"].lower()}

    exact = [p for p in people if r in handles(p) or r in words(p)]
    if exact:
        return exact
    if len(r) >= 3:
        prefix = [p for p in people if any(w.startswith(r) for w in words(p) | handles(p))]
        if prefix:
            return prefix
    vocab = {w: p["id"] for p in people for w in words(p) | handles(p) if w}
    close = {vocab[w] for w in difflib.get_close_matches(r, vocab, n=5, cutoff=0.8)}
    return [p for p in people if p["id"] in close]
