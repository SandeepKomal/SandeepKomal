import os
import sys
import unittest
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import engine

T0 = engine.parse_time("2026-11-01T12:00:00Z")


def fresh_state():
    return {
        "version": 1,
        "since": engine.fmt_time(T0 - timedelta(days=30)),
        "incidents": [],
    }


class InjectTest(unittest.TestCase):
    def setUp(self):
        self.config = engine.load_config()
        self.state = fresh_state()

    def inject(self, title, author="octocat", number="1", at=T0):
        return engine.inject(self.config, self.state, at, title, author, number)

    def test_accepts_valid_command(self):
        result, comment, message = self.inject("chaos: kill blast-radius")
        self.assertEqual(result, "accepted")
        self.assertIn("blast-radius", comment)
        self.assertEqual(message, "chaos: @octocat kill blast-radius (incident #1)")
        self.assertEqual(
            self.state["incidents"][0]["blast"], ["profile", "blast-radius"]
        )

    def test_rejects_hostile_titles(self):
        for title in [
            "chaos: kill blast-radius; rm -rf /",
            "chaos: kill $(id)",
            "chaos: kill `id`",
            "chaos: kill ../../etc",
            "chaos: kill blast-radius\nchaos: kill profile",
            "chaos: nuke profile",
            "chaos: kill nothing-here",
            "hello",
            "",
        ]:
            with self.subTest(title=title):
                result, _, message = self.inject(title, number=str(len(title) + 100))
                self.assertEqual(result, "rejected")
                self.assertEqual(message, "")
        self.assertEqual(self.state["incidents"], [])

    def test_rejects_bad_author_and_number(self):
        self.assertEqual(
            self.inject("chaos: kill profile", author="bad name")[0], "rejected"
        )
        self.assertEqual(self.inject("chaos: kill profile", author="")[0], "rejected")
        self.assertEqual(
            self.inject("chaos: kill profile", number="1; id")[0], "rejected"
        )
        self.assertEqual(self.state["incidents"], [])

    def test_same_issue_is_processed_once(self):
        self.assertEqual(self.inject("chaos: kill profile")[0], "accepted")
        self.assertEqual(self.inject("chaos: kill profile")[0], "ignored")
        self.assertEqual(len(self.state["incidents"]), 1)

    def test_limits(self):
        self.assertEqual(
            self.inject("chaos: kill git3d", author="a", number="1")[0], "accepted"
        )
        self.assertEqual(
            self.inject("chaos: latency git3d", author="b", number="2")[0], "rejected"
        )
        self.assertEqual(
            self.inject("chaos: kill devsecops", author="a", number="3")[0], "rejected"
        )
        self.assertEqual(
            self.inject("chaos: kill devsecops", author="b", number="4")[0], "accepted"
        )
        self.assertEqual(
            self.inject("chaos: kill komorapy", author="c", number="5")[0], "accepted"
        )
        self.assertEqual(
            self.inject("chaos: kill profile", author="d", number="6")[0], "rejected"
        )

    def test_owner_has_no_cooldown(self):
        self.assertEqual(
            self.inject("chaos: kill git3d", author="SandeepKomal", number="1")[0],
            "accepted",
        )
        self.assertEqual(
            self.inject("chaos: kill devsecops", author="sandeepkomal", number="2")[0],
            "accepted",
        )


