import json
import logging
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import main  # noqa: E402

LOGGER = logging.getLogger("xcert-test")
LOGGER.disabled = True
SETUP_LOG = logging.getLogger("xcert-test-setup")  # enabled: install and status rows come from what it logs
SETUP_LOG.propagate = False
SETUP_LOG.addHandler(logging.NullHandler())

PROVIDERS = {"cloudflare": {"cmd_template": "certbot certonly --dns-cloudflare --dns-cloudflare-credentials "
                                            "{credentials} --non-interactive",
                            "credentials_path": "/opt/my xcert/secrets/cf.ini"}}


def general(**extra):
    base = {"logs": 0, "renew_threshold_days": 30, "max_retries": 3, "base_backoff_seconds": 1,
            "backoff_jitter_seconds": 0, "post_renewal_cmd": "systemctl reload nginx"}
    base.update(extra)
    return base


class CertbotRun(unittest.TestCase):
    """cert_for_domains with certbot, the clock and the filesystem faked out."""

    def run_cert(self, results, days_left=None, domains=("example.com", "www.example.com"), **extra):
        calls = []

        def fake_run(cmd, *args, **kwargs):
            calls.append(cmd)
            return results.pop(0) if cmd[0] == "certbot" else (0, "", "")

        with mock.patch.object(main, "run_command", side_effect=fake_run), \
                mock.patch.object(main, "validate_provider_runtime", return_value=True), \
                mock.patch.object(main, "cert_days_left", return_value=days_left), \
                mock.patch.object(main.time, "sleep") as sleep:
            ok = main.cert_for_domains(list(domains), "admin@example.com", "cloudflare", general(**extra),
                                       PROVIDERS, LOGGER)
        return ok, calls, sleep

    def test_bad_cloudflare_token_is_not_retried(self):
        error = ("Unable to determine zone_id for example.com using zone names: ['example.com']. "
                 "Please confirm the domain is associated with the supplied Cloudflare account. "
                 "The error from Cloudflare was: 9109 Invalid access token")
        ok, calls, _ = self.run_cert([(1, "", error)])
        self.assertFalse(ok)
        self.assertEqual(len(calls), 1)

    def test_rate_limit_retries_without_sleeping_after_the_last_attempt(self):
        limited = (1, "", "Error 971: Please wait and consider throttling your request speed")
        ok, calls, sleep = self.run_cert([limited, limited, limited])
        self.assertFalse(ok)
        self.assertEqual(len(calls), 3)
        self.assertEqual(sleep.call_count, 2)

    def test_credentials_path_with_space_stays_one_argument(self):
        _, calls, _ = self.run_cert([(0, "", "")])
        cmd = calls[0]
        self.assertEqual(cmd[cmd.index("--dns-cloudflare-credentials") + 1],
                         main.resolve_project_path("/opt/my xcert/secrets/cf.ini"))

    def test_lineage_is_pinned_and_due_certificate_forced(self):
        _, calls, _ = self.run_cert([(0, "", "")], days_left=20, domains=("*.example.com", "example.com"))
        cmd = calls[0]
        self.assertEqual(cmd[cmd.index("--cert-name") + 1], "example.com")
        self.assertIn("--force-renewal", cmd)
        _, calls, _ = self.run_cert([(0, "", "")], days_left=None)
        self.assertNotIn("--force-renewal", calls[0])

    def test_valid_certificate_is_skipped_without_certbot(self):
        ok, calls, _ = self.run_cert([], days_left=60)
        self.assertTrue(ok)
        self.assertEqual(calls, [])

    def test_post_renewal_cmd_failure_fails_the_certificate(self):
        def fake_run(cmd, *args, **kwargs):
            return (0, "", "") if cmd[0] == "certbot" else (1, "", "nginx: bad config")

        with mock.patch.object(main, "run_command", side_effect=fake_run), \
                mock.patch.object(main, "validate_provider_runtime", return_value=True), \
                mock.patch.object(main, "cert_days_left", return_value=None):
            self.assertFalse(main.cert_for_domains(["example.com"], "admin@example.com", "cloudflare",
                                                   general(), PROVIDERS, LOGGER))

    def test_dry_run_and_not_due_skip_post_renewal_cmd(self):
        _, calls, _ = self.run_cert([(0, "", "")], dry_run=1)
        self.assertEqual([c[0] for c in calls], ["certbot"])
        ok, calls, _ = self.run_cert([(0, "Certificate not yet due for renewal; no action taken.", "")])
        self.assertTrue(ok)
        self.assertEqual([c[0] for c in calls], ["certbot"])

    def test_unparsable_post_renewal_cmd_fails_cleanly(self):
        ok, calls, _ = self.run_cert([(0, "", "")], post_renewal_cmd="systemctl reload 'nginx")
        self.assertFalse(ok)
        self.assertEqual(calls, [])
        config = {"general": general(post_renewal_cmd="echo 'x"), "providers": {}, "domains": []}
        self.assertFalse(main.validate_config(config, LOGGER))


