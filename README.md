# **xCert – Uniwersalny menedżer certyfikatów Let’s Encrypt**

Automatyzacja odnawiania certyfikatów SSL/TLS z wykorzystaniem DNS challenge dla ponad 13 dostawców (OVH, Cloudflare, AWS Route53, DigitalOcean i innych), z integracją MOTD, raportowaniem, walidacją danych oraz interaktywnym CLI.

## **1. Wymagania systemowe**

Przed uruchomieniem zainstaluj niezbędne pakiety:

* **Python 3** oraz **python3-pip**
* **Certbot i pluginy DNS** (wybierz potrzebne):
```bash
pip install certbot \
            certbot-dns-ovh \
            certbot-dns-cloudflare \
            certbot-dns-digitalocean \
            certbot-dns-dnsimple \
            certbot-dns-dnsmadeeasy \
            certbot-dns-gehirn \
            certbot-dns-google \
            certbot-dns-linode \
            certbot-dns-luadns \
            certbot-dns-nsone \
            certbot-dns-rfc2136 \
            certbot-dns-route53 \
            certbot-dns-sakuracloud
```
* **Narzędzia systemowe** (do MOTD i raportów):
```
apt update && apt install jq curl openssl -y
```

## **2. Konfiguracja API dostawcy (przykład: OVH)**

1. Przejdź do [OVH API Create Token](https://www.ovh.com/auth/api/createToken).
2. Nadaj uprawnienia: `GET, PUT, POST, DELETE` do ścieżki `/domain/zone/*`.
3. Zapisz dane do pliku (np. `/root/.ovhapi`):
```
dns_ovh_endpoint = ovh-eu
dns_ovh_application_key = TWOJ_KEY
dns_ovh_application_secret = TWOJ_SECRET
dns_ovh_consumer_key = TWOJ_CONSUMER_KEY
```
4. Zabezpiecz plik:
```
chmod 600 /root/.ovhapi
```

> Dla innych dostawców (Cloudflare, AWS itp.) przygotuj odpowiednie pliki poświadczeń zgodnie z dokumentacją Certbota.

## **3. Konfiguracja skryptu (`config.json`)**

Struktura pliku obsługuje **wiele domen i providerów**:

```
{
  "general": {
    "default_provider": "ovh",
    "logs": 1,
    "output": "/var/log/xcert_output.log",
    "error": "/var/log/xcert_error.log"
  },
  "providers": {
    "ovh": {
      "cmd_template": "certbot certonly --dns-ovh --dns-ovh-credentials {credentials} --non-interactive --agree-tos",
      "credentials_path": "/root/.ovhapi"
    },
    "cloudflare": {
      "cmd_template": "certbot certonly --dns-cloudflare --dns-cloudflare-credentials {credentials} --non-interactive --agree-tos",
      "credentials_path": "/root/.cloudflare.ini"
    }
  },
  "domains": [
    {
      "domain": "example.com",
      "email": "admin@example.com",
      "provider": "ovh"
    },
    {
      "domain": "blog.example.org",
      "email": "admin@example.com",
      "provider": "cloudflare"
    }
  ]
}
```

## **4. Automatyzacja (Systemd Timer)**

Zalecane: odnawianie **co tydzień** (poniedziałek, 01:00).

### **A. Usługa** (`/etc/systemd/system/xcert.service`)
```
[Unit]
Description=xCert SSL Renewal Service
After=network.target

[Service]
Type=oneshot
ExecStart=/usr/bin/python3 /root/.xcert/main.py --auto
WorkingDirectory=/root/.xcert/
User=root
```

### **B. Timer** (`/etc/systemd/system/xcert.timer`)
```
[Unit]
Description=Uruchamia xCert raz w tygodniu

[Timer]
OnCalendar=Mon *-*-* 01:00:00
Persistent=true

[Install]
WantedBy=timers.target
```

Po zapisaniu wykonaj poniższe polecenia:
```
systemctl daemon-reload
systemctl enable --now xcert.timer
```
Test ręczny:
```
systemctl start xcert.service
```

## **5. Status w MOTD (Podgląd przy logowaniu)**

1. Umieść skrypt w: `/etc/update-motd.d/99-cert-status`
2. Nadaj uprawnienia: `chmod +x /etc/update-motd.d/99-cert-status`
3. Napraw ewentualne znaki Windows (jeśli kopiowano): `sed -i 's/\r$//' /etc/update-motd.d/99-cert-status`
4. Upewnij się, że w `/etc/ssh/sshd_config` masz `#PrintMotd yes` lub `PrintMotd no` i zrestartuj SSH: `systemctl restart ssh`

## **6. Logrotate (Zarządzanie logami)**

Aby uniknąć przepełnienia dysku, skonfiguruj rotację logów:

```bash
/var/log/xcert_*.log /var/log/letsencrypt/*.log {
	weekly
	rotate 12
	compress
	delaycompress
	missingok
	notifempty
	create 640 root adm
}
```

## **7. Użycie**
* **Automatyczna certyfikacja**: `python3 main.py --auto`
* **Ręczna (jedna domena)**: `python3 main.py --domain example.com --email admin@example.com --provider cloudflare`
* **Interaktywne menu**: `python3 main.py --menu`
* **Generuj raport ważności**: `python3 main.py --report`
* **Edytuj konfigurację w terminalu**: `python3 main.py --edit-config`
* **Tryb debugowania**: Dodaj flagę `--debug` do dowolnej komendy

> Raporty zapisywane są w katalogu, z którego uruchamiany jest skrypt (domyślnie: ./xcert_report.json). Przy użyciu systemd — w katalogu roboczym usługi (/root/.xcert/).