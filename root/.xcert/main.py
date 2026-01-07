import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from json_loader import load_config


# ======================
# KOLORY KONSOLI
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
# WALIDACJA
# ======================
def is_valid_email(email):
    return re.match(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$", email) is not None

def is_valid_domain(domain):
    """Sprawdza, czy domena jest poprawna (obsługiwane: example.com, *.example.com)"""
    if not domain or len(domain) > 253:
        return False
    clean = domain.replace("*.", "")
    # Regex zgodny z RFC, uproszczony dla praktycznego użycia
    pattern = r"^(?!-)[A-Za-z0-9-]{1,63}(?<!-)(\.[A-Za-z0-9-]{1,63})*\.[A-Za-z]{2,}$"
    return re.match(pattern, clean) is not None

# ======================
# LOGOWANIE
# ======================
import logging
def setup_logger(debug=False, log_file="./xcert_debug.log"):
    level = logging.DEBUG if debug else logging.INFO
    os.makedirs(os.path.dirname(log_file), exist_ok=True)

    # Definiujemy własny format czasu: "HH:MM:SS DD-MM-YYYY"
    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S %d-%m-%Y"
    )

    # Handler do pliku
    file_handler = logging.FileHandler(log_file, encoding='utf-8')
    file_handler.setFormatter(formatter)

    # Handler do konsoli
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)

    logger = logging.getLogger("xCert")
    logger.setLevel(level)
    logger.handlers.clear()  # unikaj duplikatów przy wielokrotnym wywołaniu
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    logger.propagate = False  # zapobiegaj logowaniu przez root logger

    return logger

# ======================
# URUCHAMIANIE KOMEND
# ======================
def run_command(cmd_list, output_file=None, error_file=None, use_logs=False, logger=None):
    try:
        if use_logs and output_file and error_file:
            with open(output_file, 'a', encoding='utf-8') as out, open(error_file, 'a', encoding='utf-8') as err:
                result = subprocess.run(cmd_list, stdout=out, stderr=err, text=True)
        else:
            result = subprocess.run(cmd_list, capture_output=True, text=True)
            if logger and result.stdout:
                logger.debug(f"STDOUT: {result.stdout}")
            if logger and result.stderr:
                logger.debug(f"STDERR: {result.stderr}")
        return result.returncode == 0
    except Exception as e:
        if logger:
            logger.critical(f"Błąd podczas uruchamiania komendy: {e}")
        return False

# ======================
# CERTYFIKACJA DLA JEDNEJ DOMENY
# ======================
def cert_for_domain(domain_config, general_config, providers_config, logger):
    domain = domain_config["domain"]
    email = domain_config["email"]
    provider_name = domain_config.get("provider", general_config.get("default_provider", "ovh"))

    if not is_valid_email(email):
        logger.error(f"Nieprawidłowy e-mail dla {domain}: {email}")
        return False
    if not is_valid_domain(domain):
        logger.error(f"Nieprawidłowa domena: {domain}")
        return False

    if provider_name not in providers_config:
        logger.error(f"Nieznany provider '{provider_name}' dla domeny {domain}")
        return False

    provider = providers_config[provider_name]
    cmd_template = provider["cmd_template"]
    credentials = provider.get("credentials_path")

    if "{credentials}" in cmd_template and not credentials:
        logger.error(f"Brak ścieżki do poświadczeń dla providera {provider_name} (domena: {domain})")
        return False

    # Podstawienie ścieżki poświadczeń
    if credentials:
        full_cmd_str = cmd_template.format(credentials=credentials)
    else:
        full_cmd_str = cmd_template  # np. dla route53

    full_cmd = full_cmd_str.split() + ["--email", email, "-d", domain]

    logger.info(f"Certbot: {domain} (provider: {provider_name})")
    success = run_command(
        full_cmd,
        os.path.expanduser(general_config["output"]),
        os.path.expanduser(general_config["error"]),
        general_config["logs"] == 1,
        logger
    )

    if success:
        logger.info(f"✅ Sukces: {domain}")
    else:
        logger.error(f"❌ Błąd: {domain}")
    return success

# ======================
# AUTOMATYCZNA CERTYFIKACJA WSZYSTKICH DOMEN
# ======================
def cert_auto(config, logger):
    domains_list = config.get("domains", [])
    general = config["general"]
    providers = config["providers"]

    if not domains_list:
        logger.warning("Brak domen do certyfikacji w config.json")
        return None

    results = []
    for entry in domains_list:
        success = cert_for_domain(entry, general, providers, logger)
        results.append((entry["domain"], success))
    return results
