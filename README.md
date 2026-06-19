# xCert – Universal Let's Encrypt Certificate Manager

Automated SSL/TLS certificate renewal using DNS challenges for 13+ providers (OVH, Cloudflare, AWS Route53, DigitalOcean, and more), with MOTD integration, certificate reporting, input validation, and an interactive CLI.

## 1. System Requirements

Install the required packages before running:

* **Python 3** and **python3-pip**
* **Certbot and DNS plugins** (install only the ones you need):
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
* **System utilities** (required for MOTD and reports):
```bash
apt update && apt install jq curl openssl -y
```

## 2. DNS Provider Credentials (example: OVH)

1. Go to [OVH API Create Token](https://www.ovh.com/auth/api/createToken).
2. Grant permissions: `GET, PUT, POST, DELETE` on path `/domain/zone/*`.
3. Save credentials to a file (e.g. `/root/.ovhapi`):
```
dns_ovh_endpoint = ovh-eu
dns_ovh_application_key = YOUR_KEY
dns_ovh_application_secret = YOUR_SECRET
dns_ovh_consumer_key = YOUR_CONSUMER_KEY
```
4. Secure the file:
```bash
chmod 600 /root/.ovhapi
```

> For other providers (Cloudflare, AWS, etc.) create the appropriate credentials files as described in the Certbot documentation.

## 3. Configuration (`config.json`)

Copy the example config and edit it:
```bash
cp /root/.xcert/config.json.example /root/.xcert/config.json
```

Full structure with all available options:

```json
{
  "general": {
    "default_provider": "ovh",
    "logs": 1,
    "output": "/var/log/xcert_output.log",
    "error": "/var/log/xcert_error.log",
    "renew_threshold_days": 30,
    "post_renewal_cmd": "systemctl reload nginx",
    "retry_count": 2,
    "retry_delay_seconds": 30
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

### Configuration reference

| Key | Section | Description |
|-----|---------|-------------|
| `default_provider` | `general` | Fallback provider used when a domain has no explicit provider set |
| `logs` | `general` | `1` = write certbot output to log files, `0` = print to console |
| `output` | `general` | Path for certbot stdout log (required when `logs=1`) |
| `error` | `general` | Path for certbot stderr log (required when `logs=1`) |
| `renew_threshold_days` | `general` | Skip renewal if the certificate expires in more than this many days (saves Let's Encrypt rate limits). Set to `0` to always renew. |
| `post_renewal_cmd` | `general` | Shell command to run after each successful renewal (e.g. `systemctl reload nginx`). Leave empty to disable. |
| `retry_count` | `general` | Number of retry attempts on certbot failure. `0` = no retries. |
| `retry_delay_seconds` | `general` | Seconds to wait between retry attempts. |
| `cmd_template` | `providers.*` | Certbot command template. Use `{credentials}` as a placeholder for the credentials file path. |
| `credentials_path` | `providers.*` | Path to the DNS provider credentials file. Set to `null` for providers that use environment variables or IAM roles (e.g. route53). |

## 4. Usage

```bash
# Renew all domains from config.json
xcert --auto

# Renew a single domain manually
xcert --domain example.com --email admin@example.com --provider cloudflare

# Test configuration without issuing a certificate
xcert --auto --dry-run
xcert --domain example.com --email admin@example.com --dry-run

# Show all domains and their certificate status
xcert --list

# Generate a JSON validity report (saved to /root/.xcert/xcert_report.json)
xcert --report

# Launch the interactive text menu
xcert --menu

# Edit configuration interactively
xcert --edit-config

# Show version
xcert --version

# Enable verbose debug logging (combine with any flag)
xcert --auto --debug
```

## 5. Automation (Systemd Timer)

Recommended schedule: **weekly on Monday at 01:00**.

### A. Service (`/etc/systemd/system/xcert.service`)
```ini
[Unit]
Description=xCert SSL Renewal Service
After=network.target

[Service]
Type=oneshot
ExecStart=/usr/bin/python3 /root/.xcert/main.py --auto
WorkingDirectory=/root/.xcert/
User=root
MemoryMax=100M
```

### B. Timer (`/etc/systemd/system/xcert.timer`)
```ini
[Unit]
Description=Run xCert weekly

[Timer]
OnCalendar=Mon *-*-* 01:00:00
Persistent=true

[Install]
WantedBy=timers.target
```

Enable and start:
```bash
systemctl daemon-reload
systemctl enable --now xcert.timer
```

Manual test run:
```bash
systemctl start xcert.service
```

## 6. MOTD Integration

Display certificate status at SSH login:

1. Place the script at `/etc/update-motd.d/99-cert-status`
2. Make it executable: `chmod +x /etc/update-motd.d/99-cert-status`
3. Fix Windows line endings if copied from Windows: `sed -i 's/\r$//' /etc/update-motd.d/99-cert-status`
4. Ensure `/etc/ssh/sshd_config` has `PrintMotd yes` (or `UsePAM yes` on Debian/Ubuntu) and restart SSH: `systemctl restart ssh`

## 7. Log Rotation

Prevent log files from filling up the disk:

```
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

Save this to `/etc/logrotate.d/xcert`.
