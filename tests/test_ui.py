import dataclasses
import io
import logging
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import i18n  # noqa: E402
import json_loader  # noqa: E402
import main  # noqa: E402
import ui  # noqa: E402

NOW = datetime(2026, 10, 31, 14, 2)
QUIET = logging.getLogger("xcert-test-ui")  # real logger: validation records what it logs
QUIET.propagate = False
QUIET.addHandler(logging.NullHandler())
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
EDITOR_CONFIG = {
    "general": {"default_provider": "cloudflare", "logs": 1,
                "post_renewal_cmd": "docker exec a-container-with-a-long-name nginx -s reload && echo done twice"},
    "domains": [{"domain": "example.pl", "email": "admin@example.pl", "provider": "ovh"},
                {"domain": "a-very-long-subdomain-name.example-company.com",
                 "email": "certificates-and-alerts@example-company.com"},
                {"domain": "*.example.pl", "email": "admin@example.pl", "provider": "digitalocean-long-name"}],
}
CHECKS = [("ok", "config sections", ""),
          ("ok", "provider ovh", "/opt/xcert/secrets/ovh-with-a-rather-long-file-name.ini"),
          ("warn", "provider route53", "Insecure permissions on /opt/xcert/secrets/route53.ini: 0o644 "
                                       "(recommended: 0o600, fix: chmod 600 /opt/xcert/secrets/route53.ini)"),
          ("err", "post_renewal_cmd, notify_cmd", "general.notify_cmd cannot be parsed: No closing quotation")]
STATUS = [("ok", "certbot", "certbot 3.0.0 (venv)"), ("err", "DNS plugins",
                                                     "Missing plugins dns-ovh: xcert --install"),
          ("ok", "units, MOTD, logrotate", "installed and current"),
          ("err", "last timer run", "Last run 27.10 01:12 failed: Unable to determine zone_id for example.com"),
          ("warn", "failure notices", "notify_cmd empty: no failure notices")]
RESULTS = [("poczta.example.net", True), ("stara-strona.example.org", False), ("api.example.dev", True)]
TIMINGS = [(0, 41), (45, 160), (210, 37)]


def screens(p):
    yield ui.render_list(p, CTX, STATES)
    yield ui.render_summary(p, CTX, RESULTS, TIMINGS, 260)
    for sel in (0, 3, 7):
        yield ui.render_menu(p, CTX, STATES, main.MENU_ITEMS, sel)
    yield ui.render_motd(p, CTX, STATES)
    yield ui.render_motd(p, CTX, STATES[:2])
    failed = dataclasses.replace(CTX, last_run=datetime(2026, 10, 27, 1, 12), last_ok=False)
    yield ui.render_list(p, failed, STATES)
    yield ui.render_menu(p, failed, STATES, main.MENU_ITEMS, 0)
    yield ui.render_motd(p, failed, STATES[:2])
    for sel in (0, 5):
        yield ui.render_picker(p, CTX, dict(ui.DEFAULT_APPEARANCE), p.mode, "pl", sel)
    for sel in (0, len(EDITOR_CONFIG["domains"]) + 6):
        yield ui.render_editor(p, CTX, EDITOR_CONFIG, "/opt/xcert/config/a-rather-long-directory-name/config.json",
                               sel, main.EDITOR_ITEMS, main.DOMAIN_ITEMS)
    yield ui.render_editor(p, CTX, {"general": {}, "domains": []}, "/opt/xcert/config/config.json", 0,
                           main.EDITOR_ITEMS, main.DOMAIN_ITEMS)
    yield ui.render_choice(p, CTX, "provider for a-very-long-subdomain-name.example-company.com",
                           ["ovh", "cloudflare", "route53"], 1, "ovh")
    yield ui.render_report(p, CTX, STATES, "/opt/xcert/logs/a-rather-long-directory-name/xcert_report.json")
    yield ui.render_head(p, CTX, "configuration")
    yield ui.render_check(p, CTX, CHECKS, "/opt/xcert/config/a-rather-long-directory-name/config.json")
    yield ui.render_check(p, CTX, CHECKS[:1], "/opt/xcert/config/config.json")
    yield ui.render_check(p, CTX, CHECKS + STATUS, "/opt/xcert/config/config.json", i18n.t("status"))
    for outcome in ("renew", "issue", "skip", "ok", "fail"):
        yield ui.render_step(p, "a-very-long-subdomain-name.example-company.com", outcome, "digitalocean", 260,
                             ["⏳ Rate-limit (route53) for a-very-long-subdomain-name.example-company.com. Waiting 75s",
                              "❌ Failed: a-very-long-subdomain-name.example-company.com (rc=1)"], 79)


