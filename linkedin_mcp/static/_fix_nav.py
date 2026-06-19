#!/usr/bin/env python3
"""Remove legacy top-nav blocks and unwrap inner <main> in pages.

For each page with <nav class="sticky top-0...">, find the nav block and
remove it. Also unwrap an inner <main>...</main> when there's already an
outer <main class="ml-[240px]..."> from the shell migration.
"""
import re
from pathlib import Path

STATIC = Path(__file__).parent

# Pages that have a top nav that needs removal
PAGES = ["schedules", "engagement", "llm", "audit", "profile",
         "analytics", "install", "settings", "templates"]

NAV_PATTERN = re.compile(
    r'<nav class="sticky top-0[^"]*"[^>]*>.*?</nav>',
    re.DOTALL,
)

def fix_page(name: str) -> None:
    p = STATIC / f"{name}.html"
    text = p.read_text(encoding="utf-8")
    original = text

    # Remove any leftover top navs
    text = NAV_PATTERN.sub("", text)

    # Look for double <main> — outer is shell wrapper, inner is legacy
    if text.count("<main") >= 2:
        # Find the second <main> tag and unwrap it
        # Pattern: </div>\s*<main class="...">...</main>\s*(?!</main>)
        # Simpler: find inner <main ...> ... </main> that isn't the shell wrapper.
        # The shell wrapper is: <main class="ml-[240px] pt-14 min-h-screen">
        # Inner ones have other classes.

        # Find positions of all <main> tags
        main_positions = [m.start() for m in re.finditer(r'<main\b', text)]
        if len(main_positions) >= 2:
            # Second <main> is the legacy one — unwrap (replace <main ...> with empty, </main> with empty)
            # But careful: closing tags match — unwrap pair
            second_main_start = main_positions[1]
            # Find the matching </main> after this point
            close_pos = text.find("</main>", second_main_start)
            if close_pos != -1:
                # Find the actual opening tag end
                open_tag_end = text.find(">", second_main_start) + 1
                # Replace the opening tag (e.g. <main class="...">) with nothing
                inner_open_tag = text[second_main_start:open_tag_end]
                text = text[:second_main_start] + text[open_tag_end:close_pos] + text[close_pos + len("</main>"):]
                print(f"  unwrap inner <main> in {name}")

    if text != original:
        p.write_text(text, encoding="utf-8")
        print(f"  ✓ {name}.html")
    else:
        print(f"  - {name}.html (no change)")

def main() -> None:
    print(f"Fixing top-nav blocks in {STATIC}/")
    for p in PAGES:
        try:
            fix_page(p)
        except Exception as e:
            print(f"  ERR {p}: {e}")

if __name__ == "__main__":
    main()
