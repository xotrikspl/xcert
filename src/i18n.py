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
    "choose ›": "wybierz ›",
    # Menu
    "renew everything that qualifies": "odnów wszystko, co się kwalifikuje",
    "issue a certificate for one domain": "wystaw certyfikat dla jednej domeny",
    "list certificates": "lista certyfikatów",
    "save validity report": "zapisz raport ważności",
    "edit configuration": "edytuj konfigurację",
    "check configuration and credentials": "sprawdź konfigurację i poświadczenia",
    "change look": "zmień wygląd",
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
    "Enter domains to validate (comma-separated, Enter = all):":
        "Podaj domeny do sprawdzenia (po przecinku, Enter = wszystkie):",
    "Available domains:": "Dostępne domeny:",
    "Domains: ": "Domeny: ",
    "Domain: ": "Domena: ",
    "Email: ": "E-mail: ",
    "Provider (e.g. ovh, cloudflare) [Enter = default]: ": "Dostawca (np. ovh, cloudflare) [Enter = domyślny]: ",
    # Configuration editor
    "Warning: No default provider set in configuration.": "Uwaga: w konfiguracji nie ustawiono domyślnego dostawcy.",
    "Available providers: {names}": "Dostępni dostawcy: {names}",
    "Enter default provider: ": "Podaj domyślnego dostawcę: ",
    "EDIT xCert CONFIGURATION": "EDYCJA KONFIGURACJI xCert",
    "Default provider:": "Domyślny dostawca:",
    "Logging:": "Logowanie:",
    "Enabled": "Włączone",
    "Disabled": "Wyłączone",
    "Configured domains ({n}):": "Skonfigurowane domeny ({n}):",
    "email:": "e-mail:",
    "provider:": "dostawca:",
    "No domains configured.": "Brak skonfigurowanych domen.",
    "What would you like to do?": "Co chcesz zrobić?",
    "Add domain": "Dodaj domenę",
    "Remove domain": "Usuń domenę",
    "Edit domain / email": "Edytuj domenę / e-mail",
    "Change domain provider": "Zmień dostawcę domeny",
    "Change default provider": "Zmień domyślnego dostawcę",
    "Toggle logging": "Włącz / wyłącz logowanie",
    "Validate config": "Sprawdź konfigurację",
    "Save and exit": "Zapisz i wyjdź",
    "Cancel": "Anuluj",
    "Choose option (1-9): ": "Wybierz opcję (1-9): ",
    "Use default provider ({name})? (Y/n): ": "Użyć domyślnego dostawcy ({name})? (T/n): ",
    "Provider (available: {names}): ": "Dostawca (dostępni: {names}): ",
    "No domains to remove.": "Brak domen do usunięcia.",
    "No domains to edit.": "Brak domen do edycji.",
    "Domain number to remove: ": "Numer domeny do usunięcia: ",
    "Domain number to edit: ": "Numer domeny do edycji: ",
    "Domain number to change provider: ": "Numer domeny do zmiany dostawcy: ",
    "Invalid number.": "Nieprawidłowy numer.",
    "Please enter a number.": "Podaj liczbę.",
    "New domain [Enter = {value}]: ": "Nowa domena [Enter = {value}]: ",
    "New email  [Enter = {value}]: ": "Nowy e-mail  [Enter = {value}]: ",
    "New provider (available: {names}): ": "Nowy dostawca (dostępni: {names}): ",
    "New default provider (available: {names}): ": "Nowy domyślny dostawca (dostępni: {names}): ",
    "Cancelled, configuration unchanged.": "Anulowano, konfiguracja bez zmian.",
    "Invalid choice.": "Nieprawidłowy wybór.",
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
    "Path to config.json (default: {path})": "Ścieżka do config.json (domyślnie: {path})",
    "Print the short certificate status used at SSH login": "Wypisz krótki status certyfikatów pokazywany przy logowaniu SSH",
    "Look for this run: ledger, timeline or console": "Wygląd na to uruchomienie: ledger, timeline lub console",
    "Colours for this run: auto, full (24-bit), 16 or none": "Kolory na to uruchomienie: auto, full (24-bit), 16 lub none",
    "Palette for a dark or light terminal": "Paleta dla ciemnego lub jasnego terminala",
    "Language for this run: en or pl": "Język na to uruchomienie: en lub pl",
}

_language = DEFAULT_LANGUAGE


def set_language(language: Optional[str]) -> None:
    """Switches the language; unknown or empty values fall back to English."""
    global _language
    _language = language if language in LANGUAGES else DEFAULT_LANGUAGE


def t(text: str, **values) -> str:
    """Translates an English text and fills its {placeholders}."""
    if _language == "pl":
        text = PL.get(text, text)
    return text.format(**values) if values else text
