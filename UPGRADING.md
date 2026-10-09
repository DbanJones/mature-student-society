# Upgrading the live portal without losing data or logins

For whoever deploys a new version to the SRCF (mss.soc.srcf.net). Read the
whole page once before starting; the actual work is about fifteen minutes.

## What lives where (and what must survive an upgrade)

| Thing | Where it is | Touched by an upgrade? |
|---|---|---|
| Members, events, RSVPs, polls, pages, sessions (who is logged in) | The society **MySQL database** (`mss` on host `mysql`) | No. Code files and the database are separate. `migrate` only *adds* tables and columns. |
| Secrets and settings (`DJANGO_SECRET_KEY`, database password, `RAVEN_MODE=header`, `DJANGO_DEBUG=false`) | `.env` in the portal folder | **Must be kept.** Never overwrite or regenerate. |
| Member photos, event pictures and pictures for the pages | `media/` in the portal folder | **Must be kept.** Not in the repository. |
| Raven login | Apache `.htaccess` files under `public_html` | Not part of the code. Leave alone. |
| The code | The portal folder (everything else) | Replaced. |

Three rules that protect the live site:

1. **Never run `seed_demo` without `--categories-only` on the live site.** It
   creates demo members, events and messages. `run_on_srcf.sh` already runs
   the safe `--categories-only` form.
2. **Never change `DJANGO_SECRET_KEY` as part of an upgrade.** Sessions are
   signed with it; a new key logs every member out and invalidates the
   set-password links in any unanswered invite emails. (Rotate it only on
   purpose, after a leak, and tell members.)
3. **Never delete the portal folder to "start clean".** `.env` and `media/`
   live inside it. Copy new files over the old ones, or use `git pull`.

Logins survive an upgrade because nothing about them is in the code:
Raven members are identified by Apache and matched to their account in the
database; associates' passwords are in the database; both kinds of session
are rows in the database. Keep the database and `.env`, and everyone stays
logged in.

## Before you start: take a backup

In PuTTY on `shell.srcf.net` (replace `<password>` with the MySQL password
from the SRCF control panel email; the command asks for it if you leave `-p`
bare):

```bash
mkdir -p /societies/mss/backups
mysqldump -h mysql -u mss -p mss | gzip > /societies/mss/backups/mss-$(date +%F).sql.gz
ls -la /societies/mss/backups/
```

That file is the whole database. If anything goes wrong, restoring it is one
command (see "If something goes wrong" below). Also copy `.env` somewhere
safe: `cp /path/to/portal/.env /societies/mss/backups/env-$(date +%F)`.

## Step by step

Replace `/path/to/portal` with the folder that contains `run_on_srcf.sh`.

**1. Put the new code in place.** Either way, `.env` and `media/` are left
alone.

- *With git on the server:*
  ```bash
  cd /path/to/portal
  git fetch origin
  git checkout feature/admin-ux-and-content   # or: git pull origin main, once merged
  ```
- *With WinSCP:* download the branch as a ZIP from GitHub (Code → Download
  ZIP), unzip it on your PC, and drag the **contents** of the unzipped folder
  into `/path/to/portal`, choosing "overwrite" when asked. Do **not** delete
  the folder first.

**2. Install dependencies and apply database changes.** In PuTTY:

```bash
cd /path/to/portal
set -a; source .env; set +a
.venv/bin/pip install -r requirements.txt
.venv/bin/python manage.py check          # must say "no issues"
.venv/bin/python manage.py migrate        # adds the new tables; keeps all data
.venv/bin/python manage.py collectstatic --noinput
```

`migrate` prints each change it applies. This release adds eleven: new
tables for polls, notifications, testimonials, page revisions, committee
and activities, and new columns on users, events, pages and site settings.
Nothing is removed and no existing row is changed, except that previously
muted members stay muted.

**3. Restart the app** so it picks up the new code:

```bash
systemctl --user restart mssportal      # the name used in DEPLOYMENT_SRCF.md
systemctl --user status mssportal       # should say "active (running)"
```

If the service is named differently, `systemctl --user list-units | grep -i mss`
shows it.

**4. Check the site.** Open https://mss.soc.srcf.net/ logged out, then log
in with Raven and open /me/ and /admin/. You should still be logged in if
you were before; your admin rights are unchanged.

## After this release: three settings and one cron line

- **Messaging is now off between members by default.** Everyone can still
  message a committee admin and the host of an event they're going to. If
  the committee prefers the old behaviour, switch it on the panel's
  Messages tab; or enable individual members from the Members tab.
- **Term dates:** enter the Full Term start dates on Content → Navigation so
  the calendar labels term weeks.
- **Testimonials** is a new public page under About. Hide the tab on Content
  → Navigation until the first testimonials are approved, if you'd rather.