class RenderTest(unittest.TestCase):
    def tearDown(self):
        i18n.set_language("en")

    def test_every_look_mode_background_and_language_fits_80_columns(self):
        for lang in i18n.LANGUAGES:
            i18n.set_language(lang)
            for theme in ui.THEMES:
                for mode in ("full", "16", "none"):
                    for bg in ui.BACKGROUNDS:
                        p = ui.Painter(theme, mode, bg)
                        for text in screens(p):
                            for line in text.split("\n"):
                                with self.subTest(lang=lang, theme=theme, mode=mode, bg=bg,
                                                  line=ui.ANSI_RE.sub("", line)):
                                    self.assertLessEqual(ui.visible_len(line), ui.WIDTH)

    def test_polish_screens_are_translated(self):
        i18n.set_language("pl")
        text = ui.render_list(ui.Painter("ledger", "none"), CTX, STATES)
        self.assertIn("certyfikaty", text)
        self.assertIn("następny przebieg pon 2.11 01:00", text)
        self.assertNotIn("next run", text)

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

    def test_failed_timer_run_shows_in_list_menu_and_motd(self):
        failed = dataclasses.replace(CTX, last_run=datetime(2026, 10, 27, 1, 12), last_ok=False)
        p = ui.Painter("ledger", "none")
        for text in (ui.render_list(p, failed, STATES), ui.render_menu(p, failed, STATES, main.MENU_ITEMS, 0),
                     ui.render_motd(p, failed, STATES[:2])):
            self.assertIn("last timer run failed (Tue 27.10 01:12)", text)
        self.assertNotIn("failed (", ui.render_motd(p, CTX, STATES[:2]))
        running = dataclasses.replace(failed, running=True)
        self.assertIn("renewal running now", ui.render_motd(p, running, STATES[:2]))
        self.assertNotIn("last timer run failed", ui.render_motd(p, running, STATES[:2]))

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

    def test_board_orders_certificates_by_the_run_that_renews_them(self):
        rows = ui.render_list(ui.Painter("board", "none"), CTX, STATES).splitlines()
        longest_first = sorted((s.name[:20] for s in STATES), key=len, reverse=True)  # example.pl is in sklep.example.pl
        names = [next((name for name in longest_first if name in line), None) for line in rows if "+ " not in line]
        self.assertEqual([n for n in names if n], ["stara-strona.example", "poczta.example.net", "api.example.dev",
                                                   "sklep.example.pl", "example.pl", "a-very-long-subdomai"])
        self.assertIn("Mon 30.11", next(line for line in rows if "sklep.example.pl" in line))


class NewLooksTest(unittest.TestCase):
    def test_calendar_marks_expiry_days_and_today_without_colour(self):
        text = ui.render_list(ui.Painter("calendar", "none"), CTX, STATES)
        self.assertIn("November 2026", text)
        self.assertIn("12*", text)  # poczta.example.net expires on 12.11
        self.assertIn("27*", text)  # sklep.example.pl on 27.12
        self.assertNotIn("31<", text)  # 31.10 has under a week left, so the grid starts in November
        first = ui._month_grid(ui.Painter("calendar", "none"), datetime(2026, 10, 1), STATES, CTX)
        self.assertIn("28*", "".join(first))  # an expired certificate still marks its day
        self.assertIn("31<", "".join(first))

    def test_tree_groups_certificates_under_their_provider(self):
        lines = ui.render_list(ui.Painter("tree", "none"), CTX, STATES).splitlines()
        ovh = lines.index(next(line for line in lines if line.startswith("ovh")))
        self.assertTrue(lines[ovh + 1].startswith("├─ ● example.pl"))
        self.assertTrue(lines[ovh + 2].startswith("│  └─ *.example.pl"))
        self.assertTrue(lines[ovh + 4].startswith("└─ ● a-very-long"))

    def test_cards_put_the_most_urgent_first_and_fit_the_screen(self):
        text = ui.render_list(ui.Painter("cards", "none"), CTX, STATES)
        self.assertLess(text.index("stara-strona"), text.index("poczta"))
        self.assertLess(text.index("poczta"), text.index("sklep"))
        try:
            ui.set_screen(132)
            wide = ui.render_list(ui.Painter("cards", "none"), CTX, STATES).splitlines()
            self.assertEqual(sum(line.count("╭─") for line in wide[2:3]), 3)  # three cards a row on 132 columns
        finally:
            ui.set_screen(80)

    def test_newer_looks_use_a_wider_window(self):
        long_name = "a-very-long-subdomain-name.example-company.com"
        try:
            ui.set_screen(132)
            for theme in ("calendar", "board", "tree"):
                self.assertIn(long_name, ui.render_list(ui.Painter(theme, "none"), CTX, STATES), theme)
            calendar_text = ui.render_list(ui.Painter("calendar", "none"), CTX, STATES)
            self.assertIn("March 2027", calendar_text)  # five months fit instead of three
            cards = ui.render_list(ui.Painter("cards", "none"), CTX, STATES).splitlines()
            self.assertGreaterEqual(max(ui.visible_len(line) for line in cards[2:]), ui.INNER)  # cards share the row
        finally:
            ui.set_screen(80)

    def test_every_look_has_its_own_palette_and_renderers(self):
        for theme in ui.THEMES:
            self.assertEqual(set(ui.PALETTES[theme]), set(ui.BACKGROUNDS), theme)
            self.assertIn(theme, ui.ANSI_ACCENT)
            self.assertEqual(set(ui.RENDERERS[theme]), {"list", "summary", "menu", "motd"}, theme)
        self.assertEqual(ui.PICKER_ROWS[:len(ui.THEMES)], ("1", "2", "3", "4", "5", "6", "7"))


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
        self.assertFalse(main.validate_config(config, QUIET))

    def test_picker_saves_appearance_without_dropping_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "config.json")
            json_loader.save_config({"general": {"logs": 0}, "domains": [{"domain": "a.pl"}]}, config_path=path)
            appearance = dict(ui.DEFAULT_APPEARANCE)
            keys = iter(["3", "c", "b", "s"])
            with mock.patch("builtins.input", lambda _: next(keys)), mock.patch("builtins.print"), \
                    mock.patch.object(main, "systemd_show", return_value=[{}, {}]):
                main.pick_appearance(path, appearance, mock.Mock())
            saved = json_loader.load_config(config_path=path)
            self.assertEqual(saved["appearance"], {"theme": "console", "colors": "full", "background": "light"})
            self.assertEqual(saved["domains"], [{"domain": "a.pl"}])
            self.assertEqual(appearance["theme"], "console")
            self.assertFalse(os.path.exists(path + ".tmp"))

    def test_picker_switches_language_and_reverts_on_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "config.json")
            json_loader.save_config({"general": {"logs": 0}}, config_path=path)
            for keys, saved_language, live_language in ((["l", "s"], "pl", "pl"), (["l", "q"], None, "en")):
                i18n.set_language("en")
                keys = iter(keys)
                with mock.patch("builtins.input", lambda _: next(keys)), mock.patch("builtins.print"), \
                        mock.patch.object(main, "systemd_show", return_value=[{}, {}]):
                    main.pick_appearance(path, dict(ui.DEFAULT_APPEARANCE), mock.Mock())
                if saved_language:
                    self.assertEqual(json_loader.load_config(config_path=path)["general"]["language"], saved_language)
                self.assertEqual(i18n.current_language(), live_language)
        i18n.set_language("en")

    def test_picker_arrows_preview_and_enter_saves(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "config.json")
            json_loader.save_config({"general": {"logs": 0}}, config_path=path)
            with scripted(["down", "down", ""]):
                main.pick_appearance(path, dict(ui.DEFAULT_APPEARANCE), mock.Mock())
            self.assertEqual(json_loader.load_config(config_path=path)["appearance"]["theme"], "console")

    def test_picker_digit_picks_one_of_the_newer_looks(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "config.json")
            json_loader.save_config({"general": {"logs": 0}}, config_path=path)
            with scripted(["7", ""]):
                main.pick_appearance(path, dict(ui.DEFAULT_APPEARANCE), mock.Mock())
            self.assertEqual(json_loader.load_config(config_path=path)["appearance"]["theme"], "cards")


