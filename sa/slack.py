"""Slack adapter. The contract (on_message, post, members, presence, fetch_thread) is what every chat
platform implements; on_membership / channels / users / profile keep permissions in sync (pre-mortem #8)."""
import re
import time

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

MENTION = re.compile(r"<@(\w+)>")


def _msg(channel, m):
    text = m.get("text", "")
    return {"platform": "slack", "channel": channel, "thread": m.get("thread_ts", m["ts"]), "ts": m["ts"],
            "author": m.get("user"), "text": text, "mentions": MENTION.findall(text)}


class Slack:
    def __init__(self, bot_token, app_token):
        self.app = App(token=bot_token)
        self.client = self.app.client
        self.app_token = app_token

    def on_message(self, handler):
        @self.app.event("message")
        def _(event):
            if event.get("subtype") or event.get("bot_id"):  # edits, joins, bots
                return
            handler(_msg(event["channel"], event))

    def post(self, channel, thread, text, actions=()):
        """actions: [(label, action_id)] rendered as buttons."""
        blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": text}}]
        if actions:
            blocks.append({"type": "actions", "elements": [
                {"type": "button", "text": {"type": "plain_text", "text": label}, "action_id": aid}
                for label, aid in actions]})
        return self.client.chat_postMessage(channel=channel, thread_ts=thread, text=text, blocks=blocks)["ts"]

    def members(self, channel):
        return list(self._pages(self.client.conversations_members, "members", channel=channel))

    def presence(self, user):
        """'dnd' | 'away' | 'active'"""
        d = self.client.dnd_info(user=user)
        now = time.time()
        if d.get("snooze_enabled") or (d.get("dnd_enabled") and d["next_dnd_start_ts"] <= now < d["next_dnd_end_ts"]):
            return "dnd"
        return self.client.users_getPresence(user=user)["presence"]

    def fetch_thread(self, channel, thread):
        """Live fetch; never stored (ledger, not indexer)."""
        return [_msg(channel, m) for m in
                self._pages(self.client.conversations_replies, "messages", channel=channel, ts=thread)]

    def on_membership(self, handler):
        """handler(channel, user, joined: bool)"""
        @self.app.event("member_joined_channel")
        def _(event):
            handler(event["channel"], event["user"], True)

        @self.app.event("member_left_channel")
        def _(event):
            handler(event["channel"], event["user"], False)

    def channels(self):
        """(id, name) of public channels the bot is in (Q7: public only)."""
        return [(c["id"], c["name"]) for c in
                self._pages(self.client.users_conversations, "channels", types="public_channel")]

    def users(self):
        """Every human in the workspace, shaped for sync.load_slack_users."""
        for u in self._pages(self.client.users_list, "members"):
            if not u.get("is_bot") and u["id"] != "USLACKBOT":
                yield {"id": u["id"], "name": u["profile"].get("real_name") or u["name"],
                       "email": u["profile"].get("email"), "deleted": u["deleted"]}

    def profile(self, user):
        u = self.client.users_info(user=user)["user"]
        return u["profile"].get("real_name") or u["name"], u["profile"].get("email")

    def run(self):
        SocketModeHandler(self.app, self.app_token).start()

    @staticmethod
    def _pages(method, key, **kw):
        cursor = None
        while True:
            r = method(cursor=cursor, limit=200, **kw)
            yield from r[key]
            cursor = r.get("response_metadata", {}).get("next_cursor")
            if not cursor:
                return
