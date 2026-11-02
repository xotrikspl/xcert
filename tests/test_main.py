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


if __name__ == "__main__":
    unittest.main()
