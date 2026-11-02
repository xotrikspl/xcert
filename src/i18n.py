"""Translations for user-facing CLI, menu and MOTD texts.

The English text is the key, so English needs no table and a missing Polish
entry falls back to English instead of crashing. Logs stay English on purpose:
they get grepped and pasted into issues next to Certbot's own English output.
"""
from typing import Optional

LANGUAGES = ("en", "pl")
DEFAULT_LANGUAGE = "en"

PL = {
    # Status and counts
    "{n} days left": "zostało dni: {n}",
    "expired {n} days ago": "wygasł (dni temu: {n})",
    "no certificate": "niewystawiony",
    "certificate unreadable": "nieczytelny",
    "none": "brak",
    "n/a": "b/d",
    "expired": "wygasł",
    "ok": "ok",
    "due": "do odnowienia",
    "expiring": "pilne",
    "missing": "brak",
    "unreadable": "nieczytelne",
    "Mon": "pon",
    "Tue": "wt",
    "Wed": "śr",
    "Thu": "czw",
    "Fri": "pt",
    "Sat": "sob",
    "Sun": "ndz",
    "next run {when}": "następny przebieg {when}",
    "timer not active": "timer nieaktywny",
    "renewal running now": "odnawianie trwa",
    "last timer run failed ({when}), see {cmd}": "ostatni przebieg timera nieudany ({when}), zobacz {cmd}",
    "no certificates configured": "brak skonfigurowanych certyfikatów",
    "failed:": "nieudane:",
    "check {validate} and the error log, then run {auto}": "sprawdź {validate} i log błędów, potem uruchom {auto}",
    # Ledger
    "certificates": "certyfikaty",
    "certificate": "certyfikat",
    "provider": "dostawca",
    "expires": "wygasa",
    "days": "dni",
    "after renewal": "po odnowieniu",
    "renewal threshold {n} days": "próg odnowienia {n} dni",
    "renewed {n}": "odnowione {n}",
    "failed {n}": "nieudane {n}",
    "{n} certificates OK": "certyfikaty OK: {n}",
    "{n} certificates": "certyfikaty: {n}",
    "{n} need attention": "wymaga uwagi: {n}",
    "details:": "szczegóły:",
    # Timeline
    "today": "dziś",
    "valid": "ważny",
    "renewal window ({n} days)": "okno odnowienia ({n} dni)",
    "expiry": "wygaśnięcie",
    "timer run": "przebieg timera",
    "upcoming runs": "najbliższe przebiegi",
    "timer not active, nothing will renew on its own": "timer nieaktywny, nic nie odnowi się samo",
    "run took {elapsed}, 1 char = {scale}": "przebieg trwał {elapsed}, 1 znak = {scale}",
    "done": "gotowe",
    "failed": "błąd",
    "done {n}": "gotowe {n}",
    "skipped {n}": "pominięte {n}",
    "skipped": "pominięty",
    # Console (tags are exactly 6 characters wide)
    "  OK  ": "  OK  ",
    "RENEW ": "ODNÓW ",
    "URGENT": "PILNE ",
    "EXPIRD": "WYGASŁ",
    " NONE ": " BRAK ",
    " READ ": " BŁĄD ",
    " FAIL ": " BŁĄD ",
    "state": "stan",
    "validity": "ważność",
    "threshold {n} days": "próg {n} dni",
    "timer": "timer",
    "active": "aktywny",
    "not active": "nieaktywny",
    # Menu
    "renew everything that qualifies": "odnów wszystko, co się kwalifikuje",
    "issue a certificate for one domain": "wystaw certyfikat dla jednej domeny",
    "list certificates": "lista certyfikatów",
    "save validity report": "zapisz raport ważności",
    "edit configuration": "edytuj konfigurację",
    "check configuration and system": "sprawdź konfigurację i system",
    "change look and language": "zmień wygląd i język",
    "quit": "wyjdź",
    "Renew": "Odnów",
    "Issue": "Wystaw",
    "List": "Lista",
    "Report": "Raport",
    "Edit": "Edycja",
    "Check": "Sprawdź",
    "Look": "Wygląd",
    "Quit": "Wyjdź",
    # Look picker
    "look": "wygląd",
    "saved to config.json": "zapis w config.json",
    "{n} days": "{n} dni",
    "(now {mode})": "(teraz {mode})",
    "colours": "kolory",
    "background": "tło",
    "language": "język",
    "save": "zapisz",
    "back": "wróć",
    "auto": "auto",
    "full (24-bit)": "pełne (24-bit)",
    "16 colours": "16 kolorów",
    "dark": "ciemne",
    "light": "jasne",
    "one-off for a single command:": "jednorazowo dla jednej komendy:",
    "NO_COLOR, journald and cron always get plain text": "NO_COLOR, journald i cron zawsze dostają czysty tekst",
    # Prompts
    "Domain: ": "Domena: ",
    "Email: ": "E-mail: ",
    # Configuration editor
    "No domains configured.": "Brak skonfigurowanych domen.",
    "Cancel": "Anuluj",
    "New domain [Enter = {value}]: ": "Nowa domena [Enter = {value}]: ",
    "New email  [Enter = {value}]: ": "Nowy e-mail  [Enter = {value}]: ",
    "Cancelled, configuration unchanged.": "Anulowano, konfiguracja bez zmian.",
    # Keys, cursor and shared screens
    "↑↓ move   Enter select": "↑↓ przesuń   Enter wybierz",
    "↑↓ move   Enter select   q back": "↑↓ przesuń   Enter wybierz   q wróć",
    "move": "przesuń",
    "change": "zmień",
    "press any key to go back": "naciśnij dowolny klawisz, aby wrócić",
    "(current)": "(obecny)",
    "renewal": "odnawianie",
    "issue": "wystawianie",
    "checking": "sprawdzanie",
    "provider for {domain}": "dostawca dla {domain}",
    # Report
    "report saved": "raport zapisany",
    "closest to expiry: {name}, {status}": "najbliżej wygaśnięcia: {name}, {status}",
    # Configuration editor
    "configuration": "konfiguracja",
    "default provider": "domyślny dostawca",
    "logs": "logi",
    "enabled": "włączone",
    "disabled": "wyłączone",
    "file": "plik",
    "no.": "nr",
    "domain": "domena",
    "email": "e-mail",
    "on a domain:": "na domenie:",
    "more": "więcej",
    "add domain": "dodaj domenę",
    "command after renewal": "polecenie po odnowieniu",
    "failure notice command": "polecenie powiadomienia o błędzie",
    "Notify": "Alerty",
    "on failure": "przy błędzie",
    "no notice": "bez powiadomienia",
    "Failure notice command, gets the notice on stdin, - clears it [Enter = {value}]: ":
        "Polecenie powiadomienia (treść na stdin), - czyści [Enter = {value}]: ",
    "toggle logs": "włącz / wyłącz logi",
    "check configuration": "sprawdź konfigurację",
    "save and exit": "zapisz i wyjdź",
    "quit without saving": "wyjdź bez zapisu",
    "change domain or email": "zmień domenę lub e-mail",
    "change provider": "zmień dostawcę",
    "remove domain": "usuń domenę",
    "Add": "Dodaj",
    "Default": "Domyśl.",
    "Command": "Komenda",
    "Logs": "Logi",
    "Save": "Zapisz",
    "Provider": "Dostawca",
    "Remove": "Usuń",
    "Email [Enter = {value}]: ": "E-mail [Enter = {value}]: ",
    "Remove {domain}? (y/N) ": "Usunąć {domain}? (t/N) ",
    "Discard changes? (y/N) ": "Odrzucić zmiany? (t/N) ",
    "Command after renewal, - clears it [Enter = {value}]: ": "Polecenie po odnowieniu, - czyści [Enter = {value}]: ",
    # Check screen
    " WARN ": "UWAGA ",
    "config sections": "sekcje configu",
    "selected domains": "wybrane domeny",
    "provider {name}": "dostawca {name}",
    "domains and emails ({n})": "domeny i e-maile ({n})",
    "certificate names": "nazwy certyfikatów",
    "language and look": "język i wygląd",
    "errors {n}": "błędy {n}",
    "warnings {n}": "ostrzeżenia {n}",
    "all checks passed": "wszystko w porządku",
    # Renewal progress
    "skipped, valid for {n} more days": "pominięty, ważny jeszcze {n} dni",
    "renewing via {provider} ...": "odnawiam przez {provider} ...",
    "issuing via {provider} ...": "wystawiam przez {provider} ...",
    "done via {provider}": "odnowiono przez {provider}",
    "pause {n} s before the next certificate": "pauza {n} s przed następnym certyfikatem",
    "{n} more": "jeszcze {n}",
    "unused providers ({n})": "nieużywani dostawcy ({n})",
    # --help
    "xCert - Let's Encrypt certificate automation with 13+ DNS providers":
        "xCert - automatyzacja certyfikatów Let's Encrypt dla 13+ dostawców DNS",
    "Show this help message and exit": "Pokaż tę pomoc i wyjdź",
    "Show the version and exit": "Pokaż wersję i wyjdź",
    "Automatic certification for all configured domains": "Automatyczne certyfikaty dla wszystkich skonfigurowanych domen",
    "Email address (manual mode)": "Adres e-mail (tryb ręczny)",
    "Domain name (manual mode)": "Nazwa domeny (tryb ręczny)",
    "DNS provider (e.g. 'cloudflare', 'ovh'), manual mode only": "Dostawca DNS (np. 'cloudflare', 'ovh'), tylko tryb ręczny",
    "Dry run: passes --dry-run to certbot (no certificate issued)":
        "Próba na sucho: przekazuje --dry-run do certbota (bez wystawiania certyfikatu)",
    "Enable verbose debug logging": "Szczegółowe logi debugowania",
    "Generate a certificate validity report": "Wygeneruj raport ważności certyfikatów",
    "List all configured domains and their certificate status": "Pokaż skonfigurowane domeny i stan ich certyfikatów",
    "Launch interactive text menu": "Uruchom interaktywne menu",
    "Edit config.json interactively": "Edytuj config.json interaktywnie",
    "Validate config.json and provider credentials": "Sprawdź config.json i poświadczenia dostawców",
    "Limit --validate-config to specific domain(s), can be repeated":
        "Ogranicz --validate-config do wybranych domen, można powtarzać",
    "--validate-domain only works together with --validate-config or --status":
        "--validate-domain działa tylko razem z --validate-config lub --status",
    "Check config, certbot, DNS plugins, installed files and the timer":
        "Sprawdź config, certbota, wtyczki DNS, zainstalowane pliki i timer",
    "Install or update certbot, DNS plugins, the xcert command, systemd units, MOTD and logrotate (root)":
        "Zainstaluj lub zaktualizuj certbota, wtyczki DNS, komendę xcert, unity systemd, MOTD i logrotate (root)",
    "Remove what --install set up; keeps config, credentials, logs and certificates":
        "Usuń to, co ustawił --install; config, poświadczenia, logi i certyfikaty zostają",
    "Path to config.json (default: {path})": "Ścieżka do config.json (domyślnie: {path})",
    "Print the short certificate status used at SSH login": "Wypisz krótki status certyfikatów pokazywany przy logowaniu SSH",
    "Look for this run: ledger, timeline or console": "Wygląd na to uruchomienie: ledger, timeline lub console",
    "Colours for this run: auto, full (24-bit), 16 or none": "Kolory na to uruchomienie: auto, full (24-bit), 16 lub none",
    "Palette for a dark or light terminal": "Paleta dla ciemnego lub jasnego terminala",
    "Language for this run: en or pl": "Język na to uruchomienie: en lub pl",
    "Send the failure notice through general.notify_cmd (used by xcert-notify.service)":
        "Wyślij powiadomienie o błędzie przez general.notify_cmd (używa go xcert-notify.service)",
    # Status and install
    "status": "status",
    "install": "instalacja",
    "uninstall": "deinstalacja",
    "update": "aktualizacja",
    "already up to date": "już aktualne",
    "certbot update": "aktualizacja certbota",
    "git pull, upgrade certbot and its plugins, then what --install does (root)":
        "git pull, aktualizacja certbota i wtyczek, potem to co --install (root)",
    "DNS plugins": "wtyczki DNS",
    "none needed": "żadne niepotrzebne",
    "system packages": "pakiety systemowe",
    "nothing missing": "niczego nie brakuje",
    "installed into {path}": "zainstalowany w {path}",
    "permissions": "uprawnienia",
    "secrets readable by root only": "sekrety czytelne tylko dla roota",
    "xcert command": "komenda xcert",
    "units, MOTD, logrotate": "unity, MOTD, logrotate",
    "timer, units, MOTD, logrotate": "timer, unity, MOTD, logrotate",
    "{n} files": "plików: {n}",
    "{n} files removed": "usunięte pliki: {n}",
    "installed and current": "zainstalowane i aktualne",
    "removed": "usunięta",
    "kept": "zostaje",
    "config, secrets, logs and certificates in {path}": "config, sekrety, logi i certyfikaty w {path}",
    "last timer run": "ostatni przebieg timera",
    "no run yet": "jeszcze nie było",
    "failure notices": "powiadomienia o błędach",
    # Failure notice
    "xCert: certificate renewal failed on {host}": "xCert: odnawianie certyfikatów nie powiodło się na {host}",
    "Last lines of the xcert.service journal:": "Ostatnie linie journala xcert.service:",
    "Journal unavailable, check: journalctl -u xcert.service": "Journal niedostępny, sprawdź: journalctl -u xcert.service",
}

_language = DEFAULT_LANGUAGE


def set_language(language: Optional[str]) -> None:
    """Switches the language; unknown or empty values fall back to English."""
    global _language
    _language = language if language in LANGUAGES else DEFAULT_LANGUAGE


def current_language() -> str:
    return _language


def t(text: str, **values) -> str:
    """Translates an English text and fills its {placeholders}."""
    if _language == "pl":
        text = PL.get(text, text)
    return text.format(**values) if values else text
