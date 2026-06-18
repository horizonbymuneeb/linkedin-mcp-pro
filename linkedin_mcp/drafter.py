"""AI Post Drafter for linkedin-mcp-pro (v0.6.0, Tier 1).

Turns a topic / link / rough idea into a human-sounding LinkedIn post
draft by calling a local OpenAI-compatible LLM over HTTP. Uses only
``urllib`` (no extra deps) and a strict system prompt designed to keep
the output free of "AI-speak" and safe for LinkedIn's 3000-char cap.

Configuration
-------------
All knobs are env vars (read once at import-time, overridable per
instance)::

    LINKEDIN_MCP_DRAFT_BASE_URL   default http://127.0.0.1:5000/v1
    LINKEDIN_MCP_DRAFT_API_KEY    default sk-local
    LINKEDIN_MCP_DRAFT_MODEL      default minimax-pool
    LINKEDIN_MCP_DRAFT_TIMEOUT    default 30 (seconds)

Public surface
--------------
::

    drafter = PostDrafter()
    text = drafter.draft(
        "Why we switched to SQLite for our MCP server",
        tone="thought-leader",
        length=600,
        include_hashtags=False,
    )

    # Read the last response (for tests / inspection)
    drafter.last_model
    drafter.last_usage
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Mapping


# --- Defaults / env ----------------------------------------------------------

DEFAULT_BASE_URL = "http://127.0.0.1:5000/v1"
DEFAULT_API_KEY = "sk-local"
DEFAULT_MODEL = "minimax-pool"
DEFAULT_TIMEOUT = 30  # seconds

# LinkedIn hard cap — see linkedin_mcp.safety.SafetyConfig if you want
# to align with a softer in-house cap.
LINKEDIN_MAX_CHARS = 3000

VALID_TONES: tuple[str, ...] = (
    "professional",
    "casual",
    "thought-leader",
    "storytelling",
)


# --- System prompt -----------------------------------------------------------

# Note: keep the forbidden words *in the prompt* (as "do not use") so the
# model sees them and avoids them. Keep the prompt itself free of them
# in the imperative voice so we don't trip ourselves with our own
# "delve into the…" phrasing.
_SYSTEM_PROMPT_BASE = """\
You write LinkedIn posts that read like a real human wrote them, not \
an LLM. Follow these rules without exception:

Voice & style
- Plain, direct sentences. Contractions are fine ("don't", "we're").
- Short paragraphs (1-3 sentences) with line breaks between them.
- One idea per paragraph. No rambling lists disguised as prose.
- No "delve", "tapestry", "navigate", "leverage", "in today's fast-paced \
world", "in conclusion", "it's not just X, it's Y", or any other \
cliched AI phrasing.
- No emojis, decorative dividers, or hashtag spam. The only hashtags \
allowed are at the very end and only if explicitly requested.
- No preamble ("Here is your post:"). No trailing "Hope this helps!".

Content
- Lead with a concrete observation, not a generic statement.
- If the user gives a link, anchor the post in a specific detail from it \
(not a vague summary).
- Make a clear point. State it, defend it briefly, close with a one-line \
takeaway.
- Stay under {max_chars} characters total (hard cap, LinkedIn limit).

Tone
- Match the requested tone exactly: {tone}.
- {tone_specific}

