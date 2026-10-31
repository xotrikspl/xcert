# xCert

One installation per server that issues and renews Let's Encrypt certificates for every site on the machine, using Certbot's DNS-01 challenge. It works the same for sites served by nginx on the host and for sites running in Docker that mount the certificates.

- 13 DNS providers through the official Certbot plugins (OVH, Cloudflare, Route53, DigitalOcean, Google, Linode and more)
- Renews only what is close to expiry, groups `example.com` and `*.example.com` into one certificate
- Backs off and retries when a DNS provider rate-limits, with a timeout on every Certbot run
- Weekly systemd timer, certificate status at SSH login, JSON validity report
- Interactive menu and config editor; three terminal looks with 24-bit, 16-colour or plain output
- Python 3 standard library only, no pip packages for xCert itself

## Requirements

- Linux with systemd (Debian 11+, Ubuntu 22.04+ or similar), run as root
- Python 3.9+
- Certbot with the DNS plugin for your provider
- `openssl` (certificate dates)

## Installation

### 1. Certbot and a DNS plugin

Pick one method. Both install Certbot to `/usr/bin/certbot` or `/snap/bin/certbot`, which is what `cmd_template` calls.

**pip in a virtual environment** (supported by Certbot only inside a venv):

```bash
apt update && apt install -y python3-venv openssl
python3 -m venv /opt/certbot
/opt/certbot/bin/pip install --upgrade pip
/opt/certbot/bin/pip install certbot certbot-dns-ovh   # swap the plugin for your provider
ln -sf /opt/certbot/bin/certbot /usr/bin/certbot
```

Upgrade later with `/opt/certbot/bin/pip install --upgrade certbot certbot-dns-ovh`.

**snap**:

```bash
snap install --classic certbot
snap set certbot trust-plugin-with-root=ok
snap install certbot-dns-ovh
```

Available plugins: `certbot-dns-cloudflare`, `-digitalocean`, `-dnsimple`, `-dnsmadeeasy`, `-gehirn`, `-google`, `-linode`, `-luadns`, `-nsone`, `-ovh`, `-rfc2136`, `-route53`, `-sakuracloud`.

### 2. xCert

```bash
git clone https://github.com/xotrikspl/xcert.git /tmp/xcert
mkdir -p /root/.xcert
cp /tmp/xcert/root/.xcert/*.py /tmp/xcert/root/.xcert/config.json.example /root/.xcert/
cp /tmp/xcert/root/.xcert/config.json.example /root/.xcert/config.json
install -m 755 /tmp/xcert/bin/xcert /usr/local/bin/xcert
```

### 3. DNS provider credentials

Create a token with the smallest scope your provider allows (ideally only the DNS zones you need), store it in a file readable by root only and point `credentials_path` at it.

