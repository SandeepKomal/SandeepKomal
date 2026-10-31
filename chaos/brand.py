"""Render the profile README panels.

Black (GitHub's dark page colour) or white, headings in Hubot Sans (SIL OFL)
drawn from traced glyphs, body text in the viewer's system font, and a
terminal running through every section. Each panel is written twice,
``name.svg`` for dark mode and ``name-light.svg`` for light mode, and the
README picks one with prefers-color-scheme.

Static panels are rendered by ``python3 chaos/brand.py``. The hero and the
production panel depend on the chaos state and are re-rendered by engine.py
after every injection or heal. No font is embedded and images are inlined, so
each panel is a single small request. Content that matters is visible
without animation; motion only decorates.
"""

from __future__ import annotations

import base64
import json
import re
import sys
from datetime import datetime
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "brand"
ASSETS = ROOT / "assets"
FONTS = ROOT / "fonts"
MONO = (
    "ui-monospace,SFMono-Regular,'SF Mono',Menlo,Consolas,'Liberation Mono',monospace"
)
ACCENT = "#d63af9"

THEMES = {
    "dark": {
        "bg": "#0d1117",
        "surface": "#151b23",
        "line": "#30363d",
        "text": "#f0f6fc",
        "muted": "#9198a1",
        "cmd": "#6e7681",
        "t0": "#ffffff",
        "t1": "#9198a1",
        "ok": "#3fb950",
        "warn": "#d29922",
        "bad": "#f85149",
        "cut": "#9198a1",
        "btn": "#f0f6fc",
        "btn_text": "#0d1117",
    },
    "light": {
        "bg": "#ffffff",
        "surface": "#f6f8fa",
        "line": "#d1d9e0",
        "text": "#1f2328",
        "muted": "#59636e",
        "cmd": "#8c959f",
        "t0": "#1f2328",
        "t1": "#59636e",
        "ok": "#1a7f37",
        "warn": "#9a6700",
        "bad": "#cf222e",
        "cut": "#59636e",
        "btn": "#1f2328",
        "btn_text": "#ffffff",
    },
}
STATE_TEXT = {
    "healthy": "Running",
    "degraded": "Degraded",
    "slow": "Slow",
    "down": "CrashLoopBackOff",
    "cut": "Unreachable",
}
STATE_TONE = {
    "healthy": "ok",
    "degraded": "warn",
    "slow": "warn",
    "down": "bad",
    "cut": "cut",
}
MOTION = "@media (prefers-reduced-motion:reduce){*{animation:none!important}}"
SANS = (
    "-apple-system,BlinkMacSystemFont,'Segoe UI','Noto Sans',Helvetica,Arial,sans-serif"
)
# Body text uses the viewer's system font, as GitHub itself does; no font is embedded.
TEXT_CSS = (
    f"[font-family=Mona]{{font-family:{SANS};font-weight:400}}"
    f"[font-family=MonaW]{{font-family:{SANS};font-weight:600}}"
)
GLYPHS = json.loads((FONTS / "hubot-glyphs.json").read_text())


def data_uri(path: Path) -> str:
    mime = {".png": "image/png", ".webp": "image/webp"}[path.suffix]
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode()}"


def hubot(
    x: float, y: float, text: str, size: float, fill: str, anchor: str = "start"
) -> str:
    """Headings in Hubot Sans, drawn from traced glyphs so no font is embedded."""
    scale = size / GLYPHS["upm"]
    glyphs = [GLYPHS["glyphs"].get(ch, GLYPHS["glyphs"][" "]) for ch in text]
    width = sum(g["adv"] for g in glyphs) * scale
    start = x - width / 2 if anchor == "middle" else x - width if anchor == "end" else x
    uses, pen = [], 0.0
    for ch, g in zip(text, glyphs):
        if g["d"]:
            uses.append(f'<use href="#h{ord(ch):x}" x="{pen:.0f}"/>')
        pen += g["adv"]
    return f'<g fill="{fill}" transform="translate({start:.1f} {y}) scale({scale:.5f})">{"".join(uses)}</g>'


def glyph_defs(body: str) -> str:
    used = sorted(set(re.findall(r'href="#h([0-9a-f]+)"', body)))
    return "".join(
        f'<path id="h{code}" d="{GLYPHS["glyphs"][chr(int(code, 16))]["d"]}"/>'
        for code in used
    )


def svg(
    t: dict,
    width: int,
    height: int,
    body: str,
    label: str,
    css: str = "",
    background: bool = True,
) -> str:
    title = (
        f'<linearGradient id="title" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{t["t0"]}"/>'
        f'<stop offset="1" stop-color="{t["t1"]}"/></linearGradient>'
    )
    fill = (
        f'<rect width="{width}" height="{height}" fill="{t["bg"]}"/>'
        if background
        else ""
    )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
        f'role="img" aria-label="{escape(label)}"><defs>{title}{glyph_defs(body)}</defs><style>{TEXT_CSS}{css}{MOTION}</style>'
        f"{fill}{body}</svg>\n"
    )


def write(name: str, render) -> list[Path]:
    """Render a panel for both themes: name.svg (dark) and name-light.svg."""
    paths = []
    for theme, suffix in (("dark", ""), ("light", "-light")):
        path = ROOT / f"{name}{suffix}.svg"
        path.write_text(render(THEMES[theme]), encoding="utf-8")
        paths.append(path)
    return paths