# ======================
# RAPORT WAŻNOŚCI CERTYFIKATÓW (LOKALNIE)
# ======================
def generate_cert_report(config, logger):
    """
    Generuje raport JSON z informacjami o ważności certyfikatów dla wszystkich domen.
    Zapisuje go do /var/log/xcert_report.json
    """
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
            report["domains"][domain] = {"status": "missing", "error": "Certyfikat nie istnieje"}
            logger.warning(f"Brak certyfikatu dla: {domain}")
            continue

        try:
            # Pobierz datę wygaśnięcia
            result = subprocess.run(
                ["openssl", "x509", "-enddate", "-noout", "-in", cert_path],
                capture_output=True, text=True, check=True
            )
            end_date_str = result.stdout.strip().split("=", 1)[1]
            # Przykład: "Jul  7 12:00:00 2026 GMT"
            exp_date = datetime.strptime(end_date_str, "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
            days_left = (exp_date - now).days

            report["domains"][domain] = {
                "status": "valid",
                "expires_on": exp_date.isoformat(),
                "days_left": days_left,
                "cert_path": cert_path
            }
            logger.debug(f"{domain}: ważny jeszcze {days_left} dni")
        except Exception as e:
            report["domains"][domain] = {
                "status": "error",
                "error": str(e)
            }
            logger.error(f"Błąd analizy certyfikatu {domain}: {e}")

    # Zapisz raport
    report_path = "./xcert_report.json"
    os.makedirs(os.path.dirname(report_path), exist_ok=True)
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    logger.info(f"✅ Raport zapisany: {report_path}")
    return report_path

# ======================
# EDYCJA KONFIGURACJI
# ======================
def edit_config_interactive(config_path, logger):
    """
    Bezpieczna, interaktywna edycja konfiguracji z wyrównanym widokiem i jawnym providerem.
    """
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)

        general = config.setdefault("general", {})
        domains = config.setdefault("domains", [])

        # Upewnij się, że domyślny provider istnieje
        default_prov = general.get("default_provider")
        if not default_prov:
            print("\n⚠️ Uwaga: Brak domyślnego providera w konfiguracji.")
            available = ", ".join(config.get("providers", {}).keys()) or "brak"
            print(f"Dostępne providery: {available}")
            default_prov = input("Podaj domyślny provider: ").strip()
            if default_prov:
                general["default_provider"] = default_prov
            else:
                logger.error("Nie ustawiono domyślnego providera. Edycja przerwana.")
                return

        while True:
            print(f"\n{Colors.BOLD}{Colors.YELLOW}" + "=" * 60 + Colors.RESET)
            print(f"{Colors.BOLD}{Colors.YELLOW}🔧 EDYCJA KONFIGURACJI xCert{Colors.RESET}")
            print(f"{Colors.BOLD}{Colors.YELLOW}" + "=" * 60 + Colors.RESET)
            print(f"{Colors.CYAN}🔧 Domyślny provider: {Colors.BRIGHT_GREEN}{default_prov}{Colors.RESET}")
            print(
                f"{Colors.CYAN}📝 Logowanie: {Colors.BRIGHT_GREEN if general.get('logs', 1) == 1 else Colors.BRIGHT_RED}{'Włączone' if general.get('logs', 1) == 1 else 'Wyłączone'}{Colors.RESET}")

            if domains:
                print(f"\n{Colors.CYAN}🌐 Zdefiniowane domeny ({len(domains)}):{Colors.RESET}")
                max_dom_len = max(len(d["domain"]) for d in domains)
                max_email_len = max(len(d["email"]) for d in domains)
                for i, d in enumerate(domains, 1):
                    prov = d.get("provider", default_prov)
                    dom_part = d["domain"].ljust(max_dom_len)
                    email_part = d["email"].ljust(max_email_len)
                    print(
                        f"  {i}. {Colors.GREEN}{dom_part}{Colors.RESET} | email: {Colors.BLUE}{email_part}{Colors.RESET} | provider: {Colors.MAGENTA}{prov}{Colors.RESET}")
            else:
                print(f"\n{Colors.YELLOW}🌐 Brak zdefiniowanych domen.{Colors.RESET}")

            print(f"\n{Colors.BOLD}Co chcesz zrobić?{Colors.RESET}")
            print(f"{Colors.BOLD}{Colors.GREEN}1. ➕ Dodaj nową domenę{Colors.RESET}")
            print(f"{Colors.BOLD}{Colors.RED}2. 🗑️  Usuń domenę{Colors.RESET}")
            print(f"{Colors.BOLD}{Colors.YELLOW}3. ⚙️  Zmień domyślny provider{Colors.RESET}")
            print(f"{Colors.BOLD}{Colors.CYAN}4. 📝 Włącz/wyłącz logowanie{Colors.RESET}")
            print(f"{Colors.BOLD}{Colors.GREEN}5. 💾 Zapisz i wyjdź{Colors.RESET}")
            print(f"{Colors.BOLD}{Colors.RED}6. ❌ Anuluj (bez zapisu){Colors.RESET}")

            choice = input(f"\n{Colors.BOLD}Wybierz opcję (1-6): {Colors.RESET}").strip()

            if choice == "1":
                domain = input("  Domena: ").strip()
                email = input("  E-mail: ").strip()
                use_default = input(f"  Użyć domyślnego providera ({default_prov})? (T/n): ").strip().lower()
                if use_default in ("n", "no", "nie"):
                    prov_list = ", ".join(config.get("providers", {}).keys())
                    provider = input(f"  Provider (dostępne: {prov_list}): ").strip()
                    if not provider or provider not in config.get("providers", {}):
                        logger.error("Nieprawidłowy provider. Operacja anulowana.")
                        continue
                else:
                    provider = default_prov  # zawsze jawnie zapisujemy

                if not domain or not email:
                    logger.error("Domena i e-mail są wymagane.")
                    continue
                if not is_valid_email(email):
                    logger.error(f"Nieprawidłowy e-mail: {email}")
                    continue
                if not is_valid_domain(domain):
                    logger.error(f"Nieprawidłowa domena: {domain}")
                    continue

                # Zawsze zapisujemy "provider" w obiekcie domeny
                domains.append({
                    "domain": domain,
                    "email": email,
                    "provider": provider
                })
                logger.info(f"Dodano: {domain} (provider: {provider})")
            elif choice == "2":
                if not domains:
                    print("  Brak domen do usunięcia.")
                    continue
                try:
                    idx = int(input("  Numer domeny do usunięcia: ")) - 1
                    if 0 <= idx < len(domains):
                        removed = domains.pop(idx)
                        logger.info(f"Usunięto: {removed['domain']}")
                    else:
                        print("  Nieprawidłowy numer.")
                except ValueError:
                    print("  Wpisz liczbę.")
            elif choice == "3":
                prov_list = ", ".join(config.get("providers", {}).keys())
                new_provider = input(f"  Nowy domyślny provider (dostępne: {prov_list}): ").strip()
                if new_provider and new_provider in config.get("providers", {}):
                    general["default_provider"] = new_provider
                    default_prov = new_provider
                    logger.info(f"Domyślny provider zmieniony na: {new_provider}")
                else:
                    logger.error("Nieprawidłowy provider.")
            elif choice == "4":
                current = general.get("logs", 1)
                general["logs"] = 0 if current == 1 else 1
                state = "włączone" if general["logs"] == 1 else "wyłączone"
                logger.info(f"Logowanie zostało {state}.")
            elif choice == "5":
                with open(config_path, "w", encoding="utf-8") as f:
                    json.dump(config, f, indent=2, ensure_ascii=False)
                logger.info(f"{Colors.BRIGHT_GREEN}✅ Konfiguracja zapisana.{Colors.RESET}")
                break
            elif choice == "6":
                print(f"{Colors.BRIGHT_RED}❌ Anulowano — konfiguracja nie została zmieniona.{Colors.RESET}")
                break

            else:
                print("⚠️ Nieprawidłowy wybór.")

    except Exception as e:
        logger.error(f"{Colors.BRIGHT_RED}❌ Błąd podczas edycji konfiguracji: {e}{Colors.RESET}")