Output
- Return ONLY the post body. No markdown fences, no commentary, no \
labels, no quotes around the output.
"""


_TONE_GUIDANCE: Mapping[str, str] = {
    "professional": (
        "Polished but not stiff. Industry-standard vocabulary, third-person "
        "where natural, no slang. Think senior IC writing for peers."
    ),
    "casual": (
        "Conversational, light contractions, occasional first-person "
        "anecdote. Reads like a coffee-chat post from a friend who happens "
        "to know the topic well. Not try-hard, not 'linkedin bro'."
    ),
    "thought-leader": (
        "Confident, opinionated, willing to take a stance. Back the opinion "
        "with a concrete reason or example. Avoid fortune-cookie wisdom."
    ),
    "storytelling": (
        "Open with a small concrete scene or moment. Build to a lesson in "
        "3-5 short paragraphs. End on a single-line takeaway, not a "
        "question to the audience."
    ),
}


# --- Errors ------------------------------------------------------------------


class DrafterError(RuntimeError):
    """Base class for drafter errors."""


class DrafterConfigError(DrafterError):
    """Bad configuration (missing URL, bad tone, etc.)."""


class DrafterBackendError(DrafterError):
    """The LLM backend returned an error or non-2xx response."""


# --- Implementation ----------------------------------------------------------


class PostDrafter:
    """Generate LinkedIn post drafts via a local OpenAI-compatible LLM.

    Parameters
    ----------
    base_url:
        Root URL of the OpenAI-compatible API. Must end in ``/v1`` (or
        whatever path serves ``/chat/completions``). Read from
        ``LINKEDIN_MCP_DRAFT_BASE_URL`` if not given.
    api_key:
        Bearer token. Read from ``LINKEDIN_MCP_DRAFT_API_KEY`` if not
        given. The string ``"sk-local"`` is fine for trusted loopback
        servers.
    model:
        Model name to request. Read from
        ``LINKEDIN_MCP_DRAFT_MODEL`` if not given.
    timeout:
        HTTP timeout in seconds. Read from
        ``LINKEDIN_MCP_DRAFT_TIMEOUT`` if not given.
    """

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        timeout: int | None = None,
    ) -> None:
        self.base_url = (
            base_url
            or os.environ.get("LINKEDIN_MCP_DRAFT_BASE_URL")
            or DEFAULT_BASE_URL
        ).rstrip("/")
        self.api_key = (
            api_key
            or os.environ.get("LINKEDIN_MCP_DRAFT_API_KEY")
            or DEFAULT_API_KEY
        )
        self.model = (
            model
            or os.environ.get("LINKEDIN_MCP_DRAFT_MODEL")
            or DEFAULT_MODEL
        )
        # ``int(...)`` on a malformed env var (e.g. "abc") would raise
        # ValueError; the explicit try/except keeps the error message
        # friendly and uses the default on garbage input.
        if timeout is not None:
            self.timeout = int(timeout)
        else:
            raw = os.environ.get("LINKEDIN_MCP_DRAFT_TIMEOUT")
            try:
                self.timeout = int(raw) if raw else DEFAULT_TIMEOUT
            except (TypeError, ValueError):
                self.timeout = DEFAULT_TIMEOUT

        # Inspectable after a call. Useful for tests and for callers
        # that want to surface "drafted by <model>" in UIs.
        self.last_model: str | None = None
        self.last_usage: dict[str, int] | None = None
        self.last_raw: dict[str, Any] | None = None

    # -- public API --------------------------------------------------------

    def draft(
        self,
        topic: str,
        *,
        tone: str = "professional",
        length: int = 800,
        include_hashtags: bool = False,
        system_prompt: str | None = None,
    ) -> str:
        """Return a draft post body for ``topic``.

        ``length`` is a *soft* target — we pass it to the model in the
        user message but the cap that actually applies is
        :data:`LINKEDIN_MAX_CHARS`. We post-process to clamp the result
        to that cap and strip a few recurring AI artifacts.
        """
        if not topic or not topic.strip():
            raise DrafterConfigError("topic must be a non-empty string")
        if tone not in VALID_TONES:
            raise DrafterConfigError(
                f"invalid tone {tone!r}; must be one of {list(VALID_TONES)}"
            )
        if length < 50:
            raise DrafterConfigError("length must be >= 50 characters")
        if length > LINKEDIN_MAX_CHARS:
            # We still let it through — the system prompt enforces the
            # 3000-char ceiling; this is a guardrail for the *caller's*
            # intent, not the model.
            length = LINKEDIN_MAX_CHARS

        sys_prompt = system_prompt or self._build_system_prompt(
            tone=tone, include_hashtags=include_hashtags
        )
        user_prompt = self._build_user_prompt(
            topic=topic, length=length, include_hashtags=include_hashtags
        )

        payload = self._call_chat(sys_prompt, user_prompt)
        text = self._extract_text(payload)
        text = self._post_process(text)
        return text

    # -- system / user prompts -------------------------------------------

    def _build_system_prompt(
        self, *, tone: str, include_hashtags: bool
    ) -> str:
        tone_specific = _TONE_GUIDANCE.get(tone, _TONE_GUIDANCE["professional"])
        max_chars = LINKEDIN_MAX_CHARS
        base = _SYSTEM_PROMPT_BASE.format(
            max_chars=max_chars, tone=tone, tone_specific=tone_specific
        )
        if not include_hashtags:
            base += (
                "\n- Do not include any hashtags. End the post after the "
                "final sentence."
            )
        else:
            base += (
                "\n- You may add 1-3 relevant hashtags at the very end, "
                "each on its own line, no '#' spam, no more than 3."
            )
        return base

    @staticmethod
    def _build_user_prompt(
        *, topic: str, length: int, include_hashtags: bool
    ) -> str:
        tags_clause = (
            "1-3 relevant hashtags at the end"
            if include_hashtags
            else "no hashtags"
        )
        return (
            f"Topic: {topic.strip()}\n"
            f"Target length: ~{length} characters.\n"
            f"Hashtags: {tags_clause}.\n"
            "Return only the post body."
        )

    # -- HTTP -------------------------------------------------------------

    def _call_chat(
        self, system_prompt: str, user_prompt: str
    ) -> dict[str, Any]:
        """POST to ``/chat/completions`` and return the parsed JSON."""
        url = f"{self.base_url}/chat/completions"
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.7,
            "max_tokens": 1024,
            "stream": False,
        }
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
                "Accept": "application/json",
                "User-Agent": "linkedin-mcp-pro/drafter",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode("utf-8", errors="replace")
            except Exception:  # pragma: no cover - defensive
                pass
            raise DrafterBackendError(
                f"LLM backend HTTP {e.code} at {url}: {detail or e.reason}"
            ) from e
        except urllib.error.URLError as e:
            raise DrafterBackendError(
                f"LLM backend unreachable at {url}: {e.reason}"
            ) from e
        except TimeoutError as e:
            raise DrafterBackendError(
                f"LLM backend timed out after {self.timeout}s at {url}"
            ) from e
        except OSError as e:
            # Connection refused, DNS failure, etc.
            raise DrafterBackendError(
                f"LLM backend connection error at {url}: {e}"
            ) from e

        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as e:
            raise DrafterBackendError(
                f"LLM backend returned non-JSON response: {e}; body[:200]={raw[:200]!r}"
            ) from e

        # Cache for tests / introspection.
        self.last_raw = payload
        if isinstance(payload, dict):
            self.last_model = payload.get("model") or self.model
            usage = payload.get("usage")
            if isinstance(usage, dict):
                # Normalize to int keys, drop non-int fields.
                self.last_usage = {
                    str(k): int(v) for k, v in usage.items() if isinstance(v, (int, float))
                }
            else:
                self.last_usage = None
        return payload

    # -- response shaping --------------------------------------------------

    @staticmethod
    def _extract_text(payload: Mapping[str, Any]) -> str:
        """Pull the assistant message text out of an OpenAI-style payload."""
        if not isinstance(payload, Mapping):
            raise DrafterBackendError(
                f"LLM response is not a JSON object: {type(payload).__name__}"
            )
        if "error" in payload:
            err = payload["error"]
            if isinstance(err, Mapping):
                msg = err.get("message") or err.get("type") or "unknown"
            else:
                msg = str(err)
            raise DrafterBackendError(f"LLM returned error: {msg}")
        try:
            choice = payload["choices"][0]
        except (KeyError, IndexError, TypeError) as e:
            raise DrafterBackendError(
                f"LLM response missing 'choices[0]': {e}; "
                f"keys={list(payload.keys())}"
            ) from e
        msg = choice.get("message") if isinstance(choice, Mapping) else None
        content = None
        if isinstance(msg, Mapping):
            content = msg.get("content")
        if content is None and isinstance(choice, Mapping):
            # Some backends return {"text": "..."} for legacy completions
            # payloads; accept that as a fallback.
            content = choice.get("text")
        if not isinstance(content, str) or not content.strip():
            raise DrafterBackendError(
                "LLM response contained no usable text content"
            )
        return content

    @staticmethod
    def _post_process(text: str) -> str:
        """Trim, clamp, and strip a few common AI artifacts.

        This is intentionally lightweight — the system prompt does the
        heavy lifting. We only do the minimum needed to keep the output
        safe for ``create_post``:

        * strip leading/trailing whitespace
        * strip wrapping code fences (``` … ```)
        * strip a leading "Here is the post:" or similar preamble
        * drop a trailing "Hope this helps!" line if present
        * clamp to :data:`LINKEDIN_MAX_CHARS` on a character boundary
        """
        s = text.strip()

        # Strip ``` blocks (with or without language tag).
        if s.startswith("```"):
            # Drop the opening fence line.
            first_nl = s.find("\n")
            if first_nl != -1:
                s = s[first_nl + 1 :]
            if s.endswith("```"):
                s = s[: -3]
            s = s.strip()

        # Strip a small set of common preambles / postambles. Keep this
        # list *narrow* — we don't want to over-edit real content.
        preamble_starts = (
            "here is your post:",
            "here's your post:",
            "here is the post:",
            "here's the post:",
            "draft post:",
            "post:",
        )
        low = s.lower()
        for prefix in preamble_starts:
            if low.startswith(prefix):
                s = s[len(prefix) :].lstrip()
                break

        postamble_suffixes = (
            "hope this helps!",
            "hope that helps!",
            "let me know what you think!",
        )
        low = s.lower().rstrip()
        for suffix in postamble_suffixes:
            if low.endswith(suffix):
                # Drop the line containing the postamble.
                idx = low.rfind(suffix)
                s = s[:idx].rstrip()
                break

        # Clamp to the LinkedIn cap.
        if len(s) > LINKEDIN_MAX_CHARS:
            s = s[: LINKEDIN_MAX_CHARS - 1].rstrip() + "…"
        return s


__all__ = [
    "DEFAULT_BASE_URL",
    "DEFAULT_API_KEY",
    "DEFAULT_MODEL",
    "DEFAULT_TIMEOUT",
    "LINKEDIN_MAX_CHARS",
    "VALID_TONES",
    "DrafterError",
    "DrafterConfigError",
    "DrafterBackendError",
    "PostDrafter",
]