class SharedCertName(unittest.TestCase):
    def test_groups_sharing_a_certificate_name_are_rejected(self):
        domains = [{"domain": "example.com", "email": "a@example.com", "provider": "ovh"},
                   {"domain": "*.example.com", "email": "a@example.com", "provider": "cloudflare"}]
        config = {"general": general(), "providers": {"ovh": {"cmd_template": "certbot"},
                                                      "cloudflare": {"cmd_template": "certbot"}},
                  "domains": domains}
        self.assertFalse(main.validate_config(config, LOGGER))
        with mock.patch.object(main, "cert_for_domains") as run, mock.patch.object(main.ui, "render_summary", return_value=""):
            results = main.cert_auto(config, LOGGER)
        run.assert_not_called()
        self.assertEqual([ok for _, ok in results], [False, False])


class Validation(unittest.TestCase):
    def test_domains(self):
        for good in ("example.com", "*.example.com", "a-b.example.co.uk"):
            self.assertTrue(main.is_valid_domain(good), good)
        for bad in ("foo.*.example.com", "*.*.example.com", "bad-.com", "-bad.com", "example.com\n", "localhost", ""):
            self.assertFalse(main.is_valid_domain(bad), repr(bad))

    def test_emails(self):
        self.assertTrue(main.is_valid_email("admin@example.com"))
        for bad in ("admin@example.com\n", "admin@example", "", None):
            self.assertFalse(main.is_valid_email(bad), repr(bad))


class Output(unittest.TestCase):
    def test_certbot_output_reaches_console_without_file_logs(self):
        with mock.patch.object(main.subprocess, "run",
                               return_value=mock.Mock(returncode=0, stdout="issued\n", stderr="")):
            logger = mock.Mock()
            main.run_command(["certbot"], use_logs=False, logger=logger)
            logger.info.assert_called_once_with("STDOUT: issued")



class FailureNotice(unittest.TestCase):
    def test_notice_goes_to_notify_cmd_on_stdin(self):
        config = {"general": {"notify_cmd": "mail -s 'xCert failed' admin@example.com"}}
        sent = mock.Mock(returncode=0, stderr="")
        with mock.patch.object(main, "journal_tail", return_value="Failed: example.com (rc=1)"), \
                mock.patch.object(main.subprocess, "run", return_value=sent) as run:
            self.assertTrue(main.send_failure_notice(config, LOGGER))
        cmd, kwargs = run.call_args[0][0], run.call_args[1]
        self.assertEqual(cmd, ["mail", "-s", "xCert failed", "admin@example.com"])
        self.assertIn("certificate renewal failed", kwargs["input"])
        self.assertIn("Failed: example.com (rc=1)", kwargs["input"])

    def test_failed_or_missing_notify_cmd(self):
        with mock.patch.object(main.subprocess, "run") as run:
            self.assertTrue(main.send_failure_notice({"general": {}}, LOGGER))
            run.assert_not_called()
        with mock.patch.object(main, "journal_tail", return_value=""), \
                mock.patch.object(main.subprocess, "run", return_value=mock.Mock(returncode=1, stderr="no mta")):
            self.assertFalse(main.send_failure_notice({"general": {"notify_cmd": "mail x"}}, LOGGER))