def wrap(text: str, width: int) -> list[str]:
    lines, current = [], ""
    for word in text.split():
        if current and len(current) + len(word) + 1 > width:
            lines.append(current)
            current = word
        else:
            current = f"{current} {word}".strip()
    return lines + [current]


def terminal_frame(t, x, y, w, h, command):
    """A window with a prompt line, shared by every terminal panel."""
    return (
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="16" fill="{t["surface"]}" stroke="{t["line"]}"/>'
        f'<line x1="{x}" y1="{y + 52}" x2="{x + w}" y2="{y + 52}" stroke="{t["line"]}"/>'
        f'<text x="{x + 24}" y="{y + 32}" font-family="{MONO}" font-size="15" fill="{t["muted"]}">'
        f'<tspan fill="{ACCENT}">❯ </tspan>{escape(command)}</text>'
    )


def status_bar(t, y, label, severity, states, metrics) -> str:
    tone = t[severity if severity != "ok" else "ok"]
    affected = sum(1 for s in states.values() if s != "healthy")
    text = "   ·   ".join(
        [
            label.upper(),
            f"UPTIME {metrics['availability']:.2f}%",
            f"{affected}/{len(states)} AFFECTED",
            f"{metrics['incidents']} BROKEN",
        ]
    )
    w = len(text) * 8.6 + 70
    return (
        f'<g transform="translate({600 - w / 2:.0f} {y})"><rect width="{w:.0f}" height="40" rx="20" '
        f'fill="{t["surface"]}" stroke="{t["line"]}"/><circle class="pulse" cx="26" cy="20" r="6" fill="{tone}"/>'
        f'<text x="44" y="25" font-family="MonaW" font-size="13" letter-spacing="1.5" fill="{t["text"]}">'
        f"{escape(text)}</text></g>"
    )


PULSE = (
    ".pulse{animation:pulse 1.8s ease-in-out infinite}@keyframes pulse{50%{opacity:.3}}"
)
NUDGE = ".nudge{animation:nudge 2.4s ease-in-out infinite}@keyframes nudge{50%{transform:translateX(4px)}}"


# --------------------------------------------------------------------------- hero

COMMANDS = [
    "terraform plan -out tfplan",
    "terraform apply tfplan",
    "aws eks update-kubeconfig",
    "kubectl get pods -n prod",
    "kubectl rollout status api",
    "helm upgrade --install api",
    "argocd app sync api",
    "gh workflow run deploy.yml",
    "codeql database analyze",
    "trivy image komora:1.4",
    "checkov -d infra/",
    "kyverno apply policies/",
    "vault kv get secret/api",
    "kubectl top nodes",
    "promtool check rules",
    "ansible-playbook site.yml",
    "docker buildx build .",
    "kubectl describe hpa api",
    "aws s3 sync ./dist",
    "terraform fmt -check",
    "gh pr checks",
    "falco --list",
    "kubectl logs deploy/api",
    "sonar-scanner",
]


PROMPTS = ("#b600a8", "#7621b0", "#be4c00")


def command_stream(t, failing: list[str], top: int, bottom: int) -> str:
    """Faint terminal lines drifting slowly upward behind the portrait.

    One command per row at a scattered position and brightness, rows evenly
    spaced so they never overlap. Failing services surface as red checks.
    """
    gap, speed = 40, 18  # px between rows, px per second
    count = (bottom - top) // gap + 2
    seed = 7
    rows = []
    fail_rows = {3 + i * 6: sid for i, sid in enumerate(failing)}
    for i in range(count):
        seed = (seed * 1103515245 + 12345) & 0x7FFFFFFF
        x = 20 + seed % 840
        seed = (seed * 1103515245 + 12345) & 0x7FFFFFFF
        alpha = 0.3 + (seed % 100) / 100 * 0.32
        y = top + 20 + i * gap
        if i in fail_rows:
            rows.append(
                f'<text x="{x}" y="{y}" font-family="{MONO}" font-size="15" fill="{t["bad"]}" opacity=".85">'
                f"$ kubectl get pod {escape(fail_rows[i])}  ✗</text>"
            )
            continue
        cmd = COMMANDS[seed % len(COMMANDS)]
        prompt = PROMPTS[(seed >> 3) % len(PROMPTS)]
        rows.append(
            f'<text x="{x}" y="{y}" font-family="{MONO}" font-size="15" fill="{t["cmd"]}" opacity="{alpha:.2f}">'
            f'<tspan fill="{prompt}">$ </tspan>{escape(cmd)}<tspan fill="{t["ok"]}">  ✓</tspan></text>'
        )
    block = count * gap
    rows = "".join(rows)
    out = [
        (
            f'<g class="up" style="animation-duration:{block / speed:.0f}s;--h:-{block}px">'
            f'{rows}<g transform="translate(0 {block})">{rows}</g></g>'
        )
    ]
    # bg-coloured gradients fade the stream at both ends; cheaper to animate under than a mask
    band = 110
    out.append(
        f'<linearGradient id="fadein" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{t["bg"]}"/>'
        f'<stop offset="1" stop-color="{t["bg"]}" stop-opacity="0"/></linearGradient>'
        f'<rect y="0" width="1200" height="{top}" fill="{t["bg"]}"/>'
        f'<rect y="{top}" width="1200" height="{band}" fill="url(#fadein)"/>'
        f'<rect y="{bottom - band}" width="1200" height="{band}" fill="url(#fadein)" transform="rotate(180 600 {bottom - band / 2})"/>'
        f'<rect y="{bottom}" width="1200" height="200" fill="{t["bg"]}"/>'
    )
    return "".join(out)