class ClusterTest(unittest.TestCase):
    def setUp(self):
        self.config = engine.load_config()

    def test_control_plane_takes_everything_down(self):
        states = engine.effective_states(self.config, {"kubernetes": "kill"})
        self.assertEqual(states["kubernetes"], "down")
        self.assertTrue(all(s != engine.HEALTHY for s in states.values()))
        self.assertEqual(engine.overall_status(self.config, states)[0], "Major outage")

    def test_status_levels(self):
        ok = engine.effective_states(self.config, {})
        self.assertEqual(engine.overall_status(self.config, ok)[1], "ok")
        slow = engine.effective_states(self.config, {"git3d": "latency"})
        self.assertEqual(
            engine.overall_status(self.config, slow)[0], "Degraded performance"
        )
        cut = engine.effective_states(self.config, {"git3d": "partition"})
        self.assertEqual(engine.overall_status(self.config, cut)[0], "Partial outage")

    def test_reconcile_heals_due_incidents_only(self):
        state = fresh_state()
        engine.inject(self.config, state, T0, "chaos: kill profile", "a", "1")
        engine.inject(self.config, state, T0, "chaos: partition git3d", "b", "2")
        healed = engine.reconcile(self.config, state, T0 + timedelta(minutes=25))
        self.assertEqual([i["target"] for i in healed], ["profile"])
        self.assertEqual(list(engine.current_faults(state)), ["git3d"])

    def test_metrics_merge_overlapping_downtime(self):
        state = fresh_state()
        engine.inject(self.config, state, T0, "chaos: kill profile", "a", "1")
        engine.inject(
            self.config,
            state,
            T0 + timedelta(minutes=10),
            "chaos: kill git3d",
            "b",
            "2",
        )
        engine.reconcile(self.config, state, T0 + timedelta(minutes=30))
        m = engine.metrics(self.config, state, T0 + timedelta(minutes=30))
        self.assertAlmostEqual(
            m["availability"], 100 * (1 - 30 / (30 * 24 * 60)), places=6
        )
        self.assertEqual(m["incidents"], 2)
        self.assertAlmostEqual(m["mttr"], 20)

    def test_render_is_wellformed(self):
        import xml.dom.minidom

        import brand

        state = fresh_state()
        engine.inject(self.config, state, T0, "chaos: partition kubernetes", "a-b", "1")
        states = engine.effective_states(self.config, engine.current_faults(state))
        label, severity = engine.overall_status(self.config, states)
        m = engine.metrics(self.config, state, T0)
        t = brand.THEMES["dark"]
        for doc in (
            brand.hero(t, label, severity, states, m),
            brand.production(t, self.config, states, label, severity, m, "profile"),
        ):
            xml.dom.minidom.parseString(doc)
            self.assertIn("Major outage", doc)
        events = brand.events(t, state["incidents"])
        xml.dom.minidom.parseString(events)
        self.assertIn("pod/kubernetes", events)
        block = engine.render_readme_block(self.config, state, T0)
        self.assertTrue(
            block.startswith(engine.README_START) and block.endswith(engine.README_END)
        )
        self.assertIn("issues/new?title=chaos%3A%20kill%20", block)

    def test_buttons_target_a_healthy_service(self):
        state = fresh_state()
        engine.inject(self.config, state, T0, "chaos: kill profile", "a", "1")
        states = engine.effective_states(self.config, engine.current_faults(state))
        self.assertEqual(
            states[engine.next_target(self.config, state, states)], engine.HEALTHY
        )

    def test_static_panels_are_wellformed(self):
        import xml.dom.minidom

        import brand

        t = brand.THEMES["light"]
        for doc in (
            brand.whoami(t),
            brand.stack(t),
            brand.footer(t),
            brand.button(t, "kill"),
            brand.project_card(t, brand.PROJECTS[0], 0),
            brand.link_button(t, "email"),
        ):
            xml.dom.minidom.parseString(doc)


class ThemeTest(unittest.TestCase):
    def test_both_themes_are_wellformed_and_differ(self):
        import xml.dom.minidom

        import brand

        dark = brand.whoami(brand.THEMES["dark"])
        light = brand.whoami(brand.THEMES["light"])
        for doc in (dark, light):
            xml.dom.minidom.parseString(doc)
        self.assertIn('fill="#0d1117"', dark)
        self.assertIn('fill="#ffffff"', light)

    def test_failing_services_appear_in_the_hero_stream(self):
        import brand

        config = engine.load_config()
        states = engine.effective_states(config, {"blast-radius": "kill"})
        m = engine.metrics(config, fresh_state(), T0)
        doc = brand.hero(brand.THEMES["dark"], "Partial outage", "bad", states, m)
        self.assertIn("kubectl get pod blast-radius", doc)
        self.assertIn(
            "Sandeep Komal Pothu", doc
        )  # the heading itself is drawn as glyph paths


class StatsTest(unittest.TestCase):
    def sample(self):
        days = [
            {"date": f"2026-10-{d:02d}", "contributionCount": c}
            for d, c in zip(range(1, 32), [0, 3, 1] + [2] * 28)
        ]
        return {
            "followers": {"totalCount": 21},
            "contributionsCollection": {
                "totalCommitContributions": 900,
                "totalPullRequestContributions": 80,
                "totalPullRequestReviewContributions": 30,
                "totalIssueContributions": 12,
                "contributionCalendar": {
                    "totalContributions": 61,
                    "weeks": [{"contributionDays": days}],
                },
            },
            "repositories": {
                "totalCount": 2,
                "nodes": [
                    {
                        "stargazerCount": 5,
                        "languages": {
                            "edges": [
                                {
                                    "size": 300,
                                    "node": {"name": "Python", "color": "#3572A5"},
                                }
                            ]
                        },
                    },
                    {
                        "stargazerCount": 2,
                        "languages": {
                            "edges": [
                                {"size": 100, "node": {"name": "HCL", "color": None}}
                            ]
                        },
                    },
                ],
            },
        }

    def test_summary(self):
        import stats

        s = stats.summarise(self.sample())
        self.assertEqual((s["contributions"], s["stars"], s["followers"]), (61, 7, 21))
        self.assertEqual(s["longest_streak"], 30)
        self.assertEqual([lang["name"] for lang in s["languages"]], ["Python", "HCL"])
        self.assertAlmostEqual(s["languages"][0]["share"], 0.75)

    def test_updated_is_stamped_in_ist(self):
        import stats

        when = stats.datetime(2026, 10, 31, 19, 41, tzinfo=stats.timezone.utc)
        self.assertEqual(stats.stamp(when), "2026-11-01 01:11 IST")

    def test_missing_token_fails_without_writing(self):
        import stats

        before = stats.OUT.read_text()
        os.environ.pop("GITHUB_TOKEN", None)
        self.assertEqual(stats.main(["stats.py", "--write"]), 2)
        self.assertEqual(stats.main(["stats.py", "--bogus"]), 2)
        self.assertEqual(stats.OUT.read_text(), before)

    def test_panel_renders_partial_and_full_data(self):
        import xml.dom.minidom

        import stats

        import brand

        xml.dom.minidom.parseString(brand.stats(brand.THEMES["dark"]))
        original = stats.OUT.read_text()
        try:
            stats.OUT.write_text(
                __import__("json").dumps(stats.summarise(self.sample()))
            )
            doc = brand.stats(brand.THEMES["light"])
            xml.dom.minidom.parseString(doc)
            self.assertIn("Python", doc)
        finally:
            stats.OUT.write_text(original)


