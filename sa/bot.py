"""Chat routing: when a twin speaks, and the owner's queue. Uses only the adapter contract
(post, dm, presence, fetch_thread, base_url), so the Teams adapter (M4) plugs into the same code.

When a twin speaks (Q14): someone @mentions an opted-in person who is away or on Do Not Disturb, or DMs the bot
with an @mention, or the same asker follows up in a thread the twin already answered.
"""
import re
from datetime import UTC, datetime

from . import sync, twin
from .slack import MENTION


class Bot:
    def __init__(self, conn, chat, client=None):
        self.conn, self.chat, self.client = conn, chat, client

    def pid(self, slack_user):
        row = self.conn.execute("select id from person where slack_id = %s", (slack_user,)).fetchone()
        if not row:
            name, email = self.chat.profile(slack_user)
            sync.load_slack_users(self.conn, [{"id": slack_user, "name": name, "email": email, "deleted": False}])
            return self.pid(slack_user)
        return row[0]

    def on_message(self, m):
        conn, ch = self.conn, m["channel"]
        if not m["author"]:
            return
        if m["dm"] and m["is_reply"] and self._answer(m):
            return
        if not m["dm"]:
            self.pid(m["author"])  # posting proves membership (pre-mortem #8)
            conn.execute("insert into person_scope select id, %s from person where slack_id = %s on conflict do nothing",
                         (f"slack:{ch}", m["author"]))
            sync.link_thread(conn, sync.slack_url(self.chat.base_url, ch, m["thread"]), ch, datetime.now(UTC), m["text"])

        question = MENTION.sub("", m["text"]).strip()
        owners = [r for u in m["mentions"] if u not in (m["author"], self.chat.bot_user)
                  for r in conn.execute("select id, slack_id from person where slack_id = %s and opted_in", (u,))]
        if not owners and m["is_reply"]:  # follow-up from the same asker in a thread a twin answered
            owners = conn.execute(
                """select q.owner_id, p.slack_id from query_log q join person p on p.id = q.owner_id
                   where q.thread_ref = %s order by q.id desc limit 1""", (self._ref(m),)).fetchall()
            last_asker = conn.execute("select asker_id from query_log where thread_ref = %s order by id desc limit 1",
                                      (self._ref(m),)).fetchone()
            if not last_asker or last_asker[0] != self.pid(m["author"]):
                owners = []
        if m["dm"] and not owners and not m["is_reply"]:
            self.chat.post(ch, m["ts"], "Mention whose twin you want to ask, e.g. `@Priya is the PG16 migration done?`")
        for owner_id, owner_slack in owners:
            if m["dm"] or self.chat.presence(owner_slack) != "active":
                self._reply(m, owner_id, owner_slack, question)

    def on_ping(self, user, channel, thread, owner_slack):
        """[ping X anyway]: a real @mention, from the asker's click."""
        self.chat.post(channel, thread, f"<@{owner_slack}>, <@{user}> asked for you here.")

    @staticmethod
    def _ref(m):
        return f"slack:{m['channel']}:{m['thread']}"

    def _reply(self, m, owner_id, owner_slack, question):
        r = twin.respond(self.conn, self.pid(m["author"]), owner_id, question, chat=self.chat,
                         thread_ref=self._ref(m), client=self.client)
        if r is None:
            return
        first = self.conn.execute("select name from person where id = %s", (owner_id,)).fetchone()[0].split()[0]
        cites = ", ".join(f"<{u}|{_label(u)}>" for u in r["sources"])
        footer = f"🤖 {first}'s twin" + (f" · sources: {cites}" if cites else "")
        actions = [] if r["escalation_id"] else [(f"Ping {first} anyway", "ping", owner_slack)]
        self.chat.post(m["channel"], m["thread"], r["text"], actions, footer)
        if r["escalation_id"]:
            # ponytail: DM right away and let Slack's Do Not Disturb hold it until the owner's break (the batched
            # queue, Q10). A digest job on the `job` table if owners want fewer, bigger batches.
            where = "a DM" if m["dm"] else f"<#{m['channel']}>"
            dm_ch, dm_ts = self.chat.dm(owner_slack, f"❓ <@{m['author']}> asked your twin in {where}:\n>{question}\n"
                                        "Reply in this thread to answer. Your reply is posted back to them and saved, "
                                        "so your twin can reuse it next time.")
            self.conn.execute("update escalation set owner_ref = %s where id = %s",
                              (f"{dm_ch}:{dm_ts}", r["escalation_id"]))

    def _answer(self, m):
        """Owner replied in the DM thread of a queued question -> post the answer back, save it as a fact."""
        row = self.conn.execute("select e.id, p.name from escalation e join person p on p.id = e.owner_id "
                                "where e.owner_ref = %s and e.status = 'open'", (f"{m['channel']}:{m['thread']}",)).fetchone()
        if not row:
            return False
        thread_ref, question = twin.answer(self.conn, row[0], m["text"])
        if thread_ref:
            _, ch, th = thread_ref.split(":", 2)
            self.chat.post(ch, th, f"✅ {row[1].split()[0]} answered: {m['text']}", footer=f"Asked: {question}")
        self.chat.post(m["channel"], m["thread"], "Posted, and saved as an approved answer your twin will reuse.")
        return True


def _label(url):
    if m := re.search(r"/issue/([A-Z0-9]+-\d+)", url):
        return m[1]
    if m := re.search(r"/(pull|issues)/(\d+)", url):
        return f"{'PR ' if m[1] == 'pull' else ''}#{m[2]}"
    return "thread"
