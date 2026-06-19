import argparse
import json
import logging
import os
import re
import shlex
import subprocess
import sys
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from json_loader import load_config


VERSION = "1.1.2"

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
# VALIDATION
# ======================
def is_valid_email(email: str) -> bool:
    return re.match(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$", email) is not None

def is_valid_domain(domain: str) -> bool:
    """Validates a domain name (supports: example.com, *.example.com)."""
    if not domain or len(domain) > 253:
        return False
    clean = domain.replace("*.", "")
    # RFC-compliant regex, simplified for practical use
    pattern = r"^(?!-)[A-Za-z0-9-]{1,63}(?<!-)(\.[A-Za-z0-9-]{1,63})*\.[A-Za-z]{2,}$"
    return re.match(pattern, clean) is not None

# ======================
# LOGGING
# ======================
def setup_logger(debug: bool = False, log_file: str = "./xcert_debug.log") -> logging.Logger:
    level = logging.DEBUG if debug else logging.INFO
    log_dir = os.path.dirname(log_file)
    if log_dir:
        os.makedirs(log_dir, exist_ok=True)

    # Custom time format: "HH:MM:SS DD-MM-YYYY"
    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S %d-%m-%Y"
    )

    # File handler
    file_handler = logging.FileHandler(log_file, encoding='utf-8')
    file_handler.setFormatter(formatter)

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)

    logger = logging.getLogger("xCert")
    logger.setLevel(level)
    logger.handlers.clear()  # avoid duplicate handlers on repeated calls
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    logger.propagate = False  # prevent propagation to root logger

    return logger

