"""Render the profile README panels in the style of sandeepkomal.github.io.

Static panels (tools, about, stack, projects, contact, buttons) are rendered by
``python3 chaos/brand.py``. The hero and the production panel depend on the
chaos state and are re-rendered by ``engine.py`` after every injection or heal.

Everything a viewer sees is inside the SVGs: images and the Kanit font
(SIL OFL, see brand/fonts/OFL.txt) are embedded, nothing loads at view time.
"""

from __future__ import annotations

import base64
import json
import random
import re
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "brand"
ASSETS = ROOT / "assets"
FONTS = ROOT / "fonts"

BG = "#0c0c0c"
INK = "#0c0c0d"  # text on the white stack panel; inverts separately in the light theme
MAGENTA, PURPLE = "#b600a8", "#7621b0"
MONO = "ui-monospace,SFMono-Regular,Menlo,Consolas,monospace"
MOTION = "@media (prefers-reduced-motion:reduce){*{animation:none!important}}"
STATE_COLOR = {
    "healthy": "#3df5c0",
    "degraded": "#ffb02e",
    "slow": "#ffb02e",
    "down": "#ff3d5a",
    "cut": "#a3acc7",
}
STATE_WORD = {
    "healthy": "RUNNING",
    "degraded": "SLOWED",
    "slow": "SLOW",
    "down": "DOWN",
    "cut": "CUT OFF",
}
SEVERITY_COLOR = {"ok": "#3df5c0", "warn": "#ffb02e", "bad": "#ff3d5a"}


# --------------------------------------------------------------------------- themes

LIGHT = {
    "#0c0c0c": "#ffffff",
    "#141414": "#f6f7f9",
    "#121212": "#f4f5f7",
    "#111": "#f6f7f9",
    "#26282d": "#e3e6ea",
    "#2c2f35": "#e3e6ea",
    "#33363c": "#d5d9df",
    "#f2f5f7": "#0c0c0c",
    "#eef2f5": "#0c0c0c",
    "#e9eef2": "#0c0c0c",
    "#d7e2ea": "#2b3138",
    "#9aa3ad": "#5b6470",
    "#a5adb6": "#59616b",
    "#7d858f": "#7a828c",
    "#5a5f68": "#b7bdc5",
    "#4b8f6a": "#2f9e6a",
    "#646973": "#15171c",
    "#bbccd7": "#6b7380",
    "#3df5c0": "#0a9e74",
    "#ffb02e": "#d27c00",
    "#ff3d5a": "#e0243a",
    "#a3acc7": "#6b7698",
    "#fefefe": "#b600a8",
    "#fffffe": "#0c0c0c",
    "#0c0c0d": "#ffffff",
    "#dcdcdc": "#2a2c31",
    "#666": "#9aa3ad",
}
HEX = re.compile(r"#[0-9a-fA-F]{3,6}\b")


def to_light(doc: str) -> str:
    return HEX.sub(lambda m: LIGHT.get(m.group(0).lower(), m.group(0)), doc)


def write(name: str, doc: str) -> list[Path]:
    """Write the dark panel and its light twin (name-light.svg)."""
    dark, light = ROOT / f"{name}.svg", ROOT / f"{name}-light.svg"
    dark.write_text(doc, encoding="utf-8")
    light.write_text(to_light(doc), encoding="utf-8")
    return [dark, light]


# --------------------------------------------------------------------------- helpers


def data_uri(path: Path) -> str:
    mime = {".png": "image/png", ".webp": "image/webp"}[path.suffix]
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode()}"


def font_faces(body: str) -> str:
    weights = sorted({int(w) for w in re.findall(r'font-weight="(\d+)"', body)} | {400})
    faces = []
    for weight in weights:
        data = base64.b64encode((FONTS / f"kanit-{weight}.woff2").read_bytes()).decode()
        faces.append(
            f"@font-face{{font-family:K;font-weight:{weight};"
            f"src:url(data:font/woff2;base64,{data}) format('woff2')}}"
        )
    return "".join(faces)


DEFS = (
    '<linearGradient id="chrome" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#646973"/>'
    '<stop offset="1" stop-color="#bbccd7"/></linearGradient>'
    '<linearGradient id="pill" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="#3a0040"/>'
    f'<stop offset=".45" stop-color="{MAGENTA}"/><stop offset="1" stop-color="{PURPLE}"/></linearGradient>'
    '<filter id="pglow" x="-30%" y="-60%" width="160%" height="220%"><feGaussianBlur stdDeviation="9"/></filter>'
)


def svg(width: int, height: int, body: str, label: str, css: str = "") -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" role="img" aria-label="{escape(label)}">'
        f"<defs>{DEFS}</defs><style>{font_faces(body)}{css}{MOTION}</style>{body}</svg>\n"
    )


def title(x, y, text, size, anchor="middle", fill="url(#chrome)") -> str:
    return (
        f'<text x="{x}" y="{y}" text-anchor="{anchor}" font-family="K" font-weight="900" '
        f'font-size="{size}" letter-spacing="-1" fill="{fill}">{escape(text)}</text>'
    )


def wrap(text: str, width: int) -> list[str]:
    lines, current = [], ""
    for word in text.split():
        if current and len(current) + len(word) + 1 > width:
            lines.append(current)
            current = word
        else:
            current = f"{current} {word}".strip()
    return lines + [current]