def portrait(x, y, scale, alarm: bool) -> str:
    """The layered 3D portrait: the irises glance around and the eyes blink."""
    path = ASSETS / "portrait"
    geo = json.loads((path / "geometry.json").read_text())
    if alarm:
        gaze, duration = (
            [
                (0, 0),
                (0.9, 0.5),
                (0.9, 0.5),
                (-0.9, 0.4),
                (-0.9, 0.4),
                (0, 0.6),
                (0, 0),
            ],
            5,
        )
    else:
        gaze, duration = (
            [
                (0, 0),
                (0, 0),
                (-0.85, 0.15),
                (-0.85, 0.15),
                (0, 0),
                (0.85, 0.3),
                (0.85, 0.3),
                (0, 0),
            ],
            10,
        )
    times = ";".join(f"{i / (len(gaze) - 1):.3f}" for i in range(len(gaze)))
    splines = ";".join(["0.3 0 0.2 1"] * (len(gaze) - 1))
    out = [
        f'<g transform="translate({x} {y}) scale({scale})"><g class="breathe">',
        f'<image href="{data_uri(path / "base.webp")}" width="{geo["width"]}" height="{geo["height"]}"/>',
    ]
    for key in ("l", "r"):
        eye = geo["eyes"][key]
        bx, by, bw, bh = eye["box"]
        ix, iy, iw, ih = eye["irisBox"]
        values = ";".join(
            f"{gx * eye['r'] * 0.3:.1f} {gy * eye['r'] * 0.16:.1f}" for gx, gy in gaze
        )
        out.append(
            f'<mask id="m{key}" maskUnits="userSpaceOnUse" x="{bx}" y="{by}" width="{bw}" height="{bh}" '
            f'style="mask-type:alpha"><image href="{data_uri(path / f"mask-{key}.png")}" x="{bx}" y="{by}" '
            f'width="{bw}" height="{bh}" preserveAspectRatio="none"/></mask>'
            f'<g mask="url(#m{key})"><image href="{data_uri(path / f"iris-{key}.webp")}" x="{ix}" y="{iy}" '
            f'width="{iw}" height="{ih}"><animateTransform attributeName="transform" type="translate" '
            f'values="{values}" keyTimes="{times}" dur="{duration}s" repeatCount="indefinite" '
            f'calcMode="spline" keySplines="{splines}"/></image></g>'
        )
    hx0, hy0, hx1, hy1 = geo["hair"]
    out.append(
        f'<image href="{data_uri(path / "hair.webp")}" x="{hx0}" y="{hy0}" width="{hx1 - hx0}" height="{hy1 - hy0}"/>'
    )
    cx0, cy0, cx1, cy1 = geo["closed"]
    out.append(
        f'<image href="{data_uri(path / "closed.webp")}" x="{cx0}" y="{cy0}" width="{cx1 - cx0}" '
        f'height="{cy1 - cy0}" opacity="0"><animate attributeName="opacity" values="0;0;1;1;0;0;1;0;1;0;0" '
        'keyTimes="0;.40;.412;.425;.437;.86;.872;.884;.896;.908;1" dur="9s" repeatCount="indefinite" '
        'calcMode="discrete"/></image></g></g>'
    )
    return "".join(out)


def hero(t, label, severity, states, metrics) -> str:
    width, height = 1200, 860
    failing = [sid for sid, st in states.items() if st in ("down", "cut")]
    body = [
        (
            f'<linearGradient id="fade" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{t["bg"]}" '
            f'stop-opacity="0"/><stop offset="1" stop-color="{t["bg"]}"/></linearGradient>'
        ),
        command_stream(t, failing, 220, 790),
        (
            f'<text x="600" y="62" text-anchor="middle" font-family="MonaW" font-size="13" letter-spacing="4" '
            f'fill="{t["muted"]}">SENIOR CLOUD OPERATIONS ENGINEER</text>'
        ),
        (hubot(600, 146, "SANDEEP KOMAL POTHU", 70, "url(#title)", "middle")),
        (
            f'<text x="600" y="190" text-anchor="middle" font-family="Mona" font-size="21" fill="{t["muted"]}">'
            "I keep production boring. You're welcome to try and break it.</text>"
        ),
        portrait(600 - 1120 * 0.36 / 2, 250, 0.36, severity != "ok"),
        f'<rect y="700" width="{width}" height="80" fill="url(#fade)"/>',
        f'<rect y="780" width="{width}" height="80" fill="{t["bg"]}"/>',
        status_bar(t, 800, label, severity, states, metrics),
    ]
    css = (
        ".up{animation:up linear infinite}@keyframes up{to{transform:translateY(var(--h))}}"
        ".breathe{animation:breathe 7s ease-in-out infinite}@keyframes breathe{50%{transform:translateY(-6px)}}"
        + PULSE
    )
    label_text = (
        f"Sandeep Komal Pothu, Senior Cloud Operations Engineer. Production: {label}."
    )
    return svg(t, width, height, "".join(body), label_text, css)