def scripted(lines):
    """Feeds keys and typed answers through the line-based fallback that tests and pipes get."""
    feed = iter(lines)
    return _Patches(mock.patch("builtins.input", lambda _="": next(feed)), mock.patch("builtins.print"),
                    mock.patch.object(main, "systemd_show", return_value=[{}, {}]))


class _Patches:
    def __init__(self, *patches):
        self.patches = patches

    def __enter__(self):
        for patch in self.patches:
            patch.start()

    def __exit__(self, *exc):
        for patch in reversed(self.patches):
            patch.stop()


class KeysTest(unittest.TestCase):
    def test_escape_sequences_and_plain_keys(self):
        self.assertEqual(ui.decode_key(b"\x1b[A"), "up")
        self.assertEqual(ui.decode_key(b"\x1bOB"), "down")
        self.assertEqual(ui.decode_key(b"\r"), "enter")
        self.assertEqual(ui.decode_key(b"\x1b"), "esc")
        self.assertEqual(ui.decode_key(b"Q"), "q")
        self.assertEqual(ui.decode_key("ś".encode()), "ś")
        self.assertEqual(ui.decode_key(b"\x1b[1;5A"), "")
        with self.assertRaises(KeyboardInterrupt):
            ui.decode_key(b"\x03")

    def test_held_arrow_yields_every_press(self):
        buf, keys = b"\x1b[B\x1b[B\x1b[1;5A" + "ś".encode() + b"\rq", []
        while buf:
            key, buf = ui.split_key(buf)
            keys.append(ui.decode_key(key))
        self.assertEqual(keys, ["down", "down", "", "ś", "enter", "q"])

    def test_cursor_wraps_and_jumps_columns(self):
        self.assertEqual(ui.move("down", 7, 8), 0)
        self.assertEqual(ui.move("up", 0, 8), 7)
        self.assertEqual(ui.move("right", 1, 8, ui.nav_step("timeline", 8)), 5)
        self.assertEqual(ui.move("x", 3, 8), 3)

    def test_selected_row_is_marked_in_every_look(self):
        for theme in ui.THEMES:
            text = ui.render_menu(ui.Painter(theme, "none"), CTX, STATES, main.MENU_ITEMS, 2)
            self.assertIn("▸", text, theme)


class EditorTest(unittest.TestCase):
    def run_editor(self, keys, config=None):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = os.path.join(tmp.name, "config.json")
        config = config or {"general": {"default_provider": "ovh", "logs": 0},
                            "providers": {"ovh": {}, "cloudflare": {}},
                            "domains": [{"domain": "a.pl", "email": "admin@a.pl", "provider": "ovh"}]}
        json_loader.save_config(config, config_path=path)
        with scripted(keys):
            main.edit_config_interactive(path, QUIET, dict(ui.DEFAULT_APPEARANCE))
        return json_loader.load_config(config_path=path)

    def test_notify_cmd_is_set_checked_and_cleared(self):
        cmd = "curl -fsS --data-binary @- https://ntfy.sh/xcert"
        saved = self.run_editor(["a", "mail -s 'open", "a", cmd, "s"])  # unclosed quote is rejected
        self.assertEqual(saved["general"]["notify_cmd"], cmd)
        self.assertNotIn("post_renewal_cmd", saved["general"])  # the other command stays untouched
        cleared = self.run_editor(["a", "-", "s"], saved)
        self.assertEqual(cleared["general"]["notify_cmd"], "")

    def test_add_remove_and_command_without_typing_numbers(self):
        saved = self.run_editor(["n", "b.pl", "", "2",  # add b.pl, keep the email, pick cloudflare
                                 "1", "u", "t",          # cursor on a.pl, remove it
                                 "o", "systemctl reload nginx",
                                 "s"])
        self.assertEqual(saved["domains"], [{"domain": "b.pl", "email": "admin@a.pl", "provider": "cloudflare"}])
        self.assertEqual(saved["general"]["post_renewal_cmd"], "systemctl reload nginx")
        self.assertEqual(list(saved["providers"]), ["ovh", "cloudflare"])

    def test_enter_on_a_domain_offers_its_actions(self):
        saved = self.run_editor(["", "2", "cloudflare", "s"])  # Enter, "change provider", typed name
        self.assertEqual(saved["domains"][0]["provider"], "cloudflare")

    def test_quit_asks_before_dropping_changes(self):
        saved = self.run_editor(["l", "q", "n", "q", "t"])  # keep editing once, then discard
        self.assertEqual(saved["general"]["logs"], 0)

    def test_invalid_domain_is_rejected(self):
        saved = self.run_editor(["n", "not a domain", "x@a.pl", "s"])
        self.assertEqual([d["domain"] for d in saved["domains"]], ["a.pl"])


