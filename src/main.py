import argparse
import json
import logging
import os
import random
import re
import shlex
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import i18n
import ui
from i18n import t
from json_loader import load_config, save_config


VERSION = "1.3.0"

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
        "cloudflare",
        "unable to determine zone_id",
        "please wait",
    ],
    "ovh": [
        "http 429",
    ],
}

# ======================
# CONSOLE COLORS
# ======================
class Colors:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"
    WHITE = "\033[37m"
    BRIGHT_GREEN = "\033[92m"
    BRIGHT_RED = "\033[91m"
    BRIGHT_YELLOW = "\033[93m"

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
    return re.match(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$", email) is not None


def is_valid_domain(domain: str) -> bool:
    """Validates a domain name (supports: example.com, *.example.com)."""
    if not domain or len(domain) > 253:
        return False
    clean = domain.replace("*.", "")
    pattern = r"^(?!-)[A-Za-z0-9-]{1,63}(?<!-)(\.[A-Za-z0-9-]{1,63})*\.[A-Za-z]{2,}$"
    return re.match(pattern, clean) is not None

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
    clean_domain = domain.replace("*.", "")
    cert_path = os.path.join(
        general_path(general_config, "certbot_config_dir", "/etc/letsencrypt"),
        "live", clean_domain, "cert.pem",
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

        if logger and result.stdout:
            logger.debug(f"STDOUT: {result.stdout}")
        if logger and result.stderr:
            logger.debug(f"STDERR: {result.stderr}")

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
                    f"(recommended: 0o600 — fix: chmod 600 {cred_path})"
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
        clean = value.replace("*.", "")
        return clean[4:] if clean.startswith("www.") else clean

    for entry in domains_list:
        domain = entry["domain"]
        email = entry["email"]
        provider = entry.get("provider", default_provider)
        key = (email, provider, base_domain(domain))

        if key not in grouped:
            grouped[key] = {"email": email, "provider": provider, "domains": []}
            order.append(key)

        if domain not in grouped[key]["domains"]:
            grouped[key]["domains"].append(domain)

    return [grouped[key] for key in order]

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

    renew_threshold_days = int(general_config.get("renew_threshold_days", 30))
    if renew_threshold_days > 0:
        primary = domains[0]
        days_left = cert_days_left(primary, general_config)
        if days_left is not None and days_left > renew_threshold_days:
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
        full_cmd_str = cmd_template.format(credentials=resolve_project_path(credentials))
    else:
        full_cmd_str = cmd_template

    full_cmd = shlex.split(full_cmd_str) + ["--email", email]
    for domain in domains:
        full_cmd += ["-d", domain]

    full_cmd += [
        "--config-dir", general_path(general_config, "certbot_config_dir", "/etc/letsencrypt"),
        "--work-dir",   general_path(general_config, "certbot_work_dir",   "/var/lib/letsencrypt"),
        "--logs-dir",   general_path(general_config, "certbot_logs_dir",   "/var/log/letsencrypt"),
    ]

    if dry_run or int(general_config.get("dry_run", 0)) == 1:
        full_cmd += ["--dry-run"]

    plugin_flag_name = provider.get("certbot_plugin_name", f"dns-{provider_name}")
    propagation_seconds = provider.get("propagation_seconds")
    if propagation_seconds is not None:
        full_cmd += [f"--{plugin_flag_name}-propagation-seconds", str(propagation_seconds)]

    max_retries = int(general_config.get("max_retries", general_config.get("retry_count", 3)))
    base_backoff = int(general_config.get("base_backoff_seconds", general_config.get("retry_delay_seconds", 60)))
    jitter_max = int(general_config.get("backoff_jitter_seconds", 15))
    command_timeout = int(general_config.get("command_timeout_seconds", 900))

    use_logs = general_config.get("logs") == 1
    output_file = general_path(general_config, "output", "logs/xcert_output.log") if use_logs else None
    error_file = general_path(general_config, "error", "logs/xcert_error.log") if use_logs else None

    for attempt in range(1, max_retries + 1):
        logger.info(
            f"Certbot: {domain_label} (provider: {provider_name}"
            f"{' [DRY-RUN]' if dry_run else ''}) [attempt {attempt}/{max_retries}]"
        )

        rc, out, err = run_command(
            full_cmd,
            output_file,
            error_file,
            use_logs,
            logger,
            command_timeout,
        )

        if rc == 0:
            logger.info(f"✅ Success: {domain_label}")
            post_cmd = general_config.get("post_renewal_cmd", "").strip()
            if post_cmd:
                logger.info(f"Running post_renewal_cmd: {post_cmd}")
                run_command(shlex.split(post_cmd), logger=logger, timeout=60)
            return True

        combined = (out + "\n" + err).lower()
        if is_retryable_rate_limit(provider_name, combined):
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

    logger.error(f"❌ Failed: {domain_label} — max retries exceeded (rate-limit?)")
    return False

# ======================
# SINGLE DOMAIN (thin wrapper)
# ======================
def cert_for_domain(
    domain_config: Dict,
    general_config: Dict,
    providers_config: Dict,
    logger: logging.Logger,
    dry_run: bool = False,
) -> bool:
    domain = domain_config["domain"]
    email = domain_config["email"]
    provider_name = domain_config.get("provider", general_config.get("default_provider", "ovh"))
    return cert_for_domains(
        [domain], email, provider_name, general_config, providers_config, logger, dry_run=dry_run
    )

# ======================
# AUTOMATIC CERTIFICATION
# ======================
def cert_auto(
    config: Dict,
    logger: logging.Logger,
    dry_run: bool = False,
    painter: Optional[ui.Painter] = None,
) -> Optional[List[Tuple[str, bool]]]:
    domains_list = config.get("domains", [])
    general = config["general"]
    providers = config["providers"]
    sleep_between = int(general.get("sleep_between_domains_seconds", 10))

    if not domains_list:
        logger.warning("No domains configured in config.json")
        return None

    tasks = build_cert_tasks(domains_list, general.get("default_provider", "ovh"))
    if len(tasks) < len(domains_list):
        logger.info(f"Grouped {len(domains_list)} domains into {len(tasks)} certbot tasks.")

    results: List[Tuple[str, bool]] = []
    timings: List[Tuple[float, float]] = []
    started = time.monotonic()
    for idx, task in enumerate(tasks):
        task_start = time.monotonic()
        success = cert_for_domains(
            task["domains"],
            task["email"],
            task["provider"],
            general,
            providers,
            logger,
            dry_run=dry_run,
        )
        results.append((", ".join(task["domains"]), success))
        timings.append((task_start - started, time.monotonic() - task_start))

        if idx < len(tasks) - 1 and sleep_between > 0:
            logger.info(f"Sleeping {sleep_between}s before next domain...")
            time.sleep(sleep_between)

    print()
    print(ui.render_summary(painter or ui.Painter(), build_ui_context(config), results, timings,
                            time.monotonic() - started))
    return results

# ======================
# CONFIG VALIDATION
# ======================
def validate_config(
    config: Dict,
    logger: logging.Logger,
    domain_filters: Optional[List[str]] = None,
) -> bool:
    valid = True

    if "domains" not in config or "providers" not in config or "general" not in config:
        logger.error("Missing required sections: 'domains', 'providers', or 'general'")
        return False

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
        return False

    providers_to_check = providers.keys() if normalized_filters is None else selected_providers

    for provider_name in providers_to_check:
        provider = providers.get(provider_name)
        if provider is None:
            logger.error(f"Unknown provider '{provider_name}' referenced in config")
            valid = False
            continue
        if not provider.get("cmd_template"):
            logger.error(f"Provider '{provider_name}': missing 'cmd_template'")
            valid = False
            continue
        if not validate_provider_runtime(provider_name, provider, logger):
            valid = False

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

    scope = "entire config" if normalized_filters is None else f"selected domains ({len(selected_domains)})"
    if valid:
        logger.info(f"✅ Validation of {scope} passed")
    else:
        logger.error(f"❌ Validation of {scope} found errors")

    return valid


def prompt_domain_filters(config: Dict, logger: logging.Logger) -> List[str]:
    domains = config.get("domains", [])
    if not domains:
        logger.warning("No domains in config")
        return []

    print("\n" + t("Enter domains to validate (comma-separated, Enter = all):"))
    print(t("Available domains:"))
    for entry in domains:
        print(f"  - {entry.get('domain', '')}")

    raw = input(t("Domains: ")).strip()
    if not raw:
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]

# ======================
# CERTIFICATE VALIDITY REPORT
# ======================
def generate_cert_report(config: Dict, logger: logging.Logger) -> str:
    domains_list = config.get("domains", [])
    general = config.get("general", {})
    certbot_config_dir = general_path(general, "certbot_config_dir", "/etc/letsencrypt")
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "domains": {}
    }

    for entry in domains_list:
        domain = entry["domain"]
        clean_domain = domain.replace("*.", "")
        cert_path = os.path.join(certbot_config_dir, "live", clean_domain, "fullchain.pem")

        if not os.path.isfile(cert_path):
            report["domains"][domain] = {"status": "missing", "error": "Certificate file not found"}
            logger.warning(f"Certificate not found for: {domain}")
            continue

        try:
            result = subprocess.run(
                ["openssl", "x509", "-enddate", "-noout", "-in", cert_path],
                capture_output=True, text=True, check=True, timeout=10
            )
            end_date_str = result.stdout.strip().split("=", 1)[1]
            exp_date = datetime.strptime(end_date_str, "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
            days_left = (exp_date - now).days

            report["domains"][domain] = {
                "status": "valid",
                "expires_on": exp_date.isoformat(),
                "days_left": days_left,
                "cert_path": cert_path,
            }
            logger.debug(f"{domain}: valid for {days_left} more days")
        except Exception as e:
            report["domains"][domain] = {"status": "error", "error": str(e)}
            logger.error(f"Error reading certificate for {domain}: {e}")

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
        name = task["domains"][0].replace("*.", "")
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


def timer_next_run() -> Optional[datetime]:
    """Next xcert.timer elapse in local time, or None when systemd or the timer is unavailable."""
    try:
        out = subprocess.run(
            ["systemctl", "show", "xcert.timer", "-p", "NextElapseUSecRealtime", "--value"],
            capture_output=True, text=True, timeout=5,
        ).stdout.split()
        return datetime.strptime(" ".join(out[1:3]), "%Y-%m-%d %H:%M:%S")
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def build_ui_context(config: Dict) -> ui.Context:
    return ui.Context(
        version=VERSION,
        host=socket.gethostname().split(".")[0],
        now=datetime.now(),
        threshold=int(config.get("general", {}).get("renew_threshold_days", 30)),
        next_run=timer_next_run(),
    )


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
    if mode == "none":
        # Legacy screens (editor, prompts) print Colors directly; blank them for plain output.
        for name in [n for n in vars(Colors) if n.isupper()]:
            setattr(Colors, name, "")
    return ui.Painter(appearance["theme"], mode, appearance["background"])


def pick_appearance(config_path: str, appearance: Dict[str, str], logger: logging.Logger) -> None:
    """Interactive look picker; saves to config.json on 's' and updates `appearance` in place."""
    draft = dict(appearance)
    ctx = build_ui_context({})
    while True:
        painter = make_painter(draft)
        print()
        print(ui.render_picker(painter, ctx, draft, painter.mode))
        choice = input(ui.prompt(painter)).strip().lower()
        if choice in ("1", "2", "3"):
            draft["theme"] = ui.THEMES[int(choice) - 1]
        elif choice == "c":
            draft["colors"] = ui.COLOR_MODES[(ui.COLOR_MODES.index(draft["colors"]) + 1) % len(ui.COLOR_MODES)]
        elif choice == "b":
            draft["background"] = "light" if draft["background"] == "dark" else "dark"
        elif choice == "s":
            try:
                raw = load_config(config_path=config_path)
                raw["appearance"] = draft
                save_config(raw, config_path=config_path)
            except Exception as e:
                logger.error(f"Could not save appearance: {e}")
                return
            appearance.update(draft)
            logger.info(f"Look saved: {draft['theme']}, colours {draft['colors']}, background {draft['background']}")
            return
        elif choice in ("q", ""):
            return

# ======================
# CONFIGURATION EDITOR
# ======================
def edit_config_interactive(config_path: str, logger: logging.Logger) -> None:
    """Interactive terminal editor for config.json."""
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)

        general = config.setdefault("general", {})
        domains = config.setdefault("domains", [])

        default_prov = general.get("default_provider")
        if not default_prov:
            print("\n⚠️ " + t("Warning: No default provider set in configuration."))
            available = ", ".join(config.get("providers", {}).keys()) or t("none")
            print(t("Available providers: {names}", names=available))
            default_prov = input(t("Enter default provider: ")).strip()
            if default_prov:
                general["default_provider"] = default_prov
            else:
                logger.error("No default provider set. Aborting edit.")
                return

        while True:
            print(f"\n{Colors.BOLD}{Colors.YELLOW}" + "=" * 60 + Colors.RESET)
            print(f"{Colors.BOLD}{Colors.YELLOW}🔧 {t('EDIT xCert CONFIGURATION')}{Colors.RESET}")
            print(f"{Colors.BOLD}{Colors.YELLOW}" + "=" * 60 + Colors.RESET)
            print(f"{Colors.CYAN}🔧 {t('Default provider:')} {Colors.BRIGHT_GREEN}{default_prov}{Colors.RESET}")
            print(
                f"{Colors.CYAN}📝 {t('Logging:')} "
                f"{Colors.BRIGHT_GREEN if general.get('logs', 1) == 1 else Colors.BRIGHT_RED}"
                f"{t('Enabled') if general.get('logs', 1) == 1 else t('Disabled')}{Colors.RESET}"
            )

            if domains:
                print(f"\n{Colors.CYAN}🌐 {t('Configured domains ({n}):', n=len(domains))}{Colors.RESET}")
                max_dom_len = max(len(d["domain"]) for d in domains)
                max_email_len = max(len(d["email"]) for d in domains)
                for i, d in enumerate(domains, 1):
                    prov = d.get("provider", default_prov)
                    dom_part = d["domain"].ljust(max_dom_len)
                    email_part = d["email"].ljust(max_email_len)
                    print(
                        f"  {i}. {Colors.GREEN}{dom_part}{Colors.RESET} | "
                        f"{t('email:')} {Colors.BLUE}{email_part}{Colors.RESET} | "
                        f"{t('provider:')} {Colors.MAGENTA}{prov}{Colors.RESET}"
                    )
            else:
                print(f"\n{Colors.YELLOW}🌐 {t('No domains configured.')}{Colors.RESET}")

            print(f"\n{Colors.BOLD}{t('What would you like to do?')}{Colors.RESET}")
            print(f"{Colors.BOLD}{Colors.GREEN}1. ➕ {t('Add domain')}{Colors.RESET}")
            print(f"{Colors.BOLD}{Colors.RED}2. 🗑️  {t('Remove domain')}{Colors.RESET}")
            print(f"{Colors.BOLD}{Colors.BLUE}3. ✏️  {t('Edit domain / email')}{Colors.RESET}")
            print(f"{Colors.BOLD}{Colors.MAGENTA}4. 🔁 {t('Change domain provider')}{Colors.RESET}")
            print(f"{Colors.BOLD}{Colors.YELLOW}5. ⚙️  {t('Change default provider')}{Colors.RESET}")
            print(f"{Colors.BOLD}{Colors.CYAN}6. 📝 {t('Toggle logging')}{Colors.RESET}")
            print(f"{Colors.BOLD}{Colors.CYAN}7. 🧪 {t('Validate config')}{Colors.RESET}")
            print(f"{Colors.BOLD}{Colors.GREEN}8. 💾 {t('Save and exit')}{Colors.RESET}")
            print(f"{Colors.BOLD}{Colors.RED}9. ❌ {t('Cancel')}{Colors.RESET}")

            choice = input(f"\n{Colors.BOLD}{t('Choose option (1-9): ')}{Colors.RESET}").strip()

            if choice == "1":
                domain = input("  " + t("Domain: ")).strip()
                email = input("  " + t("Email: ")).strip()
                use_default = input("  " + t("Use default provider ({name})? (Y/n): ", name=default_prov)).strip().lower()
                if use_default in ("n", "no", "nie"):
                    prov_list = ", ".join(config.get("providers", {}).keys())
                    provider = input("  " + t("Provider (available: {names}): ", names=prov_list)).strip()
                    if not provider or provider not in config.get("providers", {}):
                        logger.error("Invalid provider. Operation cancelled.")
                        continue
                else:
                    provider = default_prov

                if not domain or not email:
                    logger.error("Domain and email are required.")
                    continue
                if not is_valid_email(email):
                    logger.error(f"Invalid email: {email}")
                    continue
                if not is_valid_domain(domain):
                    logger.error(f"Invalid domain: {domain}")
                    continue

                domains.append({"domain": domain, "email": email, "provider": provider})
                logger.info(f"Added: {domain} (provider: {provider})")

            elif choice == "2":
                if not domains:
                    print("  " + t("No domains to remove."))
                    continue
                try:
                    idx = int(input("  " + t("Domain number to remove: "))) - 1
                    if 0 <= idx < len(domains):
                        removed = domains.pop(idx)
                        logger.info(f"Removed: {removed['domain']}")
                    else:
                        print("  " + t("Invalid number."))
                except ValueError:
                    print("  " + t("Please enter a number."))

            elif choice == "3":
                if not domains:
                    print("  " + t("No domains to edit."))
                    continue
                try:
                    idx = int(input("  " + t("Domain number to edit: "))) - 1
                    if not (0 <= idx < len(domains)):
                        print("  " + t("Invalid number."))
                        continue
                    current = domains[idx]
                    new_domain = input("  " + t("New domain [Enter = {value}]: ", value=current["domain"])).strip() \
                        or current["domain"]
                    new_email = input("  " + t("New email  [Enter = {value}]: ", value=current["email"])).strip() \
                        or current["email"]
                    if not is_valid_domain(new_domain):
                        logger.error(f"Invalid domain: {new_domain}")
                        continue
                    if not is_valid_email(new_email):
                        logger.error(f"Invalid email: {new_email}")
                        continue
                    current["domain"] = new_domain
                    current["email"] = new_email
                    logger.info(f"Updated entry #{idx + 1}: {new_domain}")
                except ValueError:
                    print("  " + t("Please enter a number."))

            elif choice == "4":
                if not domains:
                    print("  " + t("No domains to edit."))
                    continue
                try:
                    idx = int(input("  " + t("Domain number to change provider: "))) - 1
                    if not (0 <= idx < len(domains)):
                        print("  " + t("Invalid number."))
                        continue
                    prov_list = ", ".join(config.get("providers", {}).keys())
                    new_provider = input("  " + t("New provider (available: {names}): ", names=prov_list)).strip()
                    if not new_provider or new_provider not in config.get("providers", {}):
                        logger.error("Invalid provider.")
                        continue
                    domains[idx]["provider"] = new_provider
                    logger.info(f"Changed provider for {domains[idx]['domain']} to {new_provider}")
                except ValueError:
                    print("  " + t("Please enter a number."))

            elif choice == "5":
                prov_list = ", ".join(config.get("providers", {}).keys())
                new_provider = input("  " + t("New default provider (available: {names}): ", names=prov_list)).strip()
                if new_provider and new_provider in config.get("providers", {}):
                    general["default_provider"] = new_provider
                    default_prov = new_provider
                    logger.info(f"Default provider changed to: {new_provider}")
                else:
                    logger.error("Invalid provider.")

            elif choice == "6":
                current_val = general.get("logs", 1)
                general["logs"] = 0 if current_val == 1 else 1
                state = "enabled" if general["logs"] == 1 else "disabled"
                logger.info(f"Logging {state}.")

            elif choice == "7":
                selected = prompt_domain_filters(config, logger)
                validate_config(config, logger, selected or None)

            elif choice == "8":
                save_config(config, config_path=config_path)
                logger.info(f"{Colors.BRIGHT_GREEN}✅ Configuration saved.{Colors.RESET}")
                break

            elif choice == "9":
                print(f"{Colors.BRIGHT_RED}❌ {t('Cancelled, configuration unchanged.')}{Colors.RESET}")
                break

            else:
                print("⚠️ " + t("Invalid choice."))

    except Exception as e:
        logger.error(f"{Colors.BRIGHT_RED}❌ Error during configuration edit: {e}{Colors.RESET}")

