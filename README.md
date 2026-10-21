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
| `/events/` | Event Calendar (public; members-only events hidden when logged out) |
| `/events/groups/` | Groups: every event tag, each with its own page at `/events/groups/<tag>/` (old `/events/tags/<tag>/` links redirect) |
| `/guide/` | The Mature Students Guide (public read, member edit) |
| `/supper-club/` | Supper Club restaurant leaderboard |
| `/testimonials/` | Members' testimonials (public once approved; members submit) |
| `/polls/` | Polls and volunteer rotas (started from an event page, or by admins for everyone) |
| `/notifications/` | A member's notifications |
| `/formals/` | Members-only college formal invitations: open or reciprocal, host approval or instant reservation |
| `/formals/mine/` | Invitations hosted, guest bookings, and return promises |
| `/formals/committee/` | Committee review of overdue or disputed formal swaps |
| `/formals/committee/statistics/` | Committee-only impact, attendance and exchange reports, with date/college filters and CSV downloads |
| `/search/` | Site-wide search |
| `/me/calendar.ics?token=…` | A member's personal calendar feed |
| `/posters/<event>/` | Poster studio: print to PDF, PNG for WhatsApp; `/p/<event>/` is the QR short link |
| `/surveys/` | Surveys: live and closed surveys, each run by its own admins; answer with your name or anonymously |
| `/polls/` | Polls: open polls to vote in and closed ones with results (under About) |
| `/admin/content/pages/` | The Pages tree: every page under its menu, text blocks on fixed pages, controls on custom pages |
| `/me/` | Member dashboard |
| `/accounts/…` | Login (Raven / associate), profile, waitlist, WhatsApp invite |
| `/admin/` | Society admin panel (approvals, members, stats, mailer, audit) |
| `/admin/people/mailing-list/` | Which accounts are on the society's mailing list, and the addresses to add or take off |
| `/admin/mailer/` | The What's On mailer, and the website's mailing addresses to download (CSV, or one per line for Mailman) |
| `/admin/health/` | Site health checks with what to do about each, a 30-day history, "Run all checks" |
| `/healthz` | Answers `ok` while the site and its database work, for an outside uptime monitor |
| `/dj-admin/` | Django admin (superusers/tech officer only) |

## Configuration

The member dashboard brings together ordinary events and formal plans, including
group quantities, requests, hosted dinners and outstanding swaps. The calendar
keeps formals hidden by default. Signed-in members can choose **Include available
formals**; the filter shows open invitations with guest places left and is kept
when changing month, layout or other filters. Anonymous visitors cannot enable it.

Ordinary event reservations, waitlist promotion and capacity edits share a row
lock, as do volunteer sign-ups and poll closure. Repeated join/cancel submissions
have the same result as one submission. A poll result already applied to an event
cannot silently change afterwards. Run the event and poll concurrency tests on
the production database engine as well as the local SQLite suite.

Important member notifications are committed to a durable email queue alongside
their in-app notices. Run `manage.py process_notifications --quiet` every five
minutes, in addition to `process_formals` and `close_polls`. Email failure does not
undo a booking or lose the message; delivery retries up to five times. Admin →
Health shows delayed queues and missing workers. After fixing the mail service,
use `process_notifications --retry-failed`, or the selected-email retry action in
Django admin. Delivery is at least once, so a crash after SMTP acceptance can
duplicate an email. No SMTP happens while event places or poll votes are locked.

Formal Exchange uses the existing member accounts and private events calendar.
Members offer one to three guest places at their profile college, attest that
they can host, and state the cost, booking deadline and college arrangements.
Members can reserve one to three places in one booking, including themselves,
and add companion names immediately or later. Reciprocal invitations can require
selected return colleges. Hosts choose matching return places or a fixed number
of return places per booking, with a deadline but no date required yet.
Host approval and instant reservation are independent of that choice.

