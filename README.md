# xCert

One installation per server that issues and renews Let's Encrypt certificates for every site on the machine, using Certbot's DNS-01 challenge. It works the same for sites served by nginx on the host and for sites running in Docker that mount the certificates.

- 13 DNS providers through the official Certbot plugins (OVH, Cloudflare, Route53, DigitalOcean, Google, Linode and more)
- Renews only what is close to expiry, groups `example.com` and `*.example.com` into one certificate
- Backs off and retries when a DNS provider rate-limits, with a timeout on every Certbot run
- One command sets up a server: Certbot, DNS plugins, systemd timer, SSH login status and log rotation
- Weekly systemd timer whose failures show up in the CLI and at SSH login, JSON validity report
- Interactive menu and config editor; three terminal looks with 24-bit, 16-colour or plain output
- Python 3 standard library only, no pip packages for xCert itself

## Requirements

- Linux with systemd (Debian 11+, Ubuntu 22.04+ or similar), run as root
- Python 3.9+
- Certbot with the DNS plugin for your provider and `openssl`; `xcert --install` sets both up

## Installation

### 1. xCert and everything around it

The repository is the installation: clone it (to `/opt/xcert` or anywhere else) and let `--install` set up the rest.

```bash
apt update && apt install -y git python3
git clone https://github.com/xotrikspl/xcert.git /opt/xcert
/opt/xcert/bin/xcert --install
```

`--install` runs as root, shows one row per step and can be run again at any time:

| Step | What it does |
|------|--------------|
| `config.json` | Copies `config.json.example` when there is no config yet (mode 600). |
| `secrets/`, `logs/` | Creates them; `secrets/` and the files in it become readable by root only. |
| `xcert` command | Symlinks `bin/xcert` into `/usr/local/bin`. |
| System packages | Installs `openssl`, plus `python3-venv` when Certbot is missing (apt only). |
| Certbot | When missing: a virtual environment in `/opt/certbot` (the pip install Certbot supports) linked to `/usr/bin/certbot`. |
| DNS plugins | `certbot-dns-*` for the providers your domains and `default_provider` use: with pip for the `/opt/certbot` venv, with snap for the Certbot snap. A Certbot from distribution packages gets the `apt install` line to run instead. |
| Units, MOTD, logrotate | Copies `etc/` into `/etc` with `/opt/xcert` replaced by the real location and Windows line endings removed. |
| Timer | `systemctl daemon-reload` and `systemctl enable --now xcert.timer`. |

It ends with the full `--status` screen, so a fresh install lists what is left to do: credentials and domains. Change providers in the config later? Run `--install` again to add their plugins.

`xcert --uninstall` removes the timer, units, MOTD script, logrotate file and the `xcert` symlink. Config, credentials, logs, certificates and Certbot stay.

```
/opt/xcert
├── bin/xcert           CLI wrapper, symlinked into /usr/local/bin
├── src/                xCert itself (main.py, ui.py, i18n.py, json_loader.py)
├── config/             config.json (yours, not in git) and config.json.example
├── secrets/            DNS provider credentials (not in git)
├── logs/               xCert logs and the JSON report (not in git)
├── etc/                systemd units, MOTD script and logrotate file that --install copies into /etc
└── tests/
```

Prefer Certbot from snap? Install it before `--install` (`snap install --classic certbot`) and `--install` adds the plugins as snaps. Available plugins: `certbot-dns-cloudflare`, `-digitalocean`, `-dnsimple`, `-dnsmadeeasy`, `-gehirn`, `-google`, `-linode`, `-luadns`, `-nsone`, `-ovh`, `-rfc2136`, `-route53`, `-sakuracloud`.

### Updating

```bash
xcert --update
```

