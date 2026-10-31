#!/usr/bin/env python3
"""Chaos engine for the profile cluster.

The profile README shows a small "production cluster" made of real projects.
Visitors open an issue titled ``chaos: <fault> <service>``; a workflow runs
``engine.py inject`` which records the fault and re-renders the cluster. A
scheduled ``engine.py reconcile`` heals faults once their recovery time has
passed. Everything is derived from ``state.json``, so the history (incidents,
time to fix, uptime) is real.

Standard library only. Untrusted input (issue title and author) arrives in
environment variables and is matched against strict patterns before use.
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent
REPO_ROOT = ROOT.parent
CONFIG_PATH = ROOT / "services.json"
STATE_PATH = ROOT / "state.json"
README_PATH = REPO_ROOT / "README.md"
SVG_PATHS = {"dark": ROOT / "cluster-dark.svg", "light": ROOT / "cluster-light.svg"}

TITLE_RE = re.compile(
    r"^\s*chaos:\s*([a-z]{1,20})\s+([a-z0-9-]{1,40})\s*$", re.IGNORECASE
)
LOGIN_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
README_START = "<!-- chaos:start -->"
README_END = "<!-- chaos:end -->"

HEALTHY, DEGRADED = "healthy", "degraded"


# --------------------------------------------------------------------------- time


def now() -> datetime:
    override = os.environ.get("CHAOS_NOW")
    if override:
        return parse_time(override)
    return datetime.now(timezone.utc).replace(microsecond=0)


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def fmt_time(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def fmt_duration(minutes: float) -> str:
    minutes = round(minutes)
    if minutes < 60:
        return f"{minutes}m"
    hours, mins = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h {mins:02d}m"
    days, hours = divmod(hours, 24)
    return f"{days}d {hours}h"


# --------------------------------------------------------------------------- state


def load_config() -> dict:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def load_state(current: datetime) -> dict:
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    return {"version": 1, "since": fmt_time(current), "incidents": []}


def save_state(state: dict) -> None:
    STATE_PATH.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def active_incidents(state: dict) -> list[dict]:
    return [i for i in state["incidents"] if not i.get("healed")]


def effective_states(config: dict, faults: dict[str, str]) -> dict[str, str]:
    """Map each service to its state given direct faults (service -> fault).

    A faulted service takes the fault's state; a service whose dependency is
    not healthy is degraded, transitively.
    """
    services = {s["id"]: s for s in config["services"]}
    result: dict[str, str] = {}

    def visit(sid: str, path: tuple[str, ...]) -> str:
        if sid in result:
            return result[sid]
        if sid in faults:
            state = config["faults"][faults[sid]]["state"]
        elif any(
            visit(dep, path + (sid,)) != HEALTHY
            for dep in services[sid]["deps"]
            if dep not in path
        ):
            state = DEGRADED
        else:
            state = HEALTHY
        result[sid] = state
        return state

    for sid in services:
        visit(sid, ())
    return {sid: result[sid] for sid in services}


def current_faults(state: dict) -> dict[str, str]:
    return {i["target"]: i["fault"] for i in active_incidents(state)}


def blast_radius(config: dict, target: str, fault: str) -> list[str]:
    states = effective_states(config, {target: fault})
    return [s["id"] for s in config["services"] if states[s["id"]] != HEALTHY]


def overall_status(config: dict, states: dict[str, str]) -> tuple[str, str]:
    """Return (label, severity) where severity is ok, warn or bad."""
    hard = [sid for sid, st in states.items() if st in ("down", "cut")]
    soft = [sid for sid, st in states.items() if st != HEALTHY]
    ingress = config["services"][0]["id"]
    if not soft:
        return "All systems operational", "ok"
    if states[ingress] in ("down", "cut") or len(soft) == len(states):
        return "Major outage", "bad"
    if hard:
        return "Partial outage", "bad"
    return "Degraded performance", "warn"


def reconcile(config: dict, state: dict, current: datetime) -> list[dict]:
    healed = []
    for incident in active_incidents(state):
        if parse_time(incident["heal_due"]) <= current:
            # Stamp the scheduled time so a late cron run doesn't inflate downtime.
            incident["healed"] = incident["heal_due"]
            healed.append(incident)
    return healed


# --------------------------------------------------------------------------- metrics


def metrics(config: dict, state: dict, current: datetime) -> dict:
    window_start = max(
        parse_time(state["since"]), current - timedelta(days=config["window_days"])
    )
    window = (current - window_start).total_seconds() / 60

    intervals = []
    for incident in state["incidents"]:
        start = max(parse_time(incident["started"]), window_start)
        end = parse_time(incident["healed"]) if incident.get("healed") else current
        end = min(end, current)
        if end > start:
            intervals.append((start, end))
    intervals.sort()
    downtime = 0.0
    cur_start = cur_end = None
    for start, end in intervals:
        if cur_end is None or start > cur_end:
            if cur_end is not None:
                downtime += (cur_end - cur_start).total_seconds() / 60
            cur_start, cur_end = start, end
        else:
            cur_end = max(cur_end, end)
    if cur_end is not None:
        downtime += (cur_end - cur_start).total_seconds() / 60

    availability = 100.0 if window <= 0 else max(0.0, 100.0 * (1 - downtime / window))
    budget_total = config["window_days"] * 24 * 60 * (100 - config["slo"]) / 100
    budget_left = 100.0 * (1 - downtime / budget_total) if budget_total else 0.0

    healed = [i for i in state["incidents"] if i.get("healed")]
    mttr = None
    if healed:
        mttr = sum(
            (parse_time(i["healed"]) - parse_time(i["started"])).total_seconds() / 60
            for i in healed
        ) / len(healed)

    return {
        "availability": availability,
        "budget_left": budget_left,
        "mttr": mttr,
        "incidents": len(state["incidents"]),
    }


# --------------------------------------------------------------------------- inject


def issue_url(config: dict, fault: str, target: str) -> str:
    title = f"chaos: {fault} {target}"
    body = (
        "Press **Submit new issue** and a GitHub Action injects this fault into the cluster "
        "on the profile. It replies with the blast radius and closes the issue.\n\n"
        "The cluster heals itself. Your handle stays in the incident log."
    )
    return f"https://github.com/{config['repo']}/issues/new?title={quote(title)}&body={quote(body)}"


def inject(
    config: dict, state: dict, current: datetime, title: str, author: str, number: str
) -> tuple[str, str, str]:
    """Return (result, comment markdown, commit message)."""
    services = {s["id"]: s for s in config["services"]}
    usage = (
        "Use a title like `chaos: kill blast-radius`.\n\n"
        f"Faults: {', '.join(f'`{f}`' for f in config['faults'])}\n"
        f"Services: {', '.join(f'`{s}`' for s in services)}"
    )

    if not LOGIN_RE.match(author or ""):
        return (
            "rejected",
            "Could not read the issue author, so nothing was injected.",
            "",
        )
    if not (number or "").isdigit():
        return (
            "rejected",
            "Could not read the issue number, so nothing was injected.",
            "",
        )
    issue = int(number)
    if any(i.get("issue") == issue for i in state["incidents"]):
        return "ignored", "", ""

    match = TITLE_RE.match(title or "")
    if not match:
        return (
            "rejected",
            f"That title isn't a chaos command, so nothing was injected.\n\n{usage}",
            "",
        )
    fault, target = match.group(1).lower(), match.group(2).lower()
    if fault not in config["faults"] or target not in services:
        return "rejected", f"Unknown fault or service.\n\n{usage}", ""

    active = active_incidents(state)
    if any(i["target"] == target for i in active):
        return (
            "rejected",
            f"`{target}` is already on fire. Pick another service and try again.\n\n{usage}",
            "",
        )
    if len(active) >= config["max_active_incidents"]:
        return (
            "rejected",
            (
                f"{len(active)} incidents are already open, the most the on-call engineer allows at once. "
                "Try again after the reconciler heals one."
            ),
            "",
        )
    if author.lower() != config["owner"].lower():
        cooldown = timedelta(minutes=config["agent_cooldown_minutes"])
        recent = [
            i
            for i in state["incidents"]
            if i["agent"].lower() == author.lower()
            and current - parse_time(i["started"]) < cooldown
        ]
        if recent:
            return (
                "rejected",
                (
                    f"Easy, @{author}. One fault every {config['agent_cooldown_minutes']} minutes per person."
                ),
                "",
            )

    spec = config["faults"][fault]
    blast = blast_radius(config, target, fault)
    incident = {
        "id": len(state["incidents"]) + 1,
        "issue": issue,
        "agent": author,
        "fault": fault,
        "target": target,
        "blast": blast,
        "started": fmt_time(current),
        "heal_due": fmt_time(current + timedelta(minutes=spec["heal_minutes"])),
        "healed": None,
    }
    state["incidents"].append(incident)

    states = effective_states(config, current_faults(state))
    label, _ = overall_status(config, states)
    affected = ", ".join(f"`{s}` ({states[s]})" for s in blast)
    comment = (
        f"**Fault injected:** {spec['label']} on `{target}` (incident #{incident['id']})\n\n"
        f"**Blast radius:** {len(blast)} of {len(services)} services: {affected}\n\n"
        f"**Status page:** {label}\n\n"
        f"**Recovery:** the reconciler heals `{target}` in about {spec['heal_minutes']} minutes. "
        "The downtime counts against the profile's uptime.\n\n"
        f"See the damage: https://github.com/{config['owner']}"
    )
    message = f"chaos: @{author} {fault} {target} (incident #{incident['id']})"
    return "accepted", comment, message


# --------------------------------------------------------------------------- render: svg

PALETTES = {
    "dark": {
        "bg0": "#03050c",
        "bg1": "#0a1230",
        "bg2": "#1a1046",
        "star": "#d6e4ff",
        "text": "#f4f7ff",
        "muted": "#8d9bc0",
        "faint": "#4a5680",
        "deck_top0": "#121c3f",
        "deck_top1": "#070b1d",
        "deck_l0": "#0e1636",
        "deck_l1": "#04060f",
        "deck_r0": "#0a1029",
        "deck_r1": "#03040a",
        "grid": "#5b7cff",
        "edge0": "#4cc9ff",
        "edge1": "#8b5cff",
        "body_top": "#33447a",
        "body_l0": "#1e2a52",
        "body_l1": "#111833",
        "body_r0": "#151d3d",
        "body_r1": "#0a0f22",
        "slot": "#070b1a",
        "shadow": "#000000",
        "shadow_op": ".45",
        "halo": "#070b1d",
        "ok": "#3df5c0",
        "warn": "#ffb02e",
        "bad": "#ff3d5a",
        "cut": "#a3acc7",
        "wire": "#4cc9ff",
    },
    "light": {
        "bg0": "#f7f9ff",
        "bg1": "#e4ecff",
        "bg2": "#efe6ff",
        "star": "#a9b8e6",
        "text": "#0b1230",
        "muted": "#56628a",
        "faint": "#a3aecd",
        "deck_top0": "#ffffff",
        "deck_top1": "#eef2fb",
        "deck_l0": "#dfe6f6",
        "deck_l1": "#c7d2ea",
        "deck_r0": "#ccd6ee",
        "deck_r1": "#b3c1e0",
        "grid": "#6d8cff",
        "edge0": "#2f9bff",
        "edge1": "#7c4dff",
        "body_top": "#ffffff",
        "body_l0": "#e6ecf9",
        "body_l1": "#c9d4ec",
        "body_r0": "#cdd7ee",
        "body_r1": "#aebcdc",
        "slot": "#9aa9cc",
        "shadow": "#1b2a5a",
        "shadow_op": ".14",
        "halo": "#ffffff",
        "ok": "#00a77a",
        "warn": "#d97800",
        "bad": "#e01e3c",
        "cut": "#6b7698",
        "wire": "#1f7bff",
    },
}
SEVERITY = {
    HEALTHY: "ok",
    DEGRADED: "warn",
    "slow": "warn",
    "down": "bad",
    "cut": "cut",
}
SANS = "-apple-system,BlinkMacSystemFont,'Segoe UI',Inter,Helvetica,Arial,sans-serif"
MONO = "ui-monospace,SFMono-Regular,'SF Mono',Menlo,Consolas,monospace"
W, H = 1200, 720
SCALE = 30
ORIGIN = (560, 232)
HALF = 0.72


def iso(x: float, y: float, z: float = 0.0) -> tuple[float, float]:
    return ORIGIN[0] + (x - y) * 0.866 * SCALE, ORIGIN[1] + ((x + y) * 0.5 - z) * SCALE


def pts(points) -> str:
    return " ".join(f"{x:.1f},{y:.1f}" for x, y in points)


def poly(points, fill, extra="") -> str:
    return f'<polygon points="{pts(points)}" fill="{fill}"{extra}/>'


def grid_pos(service: dict) -> tuple[float, float]:
    return service["tier"] * 4.4, service["row"] * 3.5


def faces(x, y, h, z0, z1):
    a, b, c, d = (x - h, y - h), (x + h, y - h), (x + h, y + h), (x - h, y + h)
    left = [iso(*d, z0), iso(*c, z0), iso(*c, z1), iso(*d, z1)]
    right = [iso(*b, z0), iso(*c, z0), iso(*c, z1), iso(*b, z1)]
    top = [iso(*a, z1), iso(*b, z1), iso(*c, z1), iso(*d, z1)]
    return left, right, top


def wire_path(a, b) -> str:
    (ax, ay), (bx, by) = a, b
    mid = (ax + bx) / 2
    points = [
        iso(ax, ay, 0.04),
        iso(mid, ay, 0.04),
        iso(mid, by, 0.04),
        iso(bx, by, 0.04),
    ]
    return "M" + " L".join(f"{x:.1f} {y:.1f}" for x, y in points)


def ground_text(
    x, y, text, size, fill, halo, weight="600", family=SANS, spacing=0.0
) -> str:
    sx, sy = iso(x, y, 0.02)
    return (
        f'<text transform="matrix(0.866 0.5 -0.866 0.5 {sx:.1f} {sy:.1f})" font-family="{family}" '
        f'font-size="{size}" font-weight="{weight}" letter-spacing="{spacing}" fill="{fill}" '
        f'stroke="{halo}" stroke-width="3" stroke-linejoin="round" paint-order="stroke">{escape(text)}</text>'
    )


def status_icon(cx: float, cy: float, state: str, color: str, r: float = 5) -> str:
    """Each state has its own shape so color is never the only signal."""
    if state == HEALTHY:
        return (
            f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{r * 0.85:.1f}" fill="{color}"/>'
        )
    if state in (DEGRADED, "slow"):
        return f'<path d="M{cx:.1f} {cy - r:.1f}L{cx + r:.1f} {cy + r * 0.8:.1f}H{cx - r:.1f}Z" fill="{color}"/>'
    if state == "down":
        k = r * 0.45
        return (
            f'<rect x="{cx - r:.1f}" y="{cy - r:.1f}" width="{2 * r:.1f}" height="{2 * r:.1f}" rx="2" fill="{color}"/>'
            f'<path d="M{cx - k:.1f} {cy - k:.1f}l{2 * k:.1f} {2 * k:.1f}M{cx + k:.1f} {cy - k:.1f}l-{2 * k:.1f} {2 * k:.1f}" '
            f'stroke="#fff" stroke-width="1.6" stroke-linecap="round"/>'
        )
    return f'<path d="M{cx:.1f} {cy - r * 1.15:.1f}L{cx + r * 1.15:.1f} {cy:.1f}L{cx:.1f} {cy + r * 1.15:.1f}L{cx - r * 1.15:.1f} {cy:.1f}Z" fill="{color}"/>'


def rng(seed: int):
    value = seed
    while True:
        value = (value * 1103515245 + 12345) & 0x7FFFFFFF
        yield value / 0x7FFFFFFF


def gradient(gid, c0, c1, x2="0", y2="1", o0="1", o1="1") -> str:
    return (
        f'<linearGradient id="{gid}" x1="0" y1="0" x2="{x2}" y2="{y2}">'
        f'<stop offset="0" stop-color="{c0}" stop-opacity="{o0}"/><stop offset="1" stop-color="{c1}" stop-opacity="{o1}"/>'
        "</linearGradient>"
    )


def render_svg(config: dict, state: dict, current: datetime, theme: str) -> str:
    p = PALETTES[theme]
    faults = current_faults(state)
    states = effective_states(config, faults)
    services = {s["id"]: s for s in config["services"]}
    label, severity = overall_status(config, states)
    m = metrics(config, state, current)
    rand = rng(11)
    out: list[str] = []
    add = out.append

    add(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" '
        f'role="img" aria-label="{escape(config["cluster"])}: {escape(label)}">'
    )
    defs = [
        (
            f'<radialGradient id="bg" cx="58%" cy="38%" r="80%"><stop offset="0" stop-color="{p["bg1"]}"/>'
            f'<stop offset=".55" stop-color="{p["bg0"]}"/><stop offset="1" stop-color="{p["bg0"]}"/></radialGradient>'
        ),
        (
            f'<radialGradient id="nebula" cx="50%" cy="50%" r="50%"><stop offset="0" stop-color="{p["bg2"]}" stop-opacity=".9"/>'
            f'<stop offset="1" stop-color="{p["bg2"]}" stop-opacity="0"/></radialGradient>'
        ),
        (
            f'<radialGradient id="decktop" cx="45%" cy="40%" r="70%"><stop offset="0" stop-color="{p["deck_top0"]}"/>'
            f'<stop offset="1" stop-color="{p["deck_top1"]}"/></radialGradient>'
        ),
        gradient("deckl", p["deck_l0"], p["deck_l1"]),
        gradient("deckr", p["deck_r0"], p["deck_r1"]),
        gradient("edge", p["edge0"], p["edge1"], x2="1", y2="0"),
        gradient("bodyl", p["body_l0"], p["body_l1"]),
        gradient("bodyr", p["body_r0"], p["body_r1"]),
        (
            '<radialGradient id="gridfade" cx="50%" cy="50%" r="50%"><stop offset="0" stop-color="#fff" stop-opacity=".9"/>'
            '<stop offset=".75" stop-color="#fff" stop-opacity=".25"/><stop offset="1" stop-color="#fff" stop-opacity="0"/></radialGradient>'
        ),
        (
            '<filter id="glow" x="-60%" y="-60%" width="220%" height="220%"><feGaussianBlur stdDeviation="2.6" result="b"/>'
            '<feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter>'
        ),
        '<filter id="bloom" x="-60%" y="-60%" width="220%" height="220%"><feGaussianBlur stdDeviation="6"/></filter>',
        '<filter id="blur" x="-30%" y="-30%" width="160%" height="160%"><feGaussianBlur stdDeviation="14"/></filter>',
        '<filter id="shadow" x="-30%" y="-30%" width="160%" height="160%"><feGaussianBlur stdDeviation="5"/></filter>',
    ]
    for key in ("ok", "warn", "bad", "cut", "wire"):
        defs.append(
            f'<radialGradient id="pool-{key}" cx="50%" cy="50%" r="50%"><stop offset="0" stop-color="{p[key]}" stop-opacity=".55"/>'
            f'<stop offset="1" stop-color="{p[key]}" stop-opacity="0"/></radialGradient>'
        )
        defs.append(gradient(f"beam-{key}", p[key], p[key], o0="0", o1=".55"))
    add("<defs>" + "".join(defs) + "</defs>")
    add(
        "<style>"
        ".float{animation:float 8s ease-in-out infinite}@keyframes float{50%{transform:translateY(-6px)}}"
        ".tw{animation:tw 4s ease-in-out infinite}@keyframes tw{50%{opacity:.1}}"
        ".led{animation:led 2.2s steps(2,jump-none) infinite}@keyframes led{50%{opacity:.2}}"
        ".pulse{transform-box:fill-box;transform-origin:center;animation:pulse 2.4s ease-out infinite}"
        "@keyframes pulse{0%{transform:scale(.35);opacity:.95}100%{transform:scale(1.5);opacity:0}}"
        ".scan{stroke-dasharray:120 2200;animation:scan 7s linear infinite}@keyframes scan{to{stroke-dashoffset:-2320}}"
        ".spin{stroke-dasharray:4 9;animation:spin 9s linear infinite}@keyframes spin{to{stroke-dashoffset:-260}}"
        ".flicker{animation:flicker 1.6s steps(4,jump-none) infinite}@keyframes flicker{30%{opacity:.35}55%{opacity:1}80%{opacity:.6}}"
        ".beam{animation:beam 2.4s ease-in-out infinite}@keyframes beam{50%{opacity:.35}}"
        ".spark{animation:spark .7s steps(2,jump-none) infinite}@keyframes spark{50%{opacity:0}}"
        ".smoke{transform-box:fill-box;transform-origin:center;animation:smoke 3.6s ease-out infinite}"
        "@keyframes smoke{0%{transform:translate(0,0) scale(.4);opacity:.55}100%{transform:translate(10px,-70px) scale(2.2);opacity:0}}"
        "@media (prefers-reduced-motion:reduce){*{animation:none!important}}"
        "</style>"
    )

    # sky
    add(f'<rect width="{W}" height="{H}" rx="20" fill="url(#bg)"/>')
    add(
        '<ellipse cx="860" cy="170" rx="420" ry="220" fill="url(#nebula)" opacity=".55"/>'
    )
    add(
        '<ellipse cx="220" cy="560" rx="360" ry="170" fill="url(#nebula)" opacity=".35"/>'
    )
    for _ in range(110):
        x, y, r = next(rand) * W, next(rand) * H, 0.3 + next(rand) ** 3 * 1.6
        add(
            f'<circle class="tw" cx="{x:.0f}" cy="{y:.0f}" r="{r:.2f}" fill="{p["star"]}" '
            f'opacity="{0.35 + next(rand) * 0.6:.2f}" style="animation-delay:-{next(rand) * 4:.1f}s"/>'
        )

    add('<g class="float">')
    # deck
    x0, y0, x1, y1, depth = -2.0, -2.0, 15.2, 12.5, 0.75
    top = [iso(x0, y0), iso(x1, y0), iso(x1, y1), iso(x0, y1)]
    cx, cy = iso((x0 + x1) / 2, (y0 + y1) / 2)
    add(
        f'<ellipse cx="{cx:.0f}" cy="{cy + 120:.0f}" rx="520" ry="90" fill="{p["edge0"]}" opacity=".16" filter="url(#blur)"/>'
    )
    add(
        poly(
            [iso(x0, y1), iso(x1, y1), iso(x1, y1, -depth), iso(x0, y1, -depth)],
            "url(#deckl)",
        )
    )
    add(
        poly(
            [iso(x1, y0), iso(x1, y1), iso(x1, y1, -depth), iso(x1, y0, -depth)],
            "url(#deckr)",
        )
    )
    add(poly(top, "url(#decktop)"))
    add(f'<mask id="gm"><polygon points="{pts(top)}" fill="url(#gridfade)"/></mask>')
    lines = []
    step = 1.1
    v = x0 + step
    while v < x1 - 1e-6:
        (ax, ay), (bx, by) = iso(v, y0), iso(v, y1)
        lines.append(f"M{ax:.1f} {ay:.1f}L{bx:.1f} {by:.1f}")
        v += step
    v = y0 + step
    while v < y1 - 1e-6:
        (ax, ay), (bx, by) = iso(x0, v), iso(x1, v)
        lines.append(f"M{ax:.1f} {ay:.1f}L{bx:.1f} {by:.1f}")
        v += step
    add(
        f'<path d="{"".join(lines)}" stroke="{p["grid"]}" stroke-width=".6" opacity=".22" mask="url(#gm)"/>'
    )
    rim = f"M{pts([iso(x0, y1), iso(x1, y1), iso(x1, y0)]).replace(' ', ' L')}"
    add(f'<path d="{rim}" fill="none" stroke="url(#edge)" stroke-width="2"/>')
    add(
        f'<path d="{rim}" fill="none" stroke="url(#edge)" stroke-width="6" opacity=".35" filter="url(#bloom)"/>'
    )
    add(
        f'<path class="scan" d="{rim}" fill="none" stroke="#ffffff" stroke-width="2.4" stroke-linecap="round" filter="url(#glow)"/>'
    )
    back = f"M{pts([iso(x0, y1), iso(x0, y0), iso(x1, y0)]).replace(' ', ' L')}"
    add(
        f'<path d="{back}" fill="none" stroke="{p["edge0"]}" stroke-width="1" opacity=".35"/>'
    )

    # ground labels, light pools and shadows
    order = sorted(config["services"], key=lambda s: sum(grid_pos(s)))
    for s in order:
        x, y = grid_pos(s)
        st = states[s["id"]]
        key = SEVERITY[st]
        core = s["id"] == "kubernetes"
        half = HALF * (1.4 if core else 1)
        height = 2.9 if core else 2.3 - 0.35 * s["tier"]
        sh = [
            iso(x - half, y - half),
            iso(x + half, y - half),
            iso(x + half, y + half + height * 0.55),
            iso(x - half, y + half + height * 0.55),
        ]
        add(poly(sh, p["shadow"], f' opacity="{p["shadow_op"]}" filter="url(#shadow)"'))
        px, py = iso(x, y)
        add(
            f'<ellipse cx="{px:.1f}" cy="{py:.1f}" rx="{half * 3.2 * SCALE:.1f}" ry="{half * 1.85 * SCALE:.1f}" '
            f'fill="url(#pool-{"wire" if st == HEALTHY else key})" opacity="{0.55 if st == HEALTHY else 1}"/>'
        )
        if st != HEALTHY:
            add(
                f'<ellipse class="pulse" cx="{px:.1f}" cy="{py:.1f}" rx="{half * 2.6 * SCALE:.1f}" ry="{half * 1.5 * SCALE:.1f}" '
                f'fill="none" stroke="{p[key]}" stroke-width="2"/>'
            )
        if core:
            add(
                f'<ellipse class="spin" cx="{px:.1f}" cy="{py:.1f}" rx="{half * 2.4 * SCALE:.1f}" ry="{half * 1.39 * SCALE:.1f}" '
                f'fill="none" stroke="{p["wire"]}" stroke-width="1.6" opacity=".9"/>'
            )
        add(
            ground_text(
                x - half,
                y + half + 0.6,
                s["name"],
                12.5,
                p["text"] if st == HEALTHY else p[key],
                p["halo"],
                spacing=0.3,
            )
        )
        if st == DEGRADED:
            add(
                ground_text(
                    x - half,
                    y + half + 1.15,
                    "slowed",
                    9.5,
                    p[key],
                    p["halo"],
                    weight="500",
                    family=MONO,
                )
            )

    # cables and packets
    for s in config["services"]:
        for dep in s["deps"]:
            d = wire_path(grid_pos(s), grid_pos(services[dep]))
            dst = states[dep]
            key = "wire" if dst == HEALTHY else SEVERITY[dst]
            color = p[key]
            add(
                f'<path d="{d}" fill="none" stroke="{p["shadow"]}" stroke-width="6" opacity="{p["shadow_op"]}" stroke-linejoin="round"/>'
            )
            add(
                f'<path d="{d}" fill="none" stroke="{color}" stroke-width="5" opacity=".18" stroke-linejoin="round"/>'
            )
            if dst in ("down", "cut"):
                add(
                    f'<path d="{d}" fill="none" stroke="{color}" stroke-width="1.6" stroke-dasharray="3 6" stroke-linejoin="round"/>'
                )
            else:
                add(
                    f'<path d="{d}" fill="none" stroke="{color}" stroke-width="1.4" opacity=".85" stroke-linejoin="round"/>'
                )
                dur = 3.0 if dst == HEALTHY else 9.0
                for k in range(3):
                    base = k * dur / 3
                    for j, (r, o) in enumerate(
                        ((3.2, 1), (2.4, 0.55), (1.7, 0.3), (1.2, 0.15))
                    ):
                        add(
                            f'<circle r="{r}" fill="{"#ffffff" if j == 0 else color}" opacity="{o}" '
                            f"{'filter=' + chr(34) + 'url(#glow)' + chr(34) if j == 0 else ''}>"
                            f'<animateMotion dur="{dur}s" begin="-{max(base - j * 0.05 * dur / 3, 0.001):.3f}s" '
                            f'repeatCount="indefinite" path="{d}"/></circle>'
                        )
            if dst == "cut":
                a, b = grid_pos(s), grid_pos(services[dep])
                mx, my = iso((a[0] + b[0]) / 2, b[1], 0.04)
                rays = "".join(
                    f'<line x1="{mx:.1f}" y1="{my:.1f}" x2="{mx + dx:.1f}" y2="{my + dy:.1f}"/>'
                    for dx, dy in (
                        (10, -10),
                        (-10, -10),
                        (13, 1),
                        (-13, 1),
                        (0, -15),
                        (6, 7),
                        (-6, 7),
                    )
                )
                add(
                    f'<circle cx="{mx:.1f}" cy="{my:.1f}" r="10" fill="{p["warn"]}" opacity=".35" filter="url(#bloom)"/>'
                )
                add(
                    f'<g class="spark" stroke="{p["warn"]}" stroke-width="1.8" stroke-linecap="round">{rays}</g>'
                )

    # towers
    for s in order:
        x, y = grid_pos(s)
        st = states[s["id"]]
        key = SEVERITY[st]
        tone = p[key]
        core = s["id"] == "kubernetes"
        half = HALF * (1.4 if core else 1)
        height = 2.9 if core else 2.3 - 0.35 * s["tier"]
        down = st == "down"
        cut = st == "cut"
        # plinth
        l, r, t = faces(x, y, half + 0.12, 0, 0.16)
        add(
            poly(l, "url(#bodyl)")
            + poly(r, "url(#bodyr)")
            + poly(t, p["body_top"], ' opacity=".9"')
        )
        # body
        l, r, t = faces(x, y, half, 0.16, height)
        group = ' class="flicker"' if cut else ""
        add(f"<g{group}>")
        add(poly(l, "url(#bodyl)") + poly(r, "url(#bodyr)") + poly(t, p["body_top"]))
        if down:
            add(
                poly(l, p["bad"], ' opacity=".28"')
                + poly(r, p["bad"], ' opacity=".22"')
                + poly(t, p["bad"], ' opacity=".35"')
            )
        if cut:
            add(
                poly(l, p["cut"], ' opacity=".25"') + poly(r, p["cut"], ' opacity=".2"')
            )
        # rack slots with leds on both faces
        units = int((height - 0.5) / 0.3)
        for u in range(units):
            z = 0.38 + u * 0.3
            for face in ("l", "r"):
                if face == "l":
                    slot = [
                        iso(x - half + 0.12, y + half, z),
                        iso(x + half - 0.12, y + half, z),
                        iso(x + half - 0.12, y + half, z + 0.17),
                        iso(x - half + 0.12, y + half, z + 0.17),
                    ]
                    leds = [
                        iso(x + half - 0.24 - i * 0.16, y + half, z + 0.085)
                        for i in range(3)
                    ]
                else:
                    slot = [
                        iso(x + half, y + half - 0.12, z),
                        iso(x + half, y - half + 0.12, z),
                        iso(x + half, y - half + 0.12, z + 0.17),
                        iso(x + half, y + half - 0.12, z + 0.17),
                    ]
                    leds = [
                        iso(x + half, y - half + 0.24 + i * 0.16, z + 0.085)
                        for i in range(3)
                    ]
                add(poly(slot, p["slot"], ' opacity=".85"'))
                if (down or cut) and (u + (face == "r")) % 2:
                    continue
                for i, (lx, ly) in enumerate(leds):
                    if st == HEALTHY:
                        c = p["ok"] if (u + i) % 4 else p["wire"]
                    else:
                        c = tone
                    add(
                        f'<circle class="led" cx="{lx:.1f}" cy="{ly:.1f}" r="1.5" fill="{c}" '
                        f'style="animation-delay:-{next(rand) * 2.2:.2f}s"/>'
                    )
        # glowing corner edge and rim
        ex0, ey0 = iso(x + half, y + half, 0.2)
        ex1, ey1 = iso(x + half, y + half, height)
        edge_color = p["wire"] if st == HEALTHY else tone
        add(
            f'<line x1="{ex0:.1f}" y1="{ey0:.1f}" x2="{ex1:.1f}" y2="{ey1:.1f}" stroke="{edge_color}" stroke-width="1.6" filter="url(#glow)"/>'
        )
        add(
            f'<polygon points="{pts(t)}" fill="none" stroke="{edge_color}" stroke-width="1" opacity=".7"/>'
        )
        add("</g>")
        # cap
        cl, cr, ct = faces(x, y, half * 0.55, height, height + 0.18)
        add(poly(cl, "url(#bodyl)") + poly(cr, "url(#bodyr)") + poly(ct, p["body_top"]))
        bx, by = iso(x, y, height + 0.18)
        if st == HEALTHY:
            add(
                f'<circle cx="{bx:.1f}" cy="{by:.1f}" r="2.6" fill="{p["ok"]}" filter="url(#glow)"/>'
            )
        else:
            add(
                f'<circle class="flicker" cx="{bx:.1f}" cy="{by:.1f}" r="3.4" fill="{tone}" filter="url(#glow)"/>'
            )
        if down:
            hw = half * 0.9
            beam = [
                iso(x - hw, y + hw, height + 0.2),
                iso(x + hw, y + hw, height + 0.2),
                iso(x + hw, y - hw, height + 0.2),
                iso(x + hw * 1.5, y - hw * 1.5, height + 4.2),
                iso(x - hw * 1.5, y + hw * 1.5, height + 4.2),
            ]
            add(
                poly(
                    beam,
                    "url(#beam-bad)",
                    ' class="beam" opacity=".9" transform="scale(1 1)"',
                )
            )
            for k in range(4):
                add(
                    f'<circle class="smoke" cx="{bx:.1f}" cy="{by - 6:.1f}" r="8" fill="{p["cut"]}" '
                    f'style="animation-delay:-{k * 0.9:.2f}s"/>'
                )
    add("</g>")

    # heads-up display
    sev = p[{"ok": "ok", "warn": "warn", "bad": "bad"}[severity]]
    add(
        f'<text x="48" y="58" font-family="{MONO}" font-size="12" letter-spacing="2.4" fill="{p["muted"]}">'
        f"{escape(config['cluster'].upper())}  ·  LIVE</text>"
    )
    if severity != "ok":
        add(f'<circle class="pulse" cx="58" cy="92" r="9" fill="{sev}"/>')
    add(f'<circle cx="58" cy="92" r="6" fill="{sev}" filter="url(#glow)"/>')
    add(
        f'<text x="76" y="102" font-family="{SANS}" font-size="30" font-weight="700" letter-spacing="-.5" fill="{p["text"]}">{escape(label)}</text>'
    )
    hint = (
        "Pick a service below and break it"
        if severity == "ok"
        else "Self-healing in progress"
    )
    add(
        f'<text x="48" y="132" font-family="{SANS}" font-size="14" fill="{p["muted"]}">{escape(hint)}</text>'
    )
    for i, (sid, fault) in enumerate(faults.items()):
        st = states[sid]
        tone = p[SEVERITY[st]]
        y = 170 + i * 30
        add(status_icon(56, y - 4, st, tone, 5))
        add(
            f'<text x="72" y="{y}" font-family="{SANS}" font-size="14" font-weight="600" fill="{p["text"]}">'
            f"{escape(services[sid]['name'])}</text>"
        )
        add(
            f'<text x="{80 + len(services[sid]["name"]) * 8.2:.0f}" y="{y}" font-family="{MONO}" font-size="12" '
            f'fill="{tone}">{escape(config["faults"][fault]["detail"])}</text>'
        )

    affected = sum(1 for st in states.values() if st != HEALTHY)
    stats = [
        (
            "Uptime, 30 days",
            f"{m['availability']:.2f}%",
            "ok" if m["availability"] >= config["slo"] else "bad",
        ),
        (
            "Services affected",
            f"{affected}/{len(services)}",
            "ok" if not affected else "bad",
        ),
        ("Times broken", str(m["incidents"]), None),
        (
            "Average fix",
            fmt_duration(m["mttr"]) if m["mttr"] is not None else "—",
            None,
        ),
    ]
    sx = W - 48
    for i, (name, value, tone) in enumerate(stats):
        col, row = i % 2, i // 2
        x = sx - (1 - col) * 170
        y = 70 + row * 66
        add(
            f'<text x="{x}" y="{y}" text-anchor="end" font-family="{SANS}" font-size="12" fill="{p["muted"]}">{escape(name)}</text>'
        )
        add(
            f'<text x="{x}" y="{y + 30}" text-anchor="end" font-family="{SANS}" font-size="28" font-weight="700" '
            f'letter-spacing="-.5" fill="{p[tone] if tone else p["text"]}">{escape(value)}</text>'
        )

    legend = [
        (HEALTHY, "running", p["ok"]),
        ("slow", "slow", p["warn"]),
        ("down", "down", p["bad"]),
        ("cut", "cut off", p["cut"]),
    ]
    lx = 48
    for st, name, color in legend:
        add(status_icon(lx + 5, H - 44, st, color, 4.5))
        add(
            f'<text x="{lx + 16}" y="{H - 40}" font-family="{SANS}" font-size="12" fill="{p["muted"]}">{name}</text>'
        )
        lx += 30 + len(name) * 7
    last = state["incidents"][-1] if state["incidents"] else None
    caption = (
        f"Last break: {last['fault']} {last['target']} by @{last['agent']}"
        if last
        else "Nobody has broken it yet"
    )
    add(
        f'<text x="{W - 48}" y="{H - 40}" text-anchor="end" font-family="{MONO}" font-size="11.5" fill="{p["muted"]}">{escape(caption)}</text>'
    )
    add("</svg>")
    return "\n".join(out) + "\n"


# --------------------------------------------------------------------------- render: readme

STATE_LABEL = {
    HEALTHY: "running",
    DEGRADED: "degraded",
    "slow": "slow",
    "down": "**down**",
    "cut": "**partitioned**",
}


def render_readme_block(config: dict, state: dict, current: datetime) -> str:
    faults = current_faults(state)
    states = effective_states(config, faults)
    lines = [README_START, ""]

    lines += ["| Service | Role | Status | Break it |", "| --- | --- | --- | --- |"]
    for s in config["services"]:
        links = " · ".join(
            f"[{f}]({issue_url(config, f, s['id'])})" for f in config["faults"]
        )
        lines.append(
            f"| [`{s['id']}`](https://github.com/{s['repo']}) | {s['role']} | {STATE_LABEL[states[s['id']]]} | {links} |"
        )
    lines.append("")

    lines += ["#### Recent breaks", ""]
    recent = list(reversed(state["incidents"]))[:8]
    if recent:
        lines += [
            "| # | When (UTC) | What broke | Broken by | Services hit | Fixed in |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
        for i in recent:
            started = parse_time(i["started"])
            if i.get("healed"):
                ttr = fmt_duration(
                    (parse_time(i["healed"]) - started).total_seconds() / 60
                )
            else:
                ttr = "**still broken**"
            lines.append(
                f"| [{i['id']}](https://github.com/{config['repo']}/issues/{i['issue']}) | "
                f"{started.strftime('%Y-%m-%d %H:%M')} | {i['fault']} `{i['target']}` | @{i['agent']} | "
                f"{len(i['blast'])} service{'s' if len(i['blast']) != 1 else ''} | {ttr} |"
            )
    else:
        lines.append("_Nobody has broken it yet._")
    lines.append("")

    lines.append(README_END)
    return "\n".join(lines)


def render(config: dict, state: dict, current: datetime) -> None:
    for theme, path in SVG_PATHS.items():
        path.write_text(render_svg(config, state, current, theme), encoding="utf-8")
    if README_PATH.exists():
        text = README_PATH.read_text(encoding="utf-8")
        start, end = text.find(README_START), text.find(README_END)
        if start != -1 and end > start:
            text = (
                text[:start]
                + render_readme_block(config, state, current)
                + text[end + len(README_END) :]
            )
            README_PATH.write_text(text, encoding="utf-8")


# --------------------------------------------------------------------------- cli


def write_outputs(values: dict[str, str]) -> None:
    target = os.environ.get("GITHUB_OUTPUT")
    if not target:
        for key, value in values.items():
            print(f"{key}={value}")
        return
    with open(target, "a", encoding="utf-8") as fh:
        for key, value in values.items():
            fh.write(f"{key}={value.replace(chr(10), ' ')}\n")


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[1] not in ("inject", "reconcile", "render"):
        print("usage: engine.py inject|reconcile|render", file=sys.stderr)
        return 2
    command = argv[1]
    config = load_config()
    current = now()
    state = load_state(current)

    healed = reconcile(config, state, current)

    if command == "inject":
        result, comment, message = inject(
            config,
            state,
            current,
            os.environ.get("ISSUE_TITLE", ""),
            os.environ.get("ISSUE_AUTHOR", ""),
            os.environ.get("ISSUE_NUMBER", ""),
        )
        comment_path = Path(os.environ.get("CHAOS_COMMENT", ROOT / ".comment.md"))
        comment_path.write_text(comment + "\n", encoding="utf-8")
        write_outputs({"result": result, "message": message})
    elif command == "reconcile":
        names = ", ".join(i["target"] for i in healed)
        write_outputs(
            {
                "healed": str(len(healed)),
                "message": f"chaos: reconciler healed {names}" if healed else "",
            }
        )

    save_state(state)
    render(config, state, current)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