# ======================
# CERTIFICATE HELPERS
# ======================
def get_cert_days_left(cert_path: str) -> Optional[int]:
    """Returns days until certificate expiry, or None on read error."""
    try:
        result = subprocess.run(
            ["openssl", "x509", "-enddate", "-noout", "-in", cert_path],
            capture_output=True, text=True, check=True, timeout=10
        )
        end_str = result.stdout.strip().split("=", 1)[1]
        exp = datetime.strptime(end_str, "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)
        return (exp - datetime.now(timezone.utc)).days
    except Exception:
        return None

# ======================
# COMMAND EXECUTION
# ======================
def run_command(
    cmd_list: List[str],
    output_file: Optional[str] = None,
    error_file: Optional[str] = None,
    use_logs: bool = False,
    logger: Optional[logging.Logger] = None,
    timeout: int = 300
) -> bool:
    try:
        if use_logs and output_file and error_file:
            with open(output_file, 'a', encoding='utf-8') as out, open(error_file, 'a', encoding='utf-8') as err:
                result = subprocess.run(cmd_list, stdout=out, stderr=err, text=True, timeout=timeout)
        else:
            result = subprocess.run(cmd_list, capture_output=True, text=True, timeout=timeout)
            if logger and result.stdout:
                logger.debug(f"STDOUT: {result.stdout}")
            if logger and result.stderr:
                logger.debug(f"STDERR: {result.stderr}")
        return result.returncode == 0
    except subprocess.TimeoutExpired:
        if logger:
            logger.critical(f"Command timed out ({timeout}s): {cmd_list[0]}")
        return False
    except Exception as e:
        if logger:
            logger.critical(f"Error running command: {e}")
        return False

# ======================
# SINGLE DOMAIN CERTIFICATION
# ======================
def cert_for_domain(
    domain_config: Dict,
    general_config: Dict,
    providers_config: Dict,
    logger: logging.Logger,
    dry_run: bool = False
) -> bool:
    domain = domain_config["domain"]
    email = domain_config["email"]
    provider_name = domain_config.get("provider", general_config.get("default_provider", "ovh"))

    if not is_valid_email(email):
        logger.error(f"Invalid email for {domain}: {email}")
        return False
    if not is_valid_domain(domain):
        logger.error(f"Invalid domain: {domain}")
        return False

    if provider_name not in providers_config:
        logger.error(f"Unknown provider '{provider_name}' for domain {domain}")
        return False

    provider = providers_config[provider_name]
    cmd_template = provider["cmd_template"]
    credentials = provider.get("credentials_path")

    if "{credentials}" in cmd_template and not credentials:
        logger.error(f"No credentials path for provider {provider_name} (domain: {domain})")
        return False

    if credentials and not os.path.isfile(credentials):
        logger.error(f"Credentials file not found: {credentials} (domain: {domain})")
        return False

    # Skip renewal if certificate has plenty of time left (saves Let's Encrypt rate limits)
    threshold = int(general_config.get("renew_threshold_days", 0))
    if threshold > 0:
        clean = domain.replace("*.", "")
        cert_path = f"/etc/letsencrypt/live/{clean}/fullchain.pem"
        if os.path.isfile(cert_path):
            days_left = get_cert_days_left(cert_path)
            if days_left is not None and days_left > threshold:
                logger.info(f"⏭️  Skipped {domain}: certificate valid for {days_left} more days (threshold: {threshold})")
                return True

    # Substitute credentials path into command template
    if credentials:
        full_cmd_str = cmd_template.format(credentials=credentials)
    else:
        full_cmd_str = cmd_template  # e.g. route53 uses IAM roles instead of a credentials file

    full_cmd = shlex.split(full_cmd_str) + ["--email", email, "-d", domain]
    if dry_run:
        full_cmd.append("--dry-run")

    retries = int(general_config.get("retry_count", 0))
    delay = int(general_config.get("retry_delay_seconds", 30))

    logger.info(f"Certbot: {domain} (provider: {provider_name}{' [DRY-RUN]' if dry_run else ''})")
    success = False
    for attempt in range(retries + 1):
        success = run_command(
            full_cmd,
            os.path.expanduser(general_config["output"]),
            os.path.expanduser(general_config["error"]),
            general_config["logs"] == 1,
            logger
        )
        if success:
            break
        if attempt < retries:
            logger.warning(f"Attempt {attempt + 1} failed for {domain}. Retrying in {delay}s...")
            time.sleep(delay)

    if success:
        logger.info(f"✅ Success: {domain}")
        post_cmd = general_config.get("post_renewal_cmd", "").strip()
        if post_cmd:
            logger.info(f"Running post_renewal_cmd: {post_cmd}")
            run_command(shlex.split(post_cmd), logger=logger, timeout=60)
    else:
        logger.error(f"❌ Failed: {domain}")
    return success

# ======================
# AUTOMATIC CERTIFICATION FOR ALL DOMAINS
# ======================
def cert_auto(
    config: Dict,
    logger: logging.Logger,
    dry_run: bool = False
) -> Optional[List[Tuple[str, bool]]]:
    domains_list = config.get("domains", [])
    general = config["general"]
    providers = config["providers"]

    if not domains_list:
        logger.warning("No domains configured in config.json")
        return None

    results = []
    for entry in domains_list:
        success = cert_for_domain(entry, general, providers, logger, dry_run=dry_run)
        results.append((entry["domain"], success))

    ok = sum(1 for _, s in results if s)
    fail = len(results) - ok
    print(f"\n{Colors.BOLD}" + "=" * 40 + Colors.RESET)
    if fail == 0:
        print(f"{Colors.BRIGHT_GREEN}✅ All done: {ok}/{len(results)} domains renewed.{Colors.RESET}")
    else:
        print(f"{Colors.BRIGHT_GREEN}✅ Renewed: {ok}/{len(results)}{Colors.RESET}")
        print(f"{Colors.BRIGHT_RED}❌ Failed: {fail}/{len(results)}{Colors.RESET}")
    print(f"{Colors.BOLD}" + "=" * 40 + Colors.RESET)

    return results

# ======================
# CERTIFICATE VALIDITY REPORT
# ======================
def generate_cert_report(config: Dict, logger: logging.Logger) -> str:
    """Generates a JSON report of certificate validity for all configured domains.
    Saves it to the script directory as xcert_report.json."""
    domains_list = config.get("domains", [])
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "domains": {}
    }

    for entry in domains_list:
        domain = entry["domain"]
        clean_domain = domain.replace("*.", "")
        cert_path = f"/etc/letsencrypt/live/{clean_domain}/fullchain.pem"

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
                "cert_path": cert_path
            }
            logger.debug(f"{domain}: valid for {days_left} more days")
        except Exception as e:
            report["domains"][domain] = {
                "status": "error",
                "error": str(e)
            }
            logger.error(f"Error reading certificate for {domain}: {e}")

    report_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "xcert_report.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    logger.info(f"✅ Report saved: {report_path}")
    return report_path