# ======================
# MENU
# ======================
def interactive_menu(config, logger):
    """
    Tekstowe menu CLI z kolorami.
    """
    config_path = os.path.join(os.path.dirname(__file__), "config.json")
    while True:
        print(f"\n{Colors.BOLD}{Colors.CYAN}" + "="*40 + Colors.RESET)
        print(f"{Colors.BOLD}{Colors.CYAN}🔐 xCert – Uniwersalny menedżer certyfikatów SSL{Colors.RESET}")
        print(f"{Colors.BOLD}{Colors.CYAN}" + "="*40 + Colors.RESET)
        print(f"{Colors.BOLD}{Colors.GREEN}1. 🔄 Uruchom certyfikację automatyczną (wszystkie domeny){Colors.RESET}")
        print(f"{Colors.BOLD}{Colors.BLUE}2. ✍️  Certyfikacja ręczna (jedna domena){Colors.RESET}")
        print(f"{Colors.BOLD}{Colors.MAGENTA}3. 📊 Wygeneruj raport ważności certyfikatów{Colors.RESET}")
        print(f"{Colors.BOLD}{Colors.YELLOW}4. ⚙️  Edytuj konfigurację{Colors.RESET}")
        print(f"{Colors.BOLD}{Colors.RED}5. 🚪 Wyjdź{Colors.RESET}")
        choice = input(f"\n{Colors.BOLD}Wybierz opcję (1-5): {Colors.RESET}").strip()

        if choice == "1":
            cert_auto(config, logger)
        elif choice == "2":
            domain = input(f"{Colors.WHITE}Domena: {Colors.RESET}").strip()
            email = input(f"{Colors.WHITE}E-mail: {Colors.RESET}").strip()
            provider = input(f"{Colors.WHITE}Provider (np. ovh, cloudflare) [Enter = domyślny]: {Colors.RESET}").strip() or None
            if not domain or not email:
                logger.error("Domena i e-mail są wymagane.")
                continue
            domain_entry = {"domain": domain, "email": email}
            if provider:
                domain_entry["provider"] = provider
            cert_for_domain(domain_entry, config["general"], config["providers"], logger)
        elif choice == "3":
            generate_cert_report(config, logger)
        elif choice == "4":
            edit_config_interactive(config_path, logger)
            try:
                config = load_config()
                logger.info("Konfiguracja przeładowana.")
            except Exception as e:
                logger.error(f"Nie udało się przeładować konfiguracji: {e}")
        elif choice == "5":
            print(f"{Colors.GREEN}Do zobaczenia! ✨{Colors.RESET}")
            break
        else:
            print(f"{Colors.BRIGHT_YELLOW}⚠️ Nieprawidłowy wybór. Spróbuj ponownie.{Colors.RESET}")