# --------------------------------------------------------------------------- sections


def section(t, kicker: str, title: str, sub: str) -> str:
    body = (
        '<linearGradient id="rule" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="#7c6cff" stop-opacity="0"/>'
        f'<stop offset=".5" stop-color="{ACCENT}"/><stop offset="1" stop-color="#1fc8e3" stop-opacity="0"/></linearGradient>'
        f'<text x="600" y="58" text-anchor="middle" font-family="MonaW" font-size="13" letter-spacing="4" '
        f'fill="{t["muted"]}">{escape(kicker)}</text>'
        f"{hubot(600, 128, title, 58, 'url(#title)', 'middle')}"
        f'<text x="600" y="170" text-anchor="middle" font-family="Mona" font-size="19" fill="{t["muted"]}">{escape(sub)}</text>'
        f'<line x1="420" y1="204" x2="780" y2="204" stroke="{t["line"]}"/>'
        '<g class="scan"><rect x="540" y="203" width="120" height="2" fill="url(#rule)"/></g>'
    )
    css = (
        ".scan{animation:scan 5s ease-in-out infinite}"
        "@keyframes scan{0%,100%{transform:translateX(-240px)}50%{transform:translateX(240px)}}"
    )
    return svg(t, 1200, 230, body, f"{title}. {sub}", css)


ABOUT = (
    "Automate the repeatable. Secure the critical. Observe the whole system. I turn infrastructure and "
    "delivery problems into repeatable engineering systems across AWS, Kubernetes, Terraform, CI/CD, "
    "security automation and observability."
)


def whoami(t) -> str:
    lines = wrap(ABOUT, 84)
    height = 120 + len(lines) * 34 + 40
    body = [terminal_frame(t, 40, 10, 1120, height - 20, "whoami")]
    for i, line in enumerate(lines):
        body.append(
            f'<text x="72" y="{110 + i * 34}" font-family="Mona" font-size="21" fill="{t["text"]}">{escape(line)}</text>'
        )
    y = 110 + len(lines) * 34
    body.append(
        f'<text x="72" y="{y}" font-family="{MONO}" font-size="16" fill="{ACCENT}">❯ '
        f'<tspan class="caret" fill="{t["muted"]}">▌</tspan></text>'
    )
    css = ".caret{animation:caret 1s steps(2,jump-none) infinite}@keyframes caret{50%{opacity:0}}"
    return svg(t, 1200, height, "".join(body), "whoami: " + ABOUT, css)


def production(t, config, states, label, severity, metrics, target) -> str:
    services = config["services"]
    row_h, top = 46, 128
    height = top + len(services) * row_h + 200
    body = [
        terminal_frame(
            t, 40, 10, 1120, height - 20, "kubectl get pods -n production --watch"
        )
    ]
    for x, name in (
        (72, "NAME"),
        (380, "READY"),
        (500, "STATUS"),
        (810, "ROLE"),
        (975, "REPO"),
    ):
        body.append(
            f'<text x="{x}" y="{top - 20}" font-family="{MONO}" font-size="13" letter-spacing="1" '
            f'fill="{t["muted"]}">{name}</text>'
        )
    for i, s in enumerate(services):
        y = top + 14 + i * row_h
        st = states[s["id"]]
        tone = t[STATE_TONE[st]]
        status = STATE_TEXT[st]
        if st == "degraded":
            dep = next((d for d in s["deps"] if states[d] != "healthy"), "")
            status = f"Degraded · via {dep}" if dep else status
        ready = "0/1" if st in ("down", "cut") else "1/1"
        blink = ' class="blink"' if st in ("down", "cut") else ""
        if i % 2 == 0:
            body.append(
                f'<rect x="56" y="{y - 30}" width="1088" height="{row_h}" rx="8" fill="{t["bg"]}" fill-opacity=".55"/>'
            )
        body.append(
            f'<circle{blink} cx="484" cy="{y - 5}" r="5" fill="{tone}"/>'
            f'<text x="72" y="{y}" font-family="{MONO}" font-size="17" fill="{t["text"]}">{escape(s["name"])}</text>'
            f'<text x="380" y="{y}" font-family="{MONO}" font-size="17" fill="{t["muted"]}">{ready}</text>'
            f'<text{blink} x="500" y="{y}" font-family="{MONO}" font-size="17" fill="{tone}">{escape(status)}</text>'
            f'<text x="810" y="{y}" font-family="{MONO}" font-size="17" fill="{t["muted"]}">{escape(s["role"])}</text>'
            f'<text x="975" y="{y}" font-family="{MONO}" font-size="15" fill="{t["muted"]}">'
            f"{escape(s['repo'].split('/')[1])}</text>"
        )
    body.append(
        f'<rect class="sweep" x="56" y="{top - 16}" width="1088" height="{row_h}" rx="8" fill="{ACCENT}" fill-opacity=".07"/>'
    )
    body.append(status_bar(t, height - 162, label, severity, states, metrics))
    notes = (
        "pick a fault below: an Action injects it, follows the dependency graph and replies on your issue",
        f"the reconciler heals it in 20 to 45 minutes · next target: {target} · edit the issue title to pick another",
    )
    for i, note in enumerate(notes):
        body.append(
            f'<text x="72" y="{height - 82 + i * 26}" font-family="{MONO}" font-size="13.5" fill="{t["muted"]}">'
            f'<tspan fill="{ACCENT}"># </tspan>{escape(note)}</text>'
        )
    css = (
        ".blink{animation:blink 1.2s steps(2,jump-none) infinite}@keyframes blink{50%{opacity:.35}}"
        f".sweep{{animation:sweep 8s ease-in-out infinite alternate}}"
        f"@keyframes sweep{{to{{transform:translateY({(len(services) - 1) * row_h}px)}}}}"
        + PULSE
    )
    return svg(
        t, 1200, height, "".join(body), f"kubectl get pods: production is {label}.", css
    )


