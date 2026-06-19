# Dashboard tour

The web dashboard at <http://localhost:8080> is a **15-page unified shell** — every page shares the same sidebar + topbar so navigation is instant and the visual language stays consistent.

![Dashboard overview](../assets/images/dashboard-home.png)

## Design system

| Token | Value | Used for |
|-------|-------|----------|
| `--app-bg` | `#08090a` | Page background (Linear dark) |
| `--app-panel` | `#0f1011` | Sidebar, topbar |
| `--app-surface` | `#191a1b` | Cards, inputs |
| `--accent` | `#5e6ad2` | Primary buttons, links, focus rings |
| `--li-blue` | `#0a66c2` | LinkedIn brand accents in content cards |
| `--li-bg` | `#f4f2ee` | Light-mode content surface |
| Font | Inter | All UI text |
| Mono | JetBrains Mono | Code blocks, IDs |

Light/dark mode toggles via the sun/moon icon in the topbar and persists per-browser via `localStorage`.

---

## Page index

The 15 pages are organized into 3 sections:

### Workspace — what you do every day

| Page | Path | What it's for |
|------|------|---------------|
| **Home** | `/` | Dashboard overview: KPI cards, recent activity, quick links |
| **Drafts** | `/drafts` | Compose posts (manual / AI / template) with live preview |
| **Schedule** | `/schedules` | Calendar view of upcoming posts + queue management |
| **Engagement** | `/engagement` | Inbox of comments/DMs to respond to (auto + manual) |
| **Jobs** | `/jobs` | CV-driven job search, match scoring, application tracker |
| **Analytics** | `/analytics` | Engagement heatmap, best-time-to-post, AB test results |

### Account — your presence

| Page | Path | What it's for |
|------|------|---------------|
| **Connect** | `/connect` | LinkedIn login (browser-driven or paste li_at) |
| **Cookies** | `/cookies` | Manage cookies, see session health, rotate |
| **Profile** | `/profile` | Your LinkedIn profile data (synced from Voyager) |

### Configure — system setup

| Page | Path | What it's for |
|------|------|---------------|
| **LLM** | `/llm` | Manage LLM API keys (for AI drafts, cover letters) |
| **Safety** | `/safety` | View/modify ban-safety rules, daily caps, velocity windows |
| **Audit** | `/audit` | Every action log with timestamps + safety verdicts |
| **Install** | `/install` | Wire any MCP host (Claude Desktop, Cursor, etc.) |
| **Settings** | `/settings` | App preferences, theme, storage location |
| **Templates** | `/templates` | Post templates (text + variables) |

---

## Common UI patterns

### Sidebar navigation

- **240px fixed**, dark Linear panel
- Active page highlighted with accent border-left
- Server status dot (green = running, amber = degraded, red = down)
- Account switcher at bottom (when multi-account enabled)

### Topbar

- **56px fixed**, hairline border-bottom
- Breadcrumbs on the left
- Command palette trigger (⌘K) on the right — *coming soon*
- Light/dark toggle + profile menu

### Content cards

LinkedIn-style cards with `#f4f2ee` background, `#0a66c2` accent on hover, hairline `#e0dfdc` borders. Used for posts, drafts, jobs.

### KPI cards

Big number + label + delta indicator (↑ green, ↓ red). Used on Home + Analytics.

---

## Keyboard shortcuts

| Shortcut | Action |
|----------|--------|
| `g` then `h` | Go home |
| `g` then `d` | Go to drafts |
| `g` then `j` | Go to jobs |
| `g` then `a` | Go to analytics |
| `c` | New composition (drafts) |
| `/` | Focus search (when on home) |
| `?` | Show shortcuts |

---

## Mobile experience

- Sidebar collapses into a drawer (hamburger menu)
- Topbar stays sticky
- Cards reflow to single column at <768px
- Composer toolbar becomes touch-friendly

---

## Next steps

- [Jobs module deep dive](jobs.md)
- [Drafts + templates](drafts.md)
- [Scheduling](scheduling.md)
- [Engagement workflow](engagement.md)