def pill(x, y, w, label, size=16) -> str:
    return (
        f'<g transform="translate({x} {y})"><rect x="4" y="6" width="{w - 8}" height="48" rx="24" '
        f'fill="{MAGENTA}" opacity=".5" filter="url(#pglow)"/>'
        f'<rect width="{w}" height="54" rx="27" fill="url(#pill)" stroke="#f2e6ff" stroke-width="2"/>'
        f'<text x="{w / 2}" y="34" text-anchor="middle" font-family="K" font-weight="600" font-size="{size}" '
        f'letter-spacing="2" fill="#fff">{escape(label)}</text></g>'
    )


BOB = (
    ".bob{animation:bob 6s ease-in-out infinite}@keyframes bob{50%{transform:translateY(-14px)}}"
    ".spin{transform-box:fill-box;transform-origin:center;animation:spin 14s linear infinite}"
    "@keyframes spin{to{transform:rotate(360deg)}}"
)


def floating(shape: str, delay: float) -> str:
    return f'<g class="bob" style="animation-delay:-{delay}s">{shape}</g>'


# --------------------------------------------------------------------------- shapes (from the site's art)


def moon(x, y, s):
    return (
        f'<g transform="translate({x} {y}) scale({s})"><defs><radialGradient id="mo" cx="35%" cy="30%" r="80%">'
        '<stop offset="0" stop-color="#FFF4C9"/><stop offset=".55" stop-color="#F2B84B"/>'
        '<stop offset="1" stop-color="#8A4A00"/></radialGradient><mask id="mc"><rect width="200" height="200" fill="#fff"/>'
        '<circle cx="135" cy="70" r="70" fill="#000"/></mask></defs>'
        '<circle cx="95" cy="105" r="80" fill="url(#mo)" mask="url(#mc)"/>'
        '<circle cx="55" cy="130" r="9" fill="#8A4A00" opacity=".35" mask="url(#mc)"/>'
        '<circle cx="160" cy="150" r="5" fill="#FFF4C9"/><circle cx="175" cy="120" r="3" fill="#FFF4C9" opacity=".7"/></g>'
    )


def brick(x, y, s):
    studs = "".join(
        f'<rect x="{a - 14}" y="{b + 2}" width="28" height="12" fill="#C9372A"/>'
        f'<ellipse cx="{a}" cy="{b + 14}" rx="14" ry="7" fill="#C9372A"/>'
        f'<ellipse cx="{a}" cy="{b + 2}" rx="14" ry="7" fill="#FF8A7B"/>'
        for a, b in ((70, 58), (110, 78), (110, 38), (150, 58))
    )
    return (
        f'<g transform="translate({x} {y}) scale({s})"><polygon points="110,40 190,80 110,120 30,80" fill="#FF6B5A"/>'
        '<polygon points="30,80 110,120 110,180 30,140" fill="#C9372A"/>'
        f'<polygon points="110,120 190,80 190,140 110,180" fill="#E14B3B"/>{studs}</g>'
    )


def spheres(x, y, s):
    balls = (
        (70, 130, 52, ("#9AF5FF", "#1E7FBF", "#08223F")),
        (140, 120, 40, ("#FFC7F4", "#B600A8", "#3A0036")),
        (108, 62, 34, ("#E8FFB5", "#6DBE2E", "#1D3D06")),
    )
    defs = "".join(
        f'<radialGradient id="sb{i}" cx="35%" cy="30%" r="75%"><stop offset="0" stop-color="{c[0]}"/>'
        f'<stop offset=".55" stop-color="{c[1]}"/><stop offset="1" stop-color="{c[2]}"/></radialGradient>'
        for i, (_, _, _, c) in enumerate(balls)
    )
    body = "".join(
        f'<circle cx="{a}" cy="{b}" r="{r}" fill="url(#sb{i})"/>'
        for i, (a, b, r, _) in enumerate(balls)
    )
    return f'<g transform="translate({x} {y}) scale({s})"><defs>{defs}</defs>{body}</g>'


def torus(x, y, s):
    ring = 'fill="none" stroke="url(#tg)" transform="rotate(-30 110 110)"'
    return (
        f'<g transform="translate({x} {y}) scale({s})"><defs><linearGradient id="tg" x1="0" y1="0" x2="1" y2="1">'
        '<stop offset="0" stop-color="#FFE29A"/><stop offset=".45" stop-color="#BE4C00"/>'
        '<stop offset="1" stop-color="#7621B0"/></linearGradient></defs>'
        f'<ellipse cx="110" cy="110" rx="80" ry="42" {ring} stroke-width="30"/>'
        '<ellipse cx="110" cy="110" rx="80" ry="42" fill="none" stroke="#fff" stroke-opacity=".35" stroke-width="4" '
        'transform="rotate(-30 110 110) translate(0 -10)"/>'
        f'<ellipse cx="110" cy="110" rx="42" ry="80" {ring} stroke-width="22" opacity=".85"/></g>'
    )


