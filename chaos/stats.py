#!/usr/bin/env python3
"""Collect the profile's GitHub stats for the README stats panel.

Reads the last twelve months from the GraphQL API with the token in
GITHUB_TOKEN and prints a summary. Nothing is written unless --write is
given, in which case brand/stats.json is replaced.

Exit codes: 0 success, 1 the API could not be reached or answered with an
error, 2 bad usage or missing token.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "brand" / "stats.json"
API = "https://api.github.com/graphql"
QUERY = """
query($login: String!) {
  user(login: $login) {
    followers { totalCount }
    contributionsCollection {
      totalCommitContributions
      totalPullRequestContributions
      totalPullRequestReviewContributions
      totalIssueContributions
      contributionCalendar { totalContributions weeks { contributionDays { date contributionCount } } }
    }
    repositories(ownerAffiliations: OWNER, isFork: false, first: 100, orderBy: {field: STARGAZERS, direction: DESC}) {
      totalCount
      nodes { stargazerCount languages(first: 6, orderBy: {field: SIZE, direction: DESC}) { edges { size node { name color } } } }
    }
  }
}
"""


def fetch(login: str, token: str, attempts: int = 3) -> dict:
    body = json.dumps({"query": QUERY, "variables": {"login": login}}).encode()
    request = urllib.request.Request(
        API,
        data=body,
        headers={
            "Authorization": f"bearer {token}",
            "Content-Type": "application/json",
        },
    )
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(request, timeout=20) as response:  # nosec B310 - fixed https URL
                payload = json.load(response)
            if payload.get("errors"):
                raise RuntimeError(
                    "; ".join(
                        e.get("message", "unknown error") for e in payload["errors"]
                    )
                )
            return payload["data"]["user"]
        except (urllib.error.URLError, TimeoutError) as error:
            if attempt == attempts:
                raise RuntimeError(
                    f"GitHub API unreachable: {error.__class__.__name__}"
                ) from None
            time.sleep(2**attempt)
    raise RuntimeError("unreachable")


def streaks(days: list[tuple[str, int]]) -> tuple[int, int]:
    """Current and longest run of days with at least one contribution."""
    longest = run = 0
    for _, count in days:
        run = run + 1 if count else 0
        longest = max(longest, run)
    current = 0
    today = datetime.now(timezone.utc).date().isoformat()
    for day, count in reversed(days):
        if count:
            current += 1
        elif day != today:  # an empty today does not break the streak yet
            break
    return current, longest


def summarise(user: dict) -> dict:
    collection = user["contributionsCollection"]
    weeks = collection["contributionCalendar"]["weeks"]
    days = [
        (d["date"], d["contributionCount"])
        for w in weeks
        for d in w["contributionDays"]
    ]
    current, longest = streaks(days)
    languages: dict[str, dict] = {}
    for repo in user["repositories"]["nodes"]:
        for edge in repo["languages"]["edges"]:
            entry = languages.setdefault(
                edge["node"]["name"],
                {"bytes": 0, "color": edge["node"]["color"] or "#8b949e"},
            )
            entry["bytes"] += edge["size"]
    total = sum(v["bytes"] for v in languages.values()) or 1
    top = sorted(languages.items(), key=lambda kv: -kv[1]["bytes"])[:5]
    return {
        "updated": datetime.now(timezone.utc).date().isoformat(),
        "contributions": collection["contributionCalendar"]["totalContributions"],
        "commits": collection["totalCommitContributions"],
        "pull_requests": collection["totalPullRequestContributions"],
        "reviews": collection["totalPullRequestReviewContributions"],
        "issues": collection["totalIssueContributions"],
        "current_streak": current,
        "longest_streak": longest,
        "active_days": sum(1 for _, c in days if c),
        "repos": user["repositories"]["totalCount"],
        "stars": sum(r["stargazerCount"] for r in user["repositories"]["nodes"]),
        "followers": user["followers"]["totalCount"],
        "weeks": [
            sum(d["contributionCount"] for d in w["contributionDays"]) for w in weeks
        ][-52:],
        "languages": [
            {"name": n, "share": round(v["bytes"] / total, 4), "color": v["color"]}
            for n, v in top
        ],
    }


def main(argv: list[str]) -> int:
    if any(arg not in ("--write",) for arg in argv[1:]):
        print("usage: stats.py [--write]", file=sys.stderr)
        return 2
    token = os.environ.get("GITHUB_TOKEN", "")
    login = os.environ.get("STATS_LOGIN", "SandeepKomal")
    if not token:
        print("GITHUB_TOKEN is not set.", file=sys.stderr)
        return 2
    try:
        stats = summarise(fetch(login, token))
    except (RuntimeError, KeyError, TypeError, ValueError) as error:
        print(f"Could not collect stats: {error}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {k: v for k, v in stats.items() if k not in ("weeks", "languages")},
            indent=2,
        )
    )
    if "--write" in argv:
        OUT.write_text(json.dumps(stats, indent=2) + "\n", encoding="utf-8")
        print(f"Wrote {OUT.name}.")
    else:
        print("Dry run: pass --write to update brand/stats.json.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