REASONS = {"kill": "Killed", "latency": "Latency", "partition": "Partitioned"}


def events(t, incidents: list[dict]) -> str:
    """Recent breaks as kubectl events: who broke what, how far it spread, how long it took to heal."""
    recent = list(reversed(incidents))[:6]
    if not recent:
        body = terminal_frame(
            t, 40, 10, 1120, 130, "kubectl get events -n production"
        ) + (
            f'<text x="72" y="110" font-family="{MONO}" font-size="16" fill="{t["muted"]}">'
            "No resources found in production namespace. Nobody has broken it yet.</text>"
        )
        return svg(t, 1200, 150, body, "Recent breaks: nobody has broken it yet.")
    row_h, top = 40, 128
    height = top + len(recent) * row_h + 40
    body = [
        terminal_frame(t, 40, 10, 1120, height - 20, "kubectl get events -n production")
    ]
    for x, name in (
        (72, "STARTED (UTC)"),
        (290, "TYPE"),
        (400, "REASON"),
        (560, "OBJECT"),
        (760, "MESSAGE"),
    ):
        body.append(
            f'<text x="{x}" y="{top - 20}" font-family="{MONO}" font-size="13" letter-spacing="1" fill="{t["muted"]}">{name}</text>'
        )
    for i, inc in enumerate(recent):
        y = top + 14 + i * row_h
        healed = inc.get("healed")
        kind, tone = ("Normal", t["ok"]) if healed else ("Warning", t["bad"])
        spread = len(inc["blast"])
        if healed:
            minutes = (
                datetime.fromisoformat(healed.replace("Z", "+00:00"))
                - datetime.fromisoformat(inc["started"].replace("Z", "+00:00"))
            ).total_seconds() / 60
            message = f"by @{inc['agent']} · {spread} hit · healed in {minutes:.0f}m"
        else:
            message = f"by @{inc['agent']} · {spread} hit · healing"
        body.append(
            f'<text x="72" y="{y}" font-family="{MONO}" font-size="15" fill="{t["muted"]}">{escape(inc["started"][:16].replace("T", " "))}</text>'
            f'<text x="290" y="{y}" font-family="{MONO}" font-size="15" fill="{tone}">{kind}</text>'
            f'<text x="400" y="{y}" font-family="{MONO}" font-size="15" fill="{t["text"]}">{REASONS.get(inc["fault"], inc["fault"])}</text>'
            f'<text x="560" y="{y}" font-family="{MONO}" font-size="15" fill="{t["text"]}">pod/{escape(inc["target"])}</text>'
            f'<text x="760" y="{y}" font-family="{MONO}" font-size="15" fill="{t["muted"]}">{escape(message)}</text>'
        )
    return svg(t, 1200, height, "".join(body), f"Recent breaks: {len(recent)} shown.")


FAULT_BUTTONS = {
    "kill": ("KILL A POD", "✕"),
    "latency": ("INJECT LATENCY", "◷"),
    "partition": ("CUT THE NETWORK", "⌁"),
}


def button(t, fault: str) -> str:
    label, glyph = FAULT_BUTTONS[fault]
    w, h = 400, 96
    mid = h / 2
    body = (
        f'<rect x="12" y="12" width="{w - 24}" height="{h - 24}" rx="{(h - 24) / 2}" fill="{t["surface"]}" '
        f'stroke="{t["line"]}"/>'
        f'<circle cx="52" cy="{mid}" r="19" fill="{ACCENT}" fill-opacity=".14"/>'
        f'<text x="52" y="{mid + 6}" text-anchor="middle" font-family="{MONO}" font-size="17" fill="{ACCENT}">{glyph}</text>'
        f'<text x="84" y="{mid + 6}" font-family="MonaW" font-size="16" letter-spacing="2" fill="{t["text"]}">{label}</text>'
        f'<g class="nudge"><text x="{w - 46}" y="{mid + 7}" text-anchor="middle" font-family="MonaW" font-size="20" '
        f'fill="{t["text"]}">→</text></g>'
    )
    return svg(t, w, h, body, label, NUDGE)