# ======================
# DOMAIN LIST
# ======================
def list_domains(config: Dict) -> None:
    """Prints all configured domains with their certificate status to the console."""
    domains_list = config.get("domains", [])
    default_provider = config["general"].get("default_provider", "?")

    if not domains_list:
        print(f"{Colors.YELLOW}No domains configured.{Colors.RESET}")
        return

    print(f"\n{Colors.BOLD}{Colors.CYAN}=== Domain list ({len(domains_list)}) ==={Colors.RESET}\n")

    for i, entry in enumerate(domains_list, 1):
        domain = entry["domain"]
        provider = entry.get("provider", default_provider)
        email = entry["email"]

        clean_domain = domain.replace("*.", "")
        cert_path = f"/etc/letsencrypt/live/{clean_domain}/fullchain.pem"

        if not os.path.isfile(cert_path):
            status = f"{Colors.RED}NO CERTIFICATE{Colors.RESET}"
        else:
            days_left = get_cert_days_left(cert_path)
            if days_left is None:
                status = f"{Colors.RED}READ ERROR{Colors.RESET}"
            elif days_left < 0:
                status = f"{Colors.BRIGHT_RED}EXPIRED ({-days_left} days ago){Colors.RESET}"
            elif days_left < 7:
                status = f"{Colors.BRIGHT_RED}CRITICAL – {days_left} days{Colors.RESET}"
            elif days_left < 21:
                status = f"{Colors.BRIGHT_YELLOW}WARNING – {days_left} days{Colors.RESET}"
            else:
                status = f"{Colors.BRIGHT_GREEN}OK – {days_left} days{Colors.RESET}"

        print(f"  {i}. {Colors.GREEN}{domain}{Colors.RESET}")
        print(f"     Provider: {Colors.MAGENTA}{provider}{Colors.RESET}  |  Email: {Colors.BLUE}{email}{Colors.RESET}  |  Status: {status}")

    print()

