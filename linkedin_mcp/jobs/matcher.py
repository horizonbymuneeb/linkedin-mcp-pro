"""Job ↔ CV matching using keyword overlap + sentence-transformers embedding cosine.

Falls back to a pure-keyword scorer (Jaccard / TF overlap) if the embedding
model can't be loaded. Either way, the output is a 0-100 score plus a list
of human-readable reasons ("5/7 skills matched", "+5 for remote match", etc).
"""
from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# Optional heavy import — degrade gracefully
try:
    from sentence_transformers import SentenceTransformer  # type: ignore

    _MODEL: Any = None
    _MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
    try:
        from sentence_transformers import util as _st_util  # type: ignore
    except Exception:
        _st_util = None

    def _get_model() -> Any:
        global _MODEL
        if _MODEL is None:
            _MODEL = SentenceTransformer(_MODEL_NAME)
        return _MODEL

    def _cos_sim(a: Any, b: Any) -> float:
        """Cosine similarity that works whether or not `util` imported."""
        if _st_util is not None:
            return float(_st_util.cos_sim(a, b).item())
        # numpy fallback
        import numpy as np
        a_np = a.cpu().numpy() if hasattr(a, "cpu") else np.asarray(a)
        b_np = b.cpu().numpy() if hasattr(b, "cpu") else np.asarray(b)
        denom = (np.linalg.norm(a_np) * np.linalg.norm(b_np)) or 1e-9
        return float(np.dot(a_np, b_np) / denom)

    _HAS_MODEL = True
except Exception as e:  # pragma: no cover
    logger.warning("sentence-transformers unavailable, using keyword-only matching: %s", e)
    _HAS_MODEL = False

    def _get_model() -> Any:  # type: ignore
        return None

    def _cos_sim(a: Any, b: Any) -> float:  # type: ignore
        return 0.0


# ==================== TOKENIZATION ====================

_WORD_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9+#.-]{1,}")


def _tokens(text: str) -> set[str]:
    return {t.lower() for t in _WORD_RE.findall(text or "")}


# ==================== SCORING ====================


def _keyword_overlap_score(cv_text: str, jd_text: str) -> tuple[int, list[str]]:
    """Return (score 0-50, reasons) from lexical overlap."""
    cv_t = _tokens(cv_text)
    jd_t = _tokens(jd_text)
    if not jd_t:
        return 0, []
    common = cv_t & jd_t
    # jaccard
    union = cv_t | jd_t
    jacc = len(common) / max(1, len(union))
    score = int(round(jacc * 100))
    # cap to 50 from this component
    capped = min(50, int(round(jacc * 60)))
    reasons: list[str] = [f"{len(common)} shared keywords with JD (jaccard {jacc:.2f})"]
    return capped, reasons[:5]


def _skill_list_bonus(profile_skills: list[str], job_text: str) -> tuple[int, list[str]]:
    """If the profile has an explicit skills list, reward matches against the JD text."""
    if not profile_skills:
        return 0, []
    jd_t = _tokens(job_text)
    if not jd_t:
        return 0, []
    matched = 0
    for s in profile_skills:
        for tok in _tokens(s):
            if tok and tok in jd_t:
                matched += 1
                break
    if not profile_skills:
        return 0, []
    ratio = matched / len(profile_skills)
    bonus = int(round(ratio * 20))  # up to +20
    return bonus, [f"{matched}/{len(profile_skills)} profile skills mentioned in JD"]


def _embedding_score(cv_text: str, jd_text: str) -> tuple[int, list[str]]:
    """Cosine similarity 0-1 → 0-50 score component."""
    if not _HAS_MODEL:
        return 0, []
    try:
        model = _get_model()
        emb = model.encode([cv_text[:5000], jd_text[:5000]], convert_to_tensor=True, normalize_embeddings=True)
        sim = _cos_sim(emb[0], emb[1])  # -1..1
        # map 0.3..1.0 → 0..50
        sim_clamped = max(0.0, min(1.0, (sim - 0.3) / 0.7))
        score = int(round(sim_clamped * 50))
        return score, [f"semantic similarity {sim:.2f}"]
    except Exception as e:
        logger.warning("embedding score failed: %s", e)
        return 0, []


def _preference_score(profile: dict[str, Any], job: dict[str, Any]) -> tuple[int, list[str]]:
    """Bonus/penalty based on profile preferences. -20 to +20 range."""
    bonus = 0
    reasons: list[str] = []
    work_mode = profile.get("work_mode")
    remote = bool(job.get("remote"))
    if work_mode == "remote" and remote:
        bonus += 10
        reasons.append("+10 remote matches your preference")
    elif work_mode == "onsite" and not remote:
        bonus += 5
        reasons.append("+5 onsite matches")
    elif work_mode == "remote" and not remote:
        bonus -= 5
        reasons.append("-5 job is not remote (you prefer remote)")
    cities = [c.lower() for c in (profile.get("cities") or [])]
    loc = (job.get("location") or "").lower()
    if cities and loc and any(c in loc for c in cities):
        bonus += 5
        reasons.append("+5 location matches your city list")
    seniority = [s.lower() for s in (profile.get("seniority") or [])]
    title = (job.get("title") or "").lower()
    for s in seniority:
        if s in title:
            bonus += 3
            reasons.append(f"+3 title matches seniority '{s}'")
            break
    blacklist = [c.lower() for c in (profile.get("blacklist_companies") or [])]
    company = (job.get("company") or "").lower()
    if company and any(b in company for b in blacklist):
        bonus -= 50
        reasons.append("-50 blacklisted company")
    return bonus, reasons


def score_job(profile: dict[str, Any], job: dict[str, Any], cv_text: str = "") -> dict[str, Any]:
    """Score a job (0-100) against a profile + CV.

    Returns: {"score": int, "reasons": list[str]}
    """
    # Build CV text from profile (skills + experience summaries + summary)
    if not cv_text:
        parts: list[str] = []
        for k in ("summary", "headline"):
            if profile.get(k):
                parts.append(str(profile[k]))
        for s in profile.get("skills") or []:
            parts.append(str(s))
        for e in profile.get("experience") or []:
            parts.append(str(e.get("title") or ""))
            parts.append(str(e.get("description") or ""))
        for ed in profile.get("education") or []:
            parts.append(str(ed.get("school") or ""))
        cv_text = " ".join(parts)

    jd_text = (job.get("description") or "") + " " + (job.get("title") or "")
    kw_score, kw_reasons = _keyword_overlap_score(cv_text, jd_text)
    em_score, em_reasons = _embedding_score(cv_text, jd_text)
    pref_bonus, pref_reasons = _preference_score(profile, job)
    sk_bonus, sk_reasons = _skill_list_bonus(profile.get("skills") or [], jd_text)
    total = max(0, min(100, kw_score + em_score + pref_bonus + sk_bonus))
    return {
        "score": total,
        "reasons": kw_reasons + em_reasons + pref_reasons + sk_reasons,
    }
