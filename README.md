# MSS Portal

The website and members' portal of the **University of Cambridge Mature
Student Society (MSS)** — public calendar, the Mature Students Guide wiki,
the Supper Club restaurant leaderboard, and a Raven-authenticated members'
area with RSVPs, dashboards and an admin panel. Designed to be hosted on the
[SRCF](https://www.srcf.net/).

- **DESIGN.md** — architecture, data model, permission matrix
- **DEPLOYMENT_SRCF.md** — production runbook (SRCF + Raven), handover notes
- **UPGRADING.md** — deploying a new version to the live site without losing
  the database, settings, photos or anyone's login
- **CONTENT.md** — copy migrated from the old cambridgematuresoc.com site

## Local development

```bash
python3.11 -m venv .venv          # any Python ≥ 3.10
.venv/bin/pip install -r requirements.txt
.venv/bin/python manage.py migrate
.venv/bin/python manage.py seed_demo   # demo members, events, ratings, guide
.venv/bin/python manage.py runserver
```

Open http://127.0.0.1:8000/. In dev mode (`RAVEN_MODE=dev`, the default) the
login page shows a **development impersonation** card — log in as any seeded
member with one click. `dbj25` and `amk67` are portal admins. All seeded
accounts also accept the password `demo-password`.

Run the test suite:

```bash
.venv/bin/python manage.py test
```

## The map

| URL | What |
|---|---|
| `/` | Public homepage |
| `/events/` | Calendar (public; members-only events hidden when logged out) |
| `/guide/` | The Mature Students Guide (public read, member edit) |
| `/supper-club/` | Supper Club restaurant leaderboard |
| `/testimonials/` | Members' testimonials (public once approved; members submit) |
| `/polls/` | Polls and volunteer rotas (started from an event page, or by admins for everyone) |
| `/notifications/` | A member's notifications |
| `/search/` | Site-wide search |
| `/me/calendar.ics?token=…` | A member's personal calendar feed |
| `/posters/<event>/` | Poster studio: print to PDF, PNG for WhatsApp; `/p/<event>/` is the QR short link |
| `/me/` | Member dashboard |
| `/accounts/…` | Login (Raven / associate), profile, waitlist, WhatsApp invite |
| `/admin/` | Society admin panel (approvals, members, stats, mailer, audit) |
| `/dj-admin/` | Django admin (superusers/tech officer only) |

## Configuration

Everything is environment-driven — see the top of `config/settings.py` and
DEPLOYMENT_SRCF.md. Key variables: `DJANGO_SECRET_KEY`, `DJANGO_DEBUG`,
`DJANGO_ALLOWED_HOSTS`, `DB_ENGINE` (`sqlite`/`postgres`/`mysql` + `DB_*`),
`RAVEN_MODE` (`dev`/`header`/`oidc`), `REAL_EMAIL`.
