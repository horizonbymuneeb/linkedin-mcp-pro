# Drafts & templates

The drafts module is the heart of LinkedIn MCP Pro — where every post starts its life.

![Drafts composer](../assets/images/drafts-composer.png)

## Composer

The drafts page (`/drafts`) is a LinkedIn-styled composer with three modes:

### Mode 1: Manual

A rich-text editor with a **live LinkedIn preview** to the right. What you see is what LinkedIn will render — same fonts, same spacing, same hashtag colors.

Toolbar:

- **B** / *I* / ~strike~ / `code` formatting
- Bullet list, numbered list
- Inline link
- Emoji picker (system-native)
- Mention picker (autocomplete from your connections)
- Hashtag insert (auto-completes from your recent tags)
- Poll insert (LinkedIn polls)

Char counter: live, color-coded (green ≤ 700, amber 701–1300, red > 1300).

### Mode 2: AI assist

Enter a 1-line prompt → the LLM drafts a full post for you.

```text
Prompt: "announcing we raised series A, 12M led by sequoia, focus on AI agents"
```

The LLM is configurable at `/llm`. Default: whatever model you set in `OPENAI_API_KEY` env var (also supports Anthropic, OpenRouter, local via Ollama).

Output goes to the editor — you can tweak before saving.

### Mode 3: Template

Pick from your saved templates at `/templates`.

A template is a post with **placeholders**:

```text
Announcing: {{role}} at {{company}}!

We're hiring a {{role}} to join our {{team_size}}-person team.
{{pitch}}

If you're interested, DM me or apply here: {{link}}
```

When you use a template, you fill in the placeholders → saved as a new draft.

---

## Saving drafts

Three save states:

| State | Visible to | Editable |
|-------|------------|----------|
| **Draft** | You only | Yes |
| **Scheduled** | You only (until fired) | Yes (until queue runs) |
| **Posted** | Public | No (only delete via LinkedIn) |

Drafts auto-save every 30 seconds. Manual save: `Cmd/Ctrl+S` or **Save draft** button.

---

## Templates

Templates live at `/templates`. CRUD operations:

| Action | CLI | API |
|--------|-----|-----|
| List | `linkedin-mcp-templates list` | `GET /api/templates` |
| Add | `linkedin-mcp-templates add --name X --body Y` | `POST /api/templates` |
| Update | `linkedin-mcp-templates update --id X --body Y` | `PUT /api/templates/{id}` |
| Delete | `linkedin-mcp-templates delete --id X` | `DELETE /api/templates/{id}` |

Template variables are auto-extracted from `{{...}}` patterns. Use them in cover letters too.

---

## Hashtag intelligence

The composer suggests hashtags based on:

- Tags you've used before (frequency-ranked)
- Trending tags in your industry (LinkedIn search API)
- Tags from successful past posts (engagement-ranked)

Click to insert. Drag to reorder. Right-click to remove.

---

## Scheduling drafts

While saving, pick **Schedule for** → date/time picker → save.

See [Scheduling](scheduling.md) for the queue mechanics.

---

## MCP tools for drafts

```python
# AI agent example
drafts_list()
drafts_create(body="...", media=[...])
drafts_update(id="...", body="...")
drafts_delete(id="...")
drafts_publish(id="...")        # subject to safety gates
drafts_schedule(id="...", at="2026-01-15T14:00:00Z")
```

See [MCP tools reference](../reference/mcp-tools/index.md#drafts).

---

## Next

- [Scheduling](scheduling.md)
- [Engagement](engagement.md)