# ======================
# CONFIGURATION EDITOR
# ======================
def edit_config_interactive(config_path: str, logger: logging.Logger) -> None:
    """Interactive terminal editor for config.json with aligned domain view."""
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)

        general = config.setdefault("general", {})
        domains = config.setdefault("domains", [])

        # Ensure a default provider is set before entering the edit loop
        default_prov = general.get("default_provider")
        if not default_prov:
            print("\n⚠️ Warning: No default provider set in configuration.")
            available = ", ".join(config.get("providers", {}).keys()) or "none"
            print(f"Available providers: {available}")
            default_prov = input("Enter default provider: ").strip()
            if default_prov:
                general["default_provider"] = default_prov
            else:
                logger.error("No default provider set. Aborting edit.")
                return

        while True:
            print(f"\n{Colors.BOLD}{Colors.YELLOW}" + "=" * 60 + Colors.RESET)
            print(f"{Colors.BOLD}{Colors.YELLOW}🔧 EDIT xCert CONFIGURATION{Colors.RESET}")
            print(f"{Colors.BOLD}{Colors.YELLOW}" + "=" * 60 + Colors.RESET)
            print(f"{Colors.CYAN}🔧 Default provider: {Colors.BRIGHT_GREEN}{default_prov}{Colors.RESET}")
            print(
                f"{Colors.CYAN}📝 Logging: {Colors.BRIGHT_GREEN if general.get('logs', 1) == 1 else Colors.BRIGHT_RED}"
                f"{'Enabled' if general.get('logs', 1) == 1 else 'Disabled'}{Colors.RESET}")

            if domains:
                print(f"\n{Colors.CYAN}🌐 Configured domains ({len(domains)}):{Colors.RESET}")
                max_dom_len = max(len(d["domain"]) for d in domains)
                max_email_len = max(len(d["email"]) for d in domains)
                for i, d in enumerate(domains, 1):
                    prov = d.get("provider", default_prov)
                    dom_part = d["domain"].ljust(max_dom_len)
                    email_part = d["email"].ljust(max_email_len)
                    print(
                        f"  {i}. {Colors.GREEN}{dom_part}{Colors.RESET} | email: {Colors.BLUE}{email_part}{Colors.RESET} | provider: {Colors.MAGENTA}{prov}{Colors.RESET}")
            else:
                print(f"\n{Colors.YELLOW}🌐 No domains configured.{Colors.RESET}")

            print(f"\n{Colors.BOLD}What would you like to do?{Colors.RESET}")
            print(f"{Colors.BOLD}{Colors.GREEN}1. ➕ Add new domain{Colors.RESET}")
            print(f"{Colors.BOLD}{Colors.RED}2. 🗑️  Remove domain{Colors.RESET}")
            print(f"{Colors.BOLD}{Colors.YELLOW}3. ⚙️  Change default provider{Colors.RESET}")
            print(f"{Colors.BOLD}{Colors.CYAN}4. 📝 Toggle logging{Colors.RESET}")
            print(f"{Colors.BOLD}{Colors.GREEN}5. 💾 Save and exit{Colors.RESET}")
            print(f"{Colors.BOLD}{Colors.RED}6. ❌ Cancel (discard changes){Colors.RESET}")

            choice = input(f"\n{Colors.BOLD}Choose option (1-6): {Colors.RESET}").strip()

            if choice == "1":
                domain = input("  Domain: ").strip()
                email = input("  Email: ").strip()
                use_default = input(f"  Use default provider ({default_prov})? (Y/n): ").strip().lower()
                if use_default in ("n", "no"):
                    prov_list = ", ".join(config.get("providers", {}).keys())
                    provider = input(f"  Provider (available: {prov_list}): ").strip()
                    if not provider or provider not in config.get("providers", {}):
                        logger.error("Invalid provider. Operation cancelled.")
                        continue
                else:
                    provider = default_prov  # always store provider explicitly in domain entry

                if not domain or not email:
                    logger.error("Domain and email are required.")
                    continue
                if not is_valid_email(email):
                    logger.error(f"Invalid email: {email}")
                    continue
                if not is_valid_domain(domain):
                    logger.error(f"Invalid domain: {domain}")
                    continue

                domains.append({
                    "domain": domain,
                    "email": email,
                    "provider": provider
                })
                logger.info(f"Added: {domain} (provider: {provider})")
            elif choice == "2":
                if not domains:
                    print("  No domains to remove.")
                    continue
                try:
                    idx = int(input("  Domain number to remove: ")) - 1
                    if 0 <= idx < len(domains):
                        removed = domains.pop(idx)
                        logger.info(f"Removed: {removed['domain']}")
                    else:
                        print("  Invalid number.")
                except ValueError:
                    print("  Please enter a number.")
            elif choice == "3":
                prov_list = ", ".join(config.get("providers", {}).keys())
                new_provider = input(f"  New default provider (available: {prov_list}): ").strip()
                if new_provider and new_provider in config.get("providers", {}):
                    general["default_provider"] = new_provider
                    default_prov = new_provider
                    logger.info(f"Default provider changed to: {new_provider}")
                else:
                    logger.error("Invalid provider.")
            elif choice == "4":
                current = general.get("logs", 1)
                general["logs"] = 0 if current == 1 else 1
                state = "enabled" if general["logs"] == 1 else "disabled"
                logger.info(f"Logging {state}.")
            elif choice == "5":
                with open(config_path, "w", encoding="utf-8") as f:
                    json.dump(config, f, indent=2, ensure_ascii=False)
                logger.info(f"{Colors.BRIGHT_GREEN}✅ Configuration saved.{Colors.RESET}")
                break
            elif choice == "6":
                print(f"{Colors.BRIGHT_RED}❌ Cancelled — configuration unchanged.{Colors.RESET}")
                break
            else:
                print("⚠️ Invalid choice.")

    except Exception as e:
        logger.error(f"{Colors.BRIGHT_RED}❌ Error during configuration edit: {e}{Colors.RESET}")

