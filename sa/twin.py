"""Twin responder: answers a question on the owner's behalf, from the ledger, with sources.

Risk tiers (Q2), enforced here in code rather than trusted to the model:
  1 facts/status, 2 how-to   -> answered now, cited
  3 opinion                  -> labelled as a likely view
  4 decisions/commitments    -> never answered; queued for the owner (Q10)
An owner's answer to a queued question becomes a `fact` the twin reuses, so it's asked once.
"""
import json
import os
from datetime import datetime, timezone

import anthropic

from . import tools

MODEL = os.environ.get("SA_MODEL", "claude-opus-5-5")
MAX_TURNS = 8

SYSTEM = """You are {owner}'s twin at work. {asker} is asking you something while {owner} is busy.
Now: {now}. Linear teams you can see: {teams}. Repos: {repos}.

Answer only from what the tools return. Never guess and never invent facts, dates or links.
Prefer the structured tools (workstreams, state, prs) for who/what/status/how-many questions; use search for
how/why or when unsure; read_thread for details a ticket doesn't have. If {owner} already approved an answer
(a fact), use it. If nothing you can see answers it, say you don't know rather than hedging.

Finish by calling `reply` exactly once with:
- text: 1-3 short sentences for Slack. Lead with the answer. Mention ticket/PR ids and how old the info is
  (e.g. "updated 3h ago"). Don't mention tools, scopes or permissions.
- level: 1 = fact or status, 2 = how-to or explanation, 3 = asks for {owner}'s opinion or a judgment call,
  4 = asks {owner} to decide, approve, commit to or change something (moving a date, taking on work, spending).
  A level 4 question is level 4 even when you know related facts; an approved fact that answers it exactly is level 1.
- sources: the URLs (from tool results) that support the answer.
- escalate: true when it's level 4, or when you can't answer and {owner} would know."""

REPLY = {"name": "reply", "description": "Send the final answer.", "strict": True, "input_schema": {
    "type": "object", "additionalProperties": False, "required": ["text", "level", "sources", "escalate"],
    "properties": {"text": {"type": "string"}, "level": {"type": "integer", "enum": [1, 2, 3, 4]},
                   "sources": {"type": "array", "items": {"type": "string"}}, "escalate": {"type": "boolean"}}}}


def _first(name):
    return name.split()[0]


def respond(conn, asker_id, owner_id, question, now=None, chat=None, thread_ref=None, client=None):
    """-> {text, level, sources, escalation_id} or None when the owner has no twin (not opted in)."""
    now = now or datetime.now(timezone.utc)
    owner, opted, active = conn.execute("select name, opted_in, active from person where id = %s", (owner_id,)).fetchone()
    asker = conn.execute("select name from person where id = %s", (asker_id,)).fetchone()[0]
    if not active:
        out = {"text": f"{owner} has left the company, so there's no one behind this twin any more. "
                       "Ask their team who took over.", "level": 1, "sources": [], "escalation_id": None}
        return _log(conn, owner_id, asker_id, question, out, thread_ref)
    if not opted:
        return None

    ctx = tools.Ctx(conn, asker_id, owner_id, now, chat)
    teams, repos = tools.teams(ctx)
    reply = _ask_model(client or anthropic.Anthropic(), ctx, question, SYSTEM.format(
        owner=owner, asker=asker, now=now.strftime("%a %Y-%m-%d %H:%M UTC"),
        teams=", ".join(teams) or "none", repos=", ".join(repos) or "none"))

    sources = [u for u in dict.fromkeys(reply["sources"]) if u in ctx.seen]  # cite only what the asker may see
    text, level, esc = reply["text"], reply["level"], None
    if level == 4 or reply["escalate"]:
        esc = conn.execute("insert into escalation (owner_id, asker_id, question, thread_ref) values (%s, %s, %s, %s) "
                           "returning id", (owner_id, asker_id, question, thread_ref or "")).fetchone()[0]
        if level == 4:  # never speak for the owner on decisions, even if the model drafted something
            text, sources = f"That's {_first(owner)}'s call, so I've added it to {_first(owner)}'s queue. " \
                            "You'll get the answer here.", []
        else:
            text += f"\nI've asked {_first(owner)} and will post the answer here."
    elif level == 3:
        text = f"_Likely view, not confirmed by {_first(owner)}:_ {text}"
    out = {"text": text, "level": level, "sources": sources, "escalation_id": esc}
    return _log(conn, owner_id, asker_id, question, out, thread_ref)


def _ask_model(client, ctx, question, system):
    messages = [{"role": "user", "content": question}]
    for _ in range(MAX_TURNS):
        r = client.beta.messages.create(
            model=MODEL, max_tokens=16000, system=system, messages=messages,
            tools=[*tools.SCHEMAS, REPLY], output_config={"effort": "medium"},
            betas=["server-side-fallback-2026-07-01"], fallbacks="default")
        if r.stop_reason == "refusal":
            break
        messages.append({"role": "assistant", "content": r.content})
        calls = [b for b in r.content if b.type == "tool_use"]
        if done := next((b for b in calls if b.name == "reply"), None):
            return done.input
        if not calls:  # answered in prose instead of calling reply; nudge once more
            messages.append({"role": "user", "content": "Call `reply` with your answer."})
            continue
        messages.append({"role": "user", "content": [_run(ctx, b) for b in calls]})
    return {"text": "I couldn't work that out.", "level": 2, "sources": [], "escalate": True}


def _run(ctx, call):
    try:
        with ctx.conn.transaction():  # savepoint: a bad query doesn't poison the caller's transaction
            out = tools.TOOLS[call.name](ctx, **call.input)
        return {"type": "tool_result", "tool_use_id": call.id, "content": json.dumps(out, default=str)}
    except Exception as e:  # bad dates, unknown args: tell the model, let it retry
        return {"type": "tool_result", "tool_use_id": call.id, "content": f"error: {e}", "is_error": True}


def _log(conn, owner_id, asker_id, question, out, thread_ref):
    conn.execute("insert into query_log (owner_id, asker_id, question, answer, level, thread_ref, sources) "
                 "values (%s, %s, %s, %s, %s, %s, %s)",
                 (owner_id, asker_id, question, out["text"], out["level"], thread_ref, out["sources"]))
    return out


def answer(conn, escalation_id, text):
    """Owner answers a queued question. The answer becomes an approved fact. -> (thread_ref, question)"""
    owner, question, thread_ref = conn.execute(
        "select owner_id, question, thread_ref from escalation where id = %s and status = 'open'",
        (escalation_id,)).fetchone()
    fact = conn.execute("insert into fact (owner_id, question, answer) values (%s, %s, %s) returning id",
                        (owner, question, text)).fetchone()[0]
    conn.execute("update escalation set status = 'answered', answer = %s, fact_id = %s, answered_at = now() "
                 "where id = %s", (text, fact, escalation_id))
    return thread_ref, question