- **Poster maps** need a free Geoapify key pasted into the Super admin tab;
  without one, posters have no map and the poster studio says why. The
  first poster for each event takes a few seconds longer while the venue is
  looked up. If a map shows the wrong spot, name the college, building or
  street in the event's location.
- **The menu changed.** "What's on" is now a plain Event Calendar link.
  Supper Club and every other tag are listed under About → Groups, and the
  Winter Ball moved under About. Old tag-page links redirect.
- **Static files collect themselves on every restart** once `run.sh` has
  the extra line shown in DEPLOYMENT_SRCF.md; add it when you deploy this
  release. Stylesheet links carry a version stamp, so browsers fetch new
  CSS as soon as it is collected. The Super admin tab has a "Site health"
  card: press "Check now" and it says whether the web server is serving
  this version's files. The boot-time collect runs as the society user, so
  `staticfiles/` must be writable by it; if it fails the site still starts
  and the card says so.
- **The ☰ icon is gone.** Phones get a "Menu" link in the header (and,
  for logged-in members, a Menu tab at the bottom), both opening a page
  that lists the whole site.
- **Check the mailer really sends.** The Mailer tab now says whether mail
  leaves the server and has a "Send a test to me" button. Delivery needs
  `REAL_EMAIL=true` in `.env` (mail then goes through the SRCF's own mail
  server on localhost); without it "Send" only writes to the log. Set
  `DEFAULT_FROM_EMAIL` to an srcf.net address the society owns, such as
  `DEFAULT_FROM_EMAIL="MSS <mss-webmaster@srcf.net>"` (quoted: `.env` is read
  by bash and an unquoted `<` stops the service from starting; this is also
  the fallback when the variable is unset): mail from the SRCF claiming to come from
  another domain is likely to be refused or junked by Gmail and Outlook.
  The Mailman list must accept posts from that address: subscribe it to
  the list with delivery off, or add it under Privacy options → Sender
  filters at lists.srcf.net; a post from an unknown sender is held for
  moderation, which looks like nothing happening. The Mailer tab repeats
  this under “Getting it to the list”.
- **Pictures on pages.** Admin → Content → Pictures takes uploads (shrunk
  on the way in, served to everyone) and shows the line to paste into a
  page. Every Markdown box now has a toolbar and a live preview.
- **Poster text can no longer run off the sheet.** Each line records the
  width it was laid out for; a viewer whose browser substitutes a font
  (say, static files missing on the server) gets any overflowing line
  squeezed to fit, and a line that fits is left exactly as the font draws
  it. Emoji and non-Latin text are measured honestly, and a long tag
  name is trimmed before it reaches the lion.
- **Organisers can hide the RSVP list.** "Show who's going" on the event
  form; the count always shows, and the organiser and admins still see
  the names.
- **"Draft with AI" takes instructions.** A box on the Mailer tab shapes
  the tone, length and order of an issue; the society's tone of voice and
  the rules about dates and links still apply.
- **Every page is editable from Admin → Content → Pages.** The tab is now
  a tree of the site's menus; fixed pages list their text blocks (the
  home page welcome, About, Community policies, the Winter Ball copy, the
  Guide and group introductions and so on), and custom pages can be moved
  between menus and re-ordered. The About introduction that used to live
  in the Django admin is carried across by a migration.
- **Surveys** are new, under Members portal → Surveys and Admin → People →
  Surveys. A society admin creates a survey and names the members who run
  it; they write the questions, open it, see the results and send
  reminders. The `close_polls` cron line below closes surveys too.
- **Polls close on time** even when nobody visits if this cron line runs on
  `sinkhole` (`crontab -e`):
  ```
  */15 * * * * cd /path/to/portal && set -a && . ./.env && set +a && .venv/bin/python manage.py close_polls
  ```

Two commands help you understand the live data without touching it:
`manage.py profile_audit` lists accounts that never finished signing up and
why, and the panel's Members tab now shows the same thing with a Remind
button.

## If something goes wrong

- **Site shows an error after restart:** `journalctl --user -u mssportal -n 50`
  shows why. The commonest cause is a missing variable in `.env`; the app
  refuses to start in production with `DJANGO_DEBUG=true` or the default
  secret key, on purpose.
- **Need to go back:** restore the backup and the previous code.
  ```bash
  gunzip < /societies/mss/backups/mss-YYYY-MM-DD.sql.gz | mysql -h mysql -u mss -p mss
  cd /path/to/portal && git checkout main      # or re-upload the previous ZIP
  systemctl --user restart mssportal
  ```
  Members stay logged in through a restore as long as `.env` is unchanged.

## Housekeeping

- Take a backup before every upgrade; keep the last few in
  `/societies/mss/backups/` (it is private space).
- `DEPLOYMENT_SRCF.md` has the full first-time setup and the Raven details;
  this page only covers upgrading a site that already works.
