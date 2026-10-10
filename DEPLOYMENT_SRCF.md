# Deploying the MSS portal to the SRCF

A runbook for putting this Django app on the SRCF under the society's group
account, and for handing it over to future committees. Everything here was
checked against https://docs.srcf.net in July 2026 — the specific pages are
linked so you can re-verify details that may have changed.

Throughout, `<soc>` is the society's SRCF group account short name (e.g.
`mss`), and paths follow SRCF conventions:

- **Private space** (code, venv, secrets, database dumps, member photos):
  `/societies/<soc>/`
- **Public space** (served by Apache, world-readable to all SRCF users —
  never put secrets or member data here): `/public/societies/<soc>/public_html/`

## 0. Prerequisites

1. A society SRCF account (https://www.srcf.net/signup — needs two members;
   the account is shared, so everything below is documented for handover).
2. SSH access for at least one admin. Web apps run on the dedicated web
   server: `ssh <crsid>@webserver.srcf.net`.
   Docs: https://docs.srcf.net/reference/web-hosting/web-applications/

## 1. Python and code

The SRCF software page has historically lagged (it long listed Python 3.8);
this app needs **Python ≥ 3.10** (Django 5.2). Check `python3 --version` on
webserver.srcf.net; if it's too old, install a modern Python with pyenv in the
society account (https://docs.srcf.net/reference/shell-and-files/software-and-installation/).

```bash
ssh webserver.srcf.net
cd /societies/<soc>/
git clone <this repo> portal && cd portal
python3 -m venv .venv                      # or ~/.pyenv/versions/3.12.x/bin/python -m venv
.venv/bin/pip install -r requirements.txt gunicorn
# database driver: psycopg[binary] for PostgreSQL, mysqlclient for MySQL
.venv/bin/pip install "psycopg[binary]"
```

## 2. Database

Request the society PostgreSQL database via https://control.srcf.net (MySQL
also works; set `DB_ENGINE=mysql` instead). Canonical hostname from any SRCF
server is `postgres` (or `mysql`).
Docs: https://docs.srcf.net/reference/other-services/sql-databases/

## 3. Environment file

Keep secrets in **private** space with tight permissions:

```bash
cat > /societies/<soc>/portal/.env <<'EOF'
DJANGO_SECRET_KEY=<generate: python -c "import secrets; print(secrets.token_urlsafe(50))">
DJANGO_DEBUG=false
DJANGO_ALLOWED_HOSTS=<soc>.soc.srcf.net,www.cambridgematuresoc.com,cambridgematuresoc.com
DJANGO_CSRF_TRUSTED_ORIGINS=https://<soc>.soc.srcf.net,https://www.cambridgematuresoc.com
DB_ENGINE=postgres
DB_NAME=<soc>
DB_USER=<soc>
DB_PASSWORD=<from control.srcf.net>
DB_HOST=postgres
RAVEN_MODE=header
REAL_EMAIL=true
DEFAULT_FROM_EMAIL="MSS <<soc>-webmaster@srcf.net>"
EOF
chmod 600 /societies/<soc>/portal/.env
```

Quote any value that has spaces or angle brackets (the From address above):
`.env` is read by bash, and an unquoted `<` stops the service from starting.

## 4. Gunicorn on a UNIX socket + systemd

SRCF's Apache reverse-proxies to your app; a **UNIX socket is required in
practice** (TCP ports on the shared server are reachable by other users —
documented as less secure, and it would also let them spoof the Raven header,
see §7). Never run `manage.py runserver` on SRCF.
Docs: https://docs.srcf.net/tutorials/websites/deploy-a-web-app/

`/societies/<soc>/portal/run.sh`:

```bash
#!/bin/bash
# Fail loudly rather than booting gunicorn with a half-loaded environment.
# The app itself refuses to start in a production auth mode (RAVEN_MODE!=dev)
# unless DJANGO_DEBUG=false and a real DJANGO_SECRET_KEY are set; this wrapper
# additionally guarantees .env was actually read before that check runs.
set -euo pipefail
cd /societies/<soc>/portal
[ -r .env ] || { echo "run.sh: .env missing or unreadable" >&2; exit 1; }
set -a; source .env; set +a
.venv/bin/python manage.py check          # aborts the boot on unsafe config
.venv/bin/python manage.py collectstatic --noinput || echo "run.sh: collectstatic failed; serving the files already on disk (see Site health)" >&2
exec .venv/bin/gunicorn -w 2 \
  -b unix:/societies/<soc>/portal/web.sock \
  --log-file - config.wsgi:application
```

`/societies/<soc>/.config/systemd/user/mssportal.service`:

```ini
[Unit]
Description=MSS portal (Django/gunicorn)
ConditionHost=sinkhole

[Service]
WorkingDirectory=/societies/<soc>/portal
ExecStart=/societies/<soc>/portal/run.sh
ExecReload=/bin/kill -HUP $MAINPID
Restart=always

[Install]
WantedBy=default.target
```

Enable (survives reboots; lingering can take ~20 min to activate):

