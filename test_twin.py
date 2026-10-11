"""Twin, tools and Slack routing without calling Claude: the model is scripted, everything else is real.
Run: DATABASE_URL=postgresql:///sa_test uv run python test_twin.py   (`sa eval` scores the real model.)"""
import json
from types import SimpleNamespace as NS

from sa import db, tools, twin
from sa.__main__ import seed
from sa.bot import Bot
from sa.evals import NOW, FixtureChat


class Model:
    """Plays back scripted turns: each turn is [(tool, input)]. Keeps the messages it was sent."""
    def __init__(self, *turns):
        self.turns, self.sent = list(turns), []
        self.beta = NS(messages=NS(create=self.create))

    def create(self, messages, **_):
        self.sent.append(json.loads(json.dumps(messages, default=lambda b: b.__dict__)))
        return NS(stop_reason="tool_use", content=[NS(type="tool_use", name=n, input=i, id=f"t{k}")
                                                   for k, (n, i) in enumerate(self.turns.pop(0))])

    def results(self):  # tool results the model saw on its last call
        return [json.loads(b["content"]) for b in self.sent[-1][-1]["content"]]


class Chat(FixtureChat):
    base_url, bot_user = "https://acme.slack.com/", "U_BOT"

    def __init__(self):
        super().__init__()
        self.posts, self.dms, self.away = [], [], {"U_PRIYA"}

    def post(self, channel, thread, text, actions=(), footer=None):
        self.posts.append(NS(channel=channel, thread=thread, text=text, actions=actions, footer=footer))
        return f"9{len(self.posts)}.0"

    def dm(self, user, text):
        self.dms.append((user, text))
        return f"D_{user}", self.post(f"D_{user}", None, text)

    def presence(self, user):
        return "dnd" if user in self.away else "active"


def msg(author, text, channel="C_INFRA", ts="100.1", thread=None, dm=False):
    from sa.slack import MENTION
    return {"channel": channel, "ts": ts, "thread": thread or ts, "author": author, "text": text,
            "mentions": MENTION.findall(text), "is_reply": bool(thread), "dm": dm}


conn = db.connect()
with conn.transaction(force_rollback=True):
    seed(conn)
    P = lambda first: conn.execute("select id from person where email = %s", (f"{first}@acme.test",)).fetchone()[0]
    ctx = lambda a, o: tools.Ctx(conn, P(a), P(o), NOW, FixtureChat())

    # ---- structured tools answer the golden questions via SQL, inside asker ∩ owner ----
    assert [w["status"] for w in tools.workstreams(ctx("sam", "priya"), anchor="INFRA-123")] == ["In Review"]
    assert [w["anchor_id"] for w in tools.workstreams(ctx("kenji", "aisha"), team="PAY", status="blocked")] == ["PAY-40"]
    assert [s["due"] for s in tools.state(ctx("olivia", "priya"), kind="commitment", owner="Priya")] == ["2026-10-19"]
    assert [p["url"][-3:] for p in tools.prs(ctx("ravi", "sam"), author="Sam", status="merged", since="2026-10-05")] == ["790"]
    assert len(tools.prs(ctx("olivia", "kenji"), repo="acme/platform", status="merged", since="2026-10-01")) == 4
    assert "maintenance window" in tools.state(ctx("yuki", "marcus"), owner="Marcus", kind="blocker")[0]["text"]
    assert [w["anchor_id"] for w in tools.workstreams(ctx("sam", "rashid"), owner="Rashid")] == ["APP-99"]
    assert tools.workstreams(ctx("sam", "priya"), owner="Rash")["candidates"]  # ambiguous: ask, don't guess
    assert "INFRA-142" in [w["anchor_id"] for w in tools.search(ctx("dana", "omar"), "speeding up CI")["workstreams"]]
    assert "Nov 3" in tools.search(ctx("grace", "hannah"), "Q4 offsite")["state"][0]["text"]
    assert "Wednesday" in tools.search(ctx("hannah", "mei"), "revenue dashboard fixed")["state"][0]["text"]
    c = ctx("grace", "sam")  # semantic: ticket -> thread pointer -> live fetch
    thread = next(u for u in tools.search(c, "why was checkout slow")["workstreams"][0]["sources"] if "slack" in u)
    assert "N+1" in tools.read_thread(c, thread)[0]["text"]
    # permissions: PAY is private, Sam isn't in it; a thread outside the shared scopes can't be read
    c = ctx("sam", "ben")
    assert tools.workstreams(c, team="PAY") == [] and "PAY-47" not in json.dumps(tools.search(c, "PCI audit"))
    assert tools.read_thread(c, "https://acme.slack.com/archives/C_PAYMENTS/p1791630000000300") == {"error": "not available"}
    c = ctx("paul", "lena")  # no Linear account: sees the #incidents thread, never INFRA-145's status
    assert tools.workstreams(c, anchor="INFRA-145") == []
    assert "C_INCIDENTS" in tools.search(c, "open incidents")["workstreams"][0]["sources"][0]

    # ---- twin: no LLM for people who left or never opted in ----
    assert "left" in twin.respond(conn, P("ravi"), P("tom"), "what's Tom working on?", NOW)["text"]
    conn.execute("update person set opted_in = false where id = %s", (P("elena"),))
    assert twin.respond(conn, P("ravi"), P("elena"), "anything?", NOW) is None
    m = Model([("reply", {"text": "Probably fine at 100%.", "level": 3, "sources": [], "escalate": False})])
    assert twin.respond(conn, P("chloe"), P("rashene"), "roll passkeys to 100%?", NOW, client=m)["text"].startswith("_Likely view")

    # ---- demo steps 1-4, through the Slack routing ----
    chat = Chat()
    infra123, pr812 = "https://linear.app/acme/issue/INFRA-123", "https://github.com/acme/platform/pull/812"
    # 1. Priya is on DND; Sam asks in #infra and her twin answers with sources and a ping button
    m = Model([("workstreams", {"anchor": "INFRA-123"}), ("state", {"workstream": "INFRA-123", "kind": "decision"})],
              [("reply", {"text": "Not yet. INFRA-123 is In Review (PR #812, 19h ago). Decided Oct 9: cutover Fri Oct 16.",
                          "level": 1, "sources": [infra123, pr812, "https://evil.example/x"], "escalate": False})])
    bot = Bot(conn, chat, client=m)
    bot.on_message(msg("U_SAM", "<@U_PRIYA> is the PG16 migration done?"))
    ws, st = m.results()
    assert ws[0]["status"] == "In Review" and pr812 in ws[0]["sources"] and "Oct 16" in st[0]["text"]
    p = chat.posts[-1]
    assert p.text.startswith("Not yet") and p.thread == "100.1" and p.actions == [("Ping Priya anyway", "ping", "U_PRIYA")]
    assert "INFRA-123" in p.footer and "PR #812" in p.footer and "evil" not in p.footer  # only cites what tools returned
    # 2. follow-up in the thread is level 4: queued for Priya, whatever the model drafted
    m.turns = [[("reply", {"text": "Sure, Monday works.", "level": 4, "sources": [], "escalate": True})]]
    bot.on_message(msg("U_SAM", "can we push the cutover to Monday?", ts="100.2", thread="100.1"))
    assert "Priya's queue" in chat.posts[-2].text and "Sure" not in chat.posts[-2].text
    assert chat.dms[-1][0] == "U_PRIYA" and "can we push the cutover" in chat.dms[-1][1]
    # someone else chatting in that thread doesn't wake the twin
    n = len(chat.posts)
    bot.on_message(msg("U_DANA", "fwiw I'd keep Friday", ts="100.3", thread="100.1"))
    assert len(chat.posts) == n
    # 3. Priya answers once, in her DM thread; Sam gets it in the original thread
    bot.on_message(msg("U_PRIYA", "No, Friday stays. Monday is quarter close.", channel="D_U_PRIYA",
                       ts="200.1", thread=f"9{n}.0", dm=True))
    assert chat.posts[-2].channel == "C_INFRA" and chat.posts[-2].text.startswith("✅ Priya answered: No, Friday stays")
    assert conn.execute("select status from escalation").fetchone()[0] == "answered"
    # 4. the next person to ask gets the approved answer instantly
    m.turns = [[("search", {"query": "push the cutover to Monday"})],
               [("reply", {"text": "No, Friday stays (Priya, today).", "level": 1, "sources": [], "escalate": False})]]
    bot.on_message(msg("U_KENJI", "<@U_PRIYA> could the cutover move to Monday?", ts="300.1"))
    assert m.results()[0]["facts"][0]["answer"].startswith("No, Friday stays")
    assert chat.posts[-1].text.startswith("No, Friday stays") and conn.execute(
        "select count(*) from escalation").fetchone()[0] == 1

    # when Priya is active, nobody speaks for her
    chat.away.clear()
    n = len(chat.posts)
    bot.on_message(msg("U_SAM", "<@U_PRIYA> quick one?", ts="400.1"))
    assert len(chat.posts) == n
    # DMs to the bot always get an answer
    m.turns = [[("reply", {"text": "In Review.", "level": 1, "sources": [], "escalate": False})]]
    bot.on_message(msg("U_SAM", "<@U_PRIYA> status of INFRA-123?", channel="D_SAM", ts="500.1", dm=True))
    assert chat.posts[-1].text == "In Review."
    assert conn.execute("select count(*) from query_log").fetchone()[0] >= 6  # owner sees every question
print("ok")
