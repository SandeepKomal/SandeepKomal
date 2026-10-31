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
        "sky0": "#05070f",
        "sky1": "#0b1430",
        "star": "#cfe3ff",
        "text": "#f2f6ff",
        "muted": "#8a9bbd",
        "slab_top": "#111a33",
        "slab_l": "#0a1124",
        "slab_r": "#070c1a",
        "slab_edge": "#2b5cff",
        "grid": "#1d2b55",
        "tower_top": "#2a3a66",
        "tower_l": "#18223f",
        "tower_r": "#10172d",
        "ok": "#3ee6b0",
        "warn": "#ffb020",
        "bad": "#ff4d5e",
        "wire": "#38bdf8",
        "panel": "#0b1226",
        "smoke": "#8b93a7",
    },
    "light": {
        "sky0": "#f4f8ff",
        "sky1": "#dbe7ff",
        "star": "#9fb6e0",
        "text": "#0d1530",
        "muted": "#51607f",
        "slab_top": "#ffffff",
        "slab_l": "#dfe7f6",
        "slab_r": "#c9d5ec",
        "slab_edge": "#4f7cff",
        "grid": "#e3eaf7",
        "tower_top": "#eef3fc",
        "tower_l": "#c4d1ea",
        "tower_r": "#a9b9d9",
        "ok": "#0f9f74",
        "warn": "#c77700",
        "bad": "#e0243a",
        "wire": "#1677ff",
        "panel": "#ffffff",
        "smoke": "#7b8496",
    },
}
SEVERITY = {
    HEALTHY: "ok",
    DEGRADED: "warn",
    "slow": "warn",
    "down": "bad",
    "cut": "bad",
}
SANS = "-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif"
MONO = "ui-monospace,SFMono-Regular,'SF Mono',Menlo,Consolas,monospace"
W, H = 1200, 760
SCALE = 31
ORIGIN = (600, 236)
TOWER_HALF = 0.62


def iso(x: float, y: float, z: float = 0.0) -> tuple[float, float]:
    return ORIGIN[0] + (x - y) * 0.866 * SCALE, ORIGIN[1] + ((x + y) * 0.5 - z) * SCALE


def poly(points, fill, extra="") -> str:
    pts = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
    return f'<polygon points="{pts}" fill="{fill}"{extra}/>'


def grid_pos(service: dict) -> tuple[float, float]:
    return service["tier"] * 4.2, service["row"] * 3.4


def prism(x, y, half, z0, z1, top, left, right, extra="") -> str:
    a, b, c, d = (
        (x - half, y - half),
        (x + half, y - half),
        (x + half, y + half),
        (x - half, y + half),
    )
    return (
        poly([iso(*d, z0), iso(*c, z0), iso(*c, z1), iso(*d, z1)], left, extra)
        + poly([iso(*b, z0), iso(*c, z0), iso(*c, z1), iso(*b, z1)], right, extra)
        + poly([iso(*a, z1), iso(*b, z1), iso(*c, z1), iso(*d, z1)], top, extra)
    )


def ground_ring(x, y, radius, color, cls) -> str:
    cx, cy = iso(x, y)
    rx, ry = radius * 1.2247 * SCALE, radius * 0.7071 * SCALE
    return f'<ellipse class="{cls}" cx="{cx:.1f}" cy="{cy:.1f}" rx="{rx:.1f}" ry="{ry:.1f}" fill="none" stroke="{color}" stroke-width="2"/>'


def wire_path(a, b) -> str:
    (ax, ay), (bx, by) = a, b
    mid = (ax + bx) / 2
    pts = [iso(ax, ay, 0.02), iso(mid, ay, 0.02), iso(mid, by, 0.02), iso(bx, by, 0.02)]
    return "M" + " L".join(f"{x:.1f} {y:.1f}" for x, y in pts)


def status_icon(cx: float, cy: float, state: str, color: str) -> str:
    """Each state has its own shape so color is never the only signal."""
    if state == HEALTHY:
        return f'<circle cx="{cx}" cy="{cy}" r="4.5" fill="{color}"/>'
    if state in (DEGRADED, "slow"):
        return f'<path d="M{cx} {cy - 5.5}L{cx + 5.5} {cy + 4.5}H{cx - 5.5}Z" fill="{color}"/>'
    if state == "down":
        return (
            f'<rect x="{cx - 5}" y="{cy - 5}" width="10" height="10" rx="2" fill="{color}"/>'
            f'<path d="M{cx - 2.3} {cy - 2.3}l4.6 4.6M{cx + 2.3} {cy - 2.3}l-4.6 4.6" stroke="#fff" stroke-width="1.5" stroke-linecap="round"/>'
        )
    return f'<path d="M{cx} {cy - 6}L{cx + 6} {cy}L{cx} {cy + 6}L{cx - 6} {cy}Z" fill="{color}"/>'


