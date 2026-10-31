#!/usr/bin/env python3
"""Chaos engine for the profile cluster.

The profile README shows a small "production cluster" made of real projects.
Visitors open an issue titled ``chaos: <fault> <service>``; a workflow runs
``engine.py inject`` which records the fault and re-renders the cluster. A
scheduled ``engine.py reconcile`` heals faults once their recovery time has
passed. Everything is derived from ``state.json``, so the history (incidents,
time to recover, availability against the SLO) is real.

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

TITLE_RE = re.compile(r"^\s*chaos:\s*([a-z]{1,20})\s+([a-z0-9-]{1,40})\s*$", re.IGNORECASE)
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
        elif any(visit(dep, path + (sid,)) != HEALTHY for dep in services[sid]["deps"] if dep not in path):
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
    window_start = max(parse_time(state["since"]), current - timedelta(days=config["window_days"]))
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
            (parse_time(i["healed"]) - parse_time(i["started"])).total_seconds() / 60 for i in healed
        ) / len(healed)

    return {
        "availability": availability,
        "budget_left": budget_left,
        "mttr": mttr,
        "incidents": len(state["incidents"]),
        "agents": len({i["agent"].lower() for i in state["incidents"]}),
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


def inject(config: dict, state: dict, current: datetime, title: str, author: str, number: str) -> tuple[str, str, str]:
    """Return (result, comment markdown, commit message)."""
    services = {s["id"]: s for s in config["services"]}
    usage = (
        "Use a title like `chaos: kill blast-radius`.\n\n"
        f"Faults: {', '.join(f'`{f}`' for f in config['faults'])}\n"
        f"Services: {', '.join(f'`{s}`' for s in services)}"
    )

    if not LOGIN_RE.match(author or ""):
        return "rejected", "Could not read the issue author, so nothing was injected.", ""
    if not (number or "").isdigit():
        return "rejected", "Could not read the issue number, so nothing was injected.", ""
    issue = int(number)
    if any(i.get("issue") == issue for i in state["incidents"]):
        return "ignored", "", ""

    match = TITLE_RE.match(title or "")
    if not match:
        return "rejected", f"That title isn't a chaos command, so nothing was injected.\n\n{usage}", ""
    fault, target = match.group(1).lower(), match.group(2).lower()
    if fault not in config["faults"] or target not in services:
        return "rejected", f"Unknown fault or service.\n\n{usage}", ""

    active = active_incidents(state)
    if any(i["target"] == target for i in active):
        return "rejected", f"`{target}` is already on fire. Pick another service and try again.\n\n{usage}", ""
    if len(active) >= config["max_active_incidents"]:
        return "rejected", (
            f"{len(active)} incidents are already open, the most the on-call engineer allows at once. "
            "Try again after the reconciler heals one."
        ), ""
    if author.lower() != config["owner"].lower():
        cooldown = timedelta(minutes=config["agent_cooldown_minutes"])
        recent = [
            i for i in state["incidents"]
            if i["agent"].lower() == author.lower() and current - parse_time(i["started"]) < cooldown
        ]
        if recent:
            return "rejected", (
                f"Easy, @{author}. One fault every {config['agent_cooldown_minutes']} minutes per person."
            ), ""

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
        f"Downtime counts against the profile's {config['slo']}% SLO.\n\n"
        f"See the damage: https://github.com/{config['owner']}"
    )
    message = f"chaos: @{author} {fault} {target} (incident #{incident['id']})"
    return "accepted", comment, message


# --------------------------------------------------------------------------- render: svg

PALETTES = {
    "dark": {
        "bg": "#0d1117", "surface": "#161b22", "border": "#30363d", "text": "#e6edf3",
        "muted": "#8b949e", "ok": "#3fb950", "warn": "#d29922", "bad": "#f85149", "accent": "#58a6ff",
    },
    "light": {
        "bg": "#ffffff", "surface": "#f6f8fa", "border": "#d0d7de", "text": "#1f2328",
        "muted": "#656d76", "ok": "#1a7f37", "warn": "#9a6700", "bad": "#cf222e", "accent": "#0969da",
    },
}
SEVERITY = {HEALTHY: "ok", DEGRADED: "warn", "slow": "warn", "down": "bad", "cut": "bad"}
SANS = "-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif"
MONO = "ui-monospace,SFMono-Regular,'SF Mono',Menlo,Consolas,monospace"
W, H = 1200, 640
CARD_W, CARD_H = 228, 68


def card_pos(service: dict) -> tuple[float, float]:
    x = 48 + service["tier"] * ((W - 96 - CARD_W) / 3)
    y = 128 + service["row"] * 102
    return x, y


def status_shape(cx: float, cy: float, state: str, color: str) -> str:
    """Each state has its own shape so color is never the only signal."""
    if state == HEALTHY:
        return f'<circle cx="{cx}" cy="{cy}" r="5" fill="{color}"/>'
    if state in (DEGRADED, "slow"):
        return f'<path d="M{cx} {cy - 6}L{cx + 6} {cy + 5}H{cx - 6}Z" fill="{color}"/>'
    if state == "down":
        return (
            f'<rect x="{cx - 5.5}" y="{cy - 5.5}" width="11" height="11" rx="2" fill="{color}"/>'
            f'<path d="M{cx - 2.5} {cy - 2.5}l5 5M{cx + 2.5} {cy - 2.5}l-5 5" stroke="#fff" stroke-width="1.6" stroke-linecap="round"/>'
        )
    return f'<path d="M{cx} {cy - 6.5}L{cx + 6.5} {cy}L{cx} {cy + 6.5}L{cx - 6.5} {cy}Z" fill="{color}"/>'


def render_svg(config: dict, state: dict, current: datetime, theme: str) -> str:
    p = PALETTES[theme]
    faults = current_faults(state)
    states = effective_states(config, faults)
    services = {s["id"]: s for s in config["services"]}
    label, severity = overall_status(config, states)
    m = metrics(config, state, current)
    out = []
    add = out.append

    add(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" '
        f'role="img" aria-label="{escape(config["cluster"])}: {escape(label)}">'
    )
    add(
        "<style>"
        ".flow{stroke-dasharray:2 10;animation:flow 1.1s linear infinite}"
        ".slowflow{stroke-dasharray:2 10;animation:flow 4.5s linear infinite}"
        "@keyframes flow{to{stroke-dashoffset:-12}}"
        ".pulse{transform-box:fill-box;transform-origin:center;animation:pulse 1.8s ease-out infinite}"
        "@keyframes pulse{0%{transform:scale(.6);opacity:.55}100%{transform:scale(2.4);opacity:0}}"
        "@media (prefers-reduced-motion:reduce){.flow,.slowflow,.pulse{animation:none}}"
        "</style>"
    )
    add(f'<rect width="{W}" height="{H}" rx="16" fill="{p["bg"]}"/>')
    add(f'<rect x=".5" y=".5" width="{W - 1}" height="{H - 1}" rx="16" fill="none" stroke="{p["border"]}"/>')

    # header
    sev = p[severity]
    add(f'<text x="48" y="50" font-family="{MONO}" font-size="13" fill="{p["muted"]}">'
        f'{escape(config["cluster"])} · {escape(config["region"])} · {len(services)} services</text>')
    if severity != "ok":
        add(f'<circle class="pulse" cx="56" cy="82" r="7" fill="{sev}"/>')
    add(status_shape(56, 82, HEALTHY if severity == "ok" else ("slow" if severity == "warn" else "down"), sev))
    add(f'<text x="76" y="90" font-family="{SANS}" font-size="24" font-weight="600" fill="{p["text"]}">{escape(label)}</text>')
    hint = "Open an issue to break something" if severity == "ok" else f"{len(faults)} active fault{'s' if len(faults) != 1 else ''} · self-healing"
    add(f'<text x="{W - 48}" y="88" text-anchor="end" font-family="{SANS}" font-size="14" fill="{p["muted"]}">{escape(hint)}</text>')
    add(f'<line x1="48" y1="112" x2="{W - 48}" y2="112" stroke="{p["border"]}"/>')

    # edges: caller -> dependency; colored by the dependency's state
    for s in config["services"]:
        x1, y1 = card_pos(s)
        x1, y1 = x1 + CARD_W, y1 + CARD_H / 2
        for dep in s["deps"]:
            x2, y2 = card_pos(services[dep])
            y2 += CARD_H / 2
            d = f"M{x1:.1f} {y1:.1f}C{x1 + 60:.1f} {y1:.1f} {x2 - 60:.1f} {y2:.1f} {x2:.1f} {y2:.1f}"
            dst = states[dep]
            add(f'<path d="{d}" fill="none" stroke="{p["border"]}" stroke-width="1.5"/>')
            if dst == HEALTHY:
                add(f'<path class="flow" d="{d}" fill="none" stroke="{p["accent"]}" stroke-width="2.5" stroke-linecap="round"/>')
            elif dst in ("slow", DEGRADED):
                add(f'<path class="slowflow" d="{d}" fill="none" stroke="{p["warn"]}" stroke-width="2.5" stroke-linecap="round"/>')
            else:
                add(f'<path d="{d}" fill="none" stroke="{p["bad"]}" stroke-width="1.5" stroke-dasharray="5 5"/>')
                if dst == "cut":
                    mx, my = (x1 + x2) / 2, (y1 + y2) / 2
                    add(f'<circle cx="{mx:.1f}" cy="{my:.1f}" r="9" fill="{p["bg"]}" stroke="{p["bad"]}"/>'
                        f'<path d="M{mx - 3.5:.1f} {my - 3.5:.1f}l7 7M{mx + 3.5:.1f} {my - 3.5:.1f}l-7 7" '
                        f'stroke="{p["bad"]}" stroke-width="1.8" stroke-linecap="round"/>')

    # service cards
    for s in config["services"]:
        x, y = card_pos(s)
        st = states[s["id"]]
        color = p[SEVERITY[st]]
        stroke = p["border"] if st == HEALTHY else color
        add(f'<rect x="{x:.1f}" y="{y:.1f}" width="{CARD_W}" height="{CARD_H}" rx="12" fill="{p["surface"]}" '
            f'stroke="{stroke}" stroke-width="{1 if st == HEALTHY else 1.5}"/>')
        if st in ("down", "cut"):
            add(f'<circle class="pulse" cx="{x + 20:.1f}" cy="{y + 25:.1f}" r="6" fill="{color}"/>')
        add(status_shape(round(x + 20, 1), round(y + 25, 1), st, color))
        add(f'<text x="{x + 34:.1f}" y="{y + 30:.1f}" font-family="{SANS}" font-size="15" font-weight="600" '
            f'fill="{p["text"]}">{escape(s["name"])}</text>')
        if st == HEALTHY:
            detail = "Running · 3/3 ready"
        elif st == DEGRADED:
            bad_deps = [d for d in s["deps"] if states[d] != HEALTHY]
            detail = f"upstream: {services[bad_deps[0]]['name']}" if bad_deps else "degraded"
        else:
            detail = config["faults"][faults[s["id"]]]["detail"]
        add(f'<text x="{x + 20:.1f}" y="{y + 53:.1f}" font-family="{MONO}" font-size="11.5" '
            f'fill="{p["muted"] if st == HEALTHY else color}">{escape(detail)}</text>')

    # footer metrics
    add(f'<line x1="48" y1="{H - 112}" x2="{W - 48}" y2="{H - 112}" stroke="{p["border"]}"/>')
    budget = m["budget_left"]
    stats = [
        ("Availability · 30d", f"{m['availability']:.2f}%", "ok" if m["availability"] >= config["slo"] else "bad"),
        (f"Error budget · SLO {config['slo']}%", f"{budget:.0f}%" if budget > 0 else "exhausted",
         "ok" if budget > 50 else "warn" if budget > 0 else "bad"),
        ("Mean time to recover", fmt_duration(m["mttr"]) if m["mttr"] is not None else "—", None),
        ("Incidents", str(m["incidents"]), None),
        ("Chaos agents", str(m["agents"]), None),
    ]
    col = (W - 96) / len(stats)
    for idx, (name, value, tone) in enumerate(stats):
        x = 48 + idx * col
        add(f'<text x="{x:.1f}" y="{H - 78}" font-family="{SANS}" font-size="12.5" fill="{p["muted"]}">{escape(name)}</text>')
        add(f'<text x="{x:.1f}" y="{H - 46}" font-family="{SANS}" font-size="26" font-weight="600" '
            f'fill="{p[tone] if tone else p["text"]}">{escape(value)}</text>')
    last = state["incidents"][-1] if state["incidents"] else None
    caption = (
        f"Last incident #{last['id']}: @{last['agent']} ran {last['fault']} on {last['target']}"
        if last else "No incidents yet. Be the first to break production."
    )
    add(f'<text x="48" y="{H - 20}" font-family="{MONO}" font-size="11.5" fill="{p["muted"]}">{escape(caption)}</text>')
    add("</svg>")
    return "\n".join(out) + "\n"


# --------------------------------------------------------------------------- render: readme

STATE_LABEL = {HEALTHY: "running", DEGRADED: "degraded", "slow": "slow", "down": "**down**", "cut": "**partitioned**"}


def render_readme_block(config: dict, state: dict, current: datetime) -> str:
    faults = current_faults(state)
    states = effective_states(config, faults)
    lines = [README_START, ""]

    lines += ["| Service | Role | Status | Break it |", "| --- | --- | --- | --- |"]
    for s in config["services"]:
        links = " · ".join(f"[{f}]({issue_url(config, f, s['id'])})" for f in config["faults"])
        lines.append(
            f"| [`{s['id']}`](https://github.com/{s['repo']}) | {s['role']} | {STATE_LABEL[states[s['id']]]} | {links} |"
        )
    lines.append("")

    lines += ["#### Incident log", ""]
    recent = list(reversed(state["incidents"]))[:8]
    if recent:
        lines += ["| # | Started (UTC) | Agent | Fault | Blast radius | Recovered in |", "| --- | --- | --- | --- | --- | --- |"]
        for i in recent:
            started = parse_time(i["started"])
            if i.get("healed"):
                ttr = fmt_duration((parse_time(i["healed"]) - started).total_seconds() / 60)
            else:
                ttr = "**ongoing**"
            lines.append(
                f"| [{i['id']}](https://github.com/{config['repo']}/issues/{i['issue']}) | "
                f"{started.strftime('%Y-%m-%d %H:%M')} | @{i['agent']} | {i['fault']} `{i['target']}` | "
                f"{len(i['blast'])} service{'s' if len(i['blast']) != 1 else ''} | {ttr} |"
            )
    else:
        lines.append("_No incidents yet. Production has never been this quiet, which is suspicious._")
    lines.append("")

    agents: dict[str, dict] = {}
    for i in state["incidents"]:
        a = agents.setdefault(i["agent"].lower(), {"name": i["agent"], "count": 0, "blast": 0})
        a["count"] += 1
        a["blast"] = max(a["blast"], len(i["blast"]))
    if agents:
        ranked = sorted(agents.values(), key=lambda a: (-a["count"], -a["blast"], a["name"].lower()))[:5]
        lines += ["#### Hall of chaos", "", "| Agent | Incidents | Biggest blast radius |", "| --- | --- | --- |"]
        for a in ranked:
            lines.append(f"| @{a['name']} | {a['count']} | {a['blast']} of {len(states)} services |")
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
            text = text[:start] + render_readme_block(config, state, current) + text[end + len(README_END):]
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
            config, state, current,
            os.environ.get("ISSUE_TITLE", ""),
            os.environ.get("ISSUE_AUTHOR", ""),
            os.environ.get("ISSUE_NUMBER", ""),
        )
        comment_path = Path(os.environ.get("CHAOS_COMMENT", ROOT / ".comment.md"))
        comment_path.write_text(comment + "\n", encoding="utf-8")
        write_outputs({"result": result, "message": message})
    elif command == "reconcile":
        names = ", ".join(i["target"] for i in healed)
        write_outputs({"healed": str(len(healed)), "message": f"chaos: reconciler healed {names}" if healed else ""})

    save_state(state)
    render(config, state, current)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
