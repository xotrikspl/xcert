import argparse
import json
import logging
import os
import random
import re
import shlex
import shutil
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import i18n
import ui
from i18n import t
from json_loader import load_config, save_config


VERSION = "1.8.1"

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
XCERT_ROOT = os.path.dirname(SCRIPT_DIR)
DEFAULT_CONFIG_PATH = os.path.join(XCERT_ROOT, "config", "config.json")

# ======================
# RATE LIMIT SIGNATURES
# ======================
RATE_LIMIT_SIGNATURES: Dict[str, List[str]] = {
    "generic": [
        "http/2 429",
        "too many requests",
        "rate limit",
        "consider throttling",
    ],
    "cloudflare": [
        "error 971",
    ],
    "ovh": [
        "http 429",
    ],
}

# ======================
# PATH RESOLUTION
# ======================
def resolve_project_path(path: Optional[str]) -> Optional[str]:
    """Returns an absolute path. Relative paths are resolved from XCERT_ROOT."""
    if not path:
        return path
    expanded = os.path.expanduser(path)
    if os.path.isabs(expanded):
        return expanded
    return os.path.abspath(os.path.join(XCERT_ROOT, expanded))


def general_path(general_config: Dict, key: str, default: str) -> str:
    return resolve_project_path(general_config.get(key, default))

# ======================
# VALIDATION
# ======================
def is_valid_email(email: str) -> bool:
    return re.fullmatch(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", email or "") is not None


DOMAIN_RE = re.compile(r"(?:(?!-)[A-Za-z0-9-]{1,63}(?<!-)\.)+[A-Za-z]{2,63}")


def cert_name(domain: str) -> str:
    """Certbot lineage name for a domain: the domain without a leading wildcard label."""
    return domain[2:] if domain.startswith("*.") else domain


def is_valid_domain(domain: str) -> bool:
    """Validates a domain name (supports: example.com, *.example.com)."""
    if not domain or len(domain) > 253:
        return False
    return DOMAIN_RE.fullmatch(cert_name(domain)) is not None

# ======================
# LOGGING
# ======================
def setup_logger(debug: bool = False, log_file: Optional[str] = None) -> logging.Logger:
    if log_file is None:
        log_file = os.path.join(XCERT_ROOT, "logs", "xcert_debug.log")

    level = logging.DEBUG if debug else logging.INFO
    log_dir = os.path.dirname(log_file)
    if log_dir:
        os.makedirs(log_dir, exist_ok=True)

    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S %d-%m-%Y"
    )

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(formatter)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)

    logger = logging.getLogger("xCert")
    logger.setLevel(level)
    logger.handlers.clear()
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    logger.propagate = False

    return logger