OVH example (create the token at https://www.ovh.com/auth/api/createToken with `GET`, `PUT`, `POST`, `DELETE` on `/domain/zone/*`):

```ini
dns_ovh_endpoint = ovh-eu
dns_ovh_application_key = YOUR_APPLICATION_KEY
dns_ovh_application_secret = YOUR_APPLICATION_SECRET
dns_ovh_consumer_key = YOUR_CONSUMER_KEY
```

```bash
chmod 600 /root/.ovhapi
```

Cloudflare uses `dns_cloudflare_api_token = YOUR_TOKEN`. Every other plugin documents its file format in the Certbot docs. Route53 reads AWS credentials from the environment or an IAM role, so its provider has no `credentials_path`. `xcert --validate-config` warns when a credentials file is readable by anyone other than root.

### 4. Configure and test

Edit `/root/.xcert/config.json` (see [Configuration](#configuration)), then:

```bash
xcert --validate-config        # config, providers and credential files
xcert --auto --dry-run         # full run against the Let's Encrypt staging flow, nothing is issued
xcert --auto                   # issue the certificates
```

Let's Encrypt allows only 5 duplicate certificates per week, so test with `--dry-run` first.

## Usage

```bash
xcert --auto                   # renew everything that qualifies
xcert --auto --dry-run         # same, without issuing anything
xcert --domain shop.example.com --email admin@example.com [--provider cloudflare]
xcert --list                   # certificates, days left, next timer run
xcert --report                 # write the JSON validity report
xcert --validate-config [--validate-domain example.com]
xcert --menu                   # interactive menu
xcert --edit-config            # interactive config editor
xcert --motd                   # short status, used at SSH login
xcert --version
xcert --auto --debug           # verbose logging, combines with any flag
```

Every command accepts `--config PATH` (default: `config.json` next to `main.py`) and the appearance overrides below. `--auto` exits with code 1 when any certificate fails, so the systemd unit shows up as failed.

## Appearance

Three looks, all within 80 columns and without emoji so columns stay aligned in any terminal:

| Look | What it shows |
|------|---------------|
| `ledger` | Quiet table, colour only on certificate state. Reads best in logs. |
| `timeline` | 90-day axis per certificate with the renewal window and the weekly timer runs, plus which run renews what. |
| `console` | htop-style bars with state counters, validity meters and `[  OK  ]` tags. |

Colours: `auto` uses 24-bit colour when the terminal reports `COLORTERM=truecolor` (or runs in Windows Terminal) and the 16 ANSI colours otherwise; `full`, `16` and `none` force a mode. `NO_COLOR`, pipes, cron and journald always get plain text. `background` picks the palette for a `dark` or `light` terminal.

Change it in the menu (`t`, saved to `config.json`) or for a single command:

```bash
xcert --list --theme timeline
xcert --list --theme console --colors 16 --background light
```

## Configuration

`config.json` has four sections. Relative paths are resolved from the xCert directory, not from the current working directory.

```json
{
  "general": {
    "default_provider": "ovh",
    "logs": 1,
    "output": "/var/log/xcert_output.log",
    "error": "/var/log/xcert_error.log",
    "report": "/root/.xcert/xcert_report.json",
    "certbot_config_dir": "/etc/letsencrypt",
    "certbot_work_dir": "/var/lib/letsencrypt",
    "certbot_logs_dir": "/var/log/letsencrypt",
    "renew_threshold_days": 30,
    "post_renewal_cmd": "systemctl reload nginx",
    "max_retries": 3,
    "base_backoff_seconds": 60,
    "backoff_jitter_seconds": 15,
    "command_timeout_seconds": 900,
    "sleep_between_domains_seconds": 10,
    "dry_run": 0
  },
  "appearance": {
    "theme": "ledger",
    "colors": "auto",
    "background": "dark"
  },
  "providers": {
    "ovh": {
      "cmd_template": "certbot certonly --dns-ovh --dns-ovh-credentials {credentials} --non-interactive --agree-tos",
      "credentials_path": "/root/.ovhapi"
    },
    "cloudflare": {
      "cmd_template": "certbot certonly --dns-cloudflare --dns-cloudflare-credentials {credentials} --non-interactive --agree-tos",
      "credentials_path": "/root/.cloudflare.ini",
      "certbot_plugin_name": "dns-cloudflare",
      "propagation_seconds": 90
    }
  },
  "domains": [
    { "domain": "example.com", "email": "admin@example.com" },
    { "domain": "*.example.com", "email": "admin@example.com" },
    { "domain": "shop.example.net", "email": "admin@example.com", "provider": "cloudflare" }
  ]
}
```

`config.json.example` contains ready `providers` entries for all 13 plugins.

### `general`

| Key | Default | Meaning |
|-----|---------|---------|
| `default_provider` | `ovh` | Provider for domains without their own `provider`. |
| `logs` | `1` | `1` writes Certbot output to `output` / `error` (both required then), `0` prints it to the console. |
| `report` | `xcert_report.json` next to `main.py` | Where `--report` writes the JSON report. |
| `certbot_config_dir` | `/etc/letsencrypt` | Certbot `--config-dir`; certificates land in `<dir>/live/<name>/`. |
| `certbot_work_dir`, `certbot_logs_dir` | `/var/lib/letsencrypt`, `/var/log/letsencrypt` | Certbot `--work-dir` and `--logs-dir`. |
| `renew_threshold_days` | `30` | Skip certificates valid for more than this many days. Set `0` to renew on every run, which burns Let's Encrypt limits. |
| `post_renewal_cmd` | empty | Command run after each successful renewal. It is split like a shell command but not run through a shell, so `&&` and pipes do not work; call a script for several steps. |
| `max_retries` | `3` | Attempts per certificate. Only rate-limit errors are retried; a bad token fails at once. |
| `base_backoff_seconds`, `backoff_jitter_seconds` | `60`, `15` | Wait before retry `n` is `base * n` plus a random jitter. |
| `command_timeout_seconds` | `900` | Certbot run is killed after this long. |
| `sleep_between_domains_seconds` | `10` | Pause between certificates, keeps DNS APIs calm. |
| `dry_run` | `0` | `1` makes every run a dry run, same as `--dry-run`. |

`retry_count` and `retry_delay_seconds` from older configs still work as aliases of `max_retries` and `base_backoff_seconds`.

### `providers.<name>`

| Key | Meaning |
|-----|---------|
| `cmd_template` | Certbot command; `{credentials}` is replaced with `credentials_path`. xCert appends `--email`, `-d` for each domain and the three Certbot directories. |
| `credentials_path` | Credentials file for the plugin. Omit for providers that read the environment (Route53). |
| `propagation_seconds` | Optional DNS propagation wait, passed as `--<plugin>-propagation-seconds`. |
| `certbot_plugin_name` | Plugin name used for that flag, default `dns-<provider name>`. |

### `domains[]`

Each entry has `domain`, `email` and an optional `provider`. Entries with the same email, provider and base domain (`example.com`, `*.example.com`, `www.example.com`) become one certificate named after the first entry, so list the bare domain first.

## Sites in Docker

Run xCert on the host and give containers the certificates read-only. Mount the whole directory, because `live/` holds relative symlinks into `archive/`:

```yaml
services:
  nginx:
    container_name: my-nginx
    volumes:
      - /etc/letsencrypt:/etc/letsencrypt:ro
```

Reload the container after renewal with `post_renewal_cmd`, for example `docker exec my-nginx nginx -s reload`. When the server has host sites and containers, point `post_renewal_cmd` at a small script that reloads all of them.

## Automation

The repository ships both units in `etc/systemd/system/`: `xcert.service` runs `--auto` as a oneshot job, `xcert.timer` starts it every Monday at 01:00 (`Persistent=true` catches up after downtime).

```bash
cp /tmp/xcert/etc/systemd/system/xcert.{service,timer} /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now xcert.timer
systemctl start xcert.service      # one manual run
journalctl -u xcert.service        # its output, always plain text
```

## Status at SSH login

`etc/update-motd.d/99-cert-status` calls `xcert --motd` with a 5-second timeout, so a slow `openssl` or `systemctl` never blocks the login, and uses the same look as the CLI.

```bash
install -m 755 /tmp/xcert/etc/update-motd.d/99-cert-status /etc/update-motd.d/
```

Debian and Ubuntu show it through `pam_motd` (`UsePAM yes` in `sshd_config`, the default). If the files were copied from Windows, strip the line endings first: `sed -i 's/\r$//' <file>`. Set `XCERT_ROOT` in the script when xCert lives somewhere other than `/root/.xcert`.

## Log rotation

```
/var/log/xcert_*.log {
    weekly
    rotate 12
    compress
    delaycompress
    missingok
    notifempty
    create 640 root adm
}
```

Save as `/etc/logrotate.d/xcert`. Certbot rotates its own logs in `certbot_logs_dir`.

## Security

- Never commit `config.json` or credentials files; the repository's `.gitignore` already excludes `config.json`.
- Keep credentials files at `chmod 600` and tokens scoped to the zones you need.
- Certbot runs without a shell, with argument lists and a timeout; domains and emails are validated before they reach the command.
- Config edits are written to a temporary file and swapped in atomically, so an interrupted save never leaves a broken `config.json`.

## Development

```bash
python3 -m unittest discover -s tests
```

The tests render every look in every colour mode and background, check the 80-column limit and the config save path. They never call Certbot or touch `/etc`.
