import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "root", ".xcert"))

import json_loader  # noqa: E402
import main  # noqa: E402
import ui  # noqa: E402

NOW = datetime(2026, 10, 31, 14, 2)
CTX = ui.Context(version="1.3.0", host="srv01", now=NOW, threshold=30, next_run=datetime(2026, 11, 2, 1, 0))


def cert(name, days, provider="ovh", sans=()):
    present = days != "missing"
    d = days if present else None
    return ui.CertState(name=name, provider=provider, sans=list(sans), present=present,
                        expires=None if d is None else NOW + timedelta(days=d), days_left=d,
                        reload="systemctl reload nginx")


STATES = [
    cert("example.pl", 79, sans=["*.example.pl"]),
    cert("sklep.example.pl", 57, "cloudflare"),
    cert("poczta.example.net", 12),
    cert("stara-strona.example.org", -3, "route53"),
    cert("api.example.dev", "missing", "digitalocean"),
    cert("a-very-long-subdomain-name.example-company.com", 200),
]
RESULTS = [("poczta.example.net", True), ("stara-strona.example.org", False), ("api.example.dev", True)]
TIMINGS = [(0, 41), (45, 160), (210, 37)]


def screens(p):
    yield ui.render_list(p, CTX, STATES)
    yield ui.render_summary(p, CTX, RESULTS, TIMINGS, 260)
    yield ui.render_menu(p, CTX, STATES, main.MENU_ITEMS)
    yield ui.render_motd(p, CTX, STATES)
    yield ui.render_motd(p, CTX, STATES[:2])
    yield ui.render_picker(p, CTX, dict(ui.DEFAULT_APPEARANCE), p.mode)


class RenderTest(unittest.TestCase):
    def test_every_look_mode_and_background_fits_80_columns(self):
        for theme in ui.THEMES:
            for mode in ("full", "16", "none"):
                for bg in ui.BACKGROUNDS:
                    p = ui.Painter(theme, mode, bg)
                    for text in screens(p):
                        for line in text.split("\n"):
                            with self.subTest(theme=theme, mode=mode, bg=bg, line=ui.ANSI_RE.sub("", line)):
                                self.assertLessEqual(ui.visible_len(line), ui.WIDTH)

    def test_plain_mode_emits_no_escape_codes(self):
        for theme in ui.THEMES:
            for text in screens(ui.Painter(theme, "none")):
                self.assertNotIn("\033", text)

    def test_full_mode_uses_24bit_and_16_mode_does_not(self):
        listing = ui.render_list(ui.Painter("timeline", "full"), CTX, STATES)
        self.assertIn("38;2;", listing)
        self.assertNotIn("38;2;", ui.render_list(ui.Painter("timeline", "16"), CTX, STATES))

    def test_backgrounds_use_different_palettes(self):
        dark = ui.render_list(ui.Painter("ledger", "full", "dark"), CTX, STATES)
        light = ui.render_list(ui.Painter("ledger", "full", "light"), CTX, STATES)
        self.assertNotEqual(dark, light)

    def test_motd_names_only_problem_certificates(self):
        text = ui.render_motd(ui.Painter("ledger", "none"), CTX, STATES)
        self.assertIn("stara-strona.example.org", text)
        self.assertIn("api.example.dev", text)
        self.assertNotIn("sklep.example.pl", text)

    def test_categories(self):
        cats = [ui.category(s, 30) for s in STATES]
        self.assertEqual(cats, ["ok", "ok", "due", "expired", "missing", "ok"])
        self.assertEqual(ui.category(cert("x.pl", 3), 30), "critical")


class PlanTest(unittest.TestCase):
    def test_due_certificates_go_to_next_run_and_fresh_ones_later(self):
        plan = dict(ui.plan_runs(STATES, CTX, limit=10))
        first = [s.name for s in plan[CTX.next_run]]
        self.assertEqual(first, ["poczta.example.net", "stara-strona.example.org", "api.example.dev"])
        # 57 days left, renews once inside the 30-day window: Mon 30.11
        self.assertEqual([s.name for s in plan[datetime(2026, 11, 30, 1, 0)]], ["sklep.example.pl"])

    def test_no_timer_means_no_plan(self):
        ctx = ui.Context("1.3.0", "srv01", NOW)
        self.assertEqual(ui.plan_runs(STATES, ctx), [])


class ColorModeTest(unittest.TestCase):
    tty = SimpleNamespace(isatty=lambda: True)
    pipe = SimpleNamespace(isatty=lambda: False)

    def test_no_color_and_pipes_always_win(self):
        self.assertEqual(ui.resolve_color_mode("full", self.tty, env={"NO_COLOR": "1"}), "none")
        self.assertEqual(ui.resolve_color_mode("full", self.pipe, env={}), "none")

    def test_auto_detects_truecolor(self):
        self.assertEqual(ui.resolve_color_mode("auto", self.tty, env={"COLORTERM": "truecolor"}), "full")
        self.assertEqual(ui.resolve_color_mode("auto", self.tty, env={"TERM": "xterm"}), "16")

    def test_motd_assumes_terminal(self):
        self.assertEqual(ui.resolve_color_mode("16", self.pipe, assume_terminal=True, env={}), "16")


class DefaultsTest(unittest.TestCase):
    def test_missing_threshold_skips_fresh_certificate_without_certbot(self):
        general = {"logs": 0}
        with mock.patch.object(main, "cert_days_left", return_value=60), \
                mock.patch.object(main, "run_command") as run:
            ok = main.cert_for_domains(["example.pl"], "admin@example.pl", "ovh", general,
                                       {"ovh": {"cmd_template": "certbot certonly"}}, mock.Mock())
        self.assertTrue(ok)
        run.assert_not_called()


class AppearanceTest(unittest.TestCase):
    def test_cli_overrides_saved_appearance(self):
        config = {"appearance": {"theme": "timeline", "colors": "16", "background": "light"}}
        args = SimpleNamespace(theme="console", colors=None, background=None)
        self.assertEqual(main.effective_appearance(config, args),
                         {"theme": "console", "colors": "16", "background": "light"})

    def test_unknown_appearance_fails_validation(self):
        config = {"general": {}, "providers": {}, "domains": [], "appearance": {"theme": "neon"}}
        self.assertFalse(main.validate_config(config, mock.Mock()))

    def test_picker_saves_appearance_without_dropping_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "config.json")
            json_loader.save_config({"general": {"logs": 0}, "domains": [{"domain": "a.pl"}]}, config_path=path)
            appearance = dict(ui.DEFAULT_APPEARANCE)
            keys = iter(["3", "c", "b", "s"])
            with mock.patch("builtins.input", lambda _: next(keys)), mock.patch("builtins.print"), \
                    mock.patch.object(main, "timer_next_run", return_value=None):
                main.pick_appearance(path, appearance, mock.Mock())
            saved = json_loader.load_config(config_path=path)
            self.assertEqual(saved["appearance"], {"theme": "console", "colors": "full", "background": "light"})
            self.assertEqual(saved["domains"], [{"domain": "a.pl"}])
            self.assertEqual(appearance["theme"], "console")
            self.assertFalse(os.path.exists(path + ".tmp"))


if __name__ == "__main__":
    unittest.main()
