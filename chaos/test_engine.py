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
        for doc in (
            brand.hero(label, severity),
            brand.production(self.config, states, label, severity, m),
        ):
            xml.dom.minidom.parseString(doc)
            self.assertIn("Major outage", doc)
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

        for doc in (
            brand.marquee(),
            brand.about(),
            brand.stack(),
            brand.contact(),
            brand.button("kill"),
            brand.project_card(brand.PROJECTS[0], 0),
        ):
            xml.dom.minidom.parseString(doc)


class CliTest(unittest.TestCase):
    def test_unknown_command_fails(self):
        self.assertEqual(engine.main(["engine.py", "explode"]), 2)


if __name__ == "__main__":
    os.environ.pop("CHAOS_NOW", None)
    unittest.main()