def orb(x, y, s):
    ring = 'cx="260" cy="280" rx="235" ry="62" fill="none" stroke="url(#or)" stroke-width="18" transform="rotate(-14 260 280)"'
    return (
        f'<g transform="translate({x} {y}) scale({s})"><defs><radialGradient id="ob" cx="35%" cy="30%" r="75%">'
        '<stop offset="0" stop-color="#F4D9FF"/><stop offset=".25" stop-color="#C04BE0"/>'
        '<stop offset=".6" stop-color="#5B1B8F"/><stop offset="1" stop-color="#12031F"/></radialGradient>'
        '<linearGradient id="or" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="#BE4C00"/>'
        '<stop offset=".5" stop-color="#FFB070"/><stop offset="1" stop-color="#7621B0"/></linearGradient>'
        '<clipPath id="of"><rect x="-100" y="280" width="720" height="400" transform="rotate(-14 260 280)"/></clipPath></defs>'
        f'<ellipse {ring} opacity=".55"/><circle cx="260" cy="280" r="175" fill="url(#ob)"/>'
        '<ellipse cx="200" cy="200" rx="62" ry="38" fill="#fff" opacity=".35" transform="rotate(-30 200 200)"/>'
        '<ellipse cx="185" cy="188" rx="20" ry="12" fill="#fff" opacity=".8" transform="rotate(-30 185 188)"/>'
        f'<ellipse {ring} clip-path="url(#of)"/></g>'
    )


# --------------------------------------------------------------------------- hero

COMMANDS = [
    "aws eks update-kubeconfig",
    "kubectl rollout status deploy/api",
    "argocd app sync api",
    "terraform apply tfplan",
    "gh workflow run deploy.yml",
    "codeql database analyze",
    "helm upgrade --install api ./chart",
    "trivy image komora:1.4.2",
    "kubectl get pods -n prod",
    "checkov -d infra/",
    "kubectl apply -f deploy.yaml",
    "terraform plan -out tfplan",
]


def drifting_commands(width, height, count=16) -> str:
    # a fixed seed keeps the layout stable between renders
    rnd = random.Random(4)  # nosec B311
    out = []
    for _ in range(count):
        x, y = rnd.uniform(10, width - 300), rnd.uniform(40, height - 30)
        cmd = rnd.choice(COMMANDS)
        duration, delay = rnd.uniform(9, 16), rnd.uniform(0, 16)
        out.append(
            f'<g class="cmd" style="animation-duration:{duration:.1f}s;animation-delay:-{delay:.1f}s">'
            f'<text x="{x:.0f}" y="{y:.0f}" font-family="{MONO}" font-size="15" fill="#5a5f68">'
            f'<tspan fill="{MAGENTA}">$ </tspan>{escape(cmd)}<tspan fill="#4b8f6a">  ✓</tspan></text></g>'
        )
    return "".join(out)


def portrait(x, y, scale, alarm: bool) -> str:
    """The site's layered 3D portrait: the irises glance around and the eyes blink."""
    path = ASSETS / "portrait"
    geo = json.loads((path / "geometry.json").read_text())
    width, height = geo["width"], geo["height"]
    if alarm:
        gaze = [
            (0, 0),
            (0.9, 0.5),
            (0.9, 0.5),
            (-0.9, 0.4),
            (-0.9, 0.4),
            (0.9, 0.5),
            (0, 0.6),
            (0, 0.6),
            (-0.7, 0.2),
            (0.6, 0.5),
            (0, 0),
        ]
        duration = 6
    else:
        gaze = [
            (0, 0),
            (0, 0),
            (-0.85, 0.1),
            (-0.85, 0.1),
            (0, 0),
            (0, 0),
            (0.8, 0.35),
            (0.8, 0.35),
            (0.2, -0.3),
            (0.2, -0.3),
            (0, 0),
        ]
        duration = 11
    times = ";".join(f"{i / (len(gaze) - 1):.3f}" for i in range(len(gaze)))
    splines = ";".join(["0.3 0 0.2 1"] * (len(gaze) - 1))
    parts = [
        f'<g transform="translate({x} {y}) scale({scale})"><g class="breathe">',
        f'<image href="{data_uri(path / "base.webp")}" width="{width}" height="{height}"/>',
    ]
    for key in ("l", "r"):
        eye = geo["eyes"][key]
        bx, by, bw, bh = eye["box"]
        ix, iy, iw, ih = eye["irisBox"]
        dx, dy = eye["r"] * 0.3, eye["r"] * 0.16
        values = ";".join(f"{gx * dx:.1f} {gy * dy:.1f}" for gx, gy in gaze)
        parts.append(
            f'<mask id="m{key}" maskUnits="userSpaceOnUse" x="{bx}" y="{by}" width="{bw}" height="{bh}" '
            f'style="mask-type:alpha"><image href="{data_uri(path / f"mask-{key}.png")}" x="{bx}" y="{by}" '
            f'width="{bw}" height="{bh}" preserveAspectRatio="none"/></mask>'
            f'<g mask="url(#m{key})"><image href="{data_uri(path / f"iris-{key}.webp")}" x="{ix}" y="{iy}" '
            f'width="{iw}" height="{ih}"><animateTransform attributeName="transform" type="translate" '
            f'values="{values}" keyTimes="{times}" dur="{duration}s" repeatCount="indefinite" '
            f'calcMode="spline" keySplines="{splines}"/></image></g>'
        )
    hx0, hy0, hx1, hy1 = geo["hair"]
    parts.append(
        f'<image href="{data_uri(path / "hair.webp")}" x="{hx0}" y="{hy0}" width="{hx1 - hx0}" height="{hy1 - hy0}"/>'
    )
    cx0, cy0, cx1, cy1 = geo["closed"]
    parts.append(
        f'<image href="{data_uri(path / "closed.webp")}" x="{cx0}" y="{cy0}" width="{cx1 - cx0}" '
        f'height="{cy1 - cy0}" opacity="0"><animate attributeName="opacity" values="0;0;1;1;0;0;1;0;1;0;0" '
        'keyTimes="0;0.40;0.412;0.425;0.437;0.86;0.872;0.884;0.896;0.908;1" dur="9s" '
        'repeatCount="indefinite" calcMode="discrete"/></image></g></g>'
    )
    return "".join(parts)


def hero(label: str, severity: str) -> str:
    width, height = 1200, 860
    alarm = severity != "ok"
    color = SEVERITY_COLOR[severity]
    body = [
        (
            '<radialGradient id="spot" cx="50%" cy="55%" r="50%">'
            f'<stop offset="0" stop-color="{MAGENTA}" stop-opacity=".22"/><stop offset="1" stop-color="{MAGENTA}" stop-opacity="0"/>'
            '</radialGradient><radialGradient id="alarm" cx="50%" cy="50%" r="50%">'
            '<stop offset="0" stop-color="#ff2440" stop-opacity=".45"/><stop offset="1" stop-color="#ff2440" stop-opacity="0"/>'
            '</radialGradient><linearGradient id="fade" x1="0" y1="0" x2="0" y2="1">'
            f'<stop offset=".72" stop-color="{BG}" stop-opacity="0"/><stop offset="1" stop-color="{BG}"/></linearGradient>'
        ),
        f'<rect width="{width}" height="{height}" fill="{BG}"/>',
        drifting_commands(width, height),
        '<ellipse cx="600" cy="560" rx="460" ry="360" fill="url(#spot)"/>',
    ]
    if alarm:
        body.append(
            '<g class="sweep"><path d="M600 520 L1500 300 L1500 740 Z" fill="url(#alarm)"/></g>'
        )
    for x, word, anchor in (
        (40, "ABOUT", "start"),
        (450, "STACK", "start"),
        (853, "PROJECTS", "start"),
        (1160, "CONTACT", "end"),
    ):
        body.append(
            f'<text x="{x}" y="54" text-anchor="{anchor}" font-family="K" font-weight="600" font-size="20" '
            f'letter-spacing=".5" fill="#e9eef2">{word}</text>'
        )
    heading = (
        'x="40" y="208" font-family="K" font-weight="900" font-size="178" textLength="1120" '
        'lengthAdjust="spacingAndGlyphs" letter-spacing="-2">HI, I\'M SANDEEP</text>'
    )
    body.append(
        f'<clipPath id="hl"><text {heading}</clipPath><g class="rise"><text fill="url(#chrome)" {heading}'
        '<g clip-path="url(#hl)"><rect class="shine" x="0" y="40" width="160" height="200" fill="#fff" opacity=".28"/></g></g>'
    )
    body.append(portrait(320, 268, 0.5, alarm))
    body.append(f'<rect width="{width}" height="{height}" fill="url(#fade)"/>')
    tagline = (
        "CLOUD, DEVOPS &amp;",
        "DEVSECOPS ENGINEER",
        "BUILDING AUTOMATED,",
        "SECURE AND OBSERVABLE",
        "SYSTEMS",
    )
    for i, line in enumerate(tagline):
        body.append(
            f'<text x="40" y="{690 + i * 28}" font-family="K" font-size="19" fill="#e9eef2">{line}</text>'
        )
    body.append(
        f'<g transform="translate(40 640)"><circle class="pulse" cx="6" cy="-6" r="5" fill="{color}"/>'
        f'<text x="20" y="0" font-family="{MONO}" font-size="13" letter-spacing="1" fill="{color}">'
        f"PRODUCTION · {escape(label.upper())}</text></g>"
    )
    body.append(pill(958, 788, 202, "CONTACT ME"))
    css = (
        ".cmd{opacity:0;animation-name:cmd;animation-timing-function:ease-in-out;animation-iteration-count:infinite}"
        "@keyframes cmd{0%{opacity:0;transform:translateY(14px)}20%{opacity:.55}75%{opacity:.4}"
        "100%{opacity:0;transform:translateY(-26px)}}"
        ".breathe{animation:breathe 6s ease-in-out infinite}@keyframes breathe{50%{transform:translateY(-8px)}}"
        ".rise{animation:rise 1.4s cubic-bezier(.2,.7,.1,1) both}@keyframes rise{from{opacity:0;transform:translateY(40px)}}"
        ".shine{animation:shine 6s ease-in-out infinite}"
        "@keyframes shine{0%,60%{transform:skewX(-20deg) translateX(-1400px)}100%{transform:skewX(-20deg) translateX(1400px)}}"
        ".pulse{animation:pulse 2s ease-in-out infinite}@keyframes pulse{50%{opacity:.25}}"
        ".sweep{transform-origin:600px 520px;animation:sweep 3s linear infinite}@keyframes sweep{to{transform:rotate(360deg)}}"
    )
    return svg(
        width,
        height,
        "".join(body),
        f"Hi, I'm Sandeep. Cloud, DevOps and DevSecOps engineer. Production: {label}.",
        css,
    )


# --------------------------------------------------------------------------- production panel


