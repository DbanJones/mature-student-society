# Upgrading the live portal without losing data or logins

For whoever deploys a new version to the SRCF (www.cambridgematuresoc.com,
also mss.soc.srcf.net). Read the whole page once before starting; the
actual work is about fifteen minutes.

The live portal is the folder `/public/societies/mss/mature-student-society`
(the one with `run_on_srcf.sh` in it), started by `run_on_srcf.sh` on
webserver.srcf.net. Apache serves `/static/` straight from
`/public/societies/mss/public_html/static`; the portal copies the current
stylesheets, scripts and fonts there every time it starts.

## Portal reliability and member plans update

Apply the new notifications migration with the normal upgrade steps, and add
`process_notifications --quiet` every five minutes alongside `process_formals`
and `close_polls` (exact cron lines are in DEPLOYMENT_SRCF.md, section 11).
Notification emails now wait safely in the database until this worker runs.
Admin → Health checks both email workers and reports stuck or failed messages.

Before release, run event, poll, survey and formal concurrency tests against an isolated
database using the live database engine; SQLite skips row-lock checks. The
changes protect last places, waitlists, volunteer slots, final poll outcomes and
survey responses submitted while an organiser changes or closes the survey.
The member dashboard includes formal plans. Members can include available
formals in the calendar using a filter that starts switched off. Draft messages
and invalid forms keep their contents so members can correct them.

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
   creates demo members, events and messages. `run_on_srcf.sh` seeds
   nothing; a brand-new install runs `seed_demo --categories-only` once.
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
safe: `cp /public/societies/mss/mature-student-society/.env /societies/mss/backups/env-$(date +%F)`
(private space, so other SRCF users can't read the copy).

## Step by step

All of this happens on **webserver.srcf.net** (PuTTY), in the portal folder
`/public/societies/mss/mature-student-society`.

**1. Put the new code in place.** Either way, `.env` and `media/` are left
alone.

- *With git on the server:*
  ```bash
  cd /public/societies/mss/mature-student-society
  git fetch origin
  git checkout feature/admin-ux-and-content && git pull   # or: git pull origin main, once merged
  ```
- *With WinSCP:* download the branch as a ZIP from GitHub (Code → Download
  ZIP), unzip it on your PC, and drag the **contents** of the unzipped folder
  into the portal folder, choosing "overwrite" when asked. Do **not** delete
  the folder first.

**2. Install dependencies and check the settings.** In PuTTY:

```bash
cd /public/societies/mss/mature-student-society
set -a; . ./.env; set +a
.venv/bin/pip install -r requirements-srcf.txt
.venv/bin/python manage.py check          # must say "no issues"
```

Don't run `migrate` here: the old code is still serving, and it can't write
to tables that already have this release's new columns. The restart in
step 3 stops the old code, then applies the migrations and copies the new
static files before starting the new code.

**3. Restart the portal.** First see which account runs it:

```bash
ps -eo user,args | grep [g]unicorn        # the first column is the account
```

- Run by **mss** (a society service, the usual case): find the unit and restart it
  ```bash
  sudo -Hu mss XDG_RUNTIME_DIR=/run/user/$(id -u mss) systemctl --user list-units --all | grep -i -E 'mss|portal'
  sudo -Hu mss XDG_RUNTIME_DIR=/run/user/$(id -u mss) systemctl --user restart <unit>
  ```
- Run by **your own account**: `systemctl --user list-units | grep -i -E 'mss|portal'`,
  then `systemctl --user restart <unit>`.

Always **restart**, never "reload": a reload skips `run_on_srcf.sh`, so
migrations and static files wouldn't be applied.

**4. Check the site.** Open https://www.cambridgematuresoc.com/ logged out,
then log in with Raven and open /me/ and /admin/. Open **Admin → Overview →
Site health** and press **Run all checks**: the banner at the top says
whether anything is broken, and every red or amber row says what to do and
who should do it. If the site shows "Service Unavailable", see "If
something goes wrong" below.

## One-time setup (October 2026 release)

Do these once, in order, between steps 2 and 3.

1. **Check nothing private is open to other SRCF users.** Everything under
   `/public/societies/mss/` can be read by any SRCF user.
   ```bash
   cd /public/societies/mss/mature-student-society
   stat -c '%A %U:%G %n' .env media
   ```
   The last three characters of each mode are what *other* users may do;
   they must be `---`. Each fix is run by the owner (the `%U` column):
   - `.env`: `chgrp mss .env && chmod 660 .env`, or when mss owns it,
     `sudo -u mss sh -c 'chgrp mss .env && chmod 660 .env'` (`sudo` in
     front of the first command alone wouldn't cover the second). The
     society's admins can still read and edit it. If it was readable by others, also change the
     MySQL password (control.srcf.net) and `DJANGO_SECRET_KEY` (this logs
     everyone out; tell members), because the SRCF's snapshots keep old
     copies of the file.
   - `media`: `chmod o-rwx media` (with `sudo -u mss` in front when mss
     owns it).

   The portal's socket closes itself to other users from this release on
   (`gunicorn.conf.py`); after the restart, `stat -c '%A %G' sockets/web.sock`
   should show `srwxrwx--- mss`.
2. **Tell the portal where Apache serves `/static/` from, and the site's own
   address.** (Without the first line, a live portal now uses
   `public_html/static` beside its own folder, which is where Apache serves
   it on the SRCF; set it if Apache serves another folder.) In
   control.srcf.net, check whether cambridgematuresoc.com has
   its own "Document root"; if it does, use that folder's `static` below.
   Add the two lines on the server (a Windows editor can add invisible
   characters that break `.env`):
   ```bash
   printf '\nDJANGO_STATIC_ROOT=/public/societies/mss/public_html/static\nDJANGO_SITE_URL=https://www.cambridgematuresoc.com\n' >> .env
   grep -c '^DJANGO_STATIC_ROOT=' .env        # must print 1
   ```
3. **Let the portal keep that folder current.** Whoever owns it (`stat -c %U /public/societies/mss/public_html/static`)
   runs `srcf-soc-permfix mss`, so the account that runs the portal can
   replace the files there.
4. Restart (step 3 above) and press **Run all checks** on Admin → Health. If “The site's look is ready on the server” is amber, run
   `set -a; . ./.env; set +a; .venv/bin/python manage.py sync_static` and restart again.
5. **Schedule the daily health check** on sinkhole (`crontab -e`, next to
   the `close_polls` line below). Cron mails its owner only when something
   needs attention, and the run also tidies old records:
   ```
   30 7 * * * cd /public/societies/mss/mature-student-society && set -a && . ./.env && set +a && .venv/bin/python manage.py health_check --trigger cron --quiet --prune
   ```
6. **Back the database up every night** (strongly advised: the SRCF's own
   snapshots are best-effort). Keep the MySQL password where only you can
   read it, then add the dump to the same crontab:
   ```bash
   ( umask 077 && printf '[client]\nhost=mysql\nuser=mss\npassword=%s\n' '<password>' > /societies/mss/.my.cnf )
   ```
   ```
   15 3 * * * bash -o pipefail -c 'mysqldump --defaults-extra-file=/societies/mss/.my.cnf mss | gzip > /societies/mss/backups/.db.part && mv /societies/mss/backups/.db.part /societies/mss/backups/db-$(date +\%u).sql.gz'
   ```
   That keeps seven files, one per weekday, and a dump that fails leaves
   the last good file in place rather than an empty one (cron mails you
   mysqldump's error). Then tell the portal where they go, so Site health
   checks that a fresh one arrives every night:
   `printf '\nMSS_BACKUP_DIR=/societies/mss/backups\n' >> .env` and restart.
7. **Ask an outside monitor to watch the site** (free, five minutes): at
   uptimerobot.com, add a *keyword* monitor for
   `https://www.cambridgematuresoc.com/healthz` that expects the word `ok`,
   alerting the webmaster and the committee address. Site health shows it
   working within the hour.

## After this release: three settings and one cron line

- **Site health** (Admin → Health) checks 23 things in
  eight groups: whether the site answers and how fast, whether visitors get
  this version's stylesheets, whether a Raven login can be faked, the
  certificate, private files, email, scheduled jobs, backups, the database,
  storage and the society's own setup. Each row says what to do and who
  should do it; super admins also see the technical detail. Most checks run
  whenever the page opens. **Run all checks** adds the ones that fetch the
  site over the internet or look through files on the server, which takes up
  to 20 seconds. A red mark on the Overview tab means something is broken
  (super admins see amber too), the dashboard has a one-line summary, and a
  30-day strip shows how each day went. Server errors are now recorded, by
  where in the code they happen (never the page's address, nor anything a
  member typed), and visitors get a friendly page instead of "Server Error
  (500)". `/healthz` answers `ok` for an outside monitor, and
  `manage.py health_check` gives the same report on the server (exit status
  0, 1 or 2). New tables: the restart's `migrate` adds them. Optional
  settings in `.env`: `MSS_BACKUP_DIR` (see one-time step 6),
  `MSS_QUOTA_MB` (the society's disk quota, for the storage gauge) and
  `MSS_PUBLIC_HTML` (only if Apache's web folder isn't the one holding
  `DJANGO_STATIC_ROOT`).
- **The society's addresses.** The mailing list is `mss-members@srcf.net`
  (a Mailman 2.1 list; its admin pages are at
  https://lists.srcf.net/mailman/admin/mss-members, behind the SRCF login
  and then the list's own admin password, which the SRCF emailed to
  mss-admins when the list was made and which the control panel can reset).
  `mss-admins@srcf.net` reaches every admin of the mss account and owns the
  list. The Mailer's "One address" now starts on the list (a migration
  fills it in), and the site sends from `MSS <mss-admins@srcf.net>` unless
  `.env` names another sender.
- **Download the website's mailing addresses.** Admin → Mailer gives
  everyone the website mails, for an audience (website members, the old
  list, or both, each inbox once; opted-out, banned and unsubscribed people
  are left out), as a spreadsheet with names or as addresses only, one per
  line. A fifth choice, "People who stopped the mailer", lists those who
  opted out, were banned or unsubscribed. Every download is recorded in the
  audit log, and so is a download of the old list's CSV. These files are
  people's personal details: keep them to committee business and delete
  them afterwards.
- **Admin → People → Mailing list** shows which website accounts are on
  `mss-members`. The site can't read Mailman itself (SRCF lists have no
  export and sit behind the SRCF login), so a list admin signs in to the
  list's admin pages, opens its subscriber list
  (https://lists.srcf.net/mailman/roster/mss-members: everyone on one page,
  members who chose to hide included), copies the whole page and pastes it
  into the site, which picks out the addresses ("someone at example.com"
  too) and leaves out the list's own. (The Membership List works as well
  once General options → "Maximum number of members to show on one page" is
  more than the list has; one page of a split Membership List is refused.
  Don't use Mailman's "who" email command: it sends the list's admin
  password by email.) The page checks the paste against Mailman's own count
  and says if anything is missing.
  That copy is "the most recent list": each account then shows as on it,
  not on it, or stopped the mailer but still on it, and the Members page
  has the same badges and a "Not on the mailing list" filter. "Download
  the addresses to add" gives everyone who gets the mailer but isn't on
  the list (and, ticked, the old list's people), for Mailman's Mass
  Subscription page; "Mark them as added" then records them ("Not used"
  forgets a download that wasn't). Taking off works the same way through
  Mass Removal, for everyone the mailer mustn't reach: accounts that opted
  out, are banned or were closed, and old-list people who unsubscribed (a
  Cambridge address's two spellings count as one mailbox throughout).
  Deleting an account, however it's deleted, queues both spellings of its
  address to take off, whether or not the site's copy has it; so does a
  member moving to a new address, when the list may have the old one, and
  someone only on the list asking to leave on the unsubscribe page (the
  address is emailed to say so). These wait at the top of the page and go
  into "Download the addresses to take off"; marking that file done clears
  the ones it had. Nothing the mailer still reaches, through an account or
  the old list, is ever taken off. A paste much smaller than the last one
  asks first, in case it was only one page; a part of the list pasted with
  the box ticked adds to the copy without counting as a fresh one.
- **Setting up `mss-members` (once, before anyone is added).** The
  Mailing list page links to each of these:
  1. Privacy options → Subscription rules: "What steps are required for
     subscription?" → Confirm and approve (otherwise anyone can join by
     emailing mss-members-request), and "Who can view subscription list?"
     → List admin only.
  2. Privacy options → Sender filters: "By default, should new list member
     postings be moderated?" → Yes, and "Action to take when a moderated
     member posts to the list" → Reject. Leave "Action to take for postings
     from non-members for which no explicit action is defined" on Hold. For
     anyone already on the list: Membership List → "Set everyone's
     moderation bit" → On → Set.
  3. Archiving options: "Is archive file source for public or private
     archival?" → private, then check
     https://lists.srcf.net/pipermail/mss-members/ no longer opens without
     a login. (It opened for anyone on 12 Oct 2026, and the What's On lists
     members-only events.)
  4. General options: "Send monthly password reminders?" → No.
  5. Non-digest options: add to "Footer added to mail sent to regular list
     members": To leave this list, email mss-members-request@srcf.net from
     the address this came to, with the subject unsubscribe, and reply to
     the confirmation it sends. (The list's own pages need an SRCF or
     University login, which some old-list people don't have.)
  6. Keep the mailer's own addresses off the list: the one it sends from
     (`mss-admins@srcf.net` unless `.env` names another) and the society's
     contact address (its Reply-To). Mailman treats a post from a member's
     address as that member's, so it would skip approval, or be rejected
     once members are moderated. The Mailing list page warns if either
     turns up.
- **Posting the mailer to the list.** Send with "One address" (it starts on
  the list). Mailman holds it, because the site isn't a subscriber, and
  emails mss-admins. The waiting-posts page
  (https://lists.srcf.net/mailman/admindb/mss-members) groups held posts by
  sender, and Accept there releases every post held from that address. If
  yours (check its subject and time) is the only one, choose Accept; if
  anything else is held from it, open yours by its number, choose
  Approve, and Discard the rest. Leave the boxes to add the sender to a
  filter or ban it unticked: anyone can forge a From address, so never add
  the site's address to the list's accepted senders. The site
  won't send the same mailer to the list and to a website audience within
  ten minutes, but a held post can be approved much later, so send each
  What's On to the list or to the website's audiences, not both.
- **gunicorn 23.** Step 2's `pip install` brings it in. With
  `header_map = "drop"` in `gunicorn.conf.py` it ignores look-alike
  headers such as `X_MSS_Remote_User`, which older gunicorn read as the Raven
  login header that Apache removes. Site health's "Nobody can fake a Raven
  login" tests exactly this, from outside, through Apache.

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
- **Static files copy themselves into place on every restart.**
  `run_on_srcf.sh` runs `manage.py sync_static`, which compares file
  contents (so ZIP and WinSCP deploys work) and copies into
  `DJANGO_STATIC_ROOT`, the folder Apache serves. Every stylesheet link
  carries a new version stamp, so browsers fetch fresh copies. The Site
  health page checks both the copy and what visitors are actually served,
  file by file. If a copy fails the site still starts and the page says so.
- **The launcher refuses to start a half-configured site.** Without a
  readable `.env`, or with one not set up for production, it stops instead
  of starting in development mode.
- **The ☰ icon is gone.** Phones get a "Menu" link in the header (and,
  for logged-in members, a Menu tab at the bottom), both opening a page
  that lists the whole site.
- **The old mailing list lives on the site.** Admin → People → Old mailing
  list imports the committee's spreadsheet of addresses (.xlsx or .csv,
  addresses in any column; nothing is duplicated or overwritten on a
  re-import), shows who has since joined the website (matched by email or
  by CRSid) and downloads the rest as CSV. The Mailer tab can send to
  website members, the old list (everyone, or only those not yet on the
  website), both at once with each person reached once, or one address.
  Anyone can stop the mailer for an address at /unsubscribe/ (they are
  emailed a link to undo it). New table: run `migrate`. Keep the
  spreadsheet itself out of the repository and off the server.
- **The mailer sends to members directly.** "Every member with an email
  address" (the default) bcc's them in batches of 50 — no Mailman list is
  needed; "One address" remains for a list or a check. Members can opt out
  with "Email me the What's On mailer" on their profile (a new column, so
  run `migrate`), every copy carries an unsubscribe line and shows only the
  society's address as its recipient, and the admin who presses Send gets
  a copy of each version (one for members, one for the old list). The tab says whether mail leaves
  the server and has a "Send a test to me" button. Delivery needs
  `REAL_EMAIL=true` in `.env` (mail then goes through the SRCF's own mail
  server on localhost); without it "Send" only writes to the log. Set
  `DEFAULT_FROM_EMAIL="MSS <mss-admins@srcf.net>"` (quoted: `.env` is read
  by bash and an unquoted `<` stops the service from starting; this is also
  the fallback when the variable is unset): mail from the SRCF claiming to
  come from another domain is likely to be refused or junked by Gmail and
  Outlook. (`mss-webmaster@srcf.net`, the address earlier versions used, is
  by default an alias of `mss-admins@srcf.net`, so either reaches the
  admins.) Watch "Recent sends" after the first real send: a failed batch
  means the mail server refused it, and the error text says why.
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
  It prints nothing unless it closed something, so cron only mails you then.

Two commands help you understand the live data without touching it:
`manage.py profile_audit` lists accounts that never finished signing up and
why, and the panel's Members tab now shows the same thing with a Remind
button.

## Mid-October update: calendar, mailing list, Health, space saver

- **The calendar looked like a plain list** because Apache was still
  serving the stylesheets from 4 September: `DJANGO_STATIC_ROOT` wasn't in
  `.env`, so each restart copied new styles into a folder Apache never
  reads. The portal now finds `public_html/static` beside its own folder by
  itself, so the restart after this update puts the current styles live.
  If pages still look old afterwards, Admin → Health says why; most often
  it's the folder's permissions (one-time step 3). The calendar's List view
  (what phones see) is now dated cards.
- **One migration** (panel 0006, the old list's archive) runs on restart.
- **Admin → Health** is its own tab next to Overview. It opens with one
  sentence, then only what needs sorting out, each with what to do and
  whose job it is; the rest folds away.
- **People → Mailing list** leads with its status and two cards. Each card
  is three steps, and its “Mark them as added” / “Mark them as taken off”
  button sits in the card, greyed out until there's a download to mark.
  The set-up and sending guides start closed.
- **People → Old mailing list keeps each imported spreadsheet** as an
  archive, for admins to download (logged) or delete. The files sit under
  `media/private/`, which `/media/` never serves. Spreadsheets imported
  before this update weren't kept: import the old list again to keep a copy
  (it never duplicates anyone).
- **Events → Space saver** deletes the pictures of events that ended more
  than a week ago (the events stay), and pictures no event uses any more.
  It shows what would go first, and nothing happens until an admin presses
  the button. Members' photos and the pages' pictures are never touched.

## If something goes wrong

- **Something is red on Site health:** each red row says what's wrong and
  who should act. On the server, the same report with all the technical
  detail is
  `set -a; . ./.env; set +a; .venv/bin/python manage.py health_check`.
- **Site shows an error after restart:** see why with
  `cd /public/societies/mss/mature-student-society && set -a && . ./.env && set +a && .venv/bin/python manage.py check`,
  and the unit's status (`... systemctl --user status <unit>`, with the
  `sudo -Hu mss` prefix when mss runs it). The commonest cause is a missing
  or unreadable `.env`: the launcher now stops rather than start without
  it, and the app refuses `DJANGO_DEBUG=true` or the default secret key.
- **"Service Unavailable" after the restart:** first look at the unit's
  status (`... systemctl --user status <unit>`): the launcher prints why it
  refused to start, most often an unreadable `.env`. If the portal is
  running, the log says whether the socket could be handed to group mss;
  when it couldn't, the socket is left open and the site works. If the
  socket *was* handed to mss and Apache still can't reach it, add
  `MSS_SOCKET_OPEN=1` to `.env`, restart, and tell David: that brings back
  the old, open socket while it's sorted out. Site health shows an open
  socket in red, because any SRCF user could then log in as anyone.
- **Need to go back:** empty the database, restore the backup, and put
  the previous code back. Emptying it first matters: a restore only
  replaces the tables in the backup, so tables this release added would
  stay behind and make the next attempt at the upgrade fail at `migrate`.
  Each `mysql` line asks for the MySQL password.
  ```bash
  mysql -h mysql -u mss -p mss -N -e "SELECT CONCAT('DROP TABLE \`', table_name, '\`;') FROM information_schema.tables WHERE table_schema = 'mss'" > /societies/mss/backups/drop-all.sql
  ( echo 'SET FOREIGN_KEY_CHECKS=0;'; cat /societies/mss/backups/drop-all.sql ) | mysql -h mysql -u mss -p mss
  gunzip < /societies/mss/backups/mss-YYYY-MM-DD.sql.gz | mysql -h mysql -u mss -p mss
  cd /public/societies/mss/mature-student-society && git checkout main   # or re-upload the previous ZIP
  # then restart as in step 3
  ```
  Members stay logged in through a restore as long as `.env` is unchanged.

## Housekeeping

- Take a backup before every upgrade; keep the last few in
  `/societies/mss/backups/` (it is private space).
- `DEPLOYMENT_SRCF.md` has the full first-time setup and the Raven details;
  this page only covers upgrading a site that already works.