# ======================
# CERTIFICATE HELPERS
# ======================
def get_cert_expiry(cert_path: str) -> Optional[datetime]:
    """Returns the certificate's notAfter as an aware UTC datetime, or None on error."""
    try:
        result = subprocess.run(
            ["openssl", "x509", "-enddate", "-noout", "-in", cert_path],
            capture_output=True, text=True, check=True, timeout=10
        )
        end_str = result.stdout.strip().split("=", 1)[1]
        return datetime.strptime(end_str, "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)
    except Exception:
        return None


def get_cert_days_left(cert_path: str) -> Optional[int]:
    """Returns days until certificate expiry, or None on error."""
    exp = get_cert_expiry(cert_path)
    return None if exp is None else (exp - datetime.now(timezone.utc)).days


def cert_days_left(domain: str, general_config: Dict) -> Optional[int]:
    """Returns days until certificate expiry using the configured certbot_config_dir."""
    cert_path = os.path.join(
        general_path(general_config, "certbot_config_dir", "/etc/letsencrypt"),
        "live", cert_name(domain), "cert.pem",
    )
    return get_cert_days_left(cert_path)

# ======================
# RATE LIMIT
# ======================
def is_retryable_rate_limit(provider_name: str, combined_output: str) -> bool:
    signatures = list(RATE_LIMIT_SIGNATURES["generic"])
    signatures.extend(RATE_LIMIT_SIGNATURES.get(provider_name, []))
    return any(sig in combined_output for sig in signatures)

# ======================
# COMMAND EXECUTION
# ======================
def run_command(
    cmd_list: List[str],
    output_file: Optional[str] = None,
    error_file: Optional[str] = None,
    use_logs: bool = False,
    logger: Optional[logging.Logger] = None,
    timeout: int = 900,
) -> Tuple[int, str, str]:
    try:
        result = subprocess.run(cmd_list, capture_output=True, text=True, timeout=timeout)

        if use_logs and output_file and error_file:
            with open(output_file, "a", encoding="utf-8") as out:
                if result.stdout:
                    out.write(result.stdout)
                    if not result.stdout.endswith("\n"):
                        out.write("\n")
            with open(error_file, "a", encoding="utf-8") as err:
                if result.stderr:
                    err.write(result.stderr)
                    if not result.stderr.endswith("\n"):
                        err.write("\n")

        if logger:
            # With file logs the output is already saved; otherwise show it.
            emit = logger.debug if use_logs else logger.info
            if result.stdout:
                emit(f"STDOUT: {result.stdout.rstrip()}")
            if result.stderr:
                emit(f"STDERR: {result.stderr.rstrip()}")

        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired as e:
        stdout = e.stdout or ""
        stderr = e.stderr or ""
        if logger:
            logger.error(f"Command timed out ({timeout}s): {' '.join(cmd_list)}")
        return 124, stdout, stderr
    except Exception as e:
        if logger:
            logger.critical(f"Error running command: {e}")
        return 999, "", str(e)

# ======================
# PROVIDER VALIDATION
# ======================
def validate_provider_runtime(provider_name: str, provider: Dict, logger: logging.Logger) -> bool:
    cmd_template = provider.get("cmd_template", "")
    credentials = provider.get("credentials_path")

    if "{credentials}" in cmd_template:
        if not credentials:
            logger.error(f"No credentials path configured for provider '{provider_name}'")
            return False

        cred_path = resolve_project_path(credentials)
        if not os.path.isfile(cred_path):
            logger.error(f"Credentials file not found for provider '{provider_name}': {cred_path}")
            return False

        if os.name != "nt":
            mode = os.stat(cred_path).st_mode & 0o777
            if mode & 0o077:
                logger.warning(
                    f"Insecure permissions on {cred_path}: {oct(mode)} "
                    f"(recommended: 0o600, fix: chmod 600 {cred_path})"
                )

    return True

# ======================
# DOMAIN TASK BUILDER
# ======================
def build_cert_tasks(domains_list: List[Dict], default_provider: str) -> List[Dict]:
    """Groups domains sharing the same base domain, email, and provider into single certbot calls."""
    grouped: Dict = {}
    order: List = []

    def base_domain(value: str) -> str:
        clean = cert_name(value)
        return clean[4:] if clean.startswith("www.") else clean

    for entry in domains_list:
        domain = entry.get("domain", "")
        email = entry.get("email", "")
        provider = entry.get("provider", default_provider)
        key = (email, provider, base_domain(domain))

        if key not in grouped:
            grouped[key] = {"email": email, "provider": provider, "domains": []}
            order.append(key)

        if domain not in grouped[key]["domains"]:
            grouped[key]["domains"].append(domain)

    return [grouped[key] for key in order]

def shared_cert_names(tasks: List[Dict]) -> set:
    """Certificate names claimed by more than one task (same base domain, different email or provider)."""
    names = [cert_name(task["domains"][0]) for task in tasks]
    return {name for name in names if names.count(name) > 1}

# ======================
# MULTI-DOMAIN CERTIFICATION
# ======================
def cert_for_domains(
    domains: List[str],
    email: str,
    provider_name: str,
    general_config: Dict,
    providers_config: Dict,
    logger: logging.Logger,
    dry_run: bool = False,
) -> bool:
    domain_label = ", ".join(domains)

    for domain in domains:
        if not is_valid_domain(domain):
            logger.error(f"Invalid domain: {domain}")
            return False

    if not is_valid_email(email):
        logger.error(f"Invalid email for {domain_label}: {email}")
        return False

    name = cert_name(domains[0])
    days_left = cert_days_left(domains[0], general_config)
    renew_threshold_days = int(general_config.get("renew_threshold_days", 30))
    if renew_threshold_days > 0 and days_left is not None and days_left > renew_threshold_days:
        logger.info(
            f"⏭️  Skipped {domain_label}: certificate valid for {days_left} more days "
            f"(threshold: {renew_threshold_days})"
        )
        return True

    if provider_name not in providers_config:
        logger.error(f"Unknown provider '{provider_name}' for: {domain_label}")
        return False

    provider = providers_config[provider_name]
    if not validate_provider_runtime(provider_name, provider, logger):
        return False

    cmd_template = provider["cmd_template"]
    credentials = provider.get("credentials_path")
    if credentials:
        cmd_template = cmd_template.format(credentials=shlex.quote(resolve_project_path(credentials)))
    try:
        full_cmd = shlex.split(cmd_template)
        post_cmd = shlex.split(general_config.get("post_renewal_cmd") or "")
    except ValueError as e:
        logger.error(f"❌ Cannot parse cmd_template or post_renewal_cmd for {domain_label}: {e}")
        return False

    # Pin the lineage xCert reads back from live/<name>/, so a changed domain list updates
    # that certificate instead of creating a stray <name>-0001 copy.
    full_cmd += ["--email", email, "--cert-name", name]
    for domain in domains:
        full_cmd += ["-d", domain]
    if days_left is not None:
        # xCert's threshold already decided the certificate is due. Without this Certbot keeps it
        # until its own 30-day window opens and still exits 0.
        full_cmd += ["--force-renewal", "--renew-with-new-domains"]

    full_cmd += [
        "--config-dir", general_path(general_config, "certbot_config_dir", "/etc/letsencrypt"),
        "--work-dir", general_path(general_config, "certbot_work_dir", "/var/lib/letsencrypt"),
        "--logs-dir", general_path(general_config, "certbot_logs_dir", "/var/log/letsencrypt"),
    ]

    dry = dry_run or int(general_config.get("dry_run", 0)) == 1
    if dry:
        full_cmd += ["--dry-run"]

    plugin_flag_name = provider.get("certbot_plugin_name", f"dns-{provider_name}")
    propagation_seconds = provider.get("propagation_seconds")
    if propagation_seconds is not None:
        full_cmd += [f"--{plugin_flag_name}-propagation-seconds", str(propagation_seconds)]

    max_retries = int(general_config.get("max_retries", general_config.get("retry_count", 3)))
    base_backoff = int(general_config.get("base_backoff_seconds", general_config.get("retry_delay_seconds", 60)))
    jitter_max = int(general_config.get("backoff_jitter_seconds", 15))
    command_timeout = int(general_config.get("command_timeout_seconds", 900))

    use_logs = general_config.get("logs", 1) == 1
    output_file = general_path(general_config, "output", "logs/xcert_output.log") if use_logs else None
    error_file = general_path(general_config, "error", "logs/xcert_error.log") if use_logs else None

    for attempt in range(1, max_retries + 1):
        logger.info(
            f"Certbot: {domain_label} (provider: {provider_name}"
            f"{' [DRY-RUN]' if dry else ''}) [attempt {attempt}/{max_retries}]"
        )

        rc, out, err = run_command(
            full_cmd,
            output_file,
            error_file,
            use_logs,
            logger,
            command_timeout,
        )
        combined = (out + "\n" + err).lower()

        if rc == 0:
            if "not yet due for renewal" in combined:
                logger.info(f"⏭️  Certbot kept the existing certificate for {domain_label} (not yet due)")
                return True
            logger.info(f"✅ Success: {domain_label}")
            return run_post_renewal(post_cmd, domain_label, dry, logger)

        if is_retryable_rate_limit(provider_name, combined):
            if attempt == max_retries:
                break
            jitter = random.randint(0, jitter_max) if jitter_max > 0 else 0
            sleep_s = (base_backoff * attempt) + jitter
            logger.warning(
                f"⏳ Rate-limit ({provider_name}) for {domain_label}. "
                f"Waiting {sleep_s}s before retry..."
            )
            time.sleep(sleep_s)
            continue

        logger.error(f"❌ Failed: {domain_label} (rc={rc})")
        return False

    logger.error(f"❌ Failed: {domain_label}, max retries exceeded (rate-limit?)")
    return False


def run_post_renewal(post_cmd: List[str], domain_label: str, dry: bool, logger: logging.Logger) -> bool:
    """Runs post_renewal_cmd after a renewal; a failed reload fails the certificate so systemd shows it."""
    if not post_cmd:
        return True
    if dry:
        logger.info(f"Dry run: skipping post_renewal_cmd: {' '.join(post_cmd)}")
        return True
    logger.info(f"Running post_renewal_cmd: {' '.join(post_cmd)}")
    rc, _, _ = run_command(post_cmd, logger=logger, timeout=60)
    if rc != 0:
        logger.error(f"❌ Certificate renewed for {domain_label}, but post_renewal_cmd failed (rc={rc})")
        return False
    return True

# ======================
# SINGLE DOMAIN
# ======================
def issue_one(config: Dict, entry: Dict, logger: logging.Logger, dry_run: bool = False,
              painter: Optional[ui.Painter] = None, live: bool = False) -> bool:
    """One domain from the menu or --domain, shown on the same progress screen as a full run."""
    results = cert_auto({**config, "domains": [entry]}, logger, dry_run=dry_run, painter=painter, live=live)
    return bool(results) and all(ok for _, ok in results)


# ======================
# AUTOMATIC CERTIFICATION
# ======================
def cert_auto(
    config: Dict,
    logger: logging.Logger,
    dry_run: bool = False,
    painter: Optional[ui.Painter] = None,
    live: bool = False,
) -> Optional[List[Tuple[str, bool]]]:
    """Renews every due certificate. `live` draws one progress line per certificate instead of console logs."""
    domains_list = config.get("domains", [])
    general = config["general"]
    providers = config["providers"]
    sleep_between = int(general.get("sleep_between_domains_seconds", 10))
    threshold = int(general.get("renew_threshold_days", 30))
    painter = painter or ui.Painter()

    if not domains_list:
        logger.warning("No domains configured in config.json")
        return None

    tasks = build_cert_tasks(domains_list, general.get("default_provider", "ovh"))
    if len(tasks) < len(domains_list):
        logger.info(f"Grouped {len(domains_list)} domains into {len(tasks)} certbot tasks.")

    def step(text: str, final: bool = True) -> None:
        if live:
            # \r and erase-line let the finished line overwrite its "working" version.
            sys.stdout.write("\r\033[K" + text + ("\n" if final else ""))
            sys.stdout.flush()

    results: List[Tuple[str, bool]] = []
    timings: List[Tuple[float, float]] = []
    started = time.monotonic()
    shared = shared_cert_names(tasks)
    if live:
        ctx = build_ui_context(config)
        print(ui.render_head(painter, ctx, t("renewal")))
    with recording(logger, quiet=live) as rec:
        for idx, task in enumerate(tasks):
            task_start = time.monotonic()
            label = ", ".join(task["domains"])
            name = cert_name(task["domains"][0])
            mark = len(rec.records)
            if name in shared:
                # Two groups writing one lineage would swap its domain list on every run.
                logger.error(f"❌ Skipped {label}: certificate name '{name}' is shared with another group")
                results.append((label, False))
                timings.append((task_start - started, 0.0))
                step(ui.render_step(painter, name, "fail", task["provider"], 0,
                                    [r.getMessage() for r in rec.records[mark:]]))
                continue
            days = cert_days_left(task["domains"][0], general)
            skipped = threshold > 0 and days is not None and days > threshold
            if not skipped:
                step(ui.render_step(painter, name, "renew" if days is not None else "issue", task["provider"]), False)
            success = cert_for_domains(
                task["domains"],
                task["email"],
                task["provider"],
                general,
                providers,
                logger,
                dry_run=dry_run,
            )
            results.append((label, success))
            timings.append((task_start - started, time.monotonic() - task_start))
            outcome = "skip" if skipped and success else "ok" if success else "fail"
            step(ui.render_step(painter, name, outcome, task["provider"], time.monotonic() - task_start,
                                [r.getMessage() for r in rec.records[mark:]], days))

            if idx < len(tasks) - 1 and sleep_between > 0 and not skipped:
                logger.info(f"Sleeping {sleep_between}s before next domain...")
                step(painter.dim("  " + t("pause {n} s before the next certificate", n=sleep_between)), False)
                time.sleep(sleep_between)

    print()
    print(ui.render_summary(painter, build_ui_context(config), results, timings, time.monotonic() - started))
    return results

# ======================
# CONFIG VALIDATION
# ======================
class Recorder(logging.Handler):
    """Keeps warnings and errors logged while it is attached, for the on-screen check and renewal lines."""

    def __init__(self) -> None:
        super().__init__(logging.WARNING)
        self.records: List[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


@contextmanager
def recording(logger: logging.Logger, quiet: bool = False):
    """Attaches a Recorder; `quiet` hides the console log meanwhile, because a screen shows the outcome."""
    recorder = Recorder()
    console = [h for h in getattr(logger, "handlers", []) if type(h) is logging.StreamHandler]
    levels = [h.level for h in console]
    logger.addHandler(recorder)
    if quiet:
        for h in console:
            h.setLevel(logging.CRITICAL + 1)
    try:
        yield recorder
    finally:
        logger.removeHandler(recorder)
        for h, lvl in zip(console, levels):
            h.setLevel(lvl)


def row_writer(rec: Recorder, checks: List[Tuple[str, str, str]]):
    """section(label, detail) adds one screen row from what was logged since the previous row."""
    mark = len(rec.records)

    def section(label: str, detail: str = "") -> None:
        nonlocal mark
        new = rec.records[mark:]
        mark = len(rec.records)
        lvl = "err" if any(r.levelno >= logging.ERROR for r in new) else "warn" if new else "ok"
        checks.append((lvl, label, new[-1].getMessage() if new else detail))
    return section


def screen_mode(args: Optional[argparse.Namespace] = None) -> bool:
    """Themed check and renewal screens replace console logs on a terminal, unless --debug asks for logs."""
    return sys.stdout.isatty() and not getattr(args, "debug", False)


def validate_config(
    config: Dict,
    logger: logging.Logger,
    domain_filters: Optional[List[str]] = None,
    checks: Optional[List[Tuple[str, str, str]]] = None,
) -> bool:
    """Checks config and credentials; with `checks`, also fills (level, label, detail) rows for the screen."""
    with recording(logger) as rec:
        return _validate(config, logger, domain_filters, rec, checks if checks is not None else [])


def _validate(config: Dict, logger: logging.Logger, domain_filters: Optional[List[str]],
              rec: Recorder, checks: List[Tuple[str, str, str]]) -> bool:
    valid = True
    section = row_writer(rec, checks)

    if "domains" not in config or "providers" not in config or "general" not in config:
        logger.error("Missing required sections: 'domains', 'providers', or 'general'")
        section(t("config sections"))
        return False
    section(t("config sections"))

    providers = config.get("providers", {})
    general = config.get("general", {})
    default_provider = general.get("default_provider", "ovh")
    normalized_filters: Optional[set] = None

    if domain_filters:
        normalized_filters = {d.strip() for d in domain_filters if d and d.strip()} or None

    domains_list = config.get("domains", [])
    selected_domains: List[Dict] = []
    selected_providers: set = set()

    for entry in domains_list:
        domain = entry.get("domain", "")
        provider_name = entry.get("provider", default_provider)
        if normalized_filters and domain not in normalized_filters:
            continue
        selected_domains.append(entry)
        selected_providers.add(provider_name)

    if normalized_filters and not selected_domains:
        logger.error(f"None of the specified domains found in config: {', '.join(sorted(normalized_filters))}")
        section(t("selected domains"))
        return False

    # Only providers a domain uses (plus the default, for new domains and --domain) need credentials;
    # the example config lists every supported provider and most of them stay unused.
    configured_default = {general["default_provider"]} if general.get("default_provider") and normalized_filters is None else set()
    providers_to_check = sorted(selected_providers | configured_default)
    unused = sorted(set(providers) - set(providers_to_check))

    for provider_name in providers_to_check:
        provider = providers.get(provider_name)
        if provider is None:
            logger.error(f"Unknown provider '{provider_name}' referenced in config")
            valid = False
        elif not provider.get("cmd_template"):
            logger.error(f"Provider '{provider_name}': missing 'cmd_template'")
            valid = False
        elif not validate_provider_runtime(provider_name, provider, logger):
            valid = False
        section(t("provider {name}", name=provider_name), (provider or {}).get("credentials_path") or "")
    if unused and normalized_filters is None:
        logger.debug(f"Not checked, no domain uses them: {', '.join(unused)}")
        checks.append(("ok", t("unused providers ({n})", n=len(unused)), ", ".join(unused)))

    for entry in selected_domains:
        domain = entry.get("domain", "")
        email = entry.get("email", "")
        provider_name = entry.get("provider", default_provider)

        if not is_valid_domain(domain):
            logger.error(f"Invalid domain: {domain}")
            valid = False
        if not is_valid_email(email):
            logger.error(f"Invalid email for {domain}: {email}")
            valid = False
        if provider_name not in providers:
            logger.error(f"Unknown provider '{provider_name}' for domain {domain}")
            valid = False
    section(t("domains and emails ({n})", n=len(selected_domains)))

    for name in sorted(shared_cert_names(build_cert_tasks(selected_domains, default_provider))):
        logger.error(f"Certificate name '{name}' is used by more than one group: "
                     f"give these domains the same email and provider")
        valid = False
    section(t("certificate names"))

    for key in ("post_renewal_cmd", "notify_cmd"):
        try:
            shlex.split(general.get(key) or "")
        except ValueError as e:
            logger.error(f"general.{key} cannot be parsed: {e}")
            valid = False
    section("post_renewal_cmd, notify_cmd", general.get("post_renewal_cmd") or "")

    language = general.get("language")
    if language is not None and language not in i18n.LANGUAGES:
        logger.error(f"general.language = '{language}' is not one of: {', '.join(i18n.LANGUAGES)}")
        valid = False

    appearance = config.get("appearance", {})
    for key, allowed in (("theme", ui.THEMES), ("colors", ui.COLOR_MODES), ("background", ui.BACKGROUNDS)):
        value = appearance.get(key)
        if value is not None and value not in allowed:
            logger.error(f"appearance.{key} = '{value}' is not one of: {', '.join(allowed)}")
            valid = False
    section(t("language and look"))

    scope = "entire config" if normalized_filters is None else f"selected domains ({len(selected_domains)})"
    if valid:
        logger.info(f"✅ Validation of {scope} passed")
    else:
        logger.error(f"❌ Validation of {scope} found errors")

    return valid


def check_all(config: Dict, logger: logging.Logger, domain_filters: Optional[List[str]] = None,
              checks: Optional[List[Tuple[str, str, str]]] = None, system: bool = False) -> bool:
    """--validate-config, plus with `system` what --status adds: certbot, plugins, units and the timer."""
    checks = checks if checks is not None else []
    ok = validate_config(config, logger, domain_filters, checks)
    return (system_status(config, logger, checks) if system else True) and ok


def show_check(painter: ui.Painter, config: Dict, logger: logging.Logger, path: str,
               domain_filters: Optional[List[str]] = None, system: bool = False) -> bool:
    """Runs the check with console logs hidden and draws its outcome in the current look."""
    checks: List[Tuple[str, str, str]] = []
    with recording(logger, quiet=True):
        ok = check_all(config, logger, domain_filters, checks, system)
    ui.draw(ui.render_check(painter, build_ui_context(config), checks, path,
                            t("status") if system else t("checking")))
    return ok


# ======================
# CERTIFICATE VALIDITY REPORT
# ======================
def generate_cert_report(config: Dict, logger: logging.Logger) -> str:
    """Writes the JSON validity report, one entry per configured domain."""
    general = config.get("general", {})
    certbot_config_dir = general_path(general, "certbot_config_dir", "/etc/letsencrypt")
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "domains": {}
    }

    tasks = build_cert_tasks(config.get("domains", []), general.get("default_provider", "ovh"))
    # collect_cert_states walks the same tasks in the same order, one state per certificate.
    for task, state in zip(tasks, collect_cert_states(config)):
        cat = ui.category(state, 10 ** 6)
        if cat == "missing":
            entry = {"status": "missing", "certificate": state.name, "error": "Certificate file not found"}
            logger.warning(f"Certificate not found for: {', '.join(task['domains'])}")
        elif cat == "unreadable":
            entry = {"status": "error", "certificate": state.name, "error": "Certificate could not be read"}
            logger.error(f"Error reading certificate: {state.name}")
        else:
            entry = {
                "status": "expired" if cat == "expired" else "valid",
                "certificate": state.name,
                "expires_on": state.expires.isoformat(),
                "days_left": state.days_left,
                "cert_path": os.path.join(certbot_config_dir, "live", state.name, "fullchain.pem"),
            }
        for domain in task["domains"]:
            report["domains"][domain] = entry

    report_path = general_path(general, "report", os.path.join(XCERT_ROOT, "logs", "xcert_report.json"))
    report_dir = os.path.dirname(report_path)
    if report_dir:
        os.makedirs(report_dir, exist_ok=True)
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    logger.info(f"✅ Report saved: {report_path}")
    return report_path

# ======================
# DOMAIN LIST
# ======================
def collect_cert_states(config: Dict) -> List[ui.CertState]:
    """One state per certbot certificate, grouped the same way --auto issues them."""
    general = config.get("general", {})
    certbot_config_dir = general_path(general, "certbot_config_dir", "/etc/letsencrypt")
    # Short label for the list: "systemctl reload nginx" -> "nginx", "docker exec x nginx -s reload" -> "docker x".
    reload_cmd = re.sub(r"^systemctl (reload|restart) |^docker exec (\S+).*$", lambda m: f"docker {m.group(2)}" if m.group(2) else "", general.get("post_renewal_cmd", ""))
    now = datetime.now(timezone.utc)
    states = []
    for task in build_cert_tasks(config.get("domains", []), general.get("default_provider", "ovh")):
        name = cert_name(task["domains"][0])
        cert_path = os.path.join(certbot_config_dir, "live", name, "fullchain.pem")
        present = os.path.isfile(cert_path)
        expires = get_cert_expiry(cert_path) if present else None
        states.append(ui.CertState(
            name=name,
            provider=task["provider"],
            sans=[d for d in task["domains"] if d != name],
            present=present,
            expires=expires,
            days_left=None if expires is None else (expires - now).days,
            reload=reload_cmd,
        ))
    return states


def journal_tail(lines: int = 40) -> str:
    """Last lines of the xcert.service journal, empty when journalctl is unavailable."""
    try:
        return subprocess.run(
            ["journalctl", "-u", "xcert.service", "-n", str(lines), "--no-pager", "-o", "cat"],
            capture_output=True, text=True, timeout=10,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def send_failure_notice(config: Dict, logger: logging.Logger) -> bool:
    """Pipes a failure notice into general.notify_cmd (mail, curl, ...) on stdin."""
    try:
        cmd = shlex.split(config.get("general", {}).get("notify_cmd") or "")
    except ValueError as e:
        logger.error(f"general.notify_cmd cannot be parsed: {e}")
        return False
    if not cmd:
        logger.warning("general.notify_cmd is not set, no notification sent")
        return True

    journal = journal_tail()
    message = "\n".join([
        t("xCert: certificate renewal failed on {host}", host=socket.gethostname()),
        "",
        t("Last lines of the xcert.service journal:") + "\n\n" + journal if journal
        else t("Journal unavailable, check: journalctl -u xcert.service"),
    ]) + "\n"
    try:
        result = subprocess.run(cmd, input=message, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as e:
        logger.error(f"notify_cmd failed: {e}")
        return False
    if result.returncode != 0:
        logger.error(f"notify_cmd failed (rc={result.returncode}): {result.stderr.strip()}")
        return False
    logger.info("Failure notice sent")
    return True


def systemd_show(*units: str) -> List[Dict[str, str]]:
    """A few `systemctl show` properties per unit; empty dicts where systemd is missing (tests, containers)."""
    props = "LoadState,ActiveState,UnitFileState,Result,NextElapseUSecRealtime,ExecMainStartTimestamp"
    try:
        out = subprocess.run(["systemctl", "show", "-p", props, *units],
                             capture_output=True, text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        out = ""
    blocks = [dict(line.split("=", 1) for line in block.splitlines() if "=" in line)
              for block in out.split("\n\n")] if out else []
    return (blocks + [{}] * len(units))[:len(units)]


def systemd_time(value: str) -> Optional[datetime]:
    """'Mon 2026-11-03 01:12:03 CET' as a local datetime, None when empty or unparsable."""
    try:
        return datetime.strptime(" ".join(value.split()[1:3]), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def automation() -> Dict:
    """What the timer does: next run, start and result of the last run, whether a run is going on now."""
    timer, service = systemd_show("xcert.timer", "xcert.service")
    return {
        "loaded": timer.get("LoadState") == "loaded",
        "active": timer.get("ActiveState") == "active",
        "enabled": timer.get("UnitFileState") == "enabled",
        "next_run": systemd_time(timer.get("NextElapseUSecRealtime", "")) if timer.get("ActiveState") == "active"
        else None,
        "last_run": systemd_time(service.get("ExecMainStartTimestamp", "")),
        "last_ok": service.get("Result", "success") == "success",
        "running": service.get("ActiveState") == "activating",
    }


def build_ui_context(config: Dict) -> ui.Context:
    auto = automation()
    return ui.Context(
        version=VERSION,
        host=socket.gethostname().split(".")[0],
        now=datetime.now(),
        threshold=int(config.get("general", {}).get("renew_threshold_days", 30)),
        next_run=auto["next_run"],
        last_run=auto["last_run"],
        last_ok=auto["last_ok"],
        running=auto["running"],
    )


# ======================
# INSTALL AND STATUS
# ======================
CERTBOT_VENV = "/opt/certbot"
# Where --install puts things; tests point these at a temporary directory.
SYSTEM_PATHS = {
    "units": "/etc/systemd/system",
    "motd": "/etc/update-motd.d/99-cert-status",
    "logrotate": "/etc/logrotate.d/xcert",
    "xcert": "/usr/local/bin/xcert",
    "certbot": "/usr/bin/certbot",
}
UNITS = ("xcert.service", "xcert.timer", "xcert-notify.service")
XCERT_BIN = os.path.join(XCERT_ROOT, "bin", "xcert")


def is_root() -> bool:
    return hasattr(os, "geteuid") and os.geteuid() == 0


def shipped_files() -> List[Tuple[str, str, int]]:
    """(file in the repo, where --install puts it, mode) for the units, the MOTD script and logrotate."""
    files = [(os.path.join(XCERT_ROOT, "etc", "systemd", "system", u), os.path.join(SYSTEM_PATHS["units"], u), 0o644)
             for u in UNITS]
    return files + [(os.path.join(XCERT_ROOT, "etc", "update-motd.d", "99-cert-status"), SYSTEM_PATHS["motd"], 0o755),
                    (os.path.join(XCERT_ROOT, "etc", "logrotate.d", "xcert"), SYSTEM_PATHS["logrotate"], 0o644)]


def rendered(source: str) -> str:
    """A shipped file pointed at this checkout instead of /opt/xcert, with Windows line endings dropped."""
    with open(source, encoding="utf-8") as f:
        return f.read().replace("\r\n", "\n").replace("/opt/xcert", XCERT_ROOT)


def is_current(source: str, target: str) -> Optional[bool]:
    """None when `target` is missing, False when it differs from what --install would write."""
    try:
        with open(target, encoding="utf-8") as f:
            return f.read() == rendered(source)
    except OSError:
        return None


def needed_plugins(config: Dict) -> List[str]:
    """Certbot plugins of the providers domains use, plus the default provider."""
    general = config.get("general", {})
    default = general.get("default_provider", "ovh")
    used = {d.get("provider", default) for d in config.get("domains", [])}
    if general.get("default_provider"):
        used.add(default)
    providers = config.get("providers", {})
    return sorted({providers[n].get("certbot_plugin_name", f"dns-{n}") for n in used if n in providers})


def certbot_origin(path: str) -> str:
    """How certbot got here: 'snap', 'venv' (pip into CERTBOT_VENV) or 'system' (apt and others)."""
    real = os.path.realpath(path)
    if path.startswith("/snap/") or os.path.basename(real) == "snap":
        return "snap"
    return "venv" if real.startswith(CERTBOT_VENV + "/") else "system"


def certbot_plugins(path: str, general: Dict) -> Tuple[Optional[set], str]:
    """Plugin names certbot reports, or None and the reason when it cannot be asked."""
    rc, out, err = run_command([path, "plugins",
                                "--config-dir", general_path(general, "certbot_config_dir", "/etc/letsencrypt"),
                                "--work-dir", general_path(general, "certbot_work_dir", "/var/lib/letsencrypt"),
                                "--logs-dir", general_path(general, "certbot_logs_dir", "/var/log/letsencrypt")],
                               timeout=120)
    if rc != 0:
        return None, ((err or out).strip().splitlines() or [f"rc={rc}"])[-1]
    return set(re.findall(r"^\* (\S+)", out, re.M)), ""


def run_step(cmd: List[str], logger: logging.Logger, timeout: int = 900) -> bool:
    """One install command; its output goes to the debug log, a failure logs the last line."""
    logger.debug(f"Running: {' '.join(cmd)}")
    rc, out, err = run_command(cmd, timeout=timeout)
    if rc != 0:
        last = ((err or out).strip().splitlines() or [""])[-1]
        # The reason goes first: screen rows cut long details, and the command is in the debug log anyway.
        logger.error(f"{last or 'failed'} (rc={rc}: {' '.join(cmd)})")
    return rc == 0


def install_certbot(config: Dict, logger: logging.Logger, section) -> None:
    """openssl, certbot and the DNS plugins of the providers in use, picked by how certbot was installed."""
    plugins = needed_plugins(config)
    packages = [f"certbot-{p}" for p in plugins]
    certbot = shutil.which("certbot")
    apt = ["openssl"] if not shutil.which("openssl") else []
    apt += ["python3-venv"] if not certbot else []
    if apt:
        if shutil.which("apt-get"):
            run_step(["apt-get", "update"], logger)
            run_step(["apt-get", "install", "-y", *apt], logger)
        else:
            logger.error(f"Install with your package manager: {' '.join(apt)}")
    section(t("system packages"), ", ".join(apt) or t("nothing missing"))

    if not certbot:
        pip = os.path.join(CERTBOT_VENV, "bin", "pip")
        if (run_step([sys.executable, "-m", "venv", CERTBOT_VENV], logger)
                and run_step([pip, "install", "--upgrade", "pip"], logger)
                and run_step([pip, "install", "certbot", *packages], logger)):
            if os.path.lexists(SYSTEM_PATHS["certbot"]):
                os.remove(SYSTEM_PATHS["certbot"])
            os.symlink(os.path.join(CERTBOT_VENV, "bin", "certbot"), SYSTEM_PATHS["certbot"])
            logger.info(f"Installed certbot and {', '.join(packages) or 'no plugins'} into {CERTBOT_VENV}")
        section("certbot", t("installed into {path}", path=CERTBOT_VENV))
        return
    section("certbot", certbot)

    installed, _ = certbot_plugins(certbot, config.get("general", {}))
    missing = [p for p in plugins if installed is None or p not in installed]
    origin = certbot_origin(certbot)
    if missing and origin == "venv":
        run_step([os.path.join(CERTBOT_VENV, "bin", "pip"), "install", *[f"certbot-{p}" for p in missing]], logger)
    elif missing and origin == "snap":
        run_step(["snap", "set", "certbot", "trust-plugin-with-root=ok"], logger)
        for p in missing:
            if run_step(["snap", "install", f"certbot-{p}"], logger):
                # Store plugins usually connect on their own; connecting again only fails harmlessly.
                run_command(["snap", "connect", "certbot:plugin", f"certbot-{p}"], timeout=120)
    elif missing:
        logger.warning(f"certbot comes from the system packages, install the plugins the same way: "
                       f"apt install {' '.join('python3-certbot-' + p for p in missing)}")
    section(t("DNS plugins"), ", ".join(plugins) or t("none needed"))


def install(config_path: str, logger: logging.Logger, checks: List[Tuple[str, str, str]]) -> bool:
    """Sets up everything around xCert; safe to run again, e.g. after git pull."""
    with recording(logger) as rec:
        section = row_writer(rec, checks)
        if not is_root():
            logger.error("Run --install as root: it writes to /etc and installs packages")
            section(t("permissions"))
            return False

        example = os.path.join(XCERT_ROOT, "config", "config.json.example")
        if not os.path.isfile(config_path):
            shutil.copyfile(example, config_path)
            os.chmod(config_path, 0o600)
            logger.warning(f"Created {config_path} from the example: add your domains with xcert --edit-config")
        section("config.json", config_path)

        for name, mode in (("secrets", 0o700), ("logs", 0o755)):
            path = os.path.join(XCERT_ROOT, name)
            os.makedirs(path, exist_ok=True)
            os.chmod(path, mode)
        for name in os.listdir(os.path.join(XCERT_ROOT, "secrets")):
            os.chmod(os.path.join(XCERT_ROOT, "secrets", name), 0o600)
        section("secrets/, logs/", t("secrets readable by root only"))

        os.chmod(XCERT_BIN, 0o755)
        if os.path.lexists(SYSTEM_PATHS["xcert"]):
            os.remove(SYSTEM_PATHS["xcert"])
        os.symlink(XCERT_BIN, SYSTEM_PATHS["xcert"])
        section(t("xcert command"), SYSTEM_PATHS["xcert"])

        try:
            config = load_config(config_path=config_path)
        except Exception as e:
            logger.error(f"Cannot read {config_path}: {e}")
            config = {}
        install_certbot(config, logger, section)

        for source, target, mode in shipped_files():
            try:
                os.makedirs(os.path.dirname(target), exist_ok=True)  # minimal systems lack update-motd.d
                with open(target, "w", encoding="utf-8", newline="\n") as f:
                    f.write(rendered(source))
                os.chmod(target, mode)
            except OSError as e:
                logger.error(f"Cannot write {target}: {e}")
        section(t("units, MOTD, logrotate"), t("{n} files", n=len(shipped_files())))

        if run_step(["systemctl", "daemon-reload"], logger, 60):
            run_step(["systemctl", "enable", "--now", "xcert.timer"], logger, 60)
        section("xcert.timer", t("enabled"))
        return not any(lvl == "err" for lvl, _, _ in checks)


def uninstall(logger: logging.Logger, checks: List[Tuple[str, str, str]]) -> bool:
    """Undoes --install except config, credentials, logs, certificates and certbot itself."""
    with recording(logger) as rec:
        section = row_writer(rec, checks)
        if not is_root():
            logger.error("Run --uninstall as root: it removes files from /etc")
            section(t("permissions"))
            return False
        run_command(["systemctl", "disable", "--now", "xcert.timer"], timeout=60)  # fails harmlessly when absent
        removed = [target for _, target, _ in shipped_files() if os.path.lexists(target)]
        for target in removed:
            os.remove(target)
        run_step(["systemctl", "daemon-reload"], logger, 60)
        section(t("timer, units, MOTD, logrotate"), t("{n} files removed", n=len(removed)))
        link = SYSTEM_PATHS["xcert"]
        if os.path.islink(link) and os.path.realpath(link) == os.path.realpath(XCERT_BIN):
            os.remove(link)
        section(t("xcert command"), t("removed"))
        checks.append(("ok", t("kept"), t("config, secrets, logs and certificates in {path}", path=XCERT_ROOT)))
        return not any(lvl == "err" for lvl, _, _ in checks)


def system_status(config: Dict, logger: logging.Logger, checks: List[Tuple[str, str, str]]) -> bool:
    """What --status adds to the config check: tools, plugins, installed files and what the timer did."""
    general = config.get("general", {})
    with recording(logger) as rec:
        section = row_writer(rec, checks)
        certbot = shutil.which("certbot")
        if not certbot:
            logger.error("certbot not found: xcert --install")
            section("certbot")
        else:
            rc, out, err = run_command([certbot, "--version"], timeout=60)
            section("certbot", f"{(out or err).strip()} ({certbot_origin(certbot)})")
            installed, why = certbot_plugins(certbot, general)
            missing = [p for p in needed_plugins(config) if installed is not None and p not in installed]
            if installed is None:
                logger.warning(f"Could not list certbot plugins: {why}")
            elif missing:
                logger.error(f"Missing plugins {', '.join(missing)}: xcert --install")
            section(t("DNS plugins"), ", ".join(needed_plugins(config)) or t("none needed"))

        if not shutil.which("openssl"):
            logger.error("openssl not found: xcert --install")
        section("openssl", shutil.which("openssl") or "")

        found = shutil.which("xcert")
        if not found or os.path.realpath(found) != os.path.realpath(XCERT_BIN):
            logger.warning(f"xcert is not linked to {XCERT_BIN}: xcert --install")
        section(t("xcert command"), found or "")

        missing, stale = [], []
        for source, target, _ in shipped_files():
            current = is_current(source, target)
            if current is None and not (target == SYSTEM_PATHS["logrotate"] and general.get("logs", 1) != 1):
                missing.append(os.path.basename(target))
            elif current is False:
                stale.append(os.path.basename(target))
        if stale:
            logger.warning(f"Outdated {', '.join(stale)}: xcert --install")
        if missing:
            logger.error(f"Missing {', '.join(missing)}: xcert --install")
        section(t("units, MOTD, logrotate"), t("installed and current"))

        auto = automation()
        if not auto["loaded"]:
            logger.error("Timer missing: xcert --install")
        elif not (auto["enabled"] and auto["active"]):
            logger.error("Timer off: systemctl enable --now xcert.timer")
        section("xcert.timer", auto["next_run"] and t("next run {when}", when=f"{auto['next_run']:%d.%m %H:%M}") or "")

        if auto["running"]:
            last = t("renewal running now")
        elif auto["last_run"] is None:
            last = t("no run yet")
        else:
            last = f"{auto['last_run']:%d.%m %H:%M}"
            if not auto["last_ok"]:
                journal = [line for line in journal_tail(20).splitlines() if line.strip()]
                logger.error(f"Last run {last} failed: {journal[-1] if journal else 'journalctl -u xcert.service'}")
        section(t("last timer run"), last)

        if not general.get("notify_cmd"):
            logger.warning("notify_cmd empty: no failure notices")
        section(t("failure notices"), general.get("notify_cmd") or "")
        return not any(r.levelno >= logging.ERROR for r in rec.records)


UPDATED_FROM_ENV = "XCERT_UPDATED_FROM"  # set before --update restarts itself with the pulled code


def pull(logger: logging.Logger, section) -> None:
    """git pull --ff-only; when the checkout moved, restarts --update with the new code (os.execv)."""
    previous = os.environ.pop(UPDATED_FROM_ENV, None)
    if previous:
        section("git pull", f"{previous} -> {VERSION}")
        return
    git = ["git", "-C", XCERT_ROOT, "-c", f"safe.directory={XCERT_ROOT}"]
    rc, before, _ = run_command(git + ["rev-parse", "--short", "HEAD"], timeout=30)
    if rc != 0:
        logger.error(f"{XCERT_ROOT} is not a git checkout, update it by hand")
    elif run_step(git + ["pull", "--ff-only"], logger, 300):
        after = run_command(git + ["rev-parse", "--short", "HEAD"], timeout=30)[1]
        if after.strip() != before.strip():
            logger.info(f"Pulled {before.strip()}..{after.strip()}, restarting with the new code")
            os.environ[UPDATED_FROM_ENV] = f"{VERSION} ({before.strip()})"
            sys.stdout.flush()
            os.execv(sys.executable, [sys.executable, os.path.join(SCRIPT_DIR, "main.py"), *sys.argv[1:]])
    section("git pull", t("already up to date"))


def upgrade_certbot(logger: logging.Logger, section) -> None:
    """Newest certbot and every certbot plugin already installed, the same way certbot was installed."""
    certbot = shutil.which("certbot")
    if not certbot:
        return  # --install sets it up next
    origin = certbot_origin(certbot)
    if origin == "venv":
        pip = os.path.join(CERTBOT_VENV, "bin", "pip")
        frozen = run_command([pip, "list", "--format=freeze"], timeout=120)[1]
        packages = {line.split("==")[0] for line in frozen.splitlines() if line.lower().startswith("certbot")}
        run_step([pip, "install", "--upgrade", *sorted(packages | {"certbot"})], logger)
    elif origin == "snap":
        listed = run_command(["snap", "list"], timeout=60)[1]
        snaps = {line.split()[0] for line in listed.splitlines() if line.startswith("certbot")}
        run_step(["snap", "refresh", *sorted(snaps | {"certbot"})], logger)
    else:
        logger.warning("certbot comes from the system packages: update it with apt upgrade")
    out, err = run_command([certbot, "--version"], timeout=60)[1:]
    section(t("certbot update"), (out or err).strip())


def update(config_path: str, logger: logging.Logger, checks: List[Tuple[str, str, str]]) -> bool:
    """git pull, newest certbot and plugins, then everything --install does."""
    with recording(logger) as rec:
        section = row_writer(rec, checks)
        if not is_root():
            logger.error("Run --update as root: it installs packages and writes to /etc")
            section(t("permissions"))
            return False
        pull(logger, section)
        if any(lvl == "err" for lvl, _, _ in checks):
            return False  # a diverged or broken checkout: fix it before touching /etc
        upgrade_certbot(logger, section)
    return install(config_path, logger, checks)


def run_setup(args: argparse.Namespace, logger: logging.Logger) -> bool:
    """--install, --update or --uninstall, drawn as one checklist; install and update end with --status."""
    config_path = resolve_project_path(args.config)
    checks: List[Tuple[str, str, str]] = []
    config: Dict = {}
    with recording(logger, quiet=screen_mode(args)):
        if args.uninstall:
            ok = uninstall(logger, checks)
        else:
            ok = (update if args.update else install)(config_path, logger, checks)
            try:
                config = load_config(config_path=config_path)
            except Exception:
                pass
            if ok and config:
                status: List[Tuple[str, str, str]] = []
                ok = check_all(config, logger, None, status, system=True)
                # Status rows say more than the matching install steps (versions, next run), so they replace them.
                labels = {label for _, label, _ in status}
                checks[:] = [c for c in checks if c[1] not in labels] + status
    if screen_mode(args):
        ui.fit_screen()
        ui.draw(ui.render_check(make_painter(effective_appearance(config, args)), build_ui_context(config), checks,
                                config_path, t("uninstall") if args.uninstall else t("update") if args.update
                                else t("install")))
    return ok


def effective_appearance(config: Dict, args: Optional[argparse.Namespace] = None) -> Dict[str, str]:
    """Saved appearance with one-off CLI overrides on top."""
    appearance = dict(ui.DEFAULT_APPEARANCE)
    appearance.update({k: v for k, v in config.get("appearance", {}).items() if k in appearance})
    for key in appearance:
        override = getattr(args, key, None) if args else None
        if override:
            appearance[key] = override
    return appearance


def make_painter(appearance: Dict[str, str], assume_terminal: bool = False) -> ui.Painter:
    mode = ui.resolve_color_mode(appearance["colors"], assume_terminal=assume_terminal)
    return ui.Painter(appearance["theme"], mode, appearance["background"])


def pick_appearance(config_path: str, appearance: Dict[str, str], logger: logging.Logger,
                    states: Optional[List[ui.CertState]] = None) -> None:
    """Interactive look and language picker; saves to config.json on Enter or 's', updates `appearance` in place."""
    draft = dict(appearance)
    original_language = language = i18n.current_language()
    ctx = build_ui_context({})
    ctx.states = states or []
    sel = ui.THEMES.index(draft["theme"])

    def cycle(values, value, step):
        return values[(values.index(value) + step) % len(values)]

    while True:
        painter = make_painter(draft)
        ui.draw(ui.render_picker(painter, ctx, draft, painter.mode, language, sel))
        key = ui.read_key()
        step = -1 if key == "left" else 1
        if key in ("up", "down") or (key in ("left", "right") and sel < len(ui.THEMES)):
            # Up and down walk all rows; on the look rows left and right walk the looks.
            count = len(ui.PICKER_ROWS) if key in ("up", "down") else len(ui.THEMES)
            sel = ui.move(key, sel, count)
            if sel < len(ui.THEMES):
                draft["theme"] = ui.THEMES[sel]  # live preview
            continue
        if key in ("left", "right"):
            key = ui.PICKER_ROWS[sel]
        elif key in ui.PICKER_ROWS:
            sel = ui.PICKER_ROWS.index(key)
        if key in ("1", "2", "3"):
            draft["theme"] = ui.THEMES[int(key) - 1]
        elif key == "c":
            draft["colors"] = cycle(ui.COLOR_MODES, draft["colors"], step)
        elif key == "b":
            draft["background"] = cycle(ui.BACKGROUNDS, draft["background"], step)
        elif key == "l":
            language = cycle(i18n.LANGUAGES, language, step)
            i18n.set_language(language)  # preview right away
        elif key in ("s", "enter"):
            try:
                raw = load_config(config_path=config_path)
                raw["appearance"] = draft
                raw.setdefault("general", {})["language"] = language
                save_config(raw, config_path=config_path)
            except Exception as e:
                logger.error(f"Could not save appearance: {e}")
                i18n.set_language(original_language)
                ui.pause(painter)
                return
            appearance.update(draft)
            logger.info(f"Look saved: {draft['theme']}, colours {draft['colors']}, background {draft['background']}, "
                        f"language {language}")
            return
        elif key in ("q", "esc"):
            i18n.set_language(original_language)
            return


def choose(painter: ui.Painter, ctx: ui.Context, title: str, options: List[str],
           current: Optional[str] = None) -> Optional[int]:
    """Arrow-key pick list; returns the index of the chosen option, or None on q / Esc."""
    if not options:
        return None
    sel = options.index(current) if current in options else 0
    while True:
        ui.draw(ui.render_choice(painter, ctx, title, options, sel, current))
        key = ui.read_key()
        if key == "enter":
            return sel
        if key in ("q", "esc"):
            return None
        if key.isdigit() and 1 <= int(key) <= len(options):
            return int(key) - 1
        if key in options:  # a name typed off a terminal
            return options.index(key)
        sel = ui.move(key, sel, len(options))


def ask(label: str, default: str = "") -> str:
    """Free-text field for what cannot be picked from a list (domain, email, command); Enter keeps the default."""
    ui.show_cursor()
    return input("  " + label).strip() or default


def ask_domain(painter: ui.Painter, logger: logging.Logger, default_email: str) -> Optional[Tuple[str, str]]:
    """Domain and email typed by hand, each checked right after it is typed; None after an error."""
    domain = ask(t("Domain: "))
    if not is_valid_domain(domain):
        logger.error(f"Invalid domain: {domain}")
        ui.pause(painter)
        return None
    email = ask(t("Email [Enter = {value}]: ", value=default_email), default_email) if default_email         else ask(t("Email: "))
    if not is_valid_email(email):
        logger.error(f"Invalid email: {email}")
        ui.pause(painter)
        return None
    return domain, email


def confirm(painter: ui.Painter, question: str) -> bool:
    key = ui.read_key("\n  " + painter.fg("warn", question))
    while key == "resize":
        key = ui.read_key()
    return key in ("y", "t")

# ======================
# CONFIGURATION EDITOR
# ======================
EDITOR_ITEMS = [
    ("n", "add domain", "Add"),
    ("d", "default provider", "Default"),
    ("o", "command after renewal", "Command"),
    ("a", "failure notice command", "Notify"),
    ("l", "toggle logs", "Logs"),
    ("v", "check configuration", "Check"),
    ("s", "save and exit", "Save"),
    ("q", "quit without saving", "Cancel"),
]
# Keys that act on the domain under the cursor; Enter on a domain lists them.
DOMAIN_ITEMS = [
    ("z", "change domain or email", "Edit"),
    ("p", "change provider", "Provider"),
    ("u", "remove domain", "Remove"),
]


def edit_config_interactive(config_path: str, logger: logging.Logger,
                            appearance: Optional[Dict[str, str]] = None) -> None:
    """Interactive editor for config.json, drawn in the configured look and driven by arrow keys."""
    try:
        config = load_config(config_path=config_path)
    except Exception as e:
        logger.error(f"Could not load configuration: {e}")
        return
    general = config.setdefault("general", {})
    domains = config.setdefault("domains", [])
    providers = list(config.get("providers", {}))
    painter = make_painter(appearance or effective_appearance(config))
    ctx = build_ui_context(config)
    ctx.states = collect_cert_states(config)
    unchanged = json.dumps(config, sort_keys=True)

    def fail(message: str) -> None:
        logger.error(message)
        ui.pause(painter)

    def pick_provider(title: str, current: Optional[str]) -> Optional[str]:
        i = choose(painter, ctx, title, providers, current)
        return None if i is None else providers[i]

    if not general.get("default_provider"):
        chosen = pick_provider(t("default provider"), None)
        if not chosen:
            logger.error("No default provider set. Aborting edit.")
            return
        general["default_provider"] = chosen

    sel = 0
    while True:
        sel = min(sel, len(domains) + len(EDITOR_ITEMS) - 1)
        ui.draw(ui.render_editor(painter, ctx, config, config_path, sel, EDITOR_ITEMS, DOMAIN_ITEMS))
        key = ui.read_key()
        on_domain = sel < len(domains)
        if key.isdigit():  # jump to a domain by its number
            if 1 <= int(key) <= len(domains):
                sel = int(key) - 1
            continue
        if key == "enter":
            if on_domain:
                i = choose(painter, ctx, domains[sel].get("domain", ""), [t(label) for _, label, _ in DOMAIN_ITEMS])
                if i is None:
                    continue
                key = DOMAIN_ITEMS[i][0]
            else:
                key = EDITOR_ITEMS[sel - len(domains)][0]
        elif key in [k for k, _, _ in EDITOR_ITEMS]:
            sel = len(domains) + [k for k, _, _ in EDITOR_ITEMS].index(key)
        if key in [k for k, _, _ in DOMAIN_ITEMS] and not on_domain:
            fail("Select a domain first (arrow keys or its number).")
            continue
        default_prov = general["default_provider"]

        if key == "n":
            print()
            typed = ask_domain(painter, logger, domains[-1].get("email", "") if domains else "")
            if typed is None:
                continue
            domain, email = typed
            provider = pick_provider(t("provider for {domain}", domain=domain), default_prov)
            if provider is None:
                continue
            domains.append({"domain": domain, "email": email, "provider": provider})
            sel = len(domains) - 1
            logger.info(f"Added: {domain} (provider: {provider})")

        elif key == "z":
            current = domains[sel]
            print()
            new_domain = ask(t("New domain [Enter = {value}]: ", value=current.get("domain", "")), current.get("domain", ""))
            new_email = ask(t("New email  [Enter = {value}]: ", value=current.get("email", "")), current.get("email", ""))
            if not is_valid_domain(new_domain):
                fail(f"Invalid domain: {new_domain}")
                continue
            if not is_valid_email(new_email):
                fail(f"Invalid email: {new_email}")
                continue
            current["domain"], current["email"] = new_domain, new_email
            logger.info(f"Updated entry #{sel + 1}: {new_domain}")

        elif key == "p":
            current = domains[sel]
            provider = pick_provider(t("provider for {domain}", domain=current.get("domain", "")),
                                     current.get("provider", default_prov))
            if provider:
                current["provider"] = provider
                logger.info(f"Changed provider for {current.get('domain')} to {provider}")

        elif key == "u":
            if confirm(painter, t("Remove {domain}? (y/N) ", domain=domains[sel].get("domain", ""))):
                removed = domains.pop(sel)
                logger.info(f"Removed: {removed.get('domain')}")

        elif key == "d":
            provider = pick_provider(t("default provider"), default_prov)
            if provider:
                general["default_provider"] = provider
                logger.info(f"Default provider changed to: {provider}")

        elif key in ("o", "a"):
            name = "post_renewal_cmd" if key == "o" else "notify_cmd"
            current_cmd = general.get(name, "")
            shown = current_cmd or "-"
            prompt = t("Command after renewal, - clears it [Enter = {value}]: ", value=shown) if key == "o" \
                else t("Failure notice command, gets the notice on stdin, - clears it [Enter = {value}]: ", value=shown)
            print()
            cmd = ask(prompt, current_cmd)
            cmd = "" if cmd == "-" else cmd
            try:
                shlex.split(cmd)
            except ValueError as e:
                fail(f"general.{name} cannot be parsed: {e}")
                continue
            general[name] = cmd
            logger.info(f"{name} set to: {cmd or '(none)'}")

        elif key == "l":
            general["logs"] = 0 if general.get("logs", 1) == 1 else 1
            logger.info(f"Logging {'enabled' if general['logs'] == 1 else 'disabled'}.")

        elif key == "v":
            show_check(painter, config, logger, config_path)
            ui.pause(painter)

        elif key == "s":
            try:
                save_config(config, config_path=config_path)
            except Exception as e:
                fail(f"Could not save configuration: {e}")
                continue
            logger.info("Configuration saved.")
            return

        elif key in ("q", "esc"):
            if json.dumps(config, sort_keys=True) != unchanged and not confirm(painter, t("Discard changes? (y/N) ")):
                continue
            print(t("Cancelled, configuration unchanged."))
            return

        else:
            sel = ui.move(key, sel, len(domains) + len(EDITOR_ITEMS))

# ======================
# INTERACTIVE MENU
# ======================
MENU_ITEMS = [
    ("o", "renew everything that qualifies", "Renew"),
    ("w", "issue a certificate for one domain", "Issue"),
    ("l", "list certificates", "List"),
    ("r", "save validity report", "Report"),
    ("e", "edit configuration", "Edit"),
    ("s", "check configuration and system", "Check"),
    ("t", "change look and language", "Look"),
    ("q", "quit", "Quit"),
]
# Digits from the pre-1.3 numbered menu keep working.
MENU_ALIASES = {"1": "o", "2": "w", "3": "r", "4": "l", "5": "e", "6": "s", "7": "q"}


def interactive_menu(config: Dict, logger: logging.Logger, config_path: str,
                     appearance: Optional[Dict[str, str]] = None) -> None:
    """Main menu drawn in the configured look: arrows and Enter, or the item's letter."""
    appearance = appearance or effective_appearance(config)
    keys = [key for key, _, _ in MENU_ITEMS]
    sel = keys.index("l")  # a stray Enter lists certificates instead of renewing them
    while True:
        painter = make_painter(appearance)
        ctx = build_ui_context(config)
        ctx.states = collect_cert_states(config)
        ui.draw(ui.render_menu(painter, ctx, ctx.states, MENU_ITEMS, sel))
        key = ui.read_key()
        key = MENU_ALIASES.get(key, key)
        if key in ("up", "down", "left", "right", "resize"):
            sel = ui.move(key, sel, len(keys), ui.nav_step(painter.theme, len(keys)))
            continue
        key = {"enter": keys[sel], "esc": "q"}.get(key, key)
        if key not in keys:
            logger.warning(f"Unknown key '{key}'.")
            continue
        sel = keys.index(key)

        if key == "o":
            ui.clear()
            cert_auto(config, logger, painter=painter, live=screen_mode())
        elif key == "w":
            ui.draw(ui.render_head(painter, ctx, t("issue")))
            domains = config.get("domains", [])
            typed = ask_domain(painter, logger, domains[0].get("email", "") if domains else "")
            if typed is None:
                continue
            providers = list(config.get("providers", {}))
            i = choose(painter, ctx, t("provider for {domain}", domain=typed[0]), providers,
                       config["general"].get("default_provider"))
            if i is None:
                continue
            ui.clear()
            issue_one(config, {"domain": typed[0], "email": typed[1], "provider": providers[i]}, logger,
                      painter=painter, live=screen_mode())
        elif key == "r":
            path = generate_cert_report(config, logger)
            ui.draw(ui.render_report(painter, ctx, collect_cert_states(config), path))
        elif key == "l":
            ui.draw(ui.render_list(painter, ctx, collect_cert_states(config)))
        elif key == "e":
            edit_config_interactive(config_path, logger, appearance)
            try:
                config = load_config(config_path=config_path)
                logger.info("Configuration reloaded.")
            except Exception as e:
                logger.error(f"Failed to reload configuration: {e}")
                ui.pause(painter)
            continue
        elif key == "s":
            show_check(painter, config, logger, config_path, system=True)
        elif key == "t":
            pick_appearance(config_path, appearance, logger, ctx.states)
            continue
        elif key == "q":
            break
        ui.pause(painter)

# ======================
# MAIN
# ======================
def configured_language(config_path: str) -> Optional[str]:
    """general.language from config.json, or None when the config is missing or broken."""
    try:
        return load_config(config_path=resolve_project_path(config_path)).get("general", {}).get("language")
    except Exception:
        return None


def main() -> None:
    # Pick the language before building the parser, so --help is translated too.
    early = argparse.ArgumentParser(add_help=False)
    early.add_argument("--lang", choices=i18n.LANGUAGES)
    early.add_argument("--config", default=DEFAULT_CONFIG_PATH)
    early_args, _ = early.parse_known_args()
    i18n.set_language(early_args.lang or configured_language(early_args.config))

    parser = argparse.ArgumentParser(
        description=t("xCert - Let's Encrypt certificate automation with 13+ DNS providers"), add_help=False
    )
    parser.add_argument("-h", "--help", action="help", help=t("Show this help message and exit"))
    parser.add_argument("--version", "-V", action="version", version=f"xCert {VERSION}",
                        help=t("Show the version and exit"))
    parser.add_argument("-a", "--auto", action="store_true", help=t("Automatic certification for all configured domains"))
    parser.add_argument("--email", help=t("Email address (manual mode)"))
    parser.add_argument("--domain", help=t("Domain name (manual mode)"))
    parser.add_argument("--provider", help=t("DNS provider (e.g. 'cloudflare', 'ovh'), manual mode only"))
    parser.add_argument("--dry-run", action="store_true",
                        help=t("Dry run: passes --dry-run to certbot (no certificate issued)"))
    parser.add_argument("--debug", action="store_true", help=t("Enable verbose debug logging"))
    parser.add_argument("--report", action="store_true", help=t("Generate a certificate validity report"))
    parser.add_argument("--list", action="store_true", help=t("List all configured domains and their certificate status"))
    parser.add_argument("--menu", action="store_true", help=t("Launch interactive text menu"))
    parser.add_argument("--edit-config", action="store_true", help=t("Edit config.json interactively"))
    parser.add_argument("--validate-config", action="store_true", help=t("Validate config.json and provider credentials"))
    parser.add_argument("--validate-domain", action="append", metavar="DOMAIN",
                        help=t("Limit --validate-config to specific domain(s), can be repeated"))
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH,
                        help=t("Path to config.json (default: {path})", path=DEFAULT_CONFIG_PATH))
    parser.add_argument("--motd", action="store_true", help=t("Print the short certificate status used at SSH login"))
    parser.add_argument("--theme", choices=ui.THEMES, help=t("Look for this run: ledger, timeline or console"))
    parser.add_argument("--colors", choices=ui.COLOR_MODES, help=t("Colours for this run: auto, full (24-bit), 16 or none"))
    parser.add_argument("--background", choices=ui.BACKGROUNDS, help=t("Palette for a dark or light terminal"))
    parser.add_argument("--lang", choices=i18n.LANGUAGES, help=t("Language for this run: en or pl"))
    parser.add_argument("--status", action="store_true",
                        help=t("Check config, certbot, DNS plugins, installed files and the timer"))
    parser.add_argument("--install", action="store_true",
                        help=t("Install or update certbot, DNS plugins, the xcert command, systemd units, MOTD "
                               "and logrotate (root)"))
    parser.add_argument("--update", action="store_true",
                        help=t("git pull, upgrade certbot and its plugins, then what --install does (root)"))
    parser.add_argument("--uninstall", action="store_true",
                        help=t("Remove what --install set up; keeps config, credentials, logs and certificates"))
    parser.add_argument("--notify-failure", action="store_true",
                        help=t("Send the failure notice through general.notify_cmd (used by xcert-notify.service)"))

    args = parser.parse_args()
    if args.validate_domain and not (args.validate_config or args.status):
        parser.error(t("--validate-domain only works together with --validate-config or --status"))

    logger = setup_logger(debug=args.debug)

    if args.install or args.update or args.uninstall:
        sys.exit(0 if run_setup(args, logger) else 1)

    try:
        config_path = resolve_project_path(args.config)
        config = load_config(config_path=config_path)
    except Exception as e:
        logger.critical(f"❌ Failed to load config.json: {e}")
        logger.critical("First run on this server? Set everything up with: xcert --install")
        sys.exit(1)

    if args.dry_run:
        config.setdefault("general", {})["dry_run"] = 1

    if "domains" not in config or "providers" not in config or "general" not in config:
        logger.critical("❌ Invalid config.json, missing 'domains', 'providers', or 'general' section")
        sys.exit(1)

    general = config["general"]
    if general.get("logs", 1) == 1:
        if "output" not in general or "error" not in general:
            logger.critical("❌ Missing 'output' or 'error' keys in 'general' section (required when logs=1)")
            sys.exit(1)

    appearance = effective_appearance(config, args)

    actions = ("auto", "report", "list", "menu", "edit_config", "validate_config", "status", "motd",
               "notify_failure", "domain", "email")
    if not any(getattr(args, a) for a in actions):
        if not ui.interactive():
            parser.print_help()
            return
        args.menu = True  # plain `xcert` on a terminal opens the menu

    if args.notify_failure:
        sys.exit(0 if send_failure_notice(config, logger) else 1)

    if args.motd:
        painter = make_painter(appearance, assume_terminal=True)
        print(ui.render_motd(painter, build_ui_context(config), collect_cert_states(config)))
        return

    ui.fit_screen()
    if args.menu or args.edit_config:
        try:
            if args.menu:
                interactive_menu(config, logger, config_path, appearance)
            else:
                edit_config_interactive(config_path, logger, appearance)
        except KeyboardInterrupt:
            print()
        finally:
            ui.show_cursor()
        return

    if args.report:
        path = generate_cert_report(config, logger)
        if sys.stdout.isatty():  # cron and timers only need the log line
            print(ui.render_report(make_painter(appearance), build_ui_context(config), collect_cert_states(config), path))
        return

    if args.list:
        painter = make_painter(appearance)
        print(ui.render_list(painter, build_ui_context(config), collect_cert_states(config)))
        return

    if args.validate_config or args.status:
        if screen_mode(args):
            ok = show_check(make_painter(appearance), config, logger, config_path, args.validate_domain, args.status)
        else:
            ok = check_all(config, logger, args.validate_domain, system=args.status)
        sys.exit(0 if ok else 1)

    if args.auto:
        results = cert_auto(config, logger, dry_run=args.dry_run, painter=make_painter(appearance),
                            live=screen_mode(args))
        if not results:
            sys.exit(1)
        failures = [name for name, success in results if not success]
        if failures:
            logger.error(f"Failed: {', '.join(failures)}")
            sys.exit(1)
        return

    if bool(args.domain) != bool(args.email):
        missing = "--email" if args.domain else "--domain"
        logger.error(f"❌ Missing argument {missing} (manual mode requires both --domain and --email)")
        sys.exit(1)

    if args.email and args.domain:
        provider = args.provider or config["general"].get("default_provider", "ovh")
        if provider not in config["providers"]:
            logger.error(f"❌ Unknown provider: {provider}. Available: {', '.join(config['providers'].keys())}")
            sys.exit(1)
        domain_entry = {"domain": args.domain, "email": args.email, "provider": provider}
        success = issue_one(config, domain_entry, logger, dry_run=args.dry_run, painter=make_painter(appearance),
                            live=screen_mode(args))
        sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
