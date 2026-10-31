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
from pathlib import Path
from urllib.parse import quote

import brand

ROOT = Path(__file__).resolve().parent
REPO_ROOT = ROOT.parent
CONFIG_PATH = ROOT / "services.json"
STATE_PATH = ROOT / "state.json"
README_PATH = REPO_ROOT / "README.md"

TITLE_RE = re.compile(
    r"^\s*chaos:\s*([a-z]{1,20})\s+([a-z0-9-]{1,40})\s*$", re.IGNORECASE
)
LOGIN_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
THEME_PATH = REPO_ROOT / "brand" / "theme.json"
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


# --------------------------------------------------------------------------- render: readme

FAULT_ALT = {
    "kill": "Kill a pod",
    "latency": "Inject latency",
    "partition": "Cut the network",
}


def next_target(config: dict, state: dict, states: dict[str, str]) -> str:
    """Rotate the one-click buttons through the services that are still healthy."""
    ids = [s["id"] for s in config["services"]]
    start = len(state["incidents"]) % len(ids)
    for sid in ids[start:] + ids[:start]:
        if states[sid] == HEALTHY:
            return sid
    return ids[start]


PICTURE_RE = re.compile(
    r'<picture><source media="\(prefers-color-scheme: dark\)" srcset="([^"]+?)(?:-light)?\.svg"><img src="[^"]+"'
)


def clock_theme(current: datetime) -> str:
    """Dark at night and light by day on the owner's clock (IST by default)."""
    clock = json.loads(THEME_PATH.read_text(encoding="utf-8"))
    local = current + timedelta(minutes=clock["utc_offset_minutes"])
    return (
        "light" if clock["day_start"] <= local.hour < clock["night_start"] else "dark"
    )


def apply_theme(text: str, theme: str) -> str:
    """Point every panel's dark source and fallback at the same file, so every
    viewer, on any device or GitHub theme, sees the clock's theme."""
    suffix = "-light" if theme == "light" else ""
    return PICTURE_RE.sub(
        lambda m: (
            f'<picture><source media="(prefers-color-scheme: dark)" srcset="{m.group(1)}{suffix}.svg">'
            f'<img src="{m.group(1)}{suffix}.svg"'
        ),
        text,
    )


def picture(name: str, alt: str, width: str = "100%") -> str:
    """A brand panel; apply_theme picks which variant it shows. align=top stops the
    gap browsers leave under inline images, so panels sit flush."""
    return (
        f'<picture><source media="(prefers-color-scheme: dark)" srcset="brand/{name}.svg">'
        f'<img src="brand/{name}-light.svg" width="{width}" align="top" alt="{alt}"></picture>'
    )


def render_readme_block(config: dict, state: dict, current: datetime) -> str:
    """Production, the three break buttons and recent events, as images only, so the
    whole section follows the profile's theme."""
    states = effective_states(config, current_faults(state))
    label, _ = overall_status(config, states)
    target = next_target(config, state, states)
    buttons = "".join(
        f'<a href="{issue_url(config, fault, target)}">{picture(f"btn-{fault}", f"{alt} on {target}", "33.33%")}</a>'
        for fault, alt in FAULT_ALT.items()
    )
    return "\n".join(
        [
            README_START,
            picture("production", f"kubectl get pods: production is {label}."),
            buttons,
            picture("events", "Recent breaks of production"),
            README_END,
        ]
    )


def render(config: dict, state: dict, current: datetime) -> None:
    states = effective_states(config, current_faults(state))
    label, severity = overall_status(config, states)
    brand.render_live(
        config,
        states,
        label,
        severity,
        metrics(config, state, current),
        state["incidents"],
        next_target(config, state, states),
    )
    if README_PATH.exists():
        text = README_PATH.read_text(encoding="utf-8")
        start, end = text.find(README_START), text.find(README_END)
        if start != -1 and end > start:
            block = render_readme_block(config, state, current)
            text = text[:start] + block + text[end + len(README_END) :]
        README_PATH.write_text(
            apply_theme(text, clock_theme(current)), encoding="utf-8"
        )


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