```bash
sudo -Hu <soc> XDG_RUNTIME_DIR=/run/user/$(id -u <soc>) systemctl --user enable --now mssportal
```

## 5. First deploy

```bash
cd /societies/<soc>/portal
set -a; source .env; set +a
.venv/bin/python manage.py check --deploy   # prod-hardening warnings; refuses on unsafe config
.venv/bin/python manage.py migrate
.venv/bin/python manage.py seed_demo --categories-only   # event categories only, no demo data
.venv/bin/python manage.py createsuperuser               # Django admin at /dj-admin/
.venv/bin/python manage.py collectstatic --noinput
# expose static files to Apache (public space):
ln -s /societies/<soc>/portal/staticfiles /public/societies/<soc>/public_html/static
```

Then in the portal (`/dj-admin/` → Site configuration, or /admin/): set the
WhatsApp Open Forum invite link, mailing list address, and promote the first
committee accounts to portal admins.

**Media/member photos**: `MEDIA_ROOT` stays in private space
(`/societies/<soc>/portal/media/`) and is served through Django: pictures
for the pages (`media/public/`) to anyone, everything else to logged-in
members only — do **not** symlink it into `public_html` (public space is
world-readable and would publish member photos).

## 6. Apache routing (.htaccess)

`/public/societies/<soc>/public_html/.htaccess`:

```apache
DirectoryIndex disabled
RewriteEngine On

# Never trust a client-supplied Raven header (Apache re-sets it in §7).
RequestHeader unset X-MSS-Remote-User

# Forwarding headers the app relies on (SECURE_PROXY_SSL_HEADER):
RequestHeader set Host expr=%{HTTP_HOST}
RequestHeader set X-Forwarded-Proto expr=%{REQUEST_SCHEME}

# Apache serves static files directly:
RewriteCond %{REQUEST_URI} !^/static/
# /accounts/raven/ is proxied by its own .htaccess after Raven auth (§7):
RewriteCond %{REQUEST_URI} !^/accounts/raven/
RewriteRule ^(.*)$ unix:/societies/<soc>/portal/web.sock|http://%{HTTP_HOST}/$1 [P,NE,L,QSA]
```

Docs (exact RequestHeader lines):
https://docs.srcf.net/reference/web-hosting/web-applications/

## 7. Raven login — Track A (SRCF Nevar, zero registration; `RAVEN_MODE=header`)

Background: the University retired Legacy Raven in December 2024. SRCF's
Apache still ships `mod_ucam_webauth`, now pointed at **Nevar**, the SRCF's
Ucam-WebAuth bridge that authenticates against real University Accounts
(Entra ID + MFA). The module only sets `REMOTE_USER` inside Apache, so we
forward it to Django as a header on exactly one path.
Docs: https://docs.srcf.net/reference/web-hosting/university-account-authentication/

Create a real directory so its `.htaccess` applies to the `/accounts/raven/`
URL, authenticating **before** proxying:

```bash
mkdir -p /public/societies/<soc>/public_html/accounts/raven
```

`/public/societies/<soc>/public_html/accounts/raven/.htaccess`:

```apache
AuthType Ucam-WebAuth
Require valid-user
# Optional: allow non-current (graduated) University accounts too:
# AARequiredPtags none

RewriteEngine On
RequestHeader set X-MSS-Remote-User "expr=%{REMOTE_USER}"
RequestHeader set X-Forwarded-Proto expr=%{REQUEST_SCHEME}
RewriteRule ^(.*)$ unix:/societies/<soc>/portal/web.sock|http://%{HTTP_HOST}/accounts/raven/$1 [P,NE,L,QSA]
```

How it flows: "Log in with Raven" → `/accounts/raven/` → Apache redirects to
Nevar → University Account login (with MFA) → back with the CRSid in
`REMOTE_USER` → forwarded as `X-MSS-Remote-User` → Django's
`NevarRemoteUserMiddleware` + `CrsidBackend` (see `accounts/auth.py`) create
or fetch the member and start a session → redirect to their dashboard.

**Security invariants — re-check these whenever the .htaccess changes:**
1. The root `.htaccess` **unsets** `X-MSS-Remote-User` on every request, so a
   client can never smuggle it in on other paths.
2. The app listens on a UNIX socket (not TCP), so other SRCF users cannot
   bypass Apache and inject the header directly.
3. `RAVEN_MODE=header` is only set in production where 1–2 hold.

Verify after setup: visit `/accounts/raven/` in a private window → you should
bounce via nevar.srcf.net to a University login and land on the profile-setup
page with your CRSid attached (check /accounts/profile/).

*Caveat:* Nevar and the UIS Ucam-WebAuth proxy are bridge services without a
published support commitment. If they are ever retired, switch to Track B.

## 8. Raven login — Track B (first-party OIDC; `RAVEN_MODE=oidc`)

The forward-looking option: authenticate directly against the University's
Microsoft Entra tenant (`49a50445-bdfa-4b79-ade3-547b4f3986e9`).

