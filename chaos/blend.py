#!/usr/bin/env python3
"""Paint the Git3D Universe and Neon Arena renders onto the profile's page colour.

Both actions fill the whole image with their own background gradient, which
showed as a hard-edged blue rectangle inside the profile. This swaps only that
gradient for GitHub's page colour (#0d1117 dark, #ffffff light); the glow,
stars and everything drawn on top are untouched and fade into the page.

Usage: blend.py [--apply] FILE THEME [FILE THEME ...]   (THEME is dark or light)
Dry run unless --apply is given. A render without the expected background is
left as it is, so a change in either action never breaks the workflow.

Exit codes: 0 done (or nothing to change), 2 bad usage.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

PAGE = {"dark": "#0d1117", "light": "#ffffff"}
BACKGROUND = re.compile(
    r'(<radialGradient id="bg"[^>]*>)(.*?)(</radialGradient>)', re.DOTALL
)


def blend(svg: str, theme: str) -> str:
    colour = PAGE[theme]
    return BACKGROUND.sub(
        lambda m: (
            m.group(1)
            + re.sub(
                r'stop-color="#[0-9a-fA-F]{3,8}"', f'stop-color="{colour}"', m.group(2)
            )
            + m.group(3)
        ),
        svg,
        count=1,
    )


def main(argv: list[str]) -> int:
    args = argv[1:]
    apply = "--apply" in args
    args = [a for a in args if a != "--apply"]
    if not args or len(args) % 2 or any(theme not in PAGE for theme in args[1::2]):
        print(
            "usage: blend.py [--apply] FILE THEME [FILE THEME ...] (THEME: dark|light)",
            file=sys.stderr,
        )
        return 2
    for name, theme in zip(args[::2], args[1::2]):
        path = Path(name)
        svg = path.read_text(encoding="utf-8")
        blended = blend(svg, theme)
        if blended == svg:
            print(
                f"{name}: already blended or no background gradient found; left as is."
            )
        elif apply:
            path.write_text(blended, encoding="utf-8")
            print(f"{name}: background set to the {theme} page colour.")
        else:
            print(
                f"{name}: would set the background to the {theme} page colour (dry run)."
            )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