class MenuTest(unittest.TestCase):
    def test_arrows_and_enter_run_the_item_under_the_cursor(self):
        config = {"general": {}, "providers": {}, "domains": []}
        with scripted(["down", "", "q"]), mock.patch.object(main, "collect_cert_states", return_value=[]), \
                mock.patch.object(main, "generate_cert_report", return_value="/tmp/r.json") as report:
            main.interactive_menu(config, QUIET, "config.json", dict(ui.DEFAULT_APPEARANCE))
        report.assert_called_once()  # the cursor starts on "list", one down is "report"

    def test_issue_checks_input_and_uses_the_renewal_screen(self):
        config = {"general": {"default_provider": "ovh"}, "providers": {"ovh": {}, "cloudflare": {}},
                  "domains": [{"domain": "a.pl", "email": "admin@a.pl"}]}
        # A bad domain stops before the email and provider questions, Enter keeps the suggested email.
        with scripted(["w", "not a domain", "w", "b.pl", "", "2", "q"]), \
                mock.patch.object(main, "collect_cert_states", return_value=[]), \
                mock.patch.object(main, "cert_auto", return_value=[("b.pl", True)]) as run:
            main.interactive_menu(config, QUIET, "config.json", dict(ui.DEFAULT_APPEARANCE))
        run.assert_called_once()
        self.assertEqual(run.call_args.args[0]["domains"],
                         [{"domain": "b.pl", "email": "admin@a.pl", "provider": "cloudflare"}])


if __name__ == "__main__":
    unittest.main()


class CheckScreenTest(unittest.TestCase):
    def test_rows_follow_what_validation_logged(self):
        config = {"general": {"post_renewal_cmd": "echo 'open", "default_provider": "ovh"},
                  "domains": [{"domain": "a.pl", "email": "x@a.pl", "provider": "broken"}],
                  "providers": {"ovh": {"cmd_template": "certbot", "credentials_path": "secrets/ovh.ini"},
                                "broken": {}}}
        checks = []
        self.assertFalse(main.validate_config(config, QUIET, None, checks))
        rows = {label: (lvl, detail) for lvl, label, detail in checks}
        self.assertEqual(rows["provider ovh"], ("ok", "secrets/ovh.ini"))
        self.assertEqual(rows["provider broken"][0], "err")
        self.assertIn("missing 'cmd_template'", rows["provider broken"][1])
        self.assertEqual(rows["post_renewal_cmd, notify_cmd"][0], "err")
        self.assertEqual(rows["language and look"][0], "ok")

    def test_unused_providers_need_no_credentials(self):
        config = {"general": {"default_provider": "ovh"}, "domains": [{"domain": "a.pl", "email": "x@a.pl"}],
                  "providers": {"ovh": {"cmd_template": "certbot"},
                                "cloudflare": {"cmd_template": "certbot {credentials}",
                                               "credentials_path": "/nonexistent/cloudflare.ini"}}}
        checks = []
        self.assertTrue(main.validate_config(config, QUIET, None, checks))
        self.assertIn(("ok", "unused providers (1)", "cloudflare"), checks)