# ======================
# INTERACTIVE MENU
# ======================
MENU_ITEMS = [
    ("o", "renew everything that qualifies", "Renew"),
    ("w", "issue a certificate for one domain", "Issue"),
    ("l", "list certificates", "List"),
    ("r", "save validity report", "Report"),
    ("e", "edit configuration", "Edit"),
    ("s", "check configuration and credentials", "Check"),
    ("t", "change look", "Look"),
    ("q", "quit", "Quit"),
]
# Digits from the pre-1.3 numbered menu keep working.
MENU_ALIASES = {"1": "o", "2": "w", "3": "r", "4": "l", "5": "e", "6": "s", "7": "q"}


def interactive_menu(config: Dict, logger: logging.Logger, config_path: str,
                     appearance: Optional[Dict[str, str]] = None) -> None:
    """Text-based interactive CLI menu drawn in the configured look."""
    appearance = appearance or effective_appearance(config)
    while True:
        painter = make_painter(appearance)
        print()
        print(ui.render_menu(painter, build_ui_context(config), collect_cert_states(config), MENU_ITEMS))
        choice = input(ui.prompt(painter)).strip().lower()
        choice = MENU_ALIASES.get(choice, choice)

        if choice == "o":
            cert_auto(config, logger, painter=painter)
        elif choice == "w":
            domain = input(t("Domain: ")).strip()
            email = input(t("Email: ")).strip()
            provider = input(t("Provider (e.g. ovh, cloudflare) [Enter = default]: ")).strip() or None
            if not domain or not email:
                logger.error("Domain and email are required.")
                continue
            domain_entry: Dict = {"domain": domain, "email": email}
            if provider:
                domain_entry["provider"] = provider
            cert_for_domain(domain_entry, config["general"], config["providers"], logger)
        elif choice == "r":
            generate_cert_report(config, logger)
        elif choice == "l":
            print()
            print(ui.render_list(painter, build_ui_context(config), collect_cert_states(config)))
        elif choice == "e":
            edit_config_interactive(config_path, logger)
            try:
                config = load_config(config_path=config_path)
                logger.info("Configuration reloaded.")
            except Exception as e:
                logger.error(f"Failed to reload configuration: {e}")
        elif choice == "s":
            selected = prompt_domain_filters(config, logger)
            validate_config(config, logger, selected or None)
        elif choice == "t":
            pick_appearance(config_path, appearance, logger)
        elif choice == "q":
            break
        else:
            logger.warning(f"Unknown key '{choice}'.")

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

    args = parser.parse_args()

    logger = setup_logger(debug=args.debug)

    try:
        config_path = resolve_project_path(args.config)
        config = load_config(config_path=config_path)
    except Exception as e:
        logger.critical(f"❌ Failed to load config.json: {e}")
        sys.exit(1)

    if args.dry_run:
        config.setdefault("general", {})["dry_run"] = 1

    if "domains" not in config or "providers" not in config or "general" not in config:
        logger.critical("❌ Invalid config.json — missing 'domains', 'providers', or 'general' section")
        sys.exit(1)

    general = config["general"]
    if general.get("logs", 1) == 1:
        if "output" not in general or "error" not in general:
            logger.critical("❌ Missing 'output' or 'error' keys in 'general' section (required when logs=1)")
            sys.exit(1)

    appearance = effective_appearance(config, args)

    if args.motd:
        painter = make_painter(appearance, assume_terminal=True)
        print(ui.render_motd(painter, build_ui_context(config), collect_cert_states(config)))
        return

    if args.menu:
        interactive_menu(config, logger, config_path, appearance)
        return

    if args.edit_config:
        edit_config_interactive(config_path, logger)
        return

    if args.report:
        generate_cert_report(config, logger)
        return

    if args.list:
        painter = make_painter(appearance)
        print(ui.render_list(painter, build_ui_context(config), collect_cert_states(config)))
        return

    if args.validate_config:
        ok = validate_config(config, logger, args.validate_domain)
        sys.exit(0 if ok else 1)

    if args.auto:
        results = cert_auto(config, logger, dry_run=args.dry_run, painter=make_painter(appearance))
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
        success = cert_for_domain(domain_entry, config["general"], config["providers"], logger, dry_run=args.dry_run)
        sys.exit(0 if success else 1)

    parser.print_help()


if __name__ == "__main__":
    main()