STACK = [
    ("cloud", "AWS · EC2 · EKS · ECS · ECR · S3 · RDS · IAM · VPC · Route 53"),
    ("platform", "Kubernetes · Docker · Helm · kind · RBAC · NetworkPolicies · HPA"),
    (
        "automation",
        "Terraform · Ansible · Python · Shell · GitHub Actions · Jenkins · Harness",
    ),
    ("security", "CodeQL · SonarQube · Trivy · Checkov · Kyverno · Falco · OWASP ZAP"),
    (
        "observability",
        "Prometheus · Grafana · Alertmanager · Blackbox Exporter · Splunk",
    ),
    ("secrets", "AWS Secrets Manager · External Secrets Operator · Vault"),
]


def stack(t) -> str:
    row = 52
    height = 120 + len(STACK) * row + 60
    tools = sum(len(x.split(" · ")) for _, x in STACK)
    body = [
        terminal_frame(t, 40, 10, 1120, height - 20, "tree ~/stack"),
        f'<text x="72" y="102" font-family="{MONO}" font-size="17" fill="{t["muted"]}">~/stack</text>',
    ]
    for i, (name, items) in enumerate(STACK):
        y = 150 + i * row
        branch = "└──" if i == len(STACK) - 1 else "├──"
        body.append(
            f'<g class="row" style="animation-delay:{i * 0.6:.1f}s">'
            f'<text x="72" y="{y}" font-family="{MONO}" font-size="17" fill="{t["muted"]}">{branch}</text>'
            f'<text x="118" y="{y}" font-family="{MONO}" font-size="17" fill="{t["text"]}">{name}/</text>'
            f'<text x="330" y="{y}" font-family="Mona" font-size="18" fill="{t["muted"]}">{escape(items)}</text></g>'
        )
    body.append(
        f'<text x="72" y="{150 + len(STACK) * row}" font-family="{MONO}" font-size="15" fill="{t["muted"]}">'
        f"{len(STACK)} directories, {tools} tools</text>"
    )
    css = ".row{animation:row 7.2s ease-in-out infinite}@keyframes row{0%,12%{opacity:1}6%{opacity:.4}}"
    return svg(
        t,
        1200,
        height,
        "".join(body),
        "Stack: " + "; ".join(f"{n}: {x}" for n, x in STACK),
        css,
    )


PROJECTS = [
    (
        "KOMORA",
        "Cloud native",
        "Flask storefront on Kubernetes: restricted pods, default-deny networking, Kyverno, scanned CI/CD.",
        ("Flask", "Kubernetes", "Kyverno", "Trivy"),
        "komora",
        "#9bd300",
        "https://github.com/SandeepKomal/komorapy_v4",
    ),
    (
        "Paste-Safe",
        "Security tool",
        "Strips secrets from logs before you paste them into an AI, then restores them. Runs in your browser.",
        ("JavaScript", "PWA", "CodeQL"),
        "paste-safe",
        "#ffb000",
        "https://github.com/SandeepKomal/paste-safe",
    ),
    (
        "Git3D Universe",
        "GitHub Action",
        "A year of GitHub activity as a living 3D universe, with top repositories orbiting as planets.",
        ("Actions", "SVG", "Automation"),
        "git3d-universe",
        "#d63af9",
        "https://github.com/SandeepKomal/Git3D-Universe",
    ),
    (
        "Neon Arena",
        "GitHub Action",
        "A year of contributions as a neon 3D stadium: lit bars, an LED ribbon and a streak light-cycle.",
        ("Actions", "SVG", "Marketplace"),
        "neon-arena",
        "#1fc8e3",
        "https://github.com/SandeepKomal/Neon-Arena",
    ),
    (
        "README-Glassfolio",
        "Profile tooling",
        "Turns any GitHub username into a live, themed, self-updating profile README.",
        ("JavaScript", "GitHub API"),
        "glassfolio",
        "#8c8cff",
        "https://github.com/SandeepKomal/README-Glassfolio",
    ),
    (
        "DevSecOps",
        "Security",
        "Defense-in-depth lab: secrets, SAST, SCA, IaC, container and DAST scanning with SBOMs.",
        ("CodeQL", "Semgrep", "Trivy", "ZAP"),
        "devsecops",
        "#ff9f1c",
        "https://github.com/SandeepKomal/DevSecOps",
    ),
]


