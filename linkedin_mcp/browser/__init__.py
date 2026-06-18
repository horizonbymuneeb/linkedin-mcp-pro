"""linkedin-mcp-pro browser module — Patchright → agent-browser (v0.2.0+).

Public surface:
  - BrowserClient: async context manager wrapping the `agent-browser` CLI
  - Auth helpers: has_valid_session, ensure_session, interactive_login
  - Action funcs: send_connection_request, accept_invitation, decline_invitation,
    withdraw_invitation, create_post, delete_post, comment_on_post, react_to_post,
    send_message
"""

from __future__ import annotations

from .auth import ensure_session, has_valid_session, interactive_login
from .client import (
    DEFAULT_PROFILE_DIR,
    LINKEDIN_BASE,
    BrowserChallenge,
    BrowserClient,
    BrowserError,
)
from .connect import (
    accept_invitation,
    decline_invitation,
    send_connection_request,
    withdraw_invitation,
)
from .engage import comment_on_post, react_to_post
from .message import send_message
from .post import create_post, delete_post

__all__ = [
    # core
    "BrowserClient",
    "BrowserError",
    "BrowserChallenge",
    "LINKEDIN_BASE",
    "DEFAULT_PROFILE_DIR",
    "ensure_session",
    "has_valid_session",
    "interactive_login",
    # connection actions
    "send_connection_request",
    "accept_invitation",
    "decline_invitation",
    "withdraw_invitation",
    # post actions
    "create_post",
    "delete_post",
    # engagement
    "comment_on_post",
    "react_to_post",
    # messaging
    "send_message",
]
