# Engagement

The engagement module handles inbound interactions — comments, mentions, DMs, connection requests.

![Engagement inbox](../assets/images/engagement.png)

## Inbox

`/engagement` shows a triaged inbox of:

- 🔔 **Mentions** — someone tagged you
- 💬 **Comments** — on your posts
- 📩 **DMs** — direct messages
- 🤝 **Connection requests** — pending
- 🎉 **Reactions** — likes/celebrates on your posts

Each item is scored:

| Score | What it means |
|-------|---------------|
| **VIP** | 1st-degree connection + recent engagement with you |
| **Hot** | 2nd-degree or frequent commenter |
| **Warm** | Cold connection but relevant topic |
| **Cold** | Spam-like, ignored by default |

---

## Auto-reply

For **Hot** and **VIP** items, the system can auto-reply using:

- A canned response (templates)
- LLM-generated reply (using conversation context)

Enable in `/settings` → Auto-engagement.

**Safety caps**:

- Max 20 auto-replies per day (configurable)
- Max 3 per conversation thread
- No auto-reply to DMs (always manual)
- No auto-reply to cold items

---

## Comment moderation

For your **own posts**:

1. The system monitors every new comment
2. Classifies as: positive / question / negative / spam
3. Spam → auto-hide (with manual override in `/audit`)
4. Question → notify you, offer 1-click reply
5. Positive → thank-you reply (if enabled)
6. Negative → notify only, never auto-reply

---

## Auto-like

The auto-like engine likes posts from your feed based on:

- Topics you follow (configurable)
- Authors in your network
- Posts similar to ones you've liked before

Disabled by default. Enable at `/settings`.

**Daily cap**: 50 likes (LinkedIn-friendly).

---

## Connection requests

Auto-accept rules:

- Mutual connections ≥ 5 → accept
- Same company → accept
- Same industry + senior role → accept
- Otherwise → manual review queue

Auto-send rules:

- After viewing someone's profile for >30s + they're in your target industry → send request with personalized note
- Daily cap: 20 (LinkedIn-friendly)

---

## RSS / webhook posting

Trigger posts from external sources:

```bash
# Webhook URL: http://localhost:8080/api/webhooks/rss
curl -X POST http://localhost:8080/api/webhooks/rss \
    -H "Content-Type: application/json" \
    -d '{"title": "...", "body": "...", "link": "..."}'
```

Auto-creates a draft, optionally auto-schedules. Configure at `/settings` → Integrations.

---

## Analytics feedback loop

Every engagement action is logged with outcome (was the person warm / did they follow back / etc). The system learns:

- Which reply styles get more positive replies
- Which auto-liked posts lead to profile views
- Which auto-accepted connections become engaged followers

This feeds the **engagement quality score** shown on `/analytics`.

---

## MCP tools

```python
engagement_list(kind="mentions", since="24h")
engagement_reply(id="...", body="...")
engagement_hide(id="...", reason="spam")
engagement_accept_connection(id="...")
engagement_send_connection(target="...", note="...")
```

See [MCP tools reference](../reference/mcp-tools/index.md#engagement).

---

## Next

- [Multi-account](multi-account.md)
- [Safety system](../operations/safety.md)