1. Register an app at **UIS Toolkit** (https://toolkit.uis.cam.ac.uk) with
   redirect URI `https://<soc>.soc.srcf.net/oidc/callback/`. Toolkit access
   is aimed at institutional Computer Officers; if a student account can't
   create a personal registration, ask your college Computer Officer or
   servicedesk@uis.cam.ac.uk to sponsor one. Client secrets last at most
   24 months — **diarise rotation**.
2. `pip install mozilla-django-oidc`, then **wire it up in code** — this is
   NOT yet implemented: `RAVEN_MODE=oidc` is currently a no-op, and the repo
   has no OIDC config, `/oidc/callback/` route, or `RAVEN_OIDC_*` reader. You
   will need to add all of that per the library docs:
   - discovery: `https://login.microsoftonline.com/49a50445-bdfa-4b79-ade3-547b4f3986e9/v2.0/.well-known/openid-configuration`
   - scopes: `openid profile email`
   - Map the username from the `upn` / `preferred_username` claim. Derive the
     CRSid as the local part **only when the domain is exactly
     `cam.ac.uk`** (UIS: treat UPNs as opaque; alumni identities differ),
     and reject/queue anything else for admin review.
3. UIS's own Django guidance uses `django-auth-adfs` with
   `USERNAME_CLAIM: "upn"` — an equally good choice if preferred.

## 9. Email

- The "What's On" mailer sends via local SMTP (`REAL_EMAIL=true`,
  `EMAIL_HOST=localhost`) straight to every member with an email address,
  bcc'd in batches of 50 so no address is shown to anyone; members opt out
  on their profile. Group web scripts' mail appears from
  `<soc>-webmaster@srcf.net` by default; use that (or another srcf.net
  address the society owns) as `DEFAULT_FROM_EMAIL`, since mail sent from
  the SRCF in another domain's name tends to fail SPF/DMARC checks. The
  Mailer tab says whether `REAL_EMAIL` is on and has a "Send a test to me"
  button. After the first real send, look at the tab's "Recent sends" table:
  a failed batch means the mail server refused it (the SRCF may rate-limit
  outgoing mail), and the error text says why.
- The society's old mailing list (a spreadsheet) is imported once from
  Admin → People → Old mailing list; the mailer can then reach those people
  too, and the page shows who has since joined the website.
- A Mailman list is optional: the Mailer tab's "One address" option posts to
  one, in which case the list must accept mail from `DEFAULT_FROM_EMAIL`
  (Privacy options → Sender filters at https://lists.srcf.net).

## 10. Custom domain (cambridgematuresoc.com)

1. DNS: apex `A 131.111.179.82` + `AAAA 2001:630:212:700:2::1`; `www` CNAME to
   `webserver.srcf.societies.cam.ac.uk`.
2. Assign the domain to the society account in https://control.srcf.net.
3. Opt in to Let's Encrypt via the form at https://srcf-admin.soc.srcf.net
   once DNS resolves. Docs: https://docs.srcf.net/reference/web-hosting/custom-domains/
4. **Only after** HTTPS is confirmed working on the live domain, enable HSTS by
   adding `DJANGO_HSTS_SECONDS=31536000` to `.env` and reloading. This clears
   the `security.W004` warning; do it last because HSTS is hard to undo if the
   certificate ever lapses (Django's own caution). `DJANGO_SSL_REDIRECT=true`
   is optional — SRCF's Apache already redirects http→https.

## 11. Housekeeping

- **Quota**: 2 GB default (`srcf-quota` to check). Profile photos are resized
  on upload, but request more quota from the sysadmins before a big intake.
- **Poster maps**: create a free Geoapify account (geoapify.com), make an API
  key and paste it into the portal's Super admin tab. Without it posters
  have no map (the poster studio says so). The key never
  reaches browsers: the server fetches each map image and passes it through.
  Geoapify takes a few seconds to answer, so the first poster for an event
  can take up to twenty seconds to show its map; the venue is then remembered
  on the event. A lookup that fails is simply tried again later.
- **Cron**: polls close themselves when loaded, but to apply results (and
  email attendees) on time even when nobody visits, add on sinkhole:
  `*/15 * * * * cd /societies/<soc>/portal && set -a && . ./.env && set +a && .venv/bin/python manage.py close_polls`
- **Backups**: SRCF keeps best-effort snapshots (`.snapshot` dirs). Add a cron
  job on sinkhole for DB dumps into private space:
  `sudo -u <soc> crontab -e` →
  `0 4 * * * pg_dump -h postgres <soc> | gzip > /societies/<soc>/backups/db-$(date +\%u).sql.gz`
- **Upgrades**: see UPGRADING.md (backup first; keep `.env` and `media/`;
  `git pull && .venv/bin/pip install -r requirements.txt &&
  manage.py migrate && manage.py collectstatic --noinput`, then
  `systemctl --user restart mssportal`).
- **Handover**: future committee needs (a) SRCF group account membership,
  (b) this file, (c) the control-panel DB password location, (d) Toolkit app
  registration ownership if on Track B. Keep at least two admins on the SRCF
  account at all times.