class Report(unittest.TestCase):
    def test_report_follows_grouped_certificates(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = {
                "general": {"certbot_config_dir": tmp, "report": os.path.join(tmp, "report.json")},
                "domains": [{"domain": "example.com", "email": "a@example.com", "provider": "ovh"},
                            {"domain": "www.example.com", "email": "a@example.com", "provider": "ovh"},
                            {"domain": "old.example.org", "email": "a@example.com", "provider": "ovh"},
                            {"domain": "broken.example.net"}],
            }
            for name in ("example.com", "old.example.org"):
                os.makedirs(os.path.join(tmp, "live", name))
                open(os.path.join(tmp, "live", name, "fullchain.pem"), "w").close()
            now = datetime.now(timezone.utc)
            expiry = {"example.com": now + timedelta(days=40, hours=1), "old.example.org": now - timedelta(days=2)}
            with mock.patch.object(main, "get_cert_expiry",
                                   side_effect=lambda path: expiry[os.path.basename(os.path.dirname(path))]):
                with open(main.generate_cert_report(config, LOGGER), encoding="utf-8") as f:
                    domains = json.load(f)["domains"]
        self.assertEqual(domains["www.example.com"]["status"], "valid")
        self.assertEqual(domains["www.example.com"]["certificate"], "example.com")
        self.assertEqual(domains["old.example.org"]["status"], "expired")
        self.assertEqual(domains["broken.example.net"]["status"], "missing")



class Automation(unittest.TestCase):
    SHOW = ("LoadState=loaded\nActiveState=active\nUnitFileState=enabled\nResult=success\n"
            "NextElapseUSecRealtime=Mon 2026-11-09 01:23:45 CET\n\n"
            "LoadState=loaded\nActiveState=failed\nUnitFileState=static\nResult=exit-code\n"
            "ExecMainStartTimestamp=Mon 2026-11-02 01:40:02 CET\n")

    def test_timer_and_last_run_come_from_systemctl_show(self):
        with mock.patch.object(main.subprocess, "run", return_value=mock.Mock(stdout=self.SHOW)):
            auto = main.automation()
        self.assertEqual(auto["next_run"], datetime(2026, 11, 9, 1, 23, 45))
        self.assertEqual(auto["last_run"], datetime(2026, 11, 2, 1, 40, 2))
        self.assertFalse(auto["last_ok"])
        self.assertFalse(auto["running"])
        self.assertTrue(auto["enabled"])

    def test_no_systemd_means_no_timer(self):
        with mock.patch.object(main.subprocess, "run", side_effect=FileNotFoundError):
            auto = main.automation()
        self.assertIsNone(auto["next_run"])
        self.assertTrue(auto["last_ok"])
        self.assertFalse(auto["loaded"])


class Setup(unittest.TestCase):
    """--install, --uninstall and --status against a temporary /etc, with every command faked."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.etc = tmp.name
        paths = {"units": tmp.name, "motd": os.path.join(tmp.name, "99-cert-status"),
                 "logrotate": os.path.join(tmp.name, "logrotate-xcert"), "xcert": os.path.join(tmp.name, "xcert"),
                 "certbot": os.path.join(tmp.name, "certbot")}
        self.config_path = os.path.join(tmp.name, "config.json")
        self.commands = []
        self.plugins = "* dns-cloudflare\n"

        self.heads = ["abc1234\n", "abc1234\n"]  # HEAD before and after git pull
        self.pull = (0, "Already up to date.", "")

        def fake_run(cmd, *args, **kwargs):
            self.commands.append(cmd)
            if "rev-parse" in cmd:
                return 0, self.heads.pop(0), ""
            if "pull" in cmd:
                return self.pull
            if "--format=freeze" in cmd:
                return 0, "acme==3.0.0\ncertbot==3.0.0\ncertbot-dns-ovh==3.0.0\n", ""
            return (0, self.plugins, "") if cmd[1:2] == ["plugins"] else (0, "certbot 3.0.0", "")

        for patch in (mock.patch.dict(main.SYSTEM_PATHS, paths), mock.patch.object(main, "is_root", return_value=True),
                      mock.patch.object(main, "run_command", side_effect=fake_run),
                      mock.patch.object(main.os, "symlink"), mock.patch.object(main.os, "chmod"),
                      mock.patch.object(main, "systemd_show", return_value=[{}, {}])):
            patch.start()
            self.addCleanup(patch.stop)

    def which(self, *found):
        return mock.patch.object(main.shutil, "which", side_effect=lambda name: f"/usr/bin/{name}"
                                 if name in found else None)

    def test_install_creates_config_copies_files_and_starts_the_timer(self):
        checks = []
        with self.which("certbot", "openssl"):
            self.assertTrue(main.install(self.config_path, SETUP_LOG, checks))
        self.assertTrue(os.path.isfile(self.config_path))
        for source, target, _ in main.shipped_files():
            with open(target, encoding="utf-8") as f:
                self.assertEqual(f.read(), main.rendered(source))
        with open(os.path.join(self.etc, "xcert.service"), encoding="utf-8") as f:
            self.assertIn(f"ExecStart=/usr/bin/python3 {main.XCERT_ROOT}", f.read())  # this checkout, not /opt/xcert
        self.assertIn(["systemctl", "enable", "--now", "xcert.timer"], self.commands)
        self.assertEqual(checks[0][0], "warn")  # config created from the example: add your domains

    def test_install_adds_missing_plugins_the_way_certbot_came(self):
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump({"general": {"default_provider": "ovh"}, "providers": {"ovh": {}, "cloudflare": {}},
                       "domains": [{"domain": "a.pl", "email": "x@a.pl", "provider": "cloudflare"}]}, f)
        with self.which("certbot", "openssl"), mock.patch.object(main, "certbot_origin", return_value="snap"):
            main.install(self.config_path, SETUP_LOG, [])
        self.assertIn(["snap", "install", "certbot-dns-ovh"], self.commands)
        self.assertNotIn(["snap", "install", "certbot-dns-cloudflare"], self.commands)

    def test_install_puts_certbot_into_a_venv_when_missing(self):
        with self.which("openssl"):
            main.install(self.config_path, SETUP_LOG, [])
        pip = os.path.join(main.CERTBOT_VENV, "bin", "pip")
        install = next(cmd for cmd in self.commands if cmd[:3] == [pip, "install", "certbot"])
        self.assertIn("certbot-dns-ovh", install)  # the example's default provider

    def test_install_needs_root(self):
        checks = []
        with mock.patch.object(main, "is_root", return_value=False):
            self.assertFalse(main.install(self.config_path, SETUP_LOG, checks))
        self.assertEqual(checks[0][0], "err")
        self.assertFalse(os.path.exists(self.config_path))

    def test_status_names_what_is_missing(self):
        config = {"general": {"default_provider": "ovh"}, "providers": {"ovh": {}}, "domains": []}
        checks = []
        with self.which("certbot"):
            self.assertFalse(main.system_status(config, SETUP_LOG, checks))
        rows = {label: (lvl, detail) for lvl, label, detail in checks}
        self.assertEqual(rows["DNS plugins"][0], "err")
        self.assertIn("dns-ovh", rows["DNS plugins"][1])
        self.assertEqual(rows["openssl"][0], "err")
        self.assertEqual(rows["xcert.timer"][0], "err")
        self.assertEqual(rows["failure notices"][0], "warn")

    def run_update(self):
        checks = []
        with self.which("certbot", "openssl"), mock.patch.object(main, "certbot_origin", return_value="venv"), \
                mock.patch.object(main.os, "execv") as execv, mock.patch.dict(os.environ):
            os.environ.pop(main.UPDATED_FROM_ENV, None)
            ok = main.update(self.config_path, SETUP_LOG, checks)
            restarted_from = os.environ.get(main.UPDATED_FROM_ENV)
        return ok, checks, execv, restarted_from

    def test_update_upgrades_certbot_and_its_plugins_then_installs(self):
        ok, checks, execv, _ = self.run_update()
        self.assertTrue(ok)
        execv.assert_not_called()
        pip = os.path.join(main.CERTBOT_VENV, "bin", "pip")
        self.assertIn([pip, "install", "--upgrade", "certbot", "certbot-dns-ovh"], self.commands)
        self.assertIn(["systemctl", "enable", "--now", "xcert.timer"], self.commands)
        self.assertEqual(checks[0][1:], ("git pull", "already up to date"))

    def test_update_restarts_with_pulled_code(self):
        self.heads = ["abc1234\n", "def5678\n"]
        _, _, execv, restarted_from = self.run_update()
        execv.assert_called_once()
        self.assertEqual(execv.call_args.args[1][1:3], [os.path.join(main.SCRIPT_DIR, "main.py"), *sys.argv[1:2]])
        self.assertEqual(restarted_from, f"{main.VERSION} (abc1234)")

    def test_restarted_update_skips_the_pull(self):
        checks = []
        with self.which("certbot", "openssl"), mock.patch.dict(os.environ, {main.UPDATED_FROM_ENV: "1.7.0 (abc1234)"}):
            main.update(self.config_path, SETUP_LOG, checks)
            self.assertNotIn(main.UPDATED_FROM_ENV, os.environ)  # a later --update pulls again
        self.assertFalse(any("pull" in cmd for cmd in self.commands))
        self.assertEqual(checks[0][2], f"1.7.0 (abc1234) -> {main.VERSION}")

    def test_diverged_checkout_stops_before_touching_the_system(self):
        self.pull = (128, "", "fatal: Not possible to fast-forward, aborting.")
        ok, checks, _, _ = self.run_update()
        self.assertFalse(ok)
        self.assertIn("fast-forward", checks[-1][2])
        self.assertFalse(any(cmd[0] == "systemctl" for cmd in self.commands))

    def test_uninstall_removes_only_what_install_wrote(self):
        with self.which("certbot", "openssl"):
            main.install(self.config_path, SETUP_LOG, [])
        self.assertTrue(main.uninstall(SETUP_LOG, []))
        for _, target, _ in main.shipped_files():
            self.assertFalse(os.path.exists(target))
        self.assertTrue(os.path.isfile(self.config_path))


if __name__ == "__main__":
    unittest.main()
