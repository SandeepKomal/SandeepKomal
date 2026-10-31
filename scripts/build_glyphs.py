#!/usr/bin/env python3
"""Trace Hubot Sans display glyphs into brand/fonts/hubot-glyphs.json.

The README panels draw headings as paths so no font has to be embedded in
every image. Run this only when the heading character set changes; it needs
fontTools (pip install fonttools brotli), the panels themselves do not.

Exit codes: 0 written, 1 fontTools missing.
"""

import json
import re
import sys
from pathlib import Path

FONTS = Path(__file__).resolve().parent.parent / "brand" / "fonts"
CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 ,.'’-·&"

try:
    from fontTools.pens.svgPathPen import SVGPathPen
    from fontTools.pens.transformPen import TransformPen
    from fontTools.ttLib import TTFont
except ImportError:
    print("fontTools is required: pip install fonttools brotli", file=sys.stderr)
    sys.exit(1)

font = TTFont(FONTS / "hubot-display.woff2")
glyphs = font.getGlyphSet()
cmap = font.getBestCmap()
out = {"upm": font["head"].unitsPerEm, "glyphs": {}}
for char in CHARS:
    name = cmap.get(ord(char))
    if name is None:
        continue
    pen = SVGPathPen(glyphs)
    glyphs[name].draw(TransformPen(pen, (1, 0, 0, -1, 0, 0)))  # flip to SVG's y-down
    # whole units are invisible at heading sizes and keep every panel smaller
    d = re.sub(r"-?\d+\.\d+", lambda m: str(round(float(m.group()))), pen.getCommands())
    out["glyphs"][char] = {"adv": glyphs[name].width, "d": d}
(FONTS / "hubot-glyphs.json").write_text(json.dumps(out, separators=(",", ":")) + "\n")
print(f"Wrote {len(out['glyphs'])} glyphs.")
