#!/bin/bash
# Launch the MSS portal locally. Run it with:  ./run.sh
# (or:  bash run.sh)
#
# This uses the project's own virtual environment, so you don't have to
# remember to activate it or worry about which Python your Mac defaults to.

cd "$(dirname "$0")" || exit 1

if [ ! -x ".venv/bin/python" ]; then
  echo "No virtual environment found at .venv/ — creating one..."
  python3 -m venv .venv || { echo "Could not create venv"; exit 1; }
  .venv/bin/pip install --quiet --upgrade pip
  .venv/bin/pip install --quiet -r requirements.txt
fi
. .venv/bin/activate
set -a; source .env; set +a

# Make sure the database exists and has demo data (safe to run repeatedly).
.venv/bin/python manage.py migrate --noinput
if [ ! -s "db.sqlite3" ]; then
  .venv/bin/python manage.py seed_demo --categories-only
fi

echo ""
echo "  MSS portal starting at http://127.0.0.1:8000/"
echo "  Log in via the 'Development impersonation' card (dbj25 = admin)."
echo "  Press Ctrl+C to stop."
echo ""
#exec .venv/bin/python manage.py runserver
exec gunicorn -w 2 -b unix:/public/societies/mss/mature-student-society/sockets/web.sock --log-file - config.wsgi:application