As root, it runs `git pull --ff-only`, restarts itself with the pulled code, upgrades Certbot and every installed `certbot-*` plugin (pip in `/opt/certbot`, `snap refresh` for snaps; Certbot from distribution packages is left to `apt upgrade`), then does everything `--install` does and ends with `--status`. The first row shows the version before and after. A checkout with local commits fails the pull and stops before anything else changes. Your `config/config.json`, `secrets/` and `logs/` are ignored by git, so a pull never touches them. Without `--update` (xCert 1.7.0 or older): `cd /opt/xcert && git pull && xcert --install`.

Coming from 1.2 or older (`/root/.xcert`): move `/root/.xcert/config.json` to `/opt/xcert/config/config.json`, run `xcert --install` and remove `/root/.xcert`. Absolute paths in an old config keep working.

### 2. Domains

Add your domains with `xcert --edit-config` (or edit `config/config.json`, see [Configuration](#configuration)).

### 3. DNS provider credentials

Create a token with the smallest scope your provider allows (ideally only the DNS zones you need) and store it in `secrets/`, readable by root only. `credentials_path` in the example config already points there.

OVH example, `secrets/ovh.ini` (create the token at https://www.ovh.com/auth/api/createToken with `GET`, `PUT`, `POST`, `DELETE` on `/domain/zone/*`):

```ini
dns_ovh_endpoint = ovh-eu
dns_ovh_application_key = YOUR_APPLICATION_KEY
dns_ovh_application_secret = YOUR_APPLICATION_SECRET
dns_ovh_consumer_key = YOUR_CONSUMER_KEY
```

```bash
chmod 600 /opt/xcert/secrets/ovh.ini
```

Cloudflare uses `dns_cloudflare_api_token = YOUR_TOKEN` in `secrets/cloudflare.ini`. Every other plugin documents its file format in the Certbot docs. Route53 reads AWS credentials from the environment or an IAM role, so its provider has no `credentials_path`. `xcert --validate-config` warns when a credentials file is readable by anyone other than root.

### 4. Test

```bash
xcert --status                 # config, credentials, Certbot, plugins, units and the timer
xcert --auto --dry-run         # full run against the Let's Encrypt staging flow, nothing is issued
xcert --auto                   # issue the certificates
```

Let's Encrypt allows only 5 duplicate certificates per week, so test with `--dry-run` first.

## Usage

```bash
xcert                          # on a terminal: the interactive menu, otherwise this help
xcert --auto                   # renew everything that qualifies
xcert --auto --dry-run         # same, without issuing anything
xcert --domain shop.example.com --email admin@example.com [--provider cloudflare]
xcert --list                   # certificates, days left, next timer run
xcert --report                 # write the JSON validity report
xcert --status                 # everything below plus Certbot, plugins, units, timer and its last run
xcert --validate-config [--validate-domain example.com]   # config and credentials only
xcert --install                # set up the server (root), see Installation
xcert --update                 # git pull, newest Certbot and plugins, then --install (root)
xcert --uninstall              # undo --install, keeps config, credentials and certificates
xcert --menu                   # interactive menu
xcert --edit-config            # interactive config editor
xcert --motd                   # short status, used at SSH login
xcert --version
xcert --auto --debug           # verbose logging, combines with any flag
```

Every command accepts `--config PATH` (default: `config/config.json` in the repository), `--lang en|pl` and the appearance overrides below. `--auto` exits with code 1 when any certificate fails, so the systemd unit shows up as failed. `--status`, `--validate-config`, `--install` and `--update` exit with 1 when a row shows an error.

`--menu` and `--edit-config` work with the arrow keys and Enter; each item's letter is a shortcut and `q` or Esc goes back. Screens redraw in place instead of scrolling. In the editor, Enter on a domain lists what you can do with it (change domain or email, change provider, remove), and providers are picked from a list, so only domains, emails and the two commands (after renewal, `a` for the failure notice) are typed. Without a terminal (pipes, scripts) both read one line per choice instead.

On a terminal, `--auto` and `--domain` show one progress line per certificate (skipped, renewing, done or failed, with the reason) and `--status`, `--validate-config`, `--install` and `--update` show one row per check, all in the chosen look; the menu's Issue and Check use the same screens. The full log still goes to the log files; add `--debug` to see it on screen instead. Under systemd, cron and pipes both commands print the plain log as before.

`--validate-config` only checks the providers your domains use, plus `general.default_provider`; the others listed in `providers` need no credentials file and show up as one "unused providers" row. Interactive screens follow the terminal window: wider windows (up to 160 columns) stretch bars and columns, short ones scroll the domain list, and resizing the window (PuTTY, tmux) redraws the screen.

## Appearance

Three looks, all within 80 columns and without emoji so columns stay aligned in any terminal:

| Look | What it shows |
|------|---------------|
| `ledger` | Quiet table, colour only on certificate state. Reads best in logs. |
| `timeline` | 90-day axis per certificate with the renewal window and the weekly timer runs, plus which run renews what. |
| `console` | htop-style bars with state counters, validity meters and `[  OK  ]` tags. |

Colours: `auto` uses 24-bit colour when the terminal reports `COLORTERM=truecolor` (or runs in Windows Terminal) and the 16 ANSI colours otherwise; `full`, `16` and `none` force a mode. `NO_COLOR`, pipes, cron and journald always get plain text. `background` picks the palette for a `dark` or `light` terminal.

Change it in the menu (`t`, saved to `config.json`; `l` there switches the language) or for a single command:

```bash
xcert --list --theme timeline
xcert --list --theme console --colors 16 --background light
```

## Configuration

`config.json` has four sections. Relative paths are resolved from the repository root (`/opt/xcert`), not from the current working directory.

```json
{
  "general": {
    "default_provider": "ovh",
    "logs": 1,
    "output": "logs/xcert_output.log",
    "error": "logs/xcert_error.log",
    "report": "logs/xcert_report.json",
    "certbot_config_dir": "/etc/letsencrypt",
    "certbot_work_dir": "/var/lib/letsencrypt",
    "certbot_logs_dir": "/var/log/letsencrypt",
    "renew_threshold_days": 30,
    "post_renewal_cmd": "systemctl reload nginx",
    "notify_cmd": "",
    "max_retries": 3,
    "base_backoff_seconds": 60,
    "backoff_jitter_seconds": 15,
    "command_timeout_seconds": 900,
    "sleep_between_domains_seconds": 10,
    "dry_run": 0,
    "language": "en"
  },
  "appearance": {
    "theme": "ledger",
    "colors": "auto",
    "background": "dark"
  },
  "providers": {
    "ovh": {
      "cmd_template": "certbot certonly --dns-ovh --dns-ovh-credentials {credentials} --non-interactive --agree-tos",
      "credentials_path": "secrets/ovh.ini"
    },
    "cloudflare": {
      "cmd_template": "certbot certonly --dns-cloudflare --dns-cloudflare-credentials {credentials} --non-interactive --agree-tos",
      "credentials_path": "secrets/cloudflare.ini",
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
| `report` | `logs/xcert_report.json` | Where `--report` writes the JSON report. |
| `certbot_config_dir` | `/etc/letsencrypt` | Certbot `--config-dir`; certificates land in `<dir>/live/<name>/`, where `<name>` is the first domain of the group without `*.`. xCert passes it as `--cert-name`, so adding or removing a domain updates that certificate instead of creating `<name>-0001`. |
| `certbot_work_dir`, `certbot_logs_dir` | `/var/lib/letsencrypt`, `/var/log/letsencrypt` | Certbot `--work-dir` and `--logs-dir`. |
| `renew_threshold_days` | `30` | Skip certificates valid for more than this many days. A certificate inside the threshold is renewed with `--force-renewal`, so values above Certbot's own 30-day window work too. Set `0` to renew on every run, which burns Let's Encrypt limits. |
| `post_renewal_cmd` | empty | Command run after each successful renewal, skipped on dry runs. It is split like a shell command but not run through a shell, so `&&` and pipes do not work; call a script for several steps. A non-zero exit marks the certificate as failed, so `--auto` and the systemd unit fail too. |
| `max_retries` | `3` | Attempts per certificate. Only rate-limit errors are retried; a bad token fails at once. |
| `base_backoff_seconds`, `backoff_jitter_seconds` | `60`, `15` | Wait before retry `n` is `base * n` plus a random jitter. |
| `command_timeout_seconds` | `900` | Certbot run is killed after this long. |
| `sleep_between_domains_seconds` | `10` | Pause between certificates, keeps DNS APIs calm. |
| `dry_run` | `0` | `1` makes every run a dry run, same as `--dry-run`. |
| `language` | `en` | Language of the CLI, menu, `--help` and MOTD: `en` or `pl`. `--lang` overrides it for one command, `l` in the look screen saves it. Logs and the JSON report stay in English. |
| `notify_cmd` | empty | Command that gets the failure notice on stdin when the timer run fails, see [Failure notifications](#failure-notifications). |

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

The repository ships the units in `etc/systemd/system/`: `xcert.service` runs `--auto` as a oneshot job, `xcert.timer` starts it every Monday between 01:00 and 02:00 (a random delay keeps clients off the full hour, `Persistent=true` catches up after downtime), and `xcert-notify.service` sends the failure notice. The service waits for `network-online.target`, so a catch-up run right after boot does not fail on DNS. `xcert --install` installs and enables them, and `xcert --status` says when the installed copies differ from the repository.

```bash
systemctl start xcert.service      # one manual run
journalctl -u xcert.service        # its output, always plain text
```

`--list`, the menu and the SSH login status show the next run, "renewal running now" while the timer works, and a red line when the last timer run failed, so a failure shows up even without `notify_cmd`.

### Failure notifications

When `xcert.service` fails, whether a certificate failed, `post_renewal_cmd` failed or the run crashed or ran out of memory, systemd starts `xcert-notify.service` (`OnFailure=`). It runs `xcert --notify-failure`, which pipes a short notice with the last 40 journal lines into `general.notify_cmd` on stdin. The command is split like a shell command, not run through a shell:

```json
"notify_cmd": "mail -s 'xCert failed' admin@example.com"
"notify_cmd": "curl -fsS -H 'Title: xCert failed' --data-binary @- https://ntfy.sh/my-topic"
```

Use `--data-binary @-` with curl; plain `-d @-` drops the line breaks. Set it in the config editor (`xcert --edit-config` or menu `e`, then `a`) or in `config.json`; run `xcert --notify-failure` by hand to send a test notice. With `notify_cmd` empty nothing is sent.

## Status at SSH login

`etc/update-motd.d/99-cert-status` calls `xcert --motd` with a 5-second timeout, so a slow `openssl` or `systemctl` never blocks the login, and uses the same look as the CLI. `xcert --install` puts it into `/etc/update-motd.d/`; Debian and Ubuntu show it through `pam_motd` (`UsePAM yes` in `sshd_config`, the default).

## Log rotation

`xcert --install` copies `etc/logrotate.d/xcert` into `/etc/logrotate.d/`: weekly, 12 compressed copies of `logs/xcert_*.log`. Certbot rotates its own logs in `certbot_logs_dir`.

## Security

- Never commit `config.json` or credentials files; `.gitignore` already excludes `config/config.json`, `secrets/` and `logs/`.
- Keep credentials files at `chmod 600` and tokens scoped to the zones you need.
- Certbot runs without a shell, with argument lists and a timeout; domains and emails are validated before they reach the command.
- Config edits are written to a temporary file and swapped in atomically, so an interrupted save never leaves a broken `config.json`.

## Development

```bash
python3 -m unittest discover -s tests
```

The tests render every look in every colour mode and background, check the 80-column limit and the config save path, and run `--install`, `--update`, `--uninstall` and `--status` against a temporary directory. They never call Certbot or touch `/etc`.