# ======================
# INTERACTIVE MENU
# ======================
def interactive_menu(config: Dict, logger: logging.Logger) -> None:
    """Text-based interactive CLI menu."""
    config_path = os.path.join(os.path.dirname(__file__), "config.json")
    while True:
        print(f"\n{Colors.BOLD}{Colors.CYAN}" + "="*40 + Colors.RESET)
        print(f"{Colors.BOLD}{Colors.CYAN}🔐 xCert – Universal SSL Certificate Manager{Colors.RESET}")
        print(f"{Colors.BOLD}{Colors.CYAN}" + "="*40 + Colors.RESET)
        print(f"{Colors.BOLD}{Colors.GREEN}1. 🔄 Run automatic certification (all domains){Colors.RESET}")
        print(f"{Colors.BOLD}{Colors.BLUE}2. ✍️  Manual certification (single domain){Colors.RESET}")
        print(f"{Colors.BOLD}{Colors.MAGENTA}3. 📊 Generate certificate validity report{Colors.RESET}")
        print(f"{Colors.BOLD}{Colors.CYAN}4. 📋 List domains{Colors.RESET}")
        print(f"{Colors.BOLD}{Colors.YELLOW}5. ⚙️  Edit configuration{Colors.RESET}")
        print(f"{Colors.BOLD}{Colors.RED}6. 🚪 Exit{Colors.RESET}")
        choice = input(f"\n{Colors.BOLD}Choose option (1-6): {Colors.RESET}").strip()

        if choice == "1":
            cert_auto(config, logger)
        elif choice == "2":
            domain = input(f"{Colors.WHITE}Domain: {Colors.RESET}").strip()
            email = input(f"{Colors.WHITE}Email: {Colors.RESET}").strip()
            provider = input(f"{Colors.WHITE}Provider (e.g. ovh, cloudflare) [Enter = default]: {Colors.RESET}").strip() or None
            if not domain or not email:
                logger.error("Domain and email are required.")
                continue
            domain_entry = {"domain": domain, "email": email}
            if provider:
                domain_entry["provider"] = provider
            cert_for_domain(domain_entry, config["general"], config["providers"], logger)
        elif choice == "3":
            generate_cert_report(config, logger)
        elif choice == "4":
            list_domains(config)
        elif choice == "5":
            edit_config_interactive(config_path, logger)
            try:
                config = load_config()
                logger.info("Configuration reloaded.")
            except Exception as e:
                logger.error(f"Failed to reload configuration: {e}")
        elif choice == "6":
            print(f"{Colors.GREEN}Goodbye! ✨{Colors.RESET}")
            break
        else:
            print(f"{Colors.BRIGHT_YELLOW}⚠️ Invalid choice. Please try again.{Colors.RESET}")

# ======================
# MAIN
# ======================
def main() -> None:
    parser = argparse.ArgumentParser(
        description="xCert – Universal Let's Encrypt certificate automation with 13+ DNS provider support"
    )
    parser.add_argument('--version', '-V', action='version', version=f'xCert {VERSION}')
    parser.add_argument('-a', '--auto', action='store_true', help="Automatic certification for all configured domains")
    parser.add_argument('--email', help="Email address (manual mode)")
    parser.add_argument('--domain', help="Domain name (manual mode)")
    parser.add_argument('--provider', help="DNS provider (e.g. 'cloudflare', 'ovh') — manual mode only")
    parser.add_argument('--dry-run', action='store_true', help="Dry run without issuing a certificate (passes --dry-run to certbot)")
    parser.add_argument('--debug', action='store_true', help="Enable verbose debug logging")
    parser.add_argument('--report', action='store_true', help="Generate a certificate validity report")
    parser.add_argument('--list', action='store_true', help="List all domains and their certificate status")
    parser.add_argument('--menu', action='store_true', help="Launch interactive text menu")
    parser.add_argument('--edit-config', action='store_true', help="Edit config.json interactively")

    args = parser.parse_args()

    # Initialise logger
    logger = setup_logger(debug=args.debug)

    # Load configuration
    try:
        config = load_config()
    except Exception as e:
        logger.critical(f"❌ Failed to load config.json: {e}")
        sys.exit(1)

    # Validate config structure
    if "domains" not in config or "providers" not in config or "general" not in config:
        logger.critical("❌ Invalid config.json — missing 'domains', 'providers', or 'general' section")
        sys.exit(1)

    # Validate log file paths when logging is enabled
    general = config["general"]
    if general.get("logs", 1) == 1:
        if "output" not in general or "error" not in general:
            logger.critical("❌ Missing 'output' or 'error' keys in 'general' section (required when logs=1)")
            sys.exit(1)

    # Handle flags
    if args.menu:
        interactive_menu(config, logger)
        return

    if args.edit_config:
        config_path = os.path.join(os.path.dirname(__file__), "config.json")
        edit_config_interactive(config_path, logger)
        return

    if args.report:
        generate_cert_report(config, logger)
        return

    if args.list:
        list_domains(config)
        return

    if args.auto:
        cert_auto(config, logger, dry_run=args.dry_run)
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
        cert_for_domain(domain_entry, config["general"], config["providers"], logger, dry_run=args.dry_run)
        return

    # Default: show help
    parser.print_help()

if __name__ == "__main__":
    main()
