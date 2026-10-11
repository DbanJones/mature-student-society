#!/bin/bash
# Start the MSS portal on the SRCF (webserver.srcf.net). systemd runs this on
# every (re)start; UPGRADING.md has the commands. It stops rather than start
# a half-configured site: no readable .env, or a .env not set up for
# production, and nothing is served.
set -euo pipefail
cd "$(dirname "$0")"

SOCKET=/public/societies/mss/mature-student-society/sockets/web.sock

[ -r .env ] || { echo "run_on_srcf.sh: .env is missing or unreadable by $(id -un)" >&2; exit 1; }
# .env is read as it always has been: a line bash can't parse prints a
# warning instead of stopping the site. What matters is checked just after.
set +eu
set -a; . ./.env; set +a
set -eu
[ "${RAVEN_MODE:-dev}" = "header" ] || { echo "run_on_srcf.sh: RAVEN_MODE must be header on the SRCF" >&2; exit 1; }
[ "${DB_ENGINE:-sqlite}" != "sqlite" ] || { echo "run_on_srcf.sh: DB_ENGINE must name the society's database" >&2; exit 1; }
[ -x .venv/bin/gunicorn ] || { echo "run_on_srcf.sh: .venv/bin/gunicorn is missing: .venv/bin/pip install -r requirements-srcf.txt" >&2; exit 1; }

.venv/bin/python manage.py check          # refuses unsafe production settings
.venv/bin/python manage.py migrate --noinput

# Copy any changed stylesheets, scripts and fonts into the folder Apache
# serves (DJANGO_STATIC_ROOT in .env). A failure leaves the files already
# there and the Super admin "Site health" card says so; it never stops the site.
( umask 002 && .venv/bin/python manage.py sync_static ) \
  || echo "run_on_srcf.sh: sync_static failed; serving the static files already on disk" >&2

mkdir -p "$(dirname "$SOCKET")"
# gunicorn.conf.py closes the socket to other SRCF users (see there).
exec .venv/bin/gunicorn -c gunicorn.conf.py -w 2 -b "unix:$SOCKET" --log-file - config.wsgi:application
