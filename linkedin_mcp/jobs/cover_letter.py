"""Cover-letter generation.

Uses the existing LLM pool (linkedin_mcp.llm_router or the configured
provider) to write a tailored cover letter given the user's profile + a
job description. If no LLM is available, falls back to a template.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


# ==================== TEMPLATES (fallback) ====================

_TEMPLATES: dict[str, str] = {
    "default": (
        "Hi {{company}} team,\n\n"
        "I came across the {{title}} role and wanted to reach out directly. "
        "My background in {{top_skills}} aligns well with what you're building — "
        "{{experience_line}}.\n\n"
        "I'm excited about the work you're doing and would love to bring my "
        "experience to the team. Happy to chat at your convenience.\n\n"
        "Best,\n{{name}}"
    ),
    "concise": (
        "Hi {{company}},\n\n"
        "{{experience_line}}. I'm interested in the {{title}} role — "
        "happy to share more if useful.\n\n{{name}}"
    ),
    "warm": (
        "Hi {{company}} team,\n\n"
        "Loved seeing the {{title}} opening — the work you're doing resonates with "
        "what I care about. {{experience_line}}.\n\n"
        "Would love to chat — let me know if you'd like to set up a quick call.\n\n{{name}}"
    ),
    "founder": (
        "Hi {{company}} founders,\n\n"
        "{{experience_line}}. I'm drawn to early-stage work and the {{title}} role "
        "looks like a strong match. {{top_skills}}.\n\n"
        "Keen to learn more about the team and what you're building.\n\n{{name}}"
    ),
}


def get_templates() -> list[dict[str, str]]:
    return [{"name": k, "preview": v[:120] + "..."} for k, v in _TEMPLATES.items()]


# ==================== HELPERS ====================


def _first_n(items: list[Any] | None, n: int = 3) -> list[Any]:
    if not items:
        return []
    return items[:n]


def _format_fallback(profile: dict[str, Any], job: dict[str, Any], template: str = "default") -> str:
    """Render a template-based cover letter (no LLM needed)."""
    tmpl = _TEMPLATES.get(template, _TEMPLATES["default"])
    skills = profile.get("skills") or []
    top_skills = ", ".join(s for s in _first_n(skills, 3))
    if not top_skills:
        top_skills = "software engineering and product development"

    experience_line = ""
    for e in _first_n(profile.get("experience"), 1):
        title = (e or {}).get("title") or "engineer"
        company = (e or {}).get("company") or ""
        if company:
            experience_line = f"Most recently, I worked as a {title} at {company}"
        else:
            experience_line = f"Most recently, I worked as a {title}"

    if not experience_line and profile.get("summary"):
        experience_line = profile["summary"][:200]

    rendered = tmpl
    rendered = rendered.replace("{{company}}", job.get("company") or "your company")
    rendered = rendered.replace("{{title}}", job.get("title") or "this role")
    rendered = rendered.replace("{{name}}", profile.get("name") or "—")
    rendered = rendered.replace("{{top_skills}}", top_skills)
    rendered = rendered.replace("{{experience_line}}", experience_line or "I've been working in this space for several years")
    return rendered


def _build_prompt(profile: dict[str, Any], job: dict[str, Any], tone: str = "professional") -> tuple[str, str]:
    """Return (system, user) messages for the LLM."""
    skills = ", ".join((profile.get("skills") or [])[:10])
    experience_bullets: list[str] = []
    for e in (profile.get("experience") or [])[:3]:
        title = e.get("title") or "engineer"
        company = e.get("company") or ""
        dates = e.get("dates") or ""
        if title or company:
            line = f"- {title}"
            if company:
                line += f" @ {company}"
            if dates:
                line += f" ({dates})"
            experience_bullets.append(line)
    experience_block = "\n".join(experience_bullets) or "- (no structured experience extracted)"

    system = (
        "You write concise, warm, specific cover letters for LinkedIn Easy Apply.\n"
        "Constraints: under 200 words, no clichés ('passionate', 'rockstar', 'guru'), "
        "no fabricated experience, 2 short paragraphs + sign-off. "
        f"Tone: {tone}."
    )

    user = (
        f"CANDIDATE\n"
        f"Name: {profile.get('name', 'Unknown')}\n"
        f"Headline: {profile.get('headline', '—')}\n"
        f"Top skills: {skills or '—'}\n"
        f"Experience:\n{experience_block}\n\n"
        f"JOB\n"
        f"Title: {job.get('title', 'Unknown')}\n"
        f"Company: {job.get('company', 'Unknown')}\n"
        f"Location: {job.get('location', '—')}\n"
        f"Description (truncated):\n"
        f"{(job.get('description') or '')[:1500]}\n\n"
        f"Write the cover letter."
    )
    return system, user


# ==================== MAIN ====================


def generate(
    profile: dict[str, Any],
    job: dict[str, Any],
    tone: str = "professional",
    template: str = "default",
) -> dict[str, Any]:
    """Generate a cover letter.

    Returns: {"text": str, "source": "llm" | "template", "model": str|None}
    """
    # Try LLM first if available
    llm_text: str | None = None
    model_used: str | None = None
    try:
        from linkedin_mcp.llm_router import chat  # type: ignore

        system, user = _build_prompt(profile, job, tone)
        out = chat(
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            max_tokens=400,
            temperature=0.7,
        )
        if isinstance(out, dict):
            llm_text = out.get("text") or out.get("content")
            model_used = out.get("model")
        elif isinstance(out, str):
            llm_text = out
    except Exception as e:
        logger.info("LLM unavailable for cover letter, using template: %s", e)

    if llm_text and llm_text.strip():
        return {"text": llm_text.strip(), "source": "llm", "model": model_used}

    # Fallback to template
    return {
        "text": _format_fallback(profile, job, template=template),
        "source": "template",
        "model": None,
    }
