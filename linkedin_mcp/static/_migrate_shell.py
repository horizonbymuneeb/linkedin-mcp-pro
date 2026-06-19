#!/usr/bin/env python3
"""Apply unified shell to existing pages.

For each HTML page in static/, replace the <head> with shell include,
and wrap body content in ml-[240px] pt-14 main wrapper. Skips pages
already on the new shell (_shell.html, index.html, connect.html, drafts.html, jobs.html).
"""
import re
import sys
from pathlib import Path

STATIC = Path(__file__).parent
SHELL_INCLUDE = '{% include "_shell.html" %}'

# Pages already migrated or special
SKIP = {"_shell.html", "index.html", "connect.html", "drafts.html", "jobs.html"}

def migrate(filepath: Path) -> None:
    text = filepath.read_text(encoding="utf-8")
    name = filepath.name

    # Skip if already migrated (has include shell)
    if '{% include "_shell.html" %}' in text:
        print(f"  skip  {name} (already migrated)")
        return

    # Replace head with shell include + keep <title> + <meta>
    title_match = re.search(r'<title>([^<]+)</title>', text)
    title = title_match.group(1) if title_match else name

    # Find <head>...</head> and replace it
    head_pattern = re.compile(r'<head>.*?</head>', re.DOTALL)
    new_head = f"<head>\n  <meta charset=\"UTF-8\">\n  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\">\n  <title>{title}</title>\n  {SHELL_INCLUDE}\n</head>"
    text = head_pattern.sub(new_head, text, count=1)

    # Find <body> tag and wrap content with main container
    # We need to handle: <body>\n...content...\n</body>
    # Strategy: replace <body> with <body><main class="ml-[240px] pt-14 min-h-screen">
    #           and </body> with </main></body>

    body_open_pattern = re.compile(r'<body[^>]*>')
    text = body_open_pattern.sub('<body>\n<main class="ml-[240px] pt-14 min-h-screen">', text, count=1)

    # Close </body> -> </main>\n</body>
    text = text.replace('</body>', '</main>\n</body>', 1)

    # Add container padding inside main
    text = text.replace(
        '<main class="ml-[240px] pt-14 min-h-screen">',
        '<main class="ml-[240px] pt-14 min-h-screen">\n    <div class="max-w-6xl mx-auto px-6 py-8">',
        1,
    )
    # Close that div before </main>
    text = text.replace('</main>', '  </div>\n</main>', 1)

    filepath.write_text(text, encoding="utf-8")
    print(f"  ✓      {name}")

def main() -> None:
    print(f"Migrating pages in {STATIC}/")
    for f in sorted(STATIC.glob("*.html")):
        if f.name in SKIP:
            print(f"  skip  {f.name} (in skip list)")
            continue
        try:
            migrate(f)
        except Exception as e:
            print(f"  ERR   {f.name}: {e}", file=sys.stderr)

if __name__ == "__main__":
    main()
