# Powiadomienia o błędach przez własny serwer ntfy

Instrukcja krok po kroku: xCert wysyła powiadomienie o nieudanym odnawianiu na Twój własny serwer [ntfy](https://docs.ntfy.sh/), a aplikacja ntfy pokazuje je na telefonie.

Za co odpowiada każdy element:

| Element | Rola |
|---------|------|
| DNS (np. OVH) | Kieruje nazwę `ntfy.example.com` na serwer. |
| xCert | Wystawia i odnawia certyfikat dla tej nazwy oraz wysyła powiadomienie (`notify_cmd`). Konfiguracji nginx nie zmienia. |
| ntfy | Przyjmuje powiadomienia i rozsyła je do telefonu. |
| nginx | Udostępnia ntfy z internetu po HTTPS, z certyfikatem od xCert. |

Wszystkie polecenia uruchamiasz na serwerze jako root. W przykładach `ntfy.example.com` zamień na swoją nazwę, a `me` na swój login.

## 1. Instalacja ntfy

Nie używaj samego `apt install ntfy`: w repozytorium Debiana pod tą nazwą jest inny program. Najpierw dodaj repozytorium ntfy:

```bash
mkdir -p /etc/apt/keyrings
curl -L -o /etc/apt/keyrings/ntfy.gpg https://archive.ntfy.sh/apt/keyring.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/ntfy.gpg] https://archive.ntfy.sh/apt stable main" \
    > /etc/apt/sources.list.d/ntfy.list
apt update && apt install ntfy
```

## 2. Konfiguracja ntfy

Zawartość `/etc/ntfy/server.yml`. Każda linia zaczyna się od początku wiersza, bez wcięcia:

```yaml
base-url: "https://ntfy.example.com"
listen-http: "127.0.0.1:2586"
behind-proxy: true
auth-file: "/var/lib/ntfy/user.db"
auth-default-access: "deny-all"
enable-login: true
require-login: true
```

- `listen-http`: ntfy słucha tylko lokalnie, bo port 80 zajmuje nginx.
- `auth-default-access: deny-all`: bez loginu albo tokenu nikt nie czyta ani nie wysyła.
- `require-login` działa tylko razem z `enable-login`. Bez tej drugiej opcji ntfy nie wystartuje (`cannot set require-login without also setting enable-login`).

Uruchom ntfy i sprawdź, czy działa:

```bash
systemctl enable --now ntfy
systemctl is-active ntfy && ls -l /var/lib/ntfy/
```

Powinno wypisać `active`, a w katalogu powinien być plik `user.db`, który ntfy tworzy przy pierwszym starcie. Jeśli ntfy padł kilka razy z rzędu, systemd przestaje go uruchamiać. Wtedy po poprawce configu zrób `systemctl reset-failed ntfy`, a potem `systemctl restart ntfy`. Powód błędu pokaże `journalctl -u ntfy -n 20 --no-pager`.

## 3. Konta

```bash
ntfy user add --role=admin me           # Ty: telefon i przeglądarka, poprosi o hasło
ntfy user add xcert                     # konto dla xCert, dowolne długie hasło
ntfy access xcert xcert write-only      # xCert może tylko wysyłać na temat "xcert"
ntfy token add --label=xcert xcert      # wypisze token tk_..., skopiuj go
```

Konto `xcert` nie może niczego czytać. Gdyby token wyciekł, nikt nie przeczyta nim Twoich powiadomień.

## 4. Token dla xCert

Token trzymaj w `secrets/` (poza gitem, tylko dla roota), a nie w `config.json`:

```bash
printf 'header = "Authorization: Bearer %s"\n' 'tk_TWOJ_TOKEN' > /opt/xcert/secrets/ntfy.curl
chmod 600 /opt/xcert/secrets/ntfy.curl
```

Potem `xcert --edit-config`, `a`, wklej poniższe polecenie i zapisz przez `s`:

```
curl -fsS -K /opt/xcert/secrets/ntfy.curl -H 'Title: xCert failed' --data-binary @- http://127.0.0.1:2586/xcert
```

- `-K` każe curlowi wczytać nagłówek z tokenem z pliku. Podaj pełną ścieżkę.
- Adres `127.0.0.1` omija nginx i certyfikat. Powiadomienie dojdzie nawet wtedy, gdy zepsuje się certyfikat, a właśnie o takim błędzie ma informować.

Sprawdź: `xcert --notify-failure` powinno wypisać `Failure notice sent`.

## 5. HTTPS dla telefonu

Telefon łączy się z ntfy z internetu, więc potrzebuje nazwy, certyfikatu i nginx.

1. **DNS:** w strefie domeny dodaj rekord `A` dla `ntfy` z IP serwera. Sprawdź: `getent hosts ntfy.example.com`.
2. **Certyfikat:** `xcert --edit-config`, potem `n`, wpisz `ntfy.example.com`, wybierz dostawcę, `s`. Następnie `xcert --auto`. Subdomena dostaje **osobny** certyfikat w `/etc/letsencrypt/live/ntfy.example.com/`. Wspólny certyfikat mają tylko `example.com`, `www.example.com` i `*.example.com`.
3. **nginx:** utwórz plik z blokiem poniżej. W nginx z nginx.org jest to `/etc/nginx/conf.d/ntfy.example.com.conf`, a końcówka `.conf` jest obowiązkowa, bo inne pliki nginx pomija. W nginx z Debiana użyj `sites-available/` i dowiązania w `sites-enabled/`.

   ```nginx
   server {
       listen 443 ssl;
       server_name ntfy.example.com;
       ssl_certificate     /etc/letsencrypt/live/ntfy.example.com/fullchain.pem;
       ssl_certificate_key /etc/letsencrypt/live/ntfy.example.com/privkey.pem;

       location / {
           proxy_pass http://127.0.0.1:2586;
           proxy_http_version 1.1;
           proxy_buffering off;
           proxy_request_buffering off;
           proxy_set_header Host $http_host;
           proxy_set_header Upgrade $http_upgrade;
           proxy_set_header Connection "upgrade";
           proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
           proxy_read_timeout 3m;
           client_max_body_size 0;
       }
   }
   ```

   Potem `nginx -t && systemctl reload nginx`. Przy kolejnych odnowieniach przeładowanie robi już xCert (`post_renewal_cmd`).
4. Sprawdź: `curl -sSI https://ntfy.example.com | head -1` powinno zwrócić `200`.

## 6. Telefon

1. W aplikacji ntfy otwórz **Ustawienia → Zarządzaj użytkownikami → Dodaj użytkownika** i wpisz serwer `https://ntfy.example.com` (bez tematu i bez ukośnika na końcu) oraz login i hasło z kroku 3.
2. Dopiero potem dodaj subskrypcję: „Użyj innego serwera” → `https://ntfy.example.com`, temat `xcert`. Subskrypcję założoną wcześniej, bez logowania, usuń i dodaj od nowa.
3. Na Androidzie zezwól aplikacji na powiadomienia i wyłącz dla niej oszczędzanie baterii.

## 7. Sprawdzenie na koniec

```bash
xcert --notify-failure                                                        # przychodzi na telefon
curl -s -o /dev/null -w '%{http_code}\n' -d test https://ntfy.example.com/xcert   # 403
```

Powiadomienie na telefonie oznacza, że xCert ma dostęp. Kod `403` oznacza, że obcy nie mają.

## Najczęstsze problemy

| Objaw | Przyczyna i rozwiązanie |
|-------|-------------------------|
| `curl: (6) Could not resolve host` | Brak rekordu DNS albo jeszcze się nie rozszedł. Sprawdź `getent hosts`. |
| `curl: (60) SSL: no alternative certificate subject name` | nginx pokazuje certyfikat innej strony: brak bloku `server` dla ntfy, plik bez `.conf` albo ścieżka do cudzego certyfikatu. |
| Adres ntfy otwiera inną stronę | To samo: nginx nie zna `server_name` ntfy i oddaje zapytanie domyślnemu blokowi. |
| `auth-file does not exist` | ntfy nie wystartował z nowym configiem. Zobacz `journalctl -u ntfy`, popraw config i zrób `systemctl reset-failed ntfy && systemctl restart ntfy`. |
| `notify_cmd failed (rc=22)` | ntfy odrzucił token. Sprawdź `ntfy token list xcert`, `ntfy access` i zawartość `secrets/ntfy.curl`. |
| xCert wysyła, telefon nic nie dostaje | W aplikacji nie dodano użytkownika dla serwera (krok 6.1) albo została stara subskrypcja. |