The `formals` app owns formal attendance; it does not create ordinary RSVPs.
Both sides confirm exactly who attended before visits are counted. Partial
attendance is recorded; disagreements go to review. The agreed return quantity
does not silently shrink if fewer original guests attend. A return holds the
remaining owed places for up to 48 hours. Partial returns can be completed at a
later dinner. Cancelling
a return dinner leaves an earned promise outstanding. The member owed a
return can waive it; deadline extensions need both members' agreement, and
the committee can review disputes. Terms freeze after the first response;
material changes require cancellation and a new invitation. Closed bookings
remain as history and cannot be submitted again for the same invitation.

The member screens put current plans and actions first; completed and cancelled
records remain available under past plans. Creating a dinner from a return
promise brings the member back to that swap to send the invitation.
Members can book directly from the browse cards. Hosts can publish up to twenty
dates together, sharing the time, capacity, terms and relative booking deadline;
the whole batch validates before any invitations are published. WhatsApp sharing
opens a composed invitation summary with a member-protected link; it sends no
message automatically. Confirmed member guests can message one another until
the formal starts. Blocks, muted accounts and other messaging restrictions still
apply, and non-participants do not see the attendee contact links.

Members can save up to twenty alerts for college, date range, invitation type,
booking method and minimum places. New matching publications queue an in-app
notification and email; existing listings are not backfilled. Overlapping alerts
produce one notification per member per formal. Alerts can be paused or removed
at `/formals/alerts/`. Email delivery uses the existing `process_formals` worker.

Committee reporting derives figures directly from the booking ledger, so no
separate statistics counters need to be maintained. It distinguishes places
offered, confirmed guest visits, distinct confirmed member guests, named companions,
hosts of attended formals, and completed exchanges. A private participation
table shows hosting, personal attendance and completed swaps by member, with
filtered CSV exports. Companion names are not treated as unique member accounts.
A reserved place or a passed dinner date does not
count as attendance. Date filters select dinners in the Cambridge time zone;
exchange outcomes use the original dinner's date and describe the current
state of that group of swaps, rather than claiming a completion date. Named
guest records, member comparisons and CSV downloads require `is_portal_admin`;
ordinary membership or Django staff status alone does not grant access. Migration
`0002` preserves the existing single-place attendance history; `0003` adds alerts.

All ledger changes go through `formals/services.py`, in a transaction under
a short database row lock. This serializes the society's allocation writes,
including changes across two formals. No email is sent inside that lock.
Use MySQL/InnoDB or PostgreSQL for production; SQLite does not implement row
locking. Run `manage.py test formals.tests.test_concurrency` against an
isolated test database using the deployment engine before releasing booking
to members. Those tests explicitly skip on SQLite.

Run `manage.py process_formals` every five minutes to expire return holds and
deliver queued emails (see DEPLOYMENT_SRCF.md). In-app notifications are
available immediately. Allocation checks ignore expired holds even if the
scheduled command is delayed. Emails retry five times; the committee page
and read-only Django admin show exhausted retries. Delivery is at least once,
so a worker crash after sending can cause a duplicate email, never a duplicate
reservation. Formal history prevents account/event deletion; suspend accounts
instead, and use a deliberate retention/anonymisation process when needed.

Everything is environment-driven — see the top of `config/settings.py` and
DEPLOYMENT_SRCF.md. Key variables: `DJANGO_SECRET_KEY`, `DJANGO_DEBUG`,
`DJANGO_ALLOWED_HOSTS`, `DB_ENGINE` (`sqlite`/`postgres`/`mysql` + `DB_*`),
`RAVEN_MODE` (`dev`/`header`/`oidc`), `REAL_EMAIL`. Optional, for the Site health page: `MSS_BACKUP_DIR`,
`MSS_QUOTA_MB`, `MSS_PUBLIC_HTML` (UPGRADING.md); `manage.py health_check`
runs the same checks from a shell or cron.
