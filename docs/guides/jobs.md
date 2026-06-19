# Jobs module

The jobs module is a **complete job-search automation workflow** — CV-driven, with intelligent matching, auto-generated cover letters, and an application tracker.

![Jobs module](../assets/images/jobs-overview.png)

## Workflow

```
┌────────┐    ┌─────────┐    ┌────────┐    ┌──────────┐    ┌──────────┐
│  Setup │ →  │ Profile │ →  │ Search │ →  │   Apply  │ →  │  Tracker │
│ (CV)   │    │ (skills)│    │ (match)│    │ (cover)  │    │ (status) │
└────────┘    └─────────┘    └────────┘    └──────────┘    └──────────┘
```

The page has **4 tabs** that map to this pipeline.

---

## 1. Setup tab — CV upload

Upload your CV once, the parser extracts everything.

**Supported formats**: PDF, DOCX (DOC requires manual paste).

**Parser extracts**:

- Contact info (email, phone, location)
- Work history (company, title, dates, descriptions)
- Education (school, degree, dates)
- Skills (categorized)
- Certifications + dates

Under the hood:

- `pdfplumber` for PDF
- `python-docx` for DOCX
- LLM extraction for normalization (when `linkedin-mcp-llm-keys` is configured)

!!! tip "Tip"
    Re-upload anytime — the old CV is versioned, never deleted. The tracker always knows which CV version produced which application.

---

## 2. Profile tab — what you want

| Field | What it controls |
|-------|------------------|
| Target roles | Job title keywords (used in search query) |
| Skills (priority) | Boosted in match scoring |
| Salary range | Filter: jobs outside range hidden by default |
| Locations | Filter: remote / specific cities / countries |
| Visa status | Filter: requires-visa jobs excluded |
| Notice period | Used in cover letter |

The profile is saved per-account. Multi-account users get separate profiles.

---

## 3. Search tab — find + rank

Click **Find jobs** → the searcher hits LinkedIn Voyager with your filters + queries.

### Match scoring

Each job gets a score from 0–100:

| Component | Weight | What it measures |
|-----------|--------|------------------|
| **Keyword Jaccard** | 60% | Overlap between job description tokens and your CV tokens |
| **Skill-set bonus** | 30% | How many of your priority skills appear in the JD |
| **Title similarity** | 10% | Levenshtein distance between job title and your target roles |

Score thresholds:

| Score | Label | Recommendation |
|-------|-------|----------------|
| 80–100 | **Strong match** | Apply with confidence |
| 60–79 | **Good match** | Apply, customise cover |
| 40–59 | **Weak match** | Review manually |
| 0–39 | **Skip** | Don't apply |

### Filters

The searcher applies your profile filters automatically. Override per-search:

- Date posted (24h / week / month / anytime)
- Experience level (internship / entry / mid / senior / director)
- Job type (full-time / part-time / contract / internship)
- Remote / hybrid / on-site
- Industry
- Company size
- Under 10 applicants
- In your network
- Fair chance (LinkedIn's diversity filter)

---

## 4. Apply tab — generate + submit

### Cover letter

When you click **Apply** on a job:

1. Match score is recomputed against the **live** JD (not just the search snippet)
2. The cover letter generator picks one of 4 templates (intro / story / technical / culture-fit) based on score + job category
3. Template is filled with: company name, role, 2 of your strongest matching skills, 1 quantified achievement from your CV
4. You see the draft → edit if you want → click **Send**

### Eligibility gate

Before submission, the safety layer checks:

- ✅ Your profile has the required skills (else: blocked with explanation)
- ✅ Daily application cap not hit (else: queued for tomorrow)
- ✅ No duplicate application to same company within 90 days
- ✅ Job is still accepting applications
- ✅ easy_apply_only=True AND easy_apply=True (else: opens LinkedIn in browser for manual finish)

### Manual finish

If the job requires LinkedIn's manual flow (about 30% of listings), you're redirected to the LinkedIn Easy Apply form with your cover letter + profile pre-filled.

---

## 5. Tracker tab — status

Every application is logged with:

| Field | Source |
|-------|--------|
| Job ID | LinkedIn Voyager |
| Title + company + location | Search result |
| Match score | Computed at time of apply |
| Cover letter used | Stored version |
| CV version used | Stored version |
| Applied at | UTC timestamp |
| Status | Pending → Submitted → Viewed → Replied → Interview → Offer / Rejected |
| LinkedIn URL | For follow-up |

Updates flow in via the engagement listener — when the recruiter responds, the tracker marks the row automatically.

---

## CLI equivalent

For headless job hunting:

```bash
# Search with default profile
linkedin-mcp-jobs search --query "Senior Python Engineer" --location "Remote"

# Apply by ID (cover letter auto-generated)
linkedin-mcp-jobs apply --job-id 1234567890 --confirm

# Tracker dump
linkedin-mcp-jobs tracker --status pending --format csv
```

!!! note
    `linkedin-mcp-jobs` is not a separate binary — it's the same `linkedin-mcp-pro` MCP server exposed via the `jobs_*` tool family. The CLI uses the same tool registry.

---

## Privacy

- Your CV stays on your machine in `~/.linkedin-mcp/profile/cv/`
- Never sent to LinkedIn until you click **Apply**
- Never sent to any LLM unless you explicitly enable AI extraction in `/llm`
- Can be deleted at any time from `/settings`

---

## Next

- [Drafts & templates](drafts.md)
- [Scheduling](scheduling.md)