def production(
    config: dict, states: dict, label: str, severity: str, metrics: dict
) -> str:
    width, height = 1200, 860
    body = [
        f'<rect width="{width}" height="{height}" fill="{BG}"/>',
        title(600, 150, "BREAK MY", 130),
        title(600, 270, "PRODUCTION", 130),
        (
            '<text x="600" y="318" text-anchor="middle" font-family="K" font-size="20" fill="#9aa3ad">'
            "Eight of my projects run as one system. Pick one, open the issue, watch it fall. It heals itself.</text>"
        ),
        (
            '<g transform="translate(150 360)"><rect width="900" height="86" rx="43" fill="#121212" stroke="#26282d"/>'
            f'<circle class="pulse" cx="44" cy="43" r="9" fill="{SEVERITY_COLOR[severity]}"/>'
            f'<text x="66" y="51" font-family="K" font-weight="800" font-size="24" fill="#f2f5f7">{escape(label.upper())}</text>'
        ),
    ]
    affected = sum(1 for st in states.values() if st != "healthy")
    stats = [
        (f"{metrics['availability']:.2f}%", "UPTIME 30D"),
        (f"{affected}/{len(states)}", "AFFECTED"),
        (str(metrics["incidents"]), "TIMES BROKEN"),
    ]
    for i, (value, key) in enumerate(stats):
        x = 520 + i * 130
        body.append(
            f'<text x="{x}" y="44" font-family="K" font-weight="800" font-size="24" fill="url(#chrome)">{value}</text>'
            f'<text x="{x}" y="64" font-family="K" font-size="11" letter-spacing="1.5" fill="#7d858f">{key}</text>'
        )
    body.append("</g>")

    columns, spacing, top = 4, 225, 545
    position = {
        s["id"]: (
            600 - 1.5 * spacing + (i % columns) * spacing,
            top + (i // columns) * 150,
        )
        for i, s in enumerate(config["services"])
    }
    for s in config["services"]:
        for dep in s["deps"]:
            (x1, y1), (x2, y2) = position[s["id"]], position[dep]
            line = MAGENTA if states[dep] == "healthy" else STATE_COLOR[states[dep]]
            body.append(
                f'<path d="M{x1} {y1}L{x2} {y2}" stroke="{line}" stroke-width="1.4" opacity=".35"/>'
            )
            if states[dep] in ("healthy", "slow", "degraded"):
                duration = 2.2 if states[dep] == "healthy" else 6
                body.append(
                    f'<circle r="3.5" fill="#fefefe"><animateMotion dur="{duration}s" repeatCount="indefinite" '
                    f'path="M{x1} {y1}L{x2} {y2}"/></circle>'
                )
    gradients = {
        "healthy": ("#F4D9FF", "#C04BE0", "#2a0b45"),
        "degraded": ("#FFE29A", "#E08A1E", "#3d1d00"),
        "slow": ("#FFE29A", "#E08A1E", "#3d1d00"),
        "down": ("#FFB3BD", "#E0243A", "#3a0008"),
        "cut": ("#E6E9F2", "#7B8399", "#1c1f29"),
    }
    for i, s in enumerate(config["services"]):
        cx, cy = position[s["id"]]
        st = states[s["id"]]
        g = gradients[st]
        motion = {"down": "shake", "cut": "flick"}.get(st, "bob")
        body.append(
            f'<radialGradient id="g{i}" cx="35%" cy="30%" r="75%"><stop offset="0" stop-color="{g[0]}"/>'
            f'<stop offset=".55" stop-color="{g[1]}"/><stop offset="1" stop-color="{g[2]}"/></radialGradient>'
            f'<g class="{motion}" style="animation-delay:-{i * 0.7:.1f}s">'
        )
        if st != "healthy":
            body.append(
                f'<circle class="ring" cx="{cx}" cy="{cy}" r="34" fill="none" stroke="{STATE_COLOR[st]}" stroke-width="2"/>'
            )
        body.append(
            f'<circle cx="{cx}" cy="{cy}" r="32" fill="url(#g{i})"/>'
            f'<ellipse cx="{cx - 10}" cy="{cy - 12}" rx="11" ry="6" fill="#fff" opacity=".55" '
            f'transform="rotate(-30 {cx - 10} {cy - 12})"/>'
        )
        if st == "down":
            body.append(
                f'<path d="M{cx - 8} {cy - 30}l6 14-7 6 9 12-4 10" stroke="#2a0008" stroke-width="2.4" fill="none"/>'
            )
        body.append(
            "</g>"
            f'<text x="{cx}" y="{cy + 60}" text-anchor="middle" font-family="K" font-weight="600" font-size="17" '
            f'fill="#eef2f5">{escape(s["name"])}</text>'
            f'<text x="{cx}" y="{cy + 80}" text-anchor="middle" font-family="K" font-size="12" letter-spacing="1.5" '
            f'fill="{STATE_COLOR[st]}">{STATE_WORD[st]}</text>'
        )
    css = BOB + (
        ".pulse{animation:pulse 1.6s ease-in-out infinite}@keyframes pulse{50%{opacity:.25}}"
        ".shake{animation:shake .5s steps(2) infinite}@keyframes shake{50%{transform:translate(2px,-1px)}}"
        ".flick{animation:flick 1.2s steps(3) infinite}@keyframes flick{40%{opacity:.4}}"
        ".ring{transform-box:fill-box;transform-origin:center;animation:ring 1.8s ease-out infinite}"
        "@keyframes ring{from{transform:scale(1);opacity:.9}to{transform:scale(1.9);opacity:0}}"
    )
    return svg(
        width, height, "".join(body), f"Break my production. Status: {label}.", css
    )


def render_live(
    config: dict, states: dict, label: str, severity: str, metrics: dict
) -> None:
    write("hero", hero(label, severity))
    write("production", production(config, states, label, severity, metrics))


# --------------------------------------------------------------------------- static panels

TOOLS = [
    "AWS",
    "EKS",
    "Terraform",
    "Docker",
    "Kubernetes",
    "Helm",
    "Ansible",
    "Python",
    "GitHub Actions",
    "Jenkins",
    "Harness",
    "CodeQL",
    "SonarQube",
    "Trivy",
    "Checkov",
    "Kyverno",
    "Falco",
    "Prometheus",
    "Grafana",
    "Vault",
    "Splunk",
]


def marquee() -> str:
    width, height, size = 1200, 200, 52
    body = [f'<rect width="{width}" height="{height}" fill="{BG}"/>']
    css = ""
    for row, items in enumerate((TOOLS[:11], TOOLS[11:])):
        x, parts = 0.0, []
        for name in items:
            parts.append(
                f'<text x="{x:.0f}" y="0" font-family="K" font-weight="900" font-size="{size}" '
                f'fill="url(#chrome)">{escape(name.upper())}</text>'
            )
            x += len(name) * size * 0.66 + 40
            parts.append(
                f'<path transform="translate({x - 26:.0f} -18)" d="M0-11L3-3 11 0 3 3 0 11-3 3-11 0-3-3Z" fill="{MAGENTA}"/>'
            )
            x += 14
        group = "".join(parts)
        start, end = (f"-{x:.0f}px", "0") if row == 0 else ("0", f"-{x:.0f}px")
        css += (
            f".r{row}{{animation:m{row} {x / 60:.1f}s linear infinite}}"
            f"@keyframes m{row}{{from{{transform:translateX({start})}}to{{transform:translateX({end})}}}}"
        )
        copies = "".join(
            f'<g transform="translate({k * x:.0f} 0)">{group}</g>' for k in range(3)
        )
        body.append(
            f'<g transform="translate(0 {80 + row * 82})"><g class="r{row}">{copies}</g></g>'
        )
    body.append(
        '<linearGradient id="edge" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="#0c0c0c"/>'
        '<stop offset=".08" stop-color="#0c0c0c" stop-opacity="0"/><stop offset=".92" stop-color="#0c0c0c" stop-opacity="0"/>'
        f'<stop offset="1" stop-color="#0c0c0c"/></linearGradient><rect width="{width}" height="{height}" fill="url(#edge)"/>'
    )
    return svg(width, height, "".join(body), "Tools: " + ", ".join(TOOLS), css)


ABOUT = (
    "Automate the repeatable. Secure the critical. Observe the whole system. I'm Sandeep Komal, and I enjoy turning "
    "infrastructure and delivery problems into repeatable engineering systems across AWS, Kubernetes, Terraform, CI/CD, "
    "security automation and observability."
)
ABOUT_STATS = [
    ("CLOUD", "AWS-first infrastructure"),
    ("PLATFORM", "Kubernetes & containers"),
    ("DELIVERY", "CI/CD automation"),
    ("SECURITY", "Shift-left controls"),
]


def about() -> str:
    width, height = 1200, 700
    body = [
        f'<rect width="{width}" height="{height}" fill="{BG}"/>',
        title(600, 175, "ABOUT ME", 160),
        floating(moon(20, 200, 0.6), 0),
        floating(brick(1020, 210, 0.7), 2),
        floating(spheres(90, 440, 1.0), 3.5),
        floating(f'<g class="spin">{torus(950, 430, 0.95)}</g>', 1.2),
    ]
    for i, line in enumerate(wrap(ABOUT, 52)):
        body.append(
            f'<text class="rev" style="animation-delay:{i * 0.25:.2f}s" x="600" y="{262 + i * 36}" text-anchor="middle" '
            f'font-family="K" font-weight="600" font-size="23" fill="#d7e2ea">{escape(line)}</text>'
        )
    for i, (name, text) in enumerate(ABOUT_STATS):
        x = 600 - 2 * 168 + i * 168
        body.append(
            f'<rect x="{x}" y="490" width="156" height="104" rx="18" fill="#111" stroke="#2c2f35"/>'
            f'<text x="{x + 78}" y="532" text-anchor="middle" font-family="K" font-weight="600" font-size="17" '
            f'fill="#eef2f5">{name}</text><text x="{x + 78}" y="560" text-anchor="middle" font-family="K" '
            f'font-size="14" fill="#9aa3ad">{escape(text)}</text>'
        )
    css = BOB + (
        ".rev{opacity:.45;animation:rev 6s ease-in-out infinite}"
        "@keyframes rev{0%,10%{opacity:.45}25%,85%{opacity:1}100%{opacity:.45}}"
    )
    return svg(width, height, "".join(body), "About me. " + ABOUT, css)


STACK = [
    ("CLOUD", "AWS · EC2 · EKS · ECS · ECR · S3 · RDS · IAM · VPC · Route 53"),
    (
        "PLATFORM",
        "Kubernetes · Docker · Helm · kind · RBAC · Network Policies · HPA · StatefulSets",
    ),
    (
        "AUTOMATION",
        "Terraform · Ansible · Python · Shell · GitHub Actions · Jenkins · Harness",
    ),
    ("SECURITY", "CodeQL · SonarQube · Trivy · Checkov · Kyverno · Falco · OWASP ZAP"),
    (
        "OBSERVABILITY",
        "Prometheus · Grafana · Alertmanager · Node Exporter · Blackbox Exporter · Splunk",
    ),
    ("SECRETS", "AWS Secrets Manager · External Secrets Operator · Vault"),
]


def stack() -> str:
    width, row = 1200, 150
    height = 230 + row * len(STACK) + 40
    body = [
        f'<rect width="{width}" height="{height}" fill="{BG}"/>',
        f'<rect width="{width}" height="{height}" rx="56" fill="#fffffe"/>',
        title(600, 170, "MY STACK", 150, fill=INK),
    ]
    for i, (name, tools) in enumerate(STACK):
        y = 230 + i * row
        body.append(
            f'<line x1="150" y1="{y}" x2="1050" y2="{y}" stroke="#dcdcdc"/>'
            f'<g class="slide" style="animation-delay:-{i * 0.9:.1f}s"><text x="150" y="{y + 112}" font-family="K" '
            f'font-weight="900" font-size="118" fill="{INK}">0{i + 1}</text></g>'
            f'<text x="390" y="{y + 66}" font-family="K" font-weight="600" font-size="30" fill="{INK}">{name}</text>'
        )
        for j, line in enumerate(wrap(tools, 62)):
            body.append(
                f'<text x="390" y="{y + 102 + j * 26}" font-family="K" font-size="19" fill="#666">{escape(line)}</text>'
            )
    end = 230 + row * len(STACK)
    body.append(f'<line x1="150" y1="{end}" x2="1050" y2="{end}" stroke="#dcdcdc"/>')
    css = (
        ".slide{animation:slide 5.4s ease-in-out infinite}"
        "@keyframes slide{0%,70%,100%{transform:none}80%{transform:translateX(10px)}}"
    )
    return svg(
        width,
        height,
        "".join(body),
        "My stack. " + ". ".join(f"{n}: {t}" for n, t in STACK),
        css,
    )


PROJECTS = [
    (
        "KOMORA",
        "Cloud Native",
        "Flask storefront on Kubernetes: restricted pods, default-deny networking, Kyverno, scanned CI/CD.",
        ("Flask", "Kubernetes", "Kyverno", "Trivy"),
        "komora",
        "#D9FF3A",
        "https://github.com/SandeepKomal/komorapy_v4",
    ),
    (
        "Paste-Safe",
        "Security Tool",
        "Strips secrets from logs before you paste them into an AI, then restores them. Runs in your browser.",
        ("JavaScript", "PWA", "CodeQL"),
        "paste-safe",
        "#FFB000",
        "https://github.com/SandeepKomal/paste-safe",
    ),
    (
        "Git3D Universe",
        "GitHub Action",
        "A year of GitHub activity as a living 3D universe, with top repositories orbiting as planets.",
        ("Actions", "SVG", "Automation"),
        "git3d-universe",
        "#B600A8",
        "https://github.com/SandeepKomal/Git3D-Universe",
    ),
    (
        "Neon Arena",
        "GitHub Action",
        "A year of contributions as a neon 3D stadium: lit bars, an LED ribbon and a streak light-cycle.",
        ("Actions", "SVG", "Marketplace"),
        "neon-arena",
        "#3FD0E0",
        "https://github.com/SandeepKomal/Neon-Arena",
    ),
    (
        "README-Glassfolio",
        "Profile Tooling",
        "Turns any GitHub username into a live, themed, self-updating profile README.",
        ("JavaScript", "GitHub API"),
        "glassfolio",
        "#8C8CFF",
        "https://github.com/SandeepKomal/README-Glassfolio",
    ),
    (
        "DevSecOps",
        "Security",
        "Defense-in-depth lab: secrets, SAST, SCA, IaC, container and DAST scanning with SBOMs.",
        ("CodeQL", "Semgrep", "Trivy", "ZAP"),
        "devsecops",
        "#FF8A3D",
        "https://github.com/SandeepKomal/DevSecOps",
    ),
]


def project_card(project, index: int) -> str:
    name, category, text, chips, image, accent, _ = project
    width, height = 580, 520
    lag = index * 1.3
    body = [
        f'<clipPath id="c"><rect width="{width}" height="{height}" rx="28"/></clipPath><g clip-path="url(#c)">',
        f'<rect width="{width}" height="{height}" fill="#141414"/>',
        (
            f'<g class="zoom"><image href="{data_uri(ASSETS / "projects" / f"{image}.webp")}" width="{width}" height="345" '
            'preserveAspectRatio="xMidYMid slice"/></g>'
        ),
        (
            '<linearGradient id="pf" x1="0" y1="0" x2="0" y2="1"><stop offset=".55" stop-color="#141414" stop-opacity="0"/>'
            f'<stop offset="1" stop-color="#141414"/></linearGradient><rect width="{width}" height="345" fill="url(#pf)"/>'
        ),
        (
            f'<rect x="28" y="24" width="{len(category) * 9 + 30}" height="30" rx="15" fill="{BG}" fill-opacity=".75" stroke="{accent}"/>'
            f'<text x="43" y="44" font-family="K" font-weight="600" font-size="13" letter-spacing="1" '
            f'fill="{accent}">{escape(category.upper())}</text>'
        ),
        f'<text x="28" y="388" font-family="K" font-weight="800" font-size="34" fill="#f2f5f7">{escape(name)}</text>',
    ]
    for i, line in enumerate(wrap(text, 58)):
        body.append(
            f'<text x="28" y="{420 + i * 23}" font-family="K" font-size="16" fill="#a5adb6">{escape(line)}</text>'
        )
    x = 28
    for chip in chips:
        w = len(chip) * 8 + 24
        body.append(
            f'<rect x="{x}" y="470" width="{w}" height="28" rx="14" fill="none" stroke="#33363c"/>'
            f'<text x="{x + w / 2}" y="489" text-anchor="middle" font-family="K" font-size="13" fill="#d7e2ea">{escape(chip)}</text>'
        )
        x += w + 8
    body.append(
        '<linearGradient id="sh" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="#fff" stop-opacity="0"/>'
        '<stop offset=".5" stop-color="#fff" stop-opacity=".22"/><stop offset="1" stop-color="#fff" stop-opacity="0"/>'
        f'</linearGradient><rect class="sheen" style="animation-delay:-{lag:.1f}s" x="-260" y="-80" width="200" '
        f'height="{height + 160}" fill="url(#sh)"/>'
        f'<circle class="halo" style="animation-delay:-{lag:.1f}s" cx="{width - 48}" cy="484" r="22" fill="{accent}"/>'
        f'<circle cx="{width - 48}" cy="484" r="22" fill="{accent}"/>'
        f'<g class="nudge" style="animation-delay:-{lag:.1f}s"><path d="M{width - 57} 484h17m-7-7 7 7-7 7" stroke="{BG}" '
        'stroke-width="2.6" fill="none" stroke-linecap="round" stroke-linejoin="round"/></g></g>'
        f'<rect x=".5" y=".5" width="{width - 1}" height="{height - 1}" rx="28" fill="none" stroke="#26282d"/>'
    )
    for delay, stroke, extra in (
        (lag, 2.5, ""),
        (lag + 3, 8, ' opacity=".25" filter="url(#pglow)"'),
    ):
        body.append(
            f'<rect class="trace" style="animation-delay:-{delay:.1f}s" x="1" y="1" width="{width - 2}" height="{height - 2}" '
            f'rx="27" fill="none" stroke="{accent}" stroke-width="{stroke}" stroke-linecap="round" pathLength="1000"{extra}/>'
        )
    css = (
        ".zoom{transform-origin:290px 170px;animation:zoom 14s ease-in-out infinite alternate}"
        "@keyframes zoom{to{transform:scale(1.07) translate(-8px,-4px)}}"
        ".sheen{animation:sheen 6s ease-in-out infinite}"
        "@keyframes sheen{0%,55%{transform:skewX(-18deg) translateX(0)}100%{transform:skewX(-18deg) translateX(1100px)}}"
        ".trace{stroke-dasharray:110 890;animation:trace 6s linear infinite}@keyframes trace{to{stroke-dashoffset:-1000}}"
        ".halo{transform-box:fill-box;transform-origin:center;animation:halo 2.4s ease-out infinite}"
        "@keyframes halo{0%{transform:scale(1);opacity:.6}100%{transform:scale(1.9);opacity:0}}"
        ".nudge{animation:nudge 2.4s ease-in-out infinite}@keyframes nudge{50%{transform:translateX(5px)}}"
    )
    return svg(width, height, "".join(body), f"{name}: {text}", css)


def heading(text: str, sub: str) -> str:
    width, height = 1200, 230
    body = (
        f'<rect width="{width}" height="{height}" fill="{BG}"/>{title(600, 160, text, 150)}'
        f'<text x="600" y="210" text-anchor="middle" font-family="K" font-size="20" fill="#9aa3ad">{escape(sub)}</text>'
    )
    return svg(width, height, body, text)


FAULT_BUTTONS = {
    "kill": "KILL A POD",
    "latency": "INJECT LATENCY",
    "partition": "CUT THE NETWORK",
}


def button(fault: str) -> str:
    return svg(264, 66, pill(4, 4, 256, FAULT_BUTTONS[fault]), FAULT_BUTTONS[fault])


def contact() -> str:
    width, height = 1200, 560
    body = [
        f'<rect width="{width}" height="{height}" fill="{BG}"/>',
        floating(orb(840, 90, 0.62), 0),
        title(80, 230, "LET'S", 190, "start"),
        title(80, 400, "TALK", 190, "start"),
        (
            '<text x="84" y="460" font-family="K" font-size="21" fill="#9aa3ad">Sev 3: say hello on LinkedIn · '
            "Sev 2: email me · Sev 1: production is down, it was probably you.</text>"
        ),
        pill(84, 490, 300, "SANDEEPKOMAL.GITHUB.IO"),
    ]
    return svg(width, height, "".join(body), "Let's talk", BOB)


def render_static() -> list[Path]:
    panels = {
        "marquee": marquee(),
        "about": about(),
        "stack": stack(),
        "projects": heading("PROJECTS", "Things I built, shipped and secured"),
        "contact": contact(),
    }
    panels.update({f"card-{p[4]}": project_card(p, i) for i, p in enumerate(PROJECTS)})
    written = [path for name, doc in panels.items() for path in write(name, doc)]
    # the magenta buttons read the same on both themes, so they have no light twin
    for fault in FAULT_BUTTONS:
        path = ROOT / f"btn-{fault}.svg"
        path.write_text(button(fault), encoding="utf-8")
        written.append(path)
    return written


if __name__ == "__main__":
    for written in render_static():
        print(written.relative_to(ROOT.parent))