def project_card(t, project, index: int) -> str:
    name, category, text, chips, image, accent, _ = project
    w, h = 580, 500
    lag = index * 1.4
    body = [
        f'<rect x="1" y="1" width="{w - 2}" height="{h - 2}" rx="20" fill="{t["surface"]}"/>',
        f'<clipPath id="shot"><rect x="16" y="16" width="{w - 32}" height="300" rx="12"/></clipPath>',
        (
            f'<g clip-path="url(#shot)"><g class="zoom"><image href="{data_uri(ASSETS / "projects" / f"{image}.webp")}" '
            f'x="16" y="16" width="{w - 32}" height="300" preserveAspectRatio="xMidYMid slice"/></g></g>'
        ),
        f'<rect x="16" y="16" width="{w - 32}" height="300" rx="12" fill="none" stroke="{t["line"]}"/>',
        (
            f'<circle cx="34" cy="352" r="5" fill="{accent}"/>'
            f'<text x="48" y="357" font-family="MonaW" font-size="12" letter-spacing="2" fill="{t["muted"]}">'
            f"{escape(category.upper())}</text>"
        ),
        hubot(28, 396, name.upper(), 28, t["text"]),
    ]
    for i, line in enumerate(wrap(text, 62)):
        body.append(
            f'<text x="28" y="{426 + i * 22}" font-family="Mona" font-size="16" fill="{t["muted"]}">{escape(line)}</text>'
        )
    x = 28
    for chip in chips:
        cw = len(chip) * 8.4 + 20
        body.append(
            f'<rect x="{x:.0f}" y="462" width="{cw:.0f}" height="24" rx="12" fill="none" stroke="{t["line"]}"/>'
            f'<text x="{x + cw / 2:.0f}" y="478" text-anchor="middle" font-family="{MONO}" font-size="12" '
            f'fill="{t["muted"]}">{escape(chip)}</text>'
        )
        x += cw + 8
    body.append(
        f'<g class="nudge" style="animation-delay:-{lag:.1f}s"><text x="{w - 30}" y="480" text-anchor="end" '
        f'font-family="MonaW" font-size="13" letter-spacing="1.5" fill="{t["text"]}">REPO →</text></g>'
        f'<rect x="1" y="1" width="{w - 2}" height="{h - 2}" rx="20" fill="none" stroke="{t["line"]}"/>'
        f'<rect class="trace" style="animation-delay:-{lag:.1f}s" x="1" y="1" width="{w - 2}" height="{h - 2}" rx="20" '
        f'fill="none" stroke="{accent}" stroke-width="2" stroke-linecap="round" pathLength="1000"/>'
    )
    css = (
        ".zoom{transform-origin:290px 166px;animation:zoom 14s ease-in-out infinite alternate}"
        "@keyframes zoom{to{transform:scale(1.06)}}"
        ".trace{stroke-dasharray:90 910;animation:trace 7s linear infinite}@keyframes trace{to{stroke-dashoffset:-1000}}"
        + NUDGE
    )
    return svg(t, w, h, "".join(body), f"{name}: {text}", css)


LINKS = [
    ("linkedin", "LINKEDIN", "Say hello", "M-8-4h16v12h-16z M-3-4v-3h6v3 M-8 1h16"),
    ("email", "EMAIL", "sandeepkomalp@gmail.com", "M-9-6h18v12h-18z M-9-6l9 7 9-7"),
    ("medium", "MEDIUM", "Articles and write-ups", "M-6 8l2-6 9-9 4 4-9 9z M-4 2l4 4"),
    (
        "website",
        "WEBSITE",
        "sandeepkomal.github.io",
        "M0-9a9 9 0 1 0 .01 0z M-9 0h18 M0-9c-5 5-5 13 0 18 M0-9c5 5 5 13 0 18",
    ),
]


def link_button(t, key) -> str:
    _, label, sub, icon = next(item for item in LINKS if item[0] == key)
    w, h = 580, 96
    mid = h / 2
    body = (
        f'<rect x="1" y="1" width="{w - 2}" height="{h - 2}" rx="20" fill="{t["surface"]}" stroke="{t["line"]}"/>'
        f'<circle cx="52" cy="{mid}" r="26" fill="{t["btn"]}"/>'
        f'<path transform="translate(52 {mid})" d="{icon}" fill="none" stroke="{t["btn_text"]}" stroke-width="2" '
        'stroke-linejoin="round" stroke-linecap="round"/>'
        f'<text x="96" y="{mid - 4}" font-family="MonaW" font-size="16" letter-spacing="2" fill="{t["text"]}">{label}</text>'
        f'<text x="96" y="{mid + 20}" font-family="Mona" font-size="16" fill="{t["muted"]}">{escape(sub)}</text>'
        f'<g class="nudge"><text x="{w - 36}" y="{mid + 8}" text-anchor="middle" font-family="MonaW" font-size="22" '
        f'fill="{t["text"]}">↗</text></g>'
    )
    return svg(t, w, h, body, f"{label}: {sub}", NUDGE)


def footer(t) -> str:
    body = (
        f'<line x1="300" y1="20" x2="900" y2="20" stroke="{t["line"]}"/>'
        f'<text x="600" y="62" text-anchor="middle" font-family="{MONO}" font-size="14" fill="{t["muted"]}">'
        f'<tspan fill="{ACCENT}">❯ </tspan>exit 0</text>'
    )
    return svg(t, 1200, 90, body, "exit 0")


STAT_FIELDS = [
    ("contributions", "CONTRIBUTIONS"),
    ("commits", "COMMITS"),
    ("pull_requests", "PULL REQUESTS"),
    ("reviews", "REVIEWS"),
    ("current_streak", "DAY STREAK"),
    ("longest_streak", "LONGEST STREAK"),
    ("active_days", "ACTIVE DAYS"),
    ("stars", "STARS EARNED"),
    ("repos", "REPOSITORIES"),
    ("followers", "FOLLOWERS"),
]


def stats(t) -> str:
    """Twelve months of GitHub activity from brand/stats.json; fields not collected yet are left out."""
    data = json.loads((ROOT / "stats.json").read_text())
    shown = [
        (label, data[key]) for key, label in STAT_FIELDS if data.get(key) is not None
    ][:8]
    per_row, cell = 4, 1056 / 4
    rows = (len(shown) + per_row - 1) // per_row
    y = 100
    body = []
    for i, (label, value) in enumerate(shown):
        x = 72 + (i % per_row) * cell
        top = y + (i // per_row) * 104
        body.append(
            f"{hubot(x, top + 44, f'{value:,}', 44, 'url(#title)')}"
            f'<text x="{x:.0f}" y="{top + 72}" font-family="MonaW" font-size="12" letter-spacing="2" '
            f'fill="{t["muted"]}">{label}</text>'
        )
    y += rows * 104 + 10
    weeks = data.get("weeks") or []
    if weeks:
        body.append(
            f'<text x="72" y="{y + 10}" font-family="{MONO}" font-size="13" fill="{t["muted"]}">weekly activity · 52 weeks</text>'
        )
        peak, width = max(weeks) or 1, 1056 / len(weeks)
        for i, count in enumerate(weeks):
            h = 4 + 86 * count / peak
            last = ' class="pulse"' if i == len(weeks) - 1 else ""
            body.append(
                f'<rect{last} x="{72 + i * width:.1f}" y="{y + 116 - h:.1f}" width="{width - 4:.1f}" height="{h:.1f}" '
                f'rx="2" fill="{ACCENT}" fill-opacity="{0.25 + 0.75 * count / peak:.2f}"/>'
            )
        y += 140
    languages = data.get("languages") or []
    if languages:
        body.append(
            f'<text x="72" y="{y + 10}" font-family="{MONO}" font-size="13" fill="{t["muted"]}">languages</text>'
        )
        x, total = 72.0, sum(lang["share"] for lang in languages) or 1
        legend = []
        for lang in languages:
            w = 1056 * lang["share"] / total
            body.append(
                f'<rect x="{x:.1f}" y="{y + 26}" width="{max(w - 3, 2):.1f}" height="12" rx="6" fill="{lang["color"]}"/>'
            )
            x += w
            legend.append(lang)
        lx = 72
        for lang in legend:
            text = f"{lang['name']} {lang['share'] / total:.0%}"
            body.append(
                f'<circle cx="{lx + 5}" cy="{y + 64}" r="5" fill="{lang["color"]}"/>'
                f'<text x="{lx + 16}" y="{y + 69}" font-family="Mona" font-size="15" fill="{t["muted"]}">{escape(text)}</text>'
            )
            lx += len(text) * 8.5 + 44
        y += 90
    body.append(
        f'<text x="72" y="{y + 20}" font-family="{MONO}" font-size="13" fill="{t["muted"]}">'
        f"last 12 months · updated {escape(data.get('updated', ''))}</text>"
    )
    height = y + 60
    frame = terminal_frame(
        t, 40, 10, 1120, height - 20, "gh stats SandeepKomal --last 12m"
    )
    label = "GitHub stats: " + ", ".join(f"{v:,} {k.lower()}" for k, v in shown)
    return svg(t, 1200, height, frame + "".join(body), label, PULSE)


# --------------------------------------------------------------------------- entry points


def render_live(
    config, states, label, severity, metrics, incidents, target
) -> list[Path]:
    return (
        write("hero", lambda t: hero(t, label, severity, states, metrics))
        + write(
            "production",
            lambda t: production(t, config, states, label, severity, metrics, target),
        )
        + write("events", lambda t: events(t, incidents))
    )


SECTIONS = {
    "sec-production": (
        "01 · PRODUCTION",
        "BREAK MY PRODUCTION",
        "Eight of my projects run as one system. Pick a fault, open the issue, watch it fail. It heals itself.",
    ),
    "sec-3d": (
        "02 · CONTRIBUTIONS",
        "MY YEAR IN 3D",
        "Rendered every hour by two GitHub Actions I wrote.",
    ),
    "sec-stack": (
        "03 · STACK",
        "WHAT I RUN",
        "The tools behind the systems I build and operate.",
    ),
    "sec-stats": (
        "04 · STATS",
        "BY THE NUMBERS",
        "Twelve months on GitHub, refreshed daily.",
    ),
    "sec-projects": (
        "05 · PROJECTS",
        "THINGS I SHIPPED",
        "Built, secured and running in public.",
    ),
    "sec-contact": (
        "06 · CONTACT",
        "LET'S TALK",
        "Sev 3: say hello. Sev 2: email me. Sev 1: production is down, and it was probably you.",
    ),
}


def render_static() -> list[Path]:
    paths = (
        write("whoami", whoami)
        + write("stack", stack)
        + write("stats", stats)
        + write("footer", footer)
    )
    for name, (kicker, title, sub) in SECTIONS.items():
        paths += write(name, lambda t, k=kicker, ti=title, s=sub: section(t, k, ti, s))
    for fault in FAULT_BUTTONS:
        paths += write(f"btn-{fault}", lambda t, f=fault: button(t, f))
    for i, project in enumerate(PROJECTS):
        paths += write(
            f"card-{project[4]}", lambda t, p=project, n=i: project_card(t, p, n)
        )
    for key, *_ in LINKS:
        paths += write(f"link-{key}", lambda t, k=key: link_button(t, k))
    return paths


if __name__ == "__main__":
    if sys.argv[1:] == ["stats"]:
        for written in write("stats", stats):
            print(written.relative_to(ROOT.parent))
        sys.exit(0)
    for written in render_static():
        print(written.relative_to(ROOT.parent))
