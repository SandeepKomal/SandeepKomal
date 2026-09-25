#!/usr/bin/env node

const token = process.env.GH_TOKEN;
const username = process.env.GH_USERNAME;
const outputPath = process.env.OUTPUT_PATH || "dist/github-jet.svg";

if (!token || !username) {
  throw new Error("GH_TOKEN and GH_USERNAME are required.");
}

const to = new Date();
const from = new Date(to);
from.setUTCFullYear(from.getUTCFullYear() - 1);

const query = `
query ContributionCalendar($login: String!, $from: DateTime!, $to: DateTime!) {
  user(login: $login) {
    contributionsCollection(from: $from, to: $to) {
      contributionCalendar {
        totalContributions
        weeks {
          contributionDays {
            contributionCount
            date
          }
        }
      }
    }
  }
}
`;

const response = await fetch("https://api.github.com/graphql", {
  method: "POST",
  headers: {
    "Authorization": `Bearer ${token}`,
    "Content-Type": "application/json",
    "User-Agent": "SandeepKomal-profile-heatmap"
  },
  body: JSON.stringify({
    query,
    variables: {
      login: username,
      from: from.toISOString(),
      to: to.toISOString()
    }
  })
});

if (!response.ok) {
  throw new Error(`GitHub GraphQL HTTP ${response.status}: ${await response.text()}`);
}

const payload = await response.json();

if (payload.errors?.length) {
  throw new Error(payload.errors.map((e) => e.message).join("; "));
}

const calendar = payload.data?.user?.contributionsCollection?.contributionCalendar;
if (!calendar) {
  throw new Error(`No contribution calendar returned for ${username}.`);
}

const days = calendar.weeks.flatMap((week) => week.contributionDays);
const max = Math.max(1, ...days.map((d) => d.contributionCount));

const level = (count) => {
  if (count === 0) return 0;
  const ratio = count / max;
  if (ratio <= 0.2) return 1;
  if (ratio <= 0.4) return 2;
  if (ratio <= 0.7) return 3;
  return 4;
};

const palette = [
  "#0b1220",
  "#063b5c",
  "#0077b5",
  "#00a7a0",
  "#00e5b0"
];

const cell = 12;
const gap = 3;
const step = cell + gap;
const left = 34;
const top = 42;
const weeks = calendar.weeks.length;
const width = left + weeks * step + 24;
const height = top + 7 * step + 42;

const esc = (value) =>
  String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");

const monthLabels = [];
let lastMonth = "";
calendar.weeks.forEach((week, index) => {
  const first = week.contributionDays[0];
  if (!first) return;
  const label = new Date(first.date + "T00:00:00Z").toLocaleString("en-US", {
    month: "short",
    timeZone: "UTC"
  });
  if (label !== lastMonth) {
    monthLabels.push({ index, label });
    lastMonth = label;
  }
});

const parts = [];
parts.push(`<?xml version="1.0" encoding="UTF-8"?>`);
parts.push(`<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}">`);
parts.push(`<defs>
  <linearGradient id="bg" x1="0" x2="1" y1="0" y2="1">
    <stop offset="0%" stop-color="#050b16"/>
    <stop offset="50%" stop-color="#071a2b"/>
    <stop offset="100%" stop-color="#061f2c"/>
  </linearGradient>
  <filter id="glow"><feGaussianBlur stdDeviation="2.2" result="blur"/><feMerge><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge></filter>
</defs>`);
parts.push(`<rect width="100%" height="100%" rx="16" fill="url(#bg)"/>`);
parts.push(`<text x="${left}" y="24" fill="#ffffff" font-family="Inter,Segoe UI,Arial,sans-serif" font-size="15" font-weight="700">GITHUB CONTRIBUTION HEATMAP</text>`);
parts.push(`<text x="${left}" y="38" fill="#6f8aa3" font-family="Inter,Segoe UI,Arial,sans-serif" font-size="9">LAST 12 MONTHS • TOTAL ${esc(calendar.totalContributions)} CONTRIBUTIONS</text>`);

for (const { index, label } of monthLabels) {
  const x = left + index * step;
  parts.push(`<text x="${x}" y="55" fill="#7f9ab0" font-family="Inter,Segoe UI,Arial,sans-serif" font-size="8">${esc(label)}</text>`);
}

const weekdayLabels = ["Mon", "", "Wed", "", "Fri", "", "Sun"];
weekdayLabels.forEach((label, row) => {
  if (!label) return;
  const y = top + row * step + 9;
  parts.push(`<text x="3" y="${y}" fill="#66839b" font-family="Inter,Segoe UI,Arial,sans-serif" font-size="7">${label}</text>`);
});

calendar.weeks.forEach((week, col) => {
  week.contributionDays.forEach((day) => {
    const date = new Date(day.date + "T00:00:00Z");
    const row = date.getUTCDay() === 0 ? 6 : date.getUTCDay() - 1;
    const x = left + col * step;
    const y = top + row * step;
    const fill = palette[level(day.contributionCount)];
    const opacity = day.contributionCount ? 1 : 0.75;
    parts.push(`<rect x="${x}" y="${y}" width="${cell}" height="${cell}" rx="3" fill="${fill}" fill-opacity="${opacity}"><title>${esc(day.contributionCount)} contributions on ${esc(day.date)}</title></rect>`);
  });
});

const legendY = height - 20;
parts.push(`<text x="${left}" y="${legendY + 8}" fill="#66839b" font-family="Inter,Segoe UI,Arial,sans-serif" font-size="8">LESS</text>`);
palette.forEach((color, i) => {
  const x = left + 28 + i * 17;
  parts.push(`<rect x="${x}" y="${legendY}" width="11" height="11" rx="3" fill="${color}"/>`);
});
parts.push(`<text x="${left + 118}" y="${legendY + 8}" fill="#66839b" font-family="Inter,Segoe UI,Arial,sans-serif" font-size="8">MORE</text>`);
parts.push(`<text x="${width - 88}" y="${legendY + 8}" fill="#00e5b0" font-family="Inter,Segoe UI,Arial,sans-serif" font-size="8" font-weight="700">S.K. / JET</text>`);
parts.push("</svg>");

const { mkdir, writeFile } = await import("node:fs/promises");
const { dirname } = await import("node:path");
await mkdir(dirname(outputPath), { recursive: true });
await writeFile(outputPath, parts.join("\n"), "utf8");
console.log(`Generated ${outputPath} with ${calendar.totalContributions} contributions.`);
