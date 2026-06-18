# Contributing to linkedin-mcp-pro

Thanks for your interest! This project is open source under MIT.

## Quick start

```bash
git clone https://github.com/your-org/linkedin-mcp-pro
cd linkedin-mcp-pro
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# Run tests
pytest

# Run a single test file
pytest tests/test_safety.py -v

# Lint
ruff check linkedin_mcp/ tests/
ruff format linkedin_mcp/ tests/

# Type check
mypy linkedin_mcp/
```

## Code style

- **Python 3.11+** syntax (use `X | None` not `Optional[X]`, etc.)
- **Type hints** on all public functions
- **Async by default** for I/O
- **Ruff** for formatting and linting
- **Docstrings** on public functions (Google style)
- **No global state** outside the `server.py` lifespan

## Adding a new tool

To add a new MCP tool:

1. **Pick a category**: read (no ban risk) or write (safety-enforced)
2. **Implement** in the appropriate module (`api/*.py` or `browser/*.py`)
3. **Register** in `server.py`:
   - Add to `TOOLS` list (with `inputSchema`)
   - Add a dispatch branch in `_dispatch_read()` or `_dispatch_write()`
4. **Add tests** in `tests/test_api.py` or `tests/test_browser.py`
5. **Update README** tool list

Example for a new write tool:

```python
# browser/post.py
async def schedule_post(client, text: str, scheduled_at: str) -> dict:
    """Schedule a post for future publication."""
    # ...
    return {"ok": True, "scheduled_at": scheduled_at}
```

```python
# linkedin_mcp/browser/__init__.py
from .post import schedule_post  # add to __all__
```

```python
# server.py
elif name == "schedule_post":
    plan = ActionPlan(action="post", target="self", payload={...}, dry_run=dry_run)
    guard.enforce(plan)
    if dry_run:
        raise DryRun(plan)
    result = await br.schedule_post(text=args["text"], scheduled_at=args["scheduled_at"])

# Add to TOOLS list
{
    "name": "schedule_post",
    "description": "Schedule a post for future publication.",
    "inputSchema": {...},
}
```

## Adding a new safety check

Safety is a single layer (`safety.py`). To add a new check:

1. **Add a method** to `SafetyGuard`, e.g. `def check_xyz(self, plan): ...`
2. **Call it from `enforce()`** in the right order
3. **Raise** the appropriate `SafetyError` subclass
4. **Add a test** in `tests/test_safety.py`
5. **Update `docs/SAFETY.md`**

## Testing

We use `pytest` with `pytest-asyncio` (configured `asyncio_mode = "auto"`).

```bash
# All tests
pytest

# Verbose
pytest -v

# With coverage
pytest --cov=linkedin_mcp --cov-report=term-missing

# Just safety
pytest tests/test_safety.py

# Just API
pytest tests/test_api.py
```

### Mocking conventions

- **httpx**: use `httpx.MockTransport` (no extra dep)
- **Patchright**: mock the page object via `unittest.mock.AsyncMock`
- **SQLite**: use `tmp_path` fixture for isolation
- **Config**: use `monkeypatch.setenv()` to override env vars

## Commit conventions

Use [Conventional Commits](https://www.conventionalcommits.org/):

```
feat: add schedule_post tool
fix: correct quota zone for 50%
docs: update SAFETY.md with new captcha pattern
test: add coverage for warm-up ramp
chore: bump pydantic to 2.7
```

## Pull request process

1. Fork the repo
2. Create a feature branch: `git checkout -b feat/your-feature`
3. Make your changes
4. Add tests (PRs without tests are unlikely to merge)
5. Run `pytest && ruff check && ruff format && mypy`
6. Commit with conventional format
7. Open a PR with a clear description
8. Address review feedback
9. Squash-merge when approved

## Especially wanted contributions

- **More Voyager endpoints** (the API is undocumented — discover & document)
- **Test coverage** — current focus: tools, safety edge cases
- **Documentation translations** (we'd love a Spanish, Chinese, or Hindi version)
- **Docker / k8s deployment** examples
- **Captcha pattern detection** improvements
- **Browser selectors** that survive LinkedIn UI changes (we mark these as TODO)

## Code of conduct

- Be kind and respectful
- Don't post spam / unsolicited outreach tools
- Don't add features that bypass LinkedIn's TOS in a way that harms other users
- This project is for **legitimate professional use** (job search, networking,
  brand building) — not for spam

## License

By contributing, you agree your contributions are licensed under MIT.