class ReadmeTest(unittest.TestCase):
    def test_every_panel_the_readme_uses_exists(self):
        import re

        root = Path(__file__).resolve().parent.parent
        readme = (root / "README.md").read_text()
        paths = set(
            re.findall(r'(?:src|srcset)="([^"?]+\.svg)(?:\?v=[0-9a-f]+)?"', readme)
        )
        self.assertTrue(paths)
        for path in paths:
            self.assertTrue((root / path).exists(), path)


class ClockThemeTest(unittest.TestCase):
    def test_ist_day_and_night(self):
        at = engine.parse_time
        self.assertEqual(
            engine.clock_theme(at("2026-11-01T00:29:00Z")), "dark"
        )  # 05:59 IST
        self.assertEqual(
            engine.clock_theme(at("2026-11-01T00:30:00Z")), "light"
        )  # 06:00 IST
        self.assertEqual(
            engine.clock_theme(at("2026-11-01T12:29:00Z")), "light"
        )  # 17:59 IST
        self.assertEqual(
            engine.clock_theme(at("2026-11-01T12:30:00Z")), "dark"
        )  # 18:00 IST

    def test_every_panel_switches_together(self):
        import re

        readme = (Path(__file__).resolve().parent.parent / "README.md").read_text()
        for theme, suffix in (("dark", ""), ("light", "-light")):
            text = engine.apply_theme(readme, theme)
            pairs = re.findall(r'srcset="([^"]+)"><img src="([^"]+)"', text)
            self.assertGreater(len(pairs), 20)
            for source, fallback in pairs:
                self.assertEqual(source, fallback)
                self.assertEqual(
                    source.split("?")[0].endswith("-light.svg"),
                    theme == "light",
                    source,
                )
            self.assertIn(f"neon-arena/arena{suffix}.svg", text)
            self.assertIn(f"profile/git3d-universe{suffix}.svg", text)
            self.assertEqual(engine.apply_theme(text, theme), text)


class SeamlessTest(unittest.TestCase):
    def test_readme_is_images_only(self):
        """Markdown text and tables follow the viewer's GitHub theme, not the clock,
        so the README must be one block of images with no blank lines between them."""
        import re

        readme = (Path(__file__).resolve().parent.parent / "README.md").read_text()
        self.assertNotIn("\n\n", readme.strip())
        body = re.sub(
            r"<picture>.*?</picture>|</?a[^>]*>|</?p[^>]*>|<!--.*?-->",
            "",
            readme,
            flags=re.DOTALL,
        )
        self.assertEqual(body.strip(), "")
        for tag in re.findall(r"<img [^>]+>", readme):
            self.assertIn('align="top"', tag)

    def test_empty_events_panel(self):
        import xml.dom.minidom

        import brand

        doc = brand.events(brand.THEMES["dark"], [])
        xml.dom.minidom.parseString(doc)
        self.assertIn("No resources found", doc)


class BlendAndCacheTest(unittest.TestCase):
    def test_blend_replaces_only_the_background(self):
        import blend

        svg = '<radialGradient id="bg"><stop stop-color="#0b1530"/><stop stop-color="#02040b"/></radialGradient><radialGradient id="nebA"><stop stop-color="#3d2bd9"/></radialGradient>'
        out = blend.blend(svg, "dark")
        self.assertEqual(out.count("#0d1117"), 2)
        self.assertIn("#3d2bd9", out)
        self.assertEqual(blend.blend(out, "dark"), out)
        self.assertEqual(blend.blend("<svg/>", "light"), "<svg/>")

    def test_brand_urls_carry_a_content_version(self):
        readme = (Path(__file__).resolve().parent.parent / "README.md").read_text()
        self.assertIn('srcset="brand/hero.svg?v=', engine.apply_theme(readme, "dark"))
        self.assertIn(
            'src="neon-arena/arena-light.svg"', engine.apply_theme(readme, "light")
        )


class CliTest(unittest.TestCase):
    def test_unknown_command_fails(self):
        self.assertEqual(engine.main(["engine.py", "explode"]), 2)


if __name__ == "__main__":
    os.environ.pop("CHAOS_NOW", None)
    unittest.main()
