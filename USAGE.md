# linkedin-mcp-pro — Usage Guide

> Practical examples for using linkedin-mcp-pro with Claude Desktop, Cursor, Windsurf, or any MCP-compatible client.

This guide shows you **what to type** in your MCP client and **what happens** behind the scenes. If you have not installed it yet, see [README.md](README.md) first.

---

## Table of contents

1. [Quick start (60 seconds)](#quick-start-60-seconds)
2. [Common workflows](#common-workflows)
   - [Post about a topic](#post-about-a-topic)
   - [Send connection requests to a target audience](#send-connection-requests-to-a-target-audience)
   - [Search and explore](#search-and-explore)
   - [Read profile data](#read-profile-data)
   - [Send a direct message](#send-a-direct-message)
   - [Engage with content (likes, comments)](#engage-with-content-likes-comments)
3. [Prompting tips](#prompting-tips)
4. [Safety defaults](#safety-defaults)
5. [Troubleshooting](#troubleshooting)

---

## Quick start (60 seconds)

Once the server is running, just talk to your MCP client (Claude Desktop, Cursor, etc.) in natural language. The LLM picks the right tools automatically.

**You type:**

> "Search for 5 AI/ML engineers in Stockholm and tell me about their backgrounds."

**What the LLM does (you don't see this):**

```
1. search_people(keywords="AI", location="Stockholm", limit=5)
2. get_profile(public_id=...) for each
```

**What you get back:**

> I found 5 AI/ML engineers in Stockholm:
>
> 1. **Anna Lindberg** — Senior ML Engineer at Spotify. Works on music recommendation systems. Recently posted about LLM evaluation...
> 2. **Erik Johansson** — AI Research Scientist at Klarna. Background in NLP and vector search...

That's it. The LLM is your interface. You don't call tools directly.

---

## Common workflows

### Post about a topic

**You type:**

> "Write a post about fine-tuning Llama 3 for code generation, with a code snippet. Post it publicly."

**What happens:**

1. LLM drafts the post text with code formatting
2. LLM calls `create_post(text="...", visibility="PUBLIC", dry_run=true)` first to show you a preview
3. You confirm ("looks good, post it")
4. LLM calls `create_post(text="...", visibility="PUBLIC", dry_run=false)` to actually post

**The post body looks like this:**

```
🚀 Fine-tuned Llama 3 8B for code generation last weekend

Training snippet:

  from peft import LoraConfig
  config = LoraConfig(r=16, lora_alpha=32, target_modules=["q_proj","v_proj"])
  # 3 epochs, 2.1 → 0.8 loss

Result: passes HumanEval at 62% (base was 34%)

Code + dataset: github.com/yourname/yourrepo

#AI #LLM #OpenSource
```

**Things to mention in your prompt for better results:**
- The topic / angle ("fine-tuning", "RAG pipeline", "vector search")
- Whether to include code, a link, or just text
- Visibility: "public" or "connections only"
- Tone: "professional", "casual", "first-person story"

---

### Send connection requests to a target audience

**You type:**

> "Find 20 product managers in San Francisco working on AI products. Send them connection requests with personalized notes mentioning their company. Don't send more than 5 per day."

**What happens:**

1. LLM calls `search_people(keywords="product manager", location="San Francisco", limit=20)`
2. LLM filters to those with AI/ML in headline
3. LLM calls `get_profile(public_id)` for each to gather personalization context
4. LLM drafts a unique note for each person (varies the wording so they're not identical)
5. LLM calls `send_connection_request(public_id, note)` one at a time
6. Safety layer enforces your 5/day cap, blocks the rest with a clear message

**What the notes look like:**

For a PM at Notion: *"Hi Sarah — saw your post about Notion AI. Building similar context-aware tools, would love to compare notes."*

For a PM at Figma: *"Hey James, your talk on design systems + LLMs was great. I'm working on something adjacent, happy to chat."*

**Things to mention in your prompt:**
- The audience: keywords, location, role, industry
- The number you want
- The note style: "personalized", "short and casual", "mention their company", or "no note at all"
- A daily cap: "5 per day" or "spread over a week"

**Important:** The LLM will use `dry_run=true` by default for safety. You'll see a preview like:

> I'll send these 5 connection requests (preview):
> 1. To **Anna L.** (Spotify, Senior ML) — note: "Hi Anna..."
> 2. To **Erik J.** (Klarna, AI Research) — note: "Hey Erik..."
>
> Proceed? (yes/no)

---

### Search and explore

**You type:**

> "What are the top 10 most-viewed jobs for 'AI engineer' in Sweden posted this week?"

**What happens:**

1. LLM calls `search_jobs(keywords="AI engineer", location="Sweden", limit=10)`
2. Returns raw job data
3. LLM summarizes: company, title, posted date, key requirements

**Other search prompts:**

> "Show me the 5 most recent posts from my connections about machine learning."

→ Uses `get_feed(limit=5)` + LLM filters by topic.

> "Who are the founders of Series A AI startups in Berlin?"

→ Uses `search_companies(keywords="AI", location="Berlin")` + LLM filters by stage/role.

> "Find recruiters at Google hiring for ML roles."

→ Uses `search_people(keywords="recruiter", company="Google")`.

---

### Read profile data

**You type:**

> "What does Sundar Pichai's profile say? Show me his current role, education, and recent activity."

**What happens:**

1. LLM calls `get_profile(public_id="sundarpichai")`
2. Returns profile data
3. LLM summarizes

**Note:** Profile data is read-only and uses LinkedIn's internal API (Voyager). Fast, no browser needed, doesn't count against any quota.

**Other profile prompts:**

> "Look up John Doe's profile and tell me if he works on AI."

> "Compare the profiles of these 3 people: alice, bob, carol. Who has the most ML experience?"

> "Show me my own profile. What does my headline say?"

→ Uses `get_my_profile()`.

---

### Send a direct message

**You type:**

> "Send a message to Anna Lindberg: 'Hi Anna, enjoyed your post on vector search. Would love to chat about RAG pipelines next week.'"

**What happens:**

1. LLM calls `send_message(public_id="anna-lindberg", text="...")`
2. Browser automation opens LinkedIn messaging
3. Composes and sends
4. Returns confirmation

**Important constraints:**
- You can only message **1st-degree connections** (people you're already connected to)
- Max 8,000 characters per message
- Daily limit: 30 messages/day (configurable)
- Business hours only (default Mon-Fri, 9am-8pm)

**Other messaging prompts:**

> "Send a thank-you message to my 3 most recent connections who accepted my invites today."

> "Reply to my unread messages from recruiters with: 'Thanks for reaching out, currently not looking but will keep you in mind.'"

---

### Engage with content (likes, comments)

**You type:**

> "Like and comment 'great post!' on the 3 most recent posts from my connections about AI."

**Current state (v0.1):** Comments and reactions are stubbed — they require a post URL, not just a URN, which needs a small v0.2 schema change.

**Workaround in v0.1:**

> "Show me the URLs of the 3 most recent posts from my connections about AI."

→ LLM fetches the feed and shows URLs.

Then you can navigate to those URLs manually, or wait for v0.2.

**Track this:** https://github.com/horizonbymuneeb/linkedin-mcp-pro/issues

---

## Prompting tips

The LLM is good at understanding natural language, but you get better results with specific prompts.

### Be specific about the goal

| Vague | Specific |
|---|---|
| "Help me with LinkedIn" | "Find 10 AI engineers in Stockholm and draft connection notes for each" |
| "Post about my work" | "Post about my new RAG pipeline, include a code snippet, public visibility" |
| "Connect with people" | "Send connection requests to 5 product managers at fintech startups in London" |

### Specify constraints

- **Numbers:** "5 per day", "10 total", "spread over 2 weeks"
- **Tone:** "professional and friendly", "casual", "first-person"
- **Personalization:** "mention their company", "reference their recent post", "no note at all"
- **Safety:** "preview first", "ask before sending", "skip if already connected"

### Use the dry-run pattern

For anything that posts, sends, or messages, you can ask:

> "Show me a preview first, don't actually send."

The LLM will use `dry_run=true` and show you the exact action before doing it.

### Chain actions

> "1) Search for AI recruiters in Sweden. 2) Read their profiles. 3) Draft 3 unique connection notes for the most relevant ones. 4) Show me the drafts before sending."

The LLM handles multi-step plans naturally.

### Reference previous results

> "Take those 5 people from the last search and send them connection requests."

The LLM has conversation memory, so it can chain.

---

## Safety defaults

These are enforced at the database level — they cannot be bypassed by the LLM.

| Action | Daily cap | Notes |
|---|---|---|
| Connection requests | 20 | Plus warmup: 5 in week 1, 10 in week 2, 15 in week 3 |
| Posts | 2 | Low cap to avoid LinkedIn's algorithm flagging spam |
| Messages | 30 | 1st-degree connections only |
| Comments | 30 | (v0.2 — currently stubbed) |
| Reactions | 100 | (v0.2 — currently stubbed) |

**Other safety rules:**

- **Business hours only** — Mon-Fri, 9am-8pm (configurable)
- **Jitter between actions** — 3-15 minutes random delay
- **Warmup mode** — If your account is new, caps are lower for the first 4 weeks
- **Audit log** — Every action is logged to a local SQLite database

If you hit a cap, the LLM will tell you:

> "You've used all 20 connection requests for today. You can send more tomorrow, or I can queue them for the next 5 business days."

You can ask to override specific safety rules in your prompt, but the database will still enforce them. To change defaults, edit `.env`:

```bash
DAILY_LIMIT_CONNECTION_REQUESTS=20
DAILY_LIMIT_POSTS=2
BUSINESS_HOURS_START=9
BUSINESS_HOURS_END=20
```

---

## Troubleshooting

### "Authentication failed" / "li_at expired"

Your session cookie expired (they last ~7 days). Get a fresh one:

1. Open LinkedIn in your browser, log in
2. DevTools → Application → Cookies → `https://www.linkedin.com` → `li_at`
3. Copy the value, update `.env`:
   ```bash
   LI_AT=AQEDAT...
   ```
4. Restart the MCP server

### "Rate limited (429)"

LinkedIn is throttling you. The safety layer will:
1. Wait the time LinkedIn told it to (from `Retry-After` header)
2. Or back off exponentially (2s, 4s, 8s)
3. Pause all writes for the rest of the day if persistent

This usually means you hit a daily quota, or LinkedIn flagged your account. Reduce caps in `.env` for a few days.

### "No Connect button found" / "No Post button found"

LinkedIn's UI changed slightly. The browser automation uses accessibility tree refs, which can break if:
- LinkedIn A/B tests a new layout
- You're using LinkedIn in a language other than English
- You have a slow network and the page didn't load

**Workaround:** Wait 24h and retry. If persistent, file an issue with the page URL.

### "No tools available" in Claude Desktop

Your `claude_desktop_config.json` doesn't have the MCP server registered. See [README.md](README.md) → "Usage with Claude Desktop".

### Posts are not appearing

LinkedIn sometimes delays posts by 1-5 minutes. Check your profile manually. If still missing after 10 minutes, the post may have been silently rejected by LinkedIn (rare, but happens if the content triggered a filter).

### Sessions keep expiring

You're being logged out by LinkedIn frequently. Possible causes:
- Another browser logged you out (concurrent sessions)
- LinkedIn security flagged activity (reduce daily caps)
- Cookie is being read from a stale location (check `BROWSER_PROFILE_DIR` in `.env`)

---

## Advanced usage

### Custom safety rules per action

Edit `.env`:

```bash
# Allow posting on weekends too
BUSINESS_DAYS=mon,tue,wed,thu,fri,sat,sun

# Disable warmup (not recommended for new accounts)
WARMUP_ENABLED=false

# Longer jitter for higher stealth
ACTION_JITTER_MIN_SECONDS=300
ACTION_JITTER_MAX_SECONDS=1800
```

### Running with HTTP transport (for remote clients)

```bash
MCP_TRANSPORT=streamable-http \
MCP_HOST=0.0.0.0 \
MCP_PORT=8765 \
linkedin-mcp serve
```

Then point any MCP client at `http://your-server:8765`.

### Docker deployment

```bash
docker build -t linkedin-mcp-pro .
docker run -it --rm \
  -e LI_AT=... \
  -v $(pwd)/data:/app/data \
  linkedin-mcp-pro
```

---

## What to do next

1. Read [SAFETY.md](docs/SAFETY.md) — understand the ban-prevention design
2. Read [ARCHITECTURE.md](docs/ARCHITECTURE.md) — understand how the pieces fit together
3. Try a few dry-run actions to see the flow
4. Start with low caps (5/day) for the first week

Questions? Issues? https://github.com/horizonbymuneeb/linkedin-mcp-pro/issues