def rng(seed: int):
    value = seed
    while True:
        value = (value * 1103515245 + 12345) & 0x7FFFFFFF
        yield value / 0x7FFFFFFF


def render_svg(config: dict, state: dict, current: datetime, theme: str) -> str:
    p = PALETTES[theme]
    faults = current_faults(state)
    states = effective_states(config, faults)
    services = {s["id"]: s for s in config["services"]}
    label, severity = overall_status(config, states)
    m = metrics(config, state, current)
    rand = rng(7)
    out = []
    add = out.append

    add(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" '
        f'role="img" aria-label="{escape(config["cluster"])}: {escape(label)}">'
    )
    add(
        "<defs>"
        f'<radialGradient id="sky" cx="50%" cy="35%" r="75%"><stop offset="0" stop-color="{p["sky1"]}"/>'
        f'<stop offset="1" stop-color="{p["sky0"]}"/></radialGradient>'
        '<filter id="glow" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="3" result="b"/>'
        '<feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter>'
        '<filter id="soft" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="10"/></filter>'
        "</defs>"
    )
    add(
        "<style>"
        ".float{animation:float 7s ease-in-out infinite}"
        "@keyframes float{50%{transform:translateY(-7px)}}"
        ".tw{animation:tw 3s ease-in-out infinite}@keyframes tw{50%{opacity:.15}}"
        ".led{animation:led 1.6s steps(2,jump-none) infinite}@keyframes led{50%{opacity:.25}}"
        ".ring{transform-box:fill-box;transform-origin:center;animation:ring 2.2s ease-out infinite}"
        "@keyframes ring{0%{transform:scale(.4);opacity:.9}100%{transform:scale(1.6);opacity:0}}"
        ".spin{stroke-dasharray:6 10;animation:spin 6s linear infinite}@keyframes spin{to{stroke-dashoffset:-160}}"
        ".flicker{animation:flicker 1.3s steps(3,jump-none) infinite}@keyframes flicker{40%{opacity:.35}70%{opacity:.9}}"
        ".spark{animation:spark .9s steps(2,jump-none) infinite}@keyframes spark{50%{opacity:0}}"
        ".smoke{animation:smoke 3.2s ease-out infinite}@keyframes smoke{0%{transform:translateY(0) scale(.5);opacity:.6}"
        "100%{transform:translateY(-60px) scale(1.8);opacity:0}}"
        ".smoke{transform-box:fill-box;transform-origin:center}"
        "@media (prefers-reduced-motion:reduce){*{animation:none!important}}"
        "</style>"
    )
    add(f'<rect width="{W}" height="{H}" rx="18" fill="url(#sky)"/>')
    for i in range(90):
        x, y, r = next(rand) * W, next(rand) * H * 0.9, 0.4 + next(rand) * 1.2
        delay = next(rand) * 3
        add(
            f'<circle class="tw" cx="{x:.0f}" cy="{y:.0f}" r="{r:.1f}" fill="{p["star"]}" '
            f'style="animation-delay:-{delay:.1f}s"/>'
        )

    add('<g class="float">')
    # floating island: slab, edge light, and grid
    x0, y0, x1, y1, depth = -1.8, -1.8, 14.4, 12.0, 0.6
    corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    cx, cy = iso((x0 + x1) / 2, (y0 + y1) / 2, -depth)
    add(
        f'<ellipse cx="{cx:.0f}" cy="{cy + 70:.0f}" rx="470" ry="80" fill="{p["slab_edge"]}" opacity=".18" filter="url(#soft)"/>'
    )
    add(
        poly(
            [iso(x0, y1, 0), iso(x1, y1, 0), iso(x1, y1, -depth), iso(x0, y1, -depth)],
            p["slab_l"],
        )
    )
    add(
        poly(
            [iso(x1, y0, 0), iso(x1, y1, 0), iso(x1, y1, -depth), iso(x1, y0, -depth)],
            p["slab_r"],
        )
    )
    add(poly([iso(*c) for c in corners], p["slab_top"]))
    gx = x0
    while gx <= x1 + 1e-6:
        (ax, ay), (bx, by) = iso(gx, y0), iso(gx, y1)
        add(
            f'<line x1="{ax:.1f}" y1="{ay:.1f}" x2="{bx:.1f}" y2="{by:.1f}" stroke="{p["grid"]}"/>'
        )
        gx += 1.2
    gy = y0
    while gy <= y1 + 1e-6:
        (ax, ay), (bx, by) = iso(x0, gy), iso(x1, gy)
        add(
            f'<line x1="{ax:.1f}" y1="{ay:.1f}" x2="{bx:.1f}" y2="{by:.1f}" stroke="{p["grid"]}"/>'
        )
        gy += 1.2
    edge = " ".join(
        f"{x:.1f},{y:.1f}" for x, y in (iso(x0, y1), iso(x1, y1), iso(x1, y0))
    )
    add(
        f'<polyline points="{edge}" fill="none" stroke="{p["slab_edge"]}" stroke-width="2" filter="url(#glow)"/>'
    )

    # wires and packets, colored by the dependency's state
    for s in config["services"]:
        for dep in s["deps"]:
            d = wire_path(grid_pos(s), grid_pos(services[dep]))
            dst = states[dep]
            color = (
                p["wire"]
                if dst == HEALTHY
                else p["warn"]
                if dst in ("slow", DEGRADED)
                else p["bad"]
            )
            add(
                f'<path d="{d}" fill="none" stroke="{color}" stroke-width="5" opacity=".18"/>'
            )
            if dst in ("down", "cut"):
                add(
                    f'<path d="{d}" fill="none" stroke="{color}" stroke-width="1.6" stroke-dasharray="4 6"/>'
                )
            else:
                add(
                    f'<path d="{d}" fill="none" stroke="{color}" stroke-width="1.6" opacity=".9"/>'
                )
                dur = 2.4 if dst == HEALTHY else 7
                for k in range(3):
                    add(
                        f'<circle r="3.2" fill="{color}" filter="url(#glow)"><animateMotion dur="{dur}s" '
                        f'begin="-{k * dur / 3:.2f}s" repeatCount="indefinite" path="{d}"/></circle>'
                    )
            if dst == "cut":
                a, b = grid_pos(s), grid_pos(services[dep])
                mx, my = iso((a[0] + b[0]) / 2, b[1], 0.02)
                rays = "".join(
                    f'<line x1="{mx:.1f}" y1="{my:.1f}" x2="{mx + dx:.1f}" y2="{my + dy:.1f}"/>'
                    for dx, dy in ((9, -9), (-9, -9), (11, 2), (-11, 2), (0, -13))
                )
                add(
                    f'<g class="spark" stroke="{p["warn"]}" stroke-width="2" stroke-linecap="round" filter="url(#glow)">{rays}</g>'
                )

    # towers, back to front; labels go on a layer above all of them
    labels = []
    for s in sorted(config["services"], key=lambda s: sum(grid_pos(s))):
        x, y = grid_pos(s)
        st = states[s["id"]]
        tone = p[SEVERITY[st]]
        core = s["id"] == "kubernetes"
        half = TOWER_HALF * (1.35 if core else 1)
        height = 2.6 if core else 2.2 - 0.3 * s["tier"]
        top, left, right = p["tower_top"], p["tower_l"], p["tower_r"]
        if st == "down":
            top, left, right = "#5a1a24", "#3a0f17", "#2a0a10"
        elif st == "cut":
            top, left, right = "#3a3f4f", "#262a36", "#1c1f29"

        if st != HEALTHY:
            add(ground_ring(x, y, half * 1.9, tone, "ring"))
        if core:
            add(
                ground_ring(x, y, half * 2.1, p["wire"], "spin").replace(
                    'class="spin"', 'class="spin" opacity=".8"'
                )
            )
        add(prism(x, y, half, 0, height, top, left, right))
        # server lights on both visible faces
        rows = int(height / 0.32)
        for r in range(rows):
            z = 0.28 + r * 0.32
            if z + 0.1 > height - 0.1:
                break
            for face in ("l", "r"):
                led = (
                    tone
                    if st != HEALTHY
                    else (p["ok"] if (r + (face == "r")) % 3 else p["wire"])
                )
                if st in ("down", "cut") and r % 2:
                    continue
                if face == "l":
                    pts = [
                        iso(x - half + 0.14, y + half, z),
                        iso(x + half - 0.14, y + half, z),
                        iso(x + half - 0.14, y + half, z + 0.09),
                        iso(x - half + 0.14, y + half, z + 0.09),
                    ]
                else:
                    pts = [
                        iso(x + half, y + half - 0.14, z),
                        iso(x + half, y - half + 0.14, z),
                        iso(x + half, y - half + 0.14, z + 0.09),
                        iso(x + half, y + half - 0.14, z + 0.09),
                    ]
                delay = next(rand) * 1.6
                add(
                    poly(
                        pts,
                        led,
                        f' class="led" opacity=".9" style="animation-delay:-{delay:.2f}s"',
                    )
                )
        # top beacon
        tx, ty = iso(x, y, height)
        if st == HEALTHY:
            add(
                f'<circle cx="{tx:.1f}" cy="{ty:.1f}" r="3" fill="{p["ok"]}" filter="url(#glow)"/>'
            )
        else:
            add(
                f'<circle class="flicker" cx="{tx:.1f}" cy="{ty:.1f}" r="4" fill="{tone}" filter="url(#glow)"/>'
            )
        if st == "down":
            for k in range(3):
                add(
                    f'<circle class="smoke" cx="{tx:.1f}" cy="{ty - 4:.1f}" r="7" fill="{p["smoke"]}" '
                    f'style="animation-delay:-{k * 1.05:.2f}s"/>'
                )

        lx, ly = iso(x, y, height + 0.9)
        if st == HEALTHY:
            detail = "running"
        elif st == DEGRADED:
            bad = [d for d in s["deps"] if states[d] != HEALTHY]
            detail = f"slowed by {services[bad[0]]['name']}" if bad else "degraded"
        else:
            detail = config["faults"][faults[s["id"]]]["detail"]
        add(
            f'<line x1="{tx:.1f}" y1="{ty:.1f}" x2="{lx:.1f}" y2="{ly:.1f}" stroke="{tone}" stroke-width="1" opacity=".6"/>'
        )
        labels.append((lx, ly, s["name"], detail, st, tone))

    for lx, ly, name, detail, st, tone in labels:
        width = max(len(name) * 8.2, len(detail) * 6.8) + 34
        bx, by = lx - width / 2, ly - 44
        add(
            f'<rect x="{bx:.1f}" y="{by:.1f}" width="{width:.1f}" height="40" rx="9" fill="{p["panel"]}" '
            f'fill-opacity=".86" stroke="{tone}" stroke-opacity="{0.35 if st == HEALTHY else 0.9}"/>'
        )
        add(status_icon(round(bx + 14, 1), round(by + 14, 1), st, tone))
        add(
            f'<text x="{bx + 25:.1f}" y="{by + 18.5:.1f}" font-family="{SANS}" font-size="13" font-weight="600" '
            f'fill="{p["text"]}">{escape(name)}</text>'
        )
        add(
            f'<text x="{bx + 25:.1f}" y="{by + 32:.1f}" font-family="{MONO}" font-size="10.5" '
            f'fill="{p["muted"] if st == HEALTHY else tone}">{escape(detail)}</text>'
        )
    add("</g>")

    # heads-up display
    sev = p[severity]
    add(
        f'<text x="44" y="56" font-family="{MONO}" font-size="13" letter-spacing="1" fill="{p["muted"]}">'
        f"{escape(config['cluster'])} · {len(services)} services · live</text>"
    )
    add(
        f'<circle class="flicker" cx="54" cy="88" r="7" fill="{sev}" filter="url(#glow)"/>'
        if severity != "ok"
        else f'<circle cx="54" cy="88" r="7" fill="{sev}" filter="url(#glow)"/>'
    )
    add(
        f'<text x="72" y="97" font-family="{SANS}" font-size="28" font-weight="700" fill="{p["text"]}">{escape(label)}</text>'
    )
    hint = (
        "Break it: open an issue from the table below"
        if severity == "ok"
        else "Self-healing in progress"
    )
    add(
        f'<text x="44" y="126" font-family="{SANS}" font-size="14" fill="{p["muted"]}">{escape(hint)}</text>'
    )

    down_now = sum(1 for st in states.values() if st != HEALTHY)
    stats = [
        (
            "Uptime · 30 days",
            f"{m['availability']:.2f}%",
            "ok" if m["availability"] >= config["slo"] else "bad",
        ),
        (
            "Services affected now",
            f"{down_now} of {len(services)}",
            "ok" if not down_now else "bad",
        ),
        ("Times broken", str(m["incidents"]), None),
        (
            "Average fix time",
            fmt_duration(m["mttr"]) if m["mttr"] is not None else "none yet",
            None,
        ),
    ]
    px, py, pw, ph = 44, H - 196, 250, 160
    add(
        f'<rect x="{px}" y="{py}" width="{pw}" height="{ph}" rx="14" fill="{p["panel"]}" opacity=".78" '
        f'stroke="{p["slab_edge"]}" stroke-opacity=".35"/>'
    )
    for i, (name, value, tone) in enumerate(stats):
        yy = py + 30 + i * 36
        add(
            f'<text x="{px + 18}" y="{yy}" font-family="{SANS}" font-size="12.5" fill="{p["muted"]}">{escape(name)}</text>'
        )
        add(
            f'<text x="{px + pw - 18}" y="{yy}" text-anchor="end" font-family="{SANS}" font-size="17" font-weight="700" '
            f'fill="{p[tone] if tone else p["text"]}">{escape(value)}</text>'
        )

    last = state["incidents"][-1] if state["incidents"] else None
    caption = (
        f"Last break: {last['fault']} {last['target']} by @{last['agent']}"
        if last
        else "Nobody has broken it yet"
    )
    add(
        f'<text x="{W - 44}" y="{H - 40}" text-anchor="end" font-family="{MONO}" font-size="12" fill="{p["muted"]}">{escape(caption)}</text>'
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