class ScreenSizeTest(unittest.TestCase):
    def tearDown(self):
        ui.set_screen(80, 0)

    def test_wide_window_stretches_bars_and_short_window_scrolls_lists(self):
        config = {"general": {"default_provider": "ovh", "logs": 1},
                  "domains": [{"domain": f"site{i}.example.pl", "email": "a@example.pl"} for i in range(30)]}
        ui.set_screen(132, 24)
        for theme in ui.THEMES:
            p = ui.Painter(theme, "none")
            for sel in (0, 17, 29, 33):
                text = ui.render_editor(p, CTX, config, "/opt/xcert/config/config.json", sel,
                                        main.EDITOR_ITEMS, main.DOMAIN_ITEMS)
                lines = text.split("\n")
                self.assertLessEqual(len(lines), 23, (theme, sel))
                self.assertTrue(all(ui.visible_len(line) <= 132 for line in lines), (theme, sel))
                if sel < 30:
                    self.assertIn(f"site{sel}.example.pl", text, (theme, sel))
            menu = ui.render_menu(p, CTX, STATES * 5, main.MENU_ITEMS, 0)
            self.assertLessEqual(len(menu.split("\n")), 23, theme)
        bar = ui.render_menu(ui.Painter("console", "none"), CTX, STATES, main.MENU_ITEMS, 0).split("\n")[0]
        self.assertEqual(ui.visible_len(bar), 132)

    def test_hidden_cursor_comes_back_on_any_exit(self):
        # --status, --install and --update draw screens without the menu's own cleanup
        with mock.patch.object(ui, "interactive", return_value=True), mock.patch("sys.stdout", io.StringIO()), \
                mock.patch.object(ui, "_cursor_hidden", False), mock.patch("atexit.register") as register:
            ui.clear()
            ui.clear()
        register.assert_called_once_with(ui.show_cursor)

    def test_narrow_window_cuts_lines_without_breaking_colours(self):
        line = ui.Painter("ledger", "16").fg("ok", "x" * 50) + "y" * 50
        cut = ui.cut(line, 60)
        self.assertEqual(ui.visible_len(cut), 60)
        self.assertTrue(cut.endswith("\033[0m"))

    def test_screen_totals_and_no_emoji(self):
        for theme in ui.THEMES:
            text = ui.render_check(ui.Painter(theme, "none"), CTX, CHECKS, "/opt/xcert/config/config.json")
            self.assertIn("errors 1", text, theme)
            self.assertIn("warnings 1", text, theme)
        self.assertEqual(ui.note_text("❌ Failed: a.pl (rc=1)"), "Failed: a.pl (rc=1)")
        self.assertEqual(ui.note_text("⏭️  Skipped a.pl"), "Skipped a.pl")


class LiveRenewalTest(unittest.TestCase):
    def test_live_run_prints_one_line_per_certificate(self):
        config = {"general": {"logs": 0, "sleep_between_domains_seconds": 0, "renew_threshold_days": 30},
                  "providers": {"ovh": {"cmd_template": "certbot"}},
                  "domains": [{"domain": "a.pl", "email": "x@a.pl"}, {"domain": "b.pl", "email": "x@a.pl"}]}
        out = io.StringIO()
        with mock.patch.object(main, "cert_days_left", side_effect=[60, 5]),                 mock.patch.object(main, "cert_for_domains", return_value=True),                 mock.patch.object(main, "systemd_show", return_value=[{}, {}]), mock.patch("sys.stdout", out):
            main.cert_auto(config, QUIET, live=True)
        text = ui.ANSI_RE.sub("", out.getvalue())
        self.assertIn("skipped, valid for 60 more days", text)
        self.assertIn("renewing via ovh ...", text)
        self.assertIn("done via ovh", text)
        self.assertIn("renewed 1   skipped 1   failed 0", text)  # a still-valid certificate is not "renewed"

    def test_summary_counts_skipped_apart_in_every_look(self):
        results = [("a.pl", True), ("b.pl", True), ("c.pl", False)]
        for theme in ui.THEMES:
            text = ui.render_summary(ui.Painter(theme, "none"), CTX, results, [(0, 0), (0, 40), (40, 5)], 45, ["a.pl"])
            self.assertIn("skipped 1", text, theme)
            self.assertNotIn(" 2 ", text.replace("skipped 1", ""), theme)
