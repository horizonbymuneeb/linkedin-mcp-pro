"""Configuration loader for linkedin-mcp-pro.

Loads from .env (or environment) with sensible defaults. Secrets can come
from inline env vars (LI_AT, JSESSIONID) or from files (LI_AT_FILE,
JSESSIONID_FILE) for safer deployment.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

# Load .env from current working directory or project root
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_PROJECT_ROOT / ".env")


def _get_secret(env_var: str, file_var: str) -> Optional[str]:
    """Read secret from env var, falling back to file path in <env_var>_FILE."""
    val = os.environ.get(env_var, "").strip()
    if val:
        return val
    file_path = os.environ.get(file_var, "").strip()
    if file_path and Path(file_path).is_file():
        return Path(file_path).read_text().strip()
    return None


def _get_int(env_var: str, default: int) -> int:
    try:
        return int(os.environ.get(env_var, str(default)))
    except ValueError:
        return default


def _get_bool(env_var: str, default: bool) -> bool:
    return os.environ.get(env_var, str(default)).lower() in ("1", "true", "yes", "on")


def _get_list(env_var: str, default: list[str]) -> list[str]:
    raw = os.environ.get(env_var, "").strip()
    if not raw:
        return default
    return [s.strip().lower() for s in raw.split(",") if s.strip()]


@dataclass
class SafetyConfig:
    """All safety-related knobs in one place."""

    daily_limit_connection_requests: int = 20
    daily_limit_posts: int = 2
    daily_limit_messages: int = 30
    daily_limit_comments: int = 30
    daily_limit_reactions: int = 100

    business_hours_start: int = 9
    business_hours_end: int = 20
    business_days: list[str] = field(
        default_factory=lambda: ["mon", "tue", "wed", "thu", "fri"]
    )

    action_jitter_min_seconds: int = 180
    action_jitter_max_seconds: int = 900

    warmup_enabled: bool = True
    warmup_week_1_limit: int = 5
    warmup_week_2_limit: int = 10
    warmup_week_3_limit: int = 15

    rate_limit_backoff_base: int = 2
    rate_limit_max_retries: int = 3
    rate_limit_auto_pause: bool = True

    def effective_daily_limit(self, action: str, account_age_weeks: int) -> int:
        """Return the effective daily cap for an action, factoring in warm-up.

        Action: 'connection' | 'post' | 'message' | 'comment' | 'reaction'
        account_age_weeks: how many weeks since account creation / first use
        """
        # Map action -> config field name (some don't follow simple plural)
        attr_map = {
            "connection": "daily_limit_connection_requests",
            "post": "daily_limit_posts",
            "message": "daily_limit_messages",
            "comment": "daily_limit_comments",
            "reaction": "daily_limit_reactions",
        }
        field_name = attr_map.get(action, f"daily_limit_{action}s")
        full_cap = getattr(self, field_name)
        if self.warmup_enabled and account_age_weeks < 4:
            ramp = {
                1: self.warmup_week_1_limit,
                2: self.warmup_week_2_limit,
                3: self.warmup_week_3_limit,
            }.get(account_age_weeks, self.warmup_week_1_limit)
            return min(ramp, full_cap)
        return full_cap


@dataclass
class ServerConfig:
    host: str = "127.0.0.1"
    port: int = 8765
    transport: str = "stdio"  # "stdio" or "streamable-http"
    log_level: str = "INFO"


@dataclass
class StorageConfig:
    db_path: Path = Path("./data/linkedin-mcp-pro.db")
    # v0.3.0: default profile moved outside the project dir so it survives
    # `git clean` and reinstalls. Users auth once via `linkedin-mcp login`.
    browser_profile_dir: Path = field(
        default_factory=lambda: Path.home() / ".linkedin-mcp" / "profile"
    )
    audit_log_retention_days: int = 90


@dataclass
class NotificationConfig:
    telegram_bot_token: Optional[str] = None
    telegram_chat_id: Optional[str] = None
    alert_on_captcha: bool = True
    alert_on_rate_limit: bool = True
    alert_on_quota_exceeded: bool = True


@dataclass
class Config:
    li_at: Optional[str] = None
    jsessionid: Optional[str] = None
    server: ServerConfig = field(default_factory=ServerConfig)
    safety: SafetyConfig = field(default_factory=SafetyConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    notifications: NotificationConfig = field(default_factory=NotificationConfig)

    def validate(self) -> list[str]:
        """Return list of validation errors (empty if OK)."""
        errors = []
        if not self.li_at:
            errors.append(
                "LI_AT not set. Set LI_AT env var or LI_AT_FILE. "
                "Get it from browser DevTools → Application → Cookies → li_at"
            )
        if self.server.transport not in ("stdio", "streamable-http"):
            errors.append(f"Invalid MCP_TRANSPORT: {self.server.transport}")
        if self.safety.business_hours_start < 0 or self.safety.business_hours_end > 24:
            errors.append("Business hours must be 0-24")
        if self.safety.business_hours_start >= self.safety.business_hours_end:
            errors.append("business_hours_start must be < business_hours_end")
        return errors


def load_config() -> Config:
    """Load full Config from environment."""
    return Config(
        li_at=_get_secret("LI_AT", "LI_AT_FILE"),
        jsessionid=_get_secret("JSESSIONID", "JSESSIONID_FILE"),
        server=ServerConfig(
            host=os.environ.get("MCP_HOST", "127.0.0.1"),
            port=_get_int("MCP_PORT", 8765),
            transport=os.environ.get("MCP_TRANSPORT", "stdio"),
            log_level=os.environ.get("LOG_LEVEL", "INFO"),
        ),
        safety=SafetyConfig(
            daily_limit_connection_requests=_get_int("DAILY_LIMIT_CONNECTION_REQUESTS", 20),
            daily_limit_posts=_get_int("DAILY_LIMIT_POSTS", 2),
            daily_limit_messages=_get_int("DAILY_LIMIT_MESSAGES", 30),
            daily_limit_comments=_get_int("DAILY_LIMIT_COMMENTS", 30),
            daily_limit_reactions=_get_int("DAILY_LIMIT_REACTIONS", 100),
            business_hours_start=_get_int("BUSINESS_HOURS_START", 9),
            business_hours_end=_get_int("BUSINESS_HOURS_END", 20),
            business_days=_get_list(
                "BUSINESS_DAYS", ["mon", "tue", "wed", "thu", "fri"]
            ),
            action_jitter_min_seconds=_get_int("ACTION_JITTER_MIN_SECONDS", 180),
            action_jitter_max_seconds=_get_int("ACTION_JITTER_MAX_SECONDS", 900),
            warmup_enabled=_get_bool("WARMUP_ENABLED", True),
            warmup_week_1_limit=_get_int("WARMUP_WEEK_1_LIMIT", 5),
            warmup_week_2_limit=_get_int("WARMUP_WEEK_2_LIMIT", 10),
            warmup_week_3_limit=_get_int("WARMUP_WEEK_3_LIMIT", 15),
            rate_limit_backoff_base=_get_int("RATE_LIMIT_BACKOFF_BASE", 2),
            rate_limit_max_retries=_get_int("RATE_LIMIT_MAX_RETRIES", 3),
            rate_limit_auto_pause=_get_bool("RATE_LIMIT_AUTO_PAUSE", True),
        ),
        storage=StorageConfig(
            db_path=Path(os.environ.get("DB_PATH", "./data/linkedin-mcp-pro.db")),
            browser_profile_dir=Path(
                os.environ.get(
                    "LINKEDIN_MCP_PROFILE_DIR",
                    str(Path.home() / ".linkedin-mcp" / "profile"),
                )
            ),
            audit_log_retention_days=_get_int("AUDIT_LOG_RETENTION_DAYS", 90),
        ),
        notifications=NotificationConfig(
            telegram_bot_token=os.environ.get("TELEGRAM_BOT_TOKEN", "").strip() or None,
            telegram_chat_id=os.environ.get("TELEGRAM_CHAT_ID", "").strip() or None,
            alert_on_captcha=_get_bool("ALERT_ON_CAPTCHA", True),
            alert_on_rate_limit=_get_bool("ALERT_ON_RATE_LIMIT", True),
            alert_on_quota_exceeded=_get_bool("ALERT_ON_QUOTA_EXCEEDED", True),
        ),
    )


if __name__ == "__main__":
    # Quick sanity check
    cfg = load_config()
    errors = cfg.validate()
    if errors:
        print("Config errors:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        sys.exit(1)
    print(f"Config OK. li_at={'set' if cfg.li_at else 'MISSING'}")
    print(f"  Server: {cfg.server.host}:{cfg.server.port} ({cfg.server.transport})")
    print(f"  Daily limits: {cfg.safety.daily_limit_connection_requests} conn, "
          f"{cfg.safety.daily_limit_posts} posts, {cfg.safety.daily_limit_messages} msg")
    print(f"  Business hours: {cfg.safety.business_hours_start:02d}:00 - "
          f"{cfg.safety.business_hours_end:02d}:00 ({', '.join(cfg.safety.business_days)})")