# ======================
# GŁÓWNA FUNKCJA
# ======================
def main():
    parser = argparse.ArgumentParser(
        description='xCert – Uniwersalna automatyzacja certyfikatów Let’s Encrypt z obsługą 13+ DNS providerów'
    )
    parser.add_argument('-a', '--auto', action='store_true', help="Automatyczna certyfikacja wszystkich domen")
    parser.add_argument('--email', help="Adres e-mail (tryb ręczny)")
    parser.add_argument('--domain', help="Domena (tryb ręczny)")
    parser.add_argument('--provider', help="Provider DNS (np. 'cloudflare', 'ovh') – tylko w trybie ręcznym")
    parser.add_argument('--debug', action='store_true', help="Szczegółowe logowanie (poziom DEBUG)")
    parser.add_argument('--report', action='store_true', help="Wygeneruj raport ważności certyfikatów")
    parser.add_argument('--menu', action='store_true', help="Interaktywne menu tekstowe")
    parser.add_argument('--edit-config', action='store_true', help="Edytuj config.json interaktywnie")

    args = parser.parse_args()

    # Inicjalizacja loggera
    logger = setup_logger(debug=args.debug)

    # Ładowanie konfiguracji
    try:
        config = load_config()
    except Exception as e:
        logger.critical(f"❌ Nie można załadować config.json: {e}")
        sys.exit(1)

    # Walidacja struktury
    if "domains" not in config or "providers" not in config or "general" not in config:
        logger.critical("❌ Błędny format config.json – brakuje sekcji 'domains', 'providers' lub 'general'")
        sys.exit(1)

    # Obsługa flag
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

    if args.auto:
        cert_auto(config, logger)
        return

    if args.email and args.domain:
        provider = args.provider or config["general"].get("default_provider", "ovh")
        if provider not in config["providers"]:
            logger.error(f"❌ Nieznany provider: {provider}. Dostępne: {', '.join(config['providers'].keys())}")
            sys.exit(1)
        domain_entry = {"domain": args.domain, "email": args.email, "provider": provider}
        cert_for_domain(domain_entry, config["general"], config["providers"], logger)
        return

    # Domyślnie: pokaż pomoc
    parser.print_help()

if __name__ == "__main__":
    main()