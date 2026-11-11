# MSS Portal — architecture & design decisions

The members' portal and public site for the University of Cambridge Mature
Student Society (MSS), replacing the static Squarespace site at
cambridgematuresoc.com. Built to run on the SRCF.

## Why Django, server-rendered

- The SRCF explicitly supports Python web apps behind its Apache reverse
  proxy (gunicorn on a UNIX socket, systemd user service) and provides
  PostgreSQL/MySQL — see DEPLOYMENT_SRCF.md.
- The feature set (accounts, roles, approvals, CRUD, audit) is exactly what
  Django's auth/ORM/admin give us for free; a JS SPA would add a build step
  and API surface for zero user benefit on a content-driven society site.
- No CDNs, no frameworks: one hand-written CSS design system
  (static/css/base.css) carrying over the society's identity (sage green
  #b6cdbb, brick-red lion #b82818). Pages work without JavaScript; small
  inline scripts add copy-buttons and form niceties.

## Apps and models

| App | Models | Purpose |
|---|---|---|
| accounts | User (custom, AUTH_USER_MODEL), WaitlistRequest, WhatsAppAccessRequest | Raven + associate members, approval queue, one-time WhatsApp invite |
| events | Category, Event, RSVP | Calendar, member-created events, official flag, RSVPs |
| supper | Restaurant, Rating | Supper Club ratings (4 dimensions × 1–5 stars, per attended visit) |
| guide | GuidePage, GuideRevision | "Mature Students Guide" wiki with history |
| core | SiteConfig (singleton), SitePage + SitePageRevision, TermsVersion/Revision/Acceptance | Society-wide settings, admin-managed pages with named editors and history, terms |
| inbox | DirectMessage, MessageBlock | Direct messages; `policy.py` decides who may message whom |
| testimonials | Testimonial | Members' testimonials, approved by admins before they go public |
| polls | Poll, PollOption, PollVote | Venue/date polls that set their event on closing, volunteer rotas, general polls |
| notifications | Notification, NotificationEmail | In-app bell and transactional email queue (`services.notify`) |
| formals | FormalListing, FormalBooking, FormalExchange, ReturnProposal, FormalAlert | Members-only formal invitations, group places, reciprocal returns and matching alerts |
| posters | EventPoster, PosterScan | One-button event posters: laid out server-side as SVG, printed and exported in the browser, nothing stored |
| faq | ContactNode, DepartmentContact | Who-to-contact map, college and department pages |
| panel | MailLog, AuditLog, OldSubscriber, HealthSnapshot, JobHeartbeat, ErrorEvent, RequestStats, SlowRequest | Admin panel (two-tier nav), stats, What's-On mailer, audit trail, Site health |
| dashboard | KeepyUppyScore | Member dashboard |

## Membership model

Two account types on one custom User:

- **Raven** — anyone with a CRSid; the CRSid is the identity (username).
  Created automatically on first Raven login.
- **Associate** — partners/family without a CRSid. They apply via the public
  waitlist form; an admin approves, which creates the account and emails a
  set-password link (email is the username). This is the manual-approval
  requirement, and it doubles as the WhatsApp-onboarding gate.

Roles: `is_portal_admin` marks society admins (approve/ban/promote/mailer);
Django's `is_superuser` is reserved for the tech officer via /dj-admin/.
Banning sets `is_active=False` and a middleware kills live sessions; the
Raven backend also refuses banned CRSids.

New logins are forced through profile setup (name, college, mobile, photo)
by `ProfileCompletionMiddleware` — requirement 14.

## Raven authentication

Legacy Raven died in December 2024. Two supported tracks (details and the
security invariants in DEPLOYMENT_SRCF.md §7–8):

- **Track A (default, `RAVEN_MODE=header`)**: SRCF's mod_ucam_webauth →
  Nevar → University Account (Entra + MFA). Apache authenticates only the
  `/accounts/raven/` path and forwards the CRSid in a header that is
  stripped from every other request; `accounts/auth.py` turns it into a
  session.
- **Track B (`RAVEN_MODE=oidc`)**: first-party OIDC against the University's
  Entra tenant via a UIS Toolkit registration.
- **Dev (`RAVEN_MODE=dev`)**: impersonation login page, DEBUG-only.

## Permission matrix

| Capability | Public | Member | Event creator | Portal admin |
|---|---|---|---|---|
| View calendar & non-hidden events | ✔ | ✔ | ✔ | ✔ |
| View members-only events, attendee initials | — | ✔ | ✔ | ✔ |
| Read the Guide | ✔ | ✔ | ✔ | ✔ |
| Edit the Guide, create events, RSVP | — | ✔ | ✔ | ✔ |
| Mark events official | — | — | — | ✔ |
| Edit/cancel an event, export attendee numbers | — | — | own events | ✔ |
| Rate a restaurant | — | attended visits only | | |
| Approve waitlist, ban, promote admins, mailer, stats | — | — | — | ✔ |
| Submit or withdraw a testimonial | — | ✔ | ✔ | ✔ |
| Approve, feature, unpublish testimonials | — | — | — | ✔ |
| Edit the words of a managed page | — | named editors | | ✔ |
| Create/delete pages, set audience, nav and editors | — | — | — | ✔ |
| Edit the wording of a fixed page (Admin → Content → Pages → Edit this page) | — | — | — | ✔ |
| Add a picture from an editor (`/pictures/upload/`) | — | named page editors, group owners | | ✔ |
| Delete a picture (only when no page shows it) | — | — | — | ✔ |
| Direct-message the committee | — | ✔ | ✔ | ✔ |
| Direct-message any member | — | only if an admin enabled them | | ✔ |
| Direct-message the host of an event you're going to (and reply) | — | ✔ | ✔ | ✔ |
| Start a poll or rota on an event | — | — | creator, host, tag owners | ✔ |
| Start a poll for the whole membership | — | — | — | ✔ |
| Vote, sign up for a slot | — | ✔ | ✔ | ✔ |
| See a rota's roster with contact details | — | — | event editors | ✔ |
| Subscribe to a personal calendar feed, download .ics | — | ✔ | ✔ | ✔ |
| Duplicate or repeat an event | — | — | event editors | ✔ |
| Bulk remind/export/ban members, edit committee and banner | — | — | — | ✔ |

Mobile numbers are treated as private data: visible only to admins and to an
event's creator for that event's attendees (the WhatsApp-group export).

## Requirement → implementation map

1. Raven-only members' portal → Track A/B above
2. Waitlist for partners → accounts:waitlist + panel:waitlist approval queue
3. Admin & stats pages → panel:home / panel:stats
4. Member-created events with Cambridge categories → events:create; Category
   table seeded with MSS's real clubs; official events prioritised in
   listings and the mailer
5. Attendee initials tokens → `.avatar-token` chips on calendar/detail
6. Public calendar, hideable events → `Event.members_only` +
   `EventQuerySet.visible_to()`
7. "Mature Students Guide" wiki → guide app (public read, member edit,
   revisions)
8. Admin powers add/edit/delete/ban/create admins → panel:members
9. Supper Club ratings → supper app (4 dimensions, attendees only)
10. One-time WhatsApp access → interstitial reveal marking
    `whatsapp_link_viewed_at`; re-access via WhatsAppAccessRequest queue
11. Share events to WhatsApp → `Event.whatsapp_share_url()` (wa.me deep link)
12. Two-week mailer with official-first tagging → panel:mailer +
    `EventQuerySet.in_next_days(14)`
13. RSVP + export numbers → events:rsvp / events:export (creator/admin only)
14. Basic details at signup → profile-setup gate
15. Personal dashboard → dashboard:home (next up, going, running, stats)
16. Old-site content → CONTENT.md, migrated into core pages and seed data

## Messaging policy

Member-to-member messaging is **off by default** (`SiteConfig.messaging_mode
= restricted`), in line with the community policy's "no unsolicited DMs".
Every member can write to a committee admin and reply to them; admins can
message anyone; an admin can set a member to `enabled` (message anyone) or
`muted` (send nothing) from the Members tab. The whole rule lives in
`inbox/policy.py` and is applied to every send path and to the Message
button on profiles. The mode can be switched back to `open` on the panel's
Messages tab without a deploy.

## Managed pages

`SitePage` is the CMS: admins create, publish, delete, set the audience
(which now applies to the page itself, not just its nav link) and name
`editors`. Editors change the title and body from the page itself
(`/pages/<slug>/edit/`) with preview; every save writes a `SitePageRevision`
and any version can be restored. Panel → Content → Pages lists every managed
page plus the fixed pages and where each is edited.

## Unfinished sign-ups

An account exists from first Raven login (or waitlist approval), *before*
the terms gate and the profile form, so anyone who stops there leaves a
blank account: that is why some members show no college or mobile.
`User.onboarding_status()` names the step they stopped at; the Members tab
shows it, filters on it, can email a reminder, and can remove unfinished
accounts older than 90 days that have left nothing behind.
`manage.py profile_audit` prints the same picture from the shell.

## Polls and rotas

`polls.Poll` is one mechanism with four kinds. Venue and date polls belong
to an event and, on closing, write the winner onto it (location, or start
with the end shifted by the original duration), audit it, and email and
notify everyone going; a tie waits for the host to decide. Volunteer polls
are rotas: options are slots with a capacity, votes are sign-ups, and the
host gets a roster with contact details under the attendee-export privacy
rule. Polls close lazily whenever loaded and from `manage.py close_polls`
on cron. They surface on the event page, the dashboard, calendar cards and
the What's On mailer.

## Notifications

`notifications.services.notify()` is the only writer. Hooks: a poll opens
or decides on an event you're going to, an event you're going to is
cancelled or moved, you come off a waitlist, a guest takes or gives back a
place at a lunch you host, your testimonial is reviewed,
a page you edit is changed by someone else. Cancellations and waitlist
promotions are emailed as well. The bell in the header and the mobile tab
bar show the unread count. An email's link is the notice's path after
`settings.SITE_URL`; when that is not set the email goes without a link
rather than with a bare path.

`notify()` writes its email jobs in the same transaction. The
`process_notifications` worker claims each job with a ten-minute lease and sends
outside transactions, retries failures with backoff, and records a heartbeat.
Claims carry a token so a stale worker cannot overwrite another worker's result.
One recipient per email keeps addresses private. Suspended recipients are checked
again at delivery. Site health reports stopped workers, delayed queues and
exhausted retries without exposing message content.

`events.services` locks the event row for RSVP changes, cancellations, waitlist
promotion and capacity edits. Poll voting and closure use a consistent event-then-
poll lock order, and applying an outcome commits the event change and notices
together. User-facing forms submit explicit intent rather than toggling state.

## Calendar feeds, waitlists and repeats

Every event has an `.ics` download; every member has a token-protected
feed at `/me/calendar.ics` (reset from the dashboard). A full event puts
new RSVPs on a waitlist and promotes the first in line when someone drops
out. Events can be duplicated a week on, or repeated weekly for up to 12
weeks; the copies keep the time on the clock (12:30 stays 12:30 across a
clock change), worked out in Python rather than by adding days in UTC.

## Lunch swaps

A lunch swap is an ordinary event carrying the `lunch-swap` tag; there is no
separate model and no ledger. `Event.save()` keeps anything with that tag
members only. `events/lunch.py` holds what is special: the short form's
builder, the notices to the host when a guest takes or gives back a place
(bell and email, bell only after six in an hour), and the "host one back"
nudge on a finished lunch's own page. The host cannot take one of their own
guest places. The What's On draft lists lunches in one short section
instead of a full entry each. A lunch's place is stored as "meeting point,
college", but its map is asked for the college alone (`Event.map_query`),
because a meeting point such as "Porters' Lodge" matches every college.

## Event posters

Any logged-in member can make a poster for an event (`/posters/<slug>/`).
`posters/layout.py` lays it out as a scene of primitives in the poster's own
units (mm for A4/A3, pixels for square and story), measuring text with the
bundled fonts (static/fonts, SIL OFL) so line breaks match what prints;
`templates/posters/_scene.svg` draws it. The browser does the rest: the
print page uses `@page` sizes so Save as PDF gives vector text, PNGs come
from a canvas, and a photo chosen on the device is read with FileReader and
kept in that browser's storage, never uploaded (shrunk to 2000 px first so
a phone photo fits; the print page drops it in too, and waits for every
picture before printing; if a photo still won't fit, the studio says Print
won't include it). Print marks add 3 mm of bleed in each sheet's own units,
so A3 crop marks fall on a true A3 trim. Every layout has
exactly one QR code (segno), encoding `/p/<slug>/`, a redirect that counts
scans and lands on the event page with its directions. Every layout also
has the map when the venue is found: without a photo the map takes the
picture's place, with one it sits beside the QR code. Each layout is
measured before it is drawn, and a test checks that no text overlaps the
QR code or the map. The map is a Geoapify static image (klokantech-basic,
asked for at the slot's exact shape so the OpenStreetMap credit in its
corner is never cropped) proxied by `posters.views.map_image` so the key
(SiteConfig) never reaches the browser. Venues are geocoded once and cached
on the event; the full location and its broader parts are looked up
together, a named place beats a street, and a whole-city match counts as
not found (Geoapify answers in seconds, so a save waits only briefly, the
studio waits longer, and a failed lookup is retried). When there is no map
the studio says why. The SVG download carries its pictures inside it. The
organiser's choices live in one `EventPoster` row; other members' tweaks are
query-string overrides that are never saved. The SRCF stores no poster
files.

## Pages and the words on them

Admin → Content → Pages is a tree: each menu section, the fixed pages in
it, and the custom `SitePage`s with their controls (edit text, rename and
settings, move up or down, move to another menu, publish, history,
delete). Custom pages carry a `section` (About, Guide or Members portal)
that decides which menu lists them.

A fixed page keeps its layout in its template, but every heading, line
and passage a committee might reword is a text block registered in
`core/blocks.py` (key, page, label, format, default wording, and whether
it may be left empty) and read with `{% text "key" %}`. An admin's edit is
a `core.TextBlock` row with that key; no row means the default. There are
four formats: `plain` (one run of text, at most 400 characters, escaped),
`inline` (one line of Markdown without the wrapping paragraph, at most
600, for a sentence that needs a link or bold), `markdown` (full rich
text, the only format that takes pictures) and `lines` (one entry per
line split on " | ", for the home page numbers and the Winter Ball
timeline, cards and FAQ; a line with no "|" is refused, though the second
part may be left empty). A default must equal what the template showed
before the block existed, so a fresh site reads the same. An optional
block that is emptied hides its element, wrapper included: templates use
`{% text "key" as name %}{% if name %}<h2>{{ name }}</h2>{% endif %}`,
and a test empties every optional block in turn and fails if a page is
left with an empty box. Page titles and the home page's headline are
`required` and cannot be saved empty. "Edited" means the stored wording
differs from the default, on the Pages tab and the editing screen alike.

`blocks.LAYOUT` lists everything on each fixed page in the order it
appears: text blocks, `link` rows for parts edited on another screen (the
committee, the contact map, the Winter Ball event), `auto` rows for lists
that fill themselves, and one `body` row for the Wellbeing page's main
text, which is a `SitePage` with history. `panel:page_text`
(`/admin/content/pages/text/<page>/`) renders that layout as one form
with one Save, and is where the "Edit this page" button on each fixed
page leads (`templates/core/_edit_text.html`). The rules it keeps, also
written on the view: a save is all or nothing; a box the admin did not
touch is never written (each carries a hidden fingerprint of the wording
it was shown), so two admins editing different sections both keep their
work; if both changed the same section the second is refused once and
shown the other's wording; saving the default wording deletes the row;
"Back to the default wording" and Preview save nothing. Every change is
in the audit log (`edit_text`, `reset_text`). `core/tests_page_editor.py`
checks that every `{% text %}` in the templates names a block, and that
every block sits in its own page's layout exactly once: add a block to
`BLOCKS`, to `LAYOUT` and to the template together. If the Wellbeing
`SitePage` is missing, the page shows `core.views.WELLBEING_FALLBACK`
(the support numbers), and `panel:page_body_create` makes the page from
that text rather than from a blank form.

The Winter Ball page takes its date, venue and booking button from one
calendar event, decided in `core/ball.py` (an admin's choice, or the next
super event with "Winter Ball" in its title). When that event is hidden
from the public calendar, a visitor who is not logged in sees the
`ball.when_locked` and `ball.where_fallback` lines instead of its date and
venue. Lists of events link with
`{% load event_links %}` and `{% event_url event %}` rather than
`event.get_absolute_url`, so the ball's title opens the Winter Ball page.
Use the tag in any new list of events. Links for managing an event or a
booking keep using `get_absolute_url`.

## Pictures and the editor

Admin → Content → Pictures holds pictures for the pages: uploads are
checked and shrunk like event photos, stored under `media/public/` and
served to everyone by `config.media.protected_media` (every other upload
stays members-only). Each picture shows the Markdown line to paste into
a page. Every Markdown editor (pages, the page editor's formatted-text
sections, guide articles, group pages) gets a toolbar and a live preview
from `static/js/editor.js`, which renders through `core:preview` with the
same sanitiser as the pages, so what the preview shows is what the page
will show.

Pictures go inside formatted text; there are no separate picture slots.
When a box carries `data-upload` the toolbar's "Add a picture" button
opens a small panel (file, description, "Add it") that posts to
`core:picture_upload` (`/pictures/upload/`, JSON, 20 an hour each) and
puts the returned Markdown line into the text. `core.pictures
.enable_uploads(form, field, user)` sets that attribute, and only for
people who may upload (`can_upload_pictures`): portal admins, anyone
named as an editor of a custom page, and group owners. Ordinary members
may not, because the files are readable by the whole internet and nobody
reviews them; without the attribute the button only inserts the shape of
the line. The mailer's toolbar has no picture button at all, since
emails carry no pictures. Without JavaScript a note points to the
Pictures tab. `core/images.shrink_image` (also used for profile and event
photos) accepts JPEG, PNG, WebP and GIF only, refuses anything over 50
megapixels before decoding it, and re-encodes every file. The sanitiser
gives every `<img>` `class="rich-img" loading="lazy" decoding="async"` and
drops any class the writer typed; `.prose img, img.rich-img` keeps
pictures inside the column. `core.pictures.picture_uses` finds where each
picture is shown (text blocks, custom pages, guide articles, group pages,
the terms, the banner and events that are not over yet, one under way
included); the Pictures tab lists that, and a picture still in use cannot
be deleted. A file name is matched as typed and as a web address writes it
(a space as `%20`). Past events and old page
revisions are not counted, so a finished event never blocks a deletion.

## Emails

Every email is built by `core/emailbody.py` inside
`templates/email/shell.html`: the green header, a white card, a quiet
footer. Email cannot use the site's stylesheet, so the colours there are
the light and dark design tokens written out by hand; change them in both
places. Rules: no pictures, no web address as visible text (Outlook
rewrites them), a link on a line of its own is a button, and nothing but
inline styles outside the one small `<style>` block for dark mode and
phones. Each email goes out with a plain-text part and an HTML part; the
site's other emails (notifications, invitations, formal alerts,
unsubscribe confirmations) keep their text and get the HTML part from
`emailbody.plain_html`.

The What's On mailer is written in Markdown.
`panel.services.build_whats_on_email` drafts it in a fixed order: super
events, official events, "Also on", one short "Lunch swaps" section (a
line a lunch, never a full entry each), then "Have your say" for open
polls and surveys. Words a member wrote go through `md_escape`. Every
event's address comes from `panel.services.event_link`, which sends the
Winter Ball to its own page. `panel.services.render_mailer` is the only
place the Markdown becomes an email: the preview in the Mailer tab, the
test send and every real send call it, so what the preview shows is what
is sent. "Draft with AI" may reword the draft but an answer whose links
differ from the draft's, or that carries an email address the draft did
not have, is refused.

## Surveys

`surveys` is a sibling of `polls` rather than an extension of it: a poll
is one question with named votes that settles an event's venue or date; a
survey is several questions with answers grouped per member, optionally
anonymous. A society admin creates a `Survey` and names its `admins`, the
members who run it: they write `Question`s (short answer, paragraph,
choose one, choose any, dropdown, 1 to 5 scale, yes or no), open and
close it, see the results, download a CSV and send reminders. `/surveys/`
lists live, opening-soon and closed surveys for every member, and drafts
for the people running them; closed surveys stay there with their results
when `results_visibility` allows.

Anonymity is in the data. Who has answered lives in `Participation` (one
answer per member, reminders to the rest); what they said lives in
`Response`. An anonymous response has no respondent and no time at all,
and every row of it (`Response`, `Answer`, `AnswerChoice`) is keyed by a
random UUID rather than a running number, so neither a sequence nor a
timestamp lines the two tables up; a deleted member's named responses
become anonymous. Named responses carry the member and can be edited,
anonymous ones are final. The site shows the people running a survey how
many have answered, never who; anonymous free-text answers are listed in
a different order for every question, so one member's answers can't be
lined up; and the row-by-row CSV (which guards against spreadsheet
formulas) is only offered once the survey has closed.
(SQLite keeps a hidden row number per table, so only the live MySQL
database meets the promise completely.)
Once anyone has answered, questions and choices can be reworded but not
added or removed, since existing answers point at them. Surveys close on
their date lazily (the first page load) and from the `close_polls` cron
command. The What's On mailer lists live surveys under "Have your say".

Responses, question changes and closing or reopening a survey share a survey
row lock. Submission rechecks the current questions, availability and anonymity
choice before saving, so a stale form cannot create a partial response or an
anonymous participation receipt. The low-level admin is inspection-only;
organisers make changes through the survey builder and settings.

## Site health

`panel/healthchecks` groups checks into eight sections. A check is a
function registered with `@check(key, title, section, slow=...)` that
returns a `Result`: a status (ok, warn, fail, unknown, off or na; only
warn and fail colour a verdict), a plain sentence and next step for the
committee, and details and a next step for the webmaster, which only super
admins see. Quick checks read the database, the settings and a few files,
and run on every view of Admin → Health. Slow checks read
what their probe found. Probes (`probes.py`) fetch the site through
Apache, check certificates and the mail server, and walk the disk. They
run all at once in threads within a 15-second budget, never touch the
database, and run only from "Run all checks" (once a minute at most), the
daily `manage.py health_check --trigger cron`, or a shell. Every run is
kept as a `HealthSnapshot`. The page shows the newest full run's slow
results with their age; the Overview tab icon and the dashboard merge the
newest full run with any newer quick run (a restart's), so a restart
can't hide a failure; the 30-day strip groups runs by local day in Python.

What feeds the checks: `RequestTimingMiddleware` counts requests per UTC
hour by status and duration (`RequestStats`, written once a minute per
worker with F() updates) and keeps requests over five seconds
(`SlowRequest`). `panel.errorlog.DatabaseErrorHandler`, wired in
`settings.LOGGING`, groups server errors into `ErrorEvent`s by type,
innermost project frame and route pattern; it keeps no addresses, form
data or cookies, and blanks secrets and email addresses. Scheduled jobs
record `JobHeartbeat`s; gunicorn's `worker_abort` hook appends to
`state/worker-timeouts.log`; web workers fingerprint the code as they
start (`deploy.py`), so new code waiting for a restart shows.
`HealthEndpointMiddleware` answers `/healthz`, for an outside uptime
monitor, and `/healthz/probe`, which answers only a token signed with the
secret key in the last minute, and reports whether a forged Raven login header got past Apache.
Both run before sessions and logins, so neither can log anyone in.

## Visual layer

Stats charts are inline SVG from `panel/charts.py` (no JavaScript, no
CDN). The calendar labels Cambridge term weeks from term dates on
SiteConfig and has an agenda view that is the default on phones. Its tag
filter is a closed `<details>`: chosen tags show beside it as chips, and
`?tags=open` (carried only by the links inside the list) keeps it open
while several tags are picked. Revision
diffs (`core/diff.py`) cover managed pages and the terms. On phones the
header keeps the lion and a Menu link, and `/menu/` lists every section
(logged-in members also get a bottom tab bar); dark mode
re-points the CSS tokens; `data-confirm` forms confirm through a `<dialog>`
and simply post without JavaScript.

## Deliberate simplifications

- **WhatsApp**: there is no usable WhatsApp API for community groups, so
  integration is deep links (wa.me share), the one-time invite link, and a
  copy-paste/CSV export of attendee numbers for the group admin.
- **Mailer** sends straight to members: every active account with an
  email address that hasn't opted out (`User.wants_mailer`, a profile
  checkbox), bcc'd in batches of 50 through the SRCF's own mail server,
  each copy with an unsubscribe line and a List-Unsubscribe header. The
  old mailing list (`panel.OldSubscriber`, imported from a spreadsheet by
  `panel.sheets`, which reads .xlsx and .csv without a library) is a second
  audience, de-duplicated against members by address or CRSid; anyone can
  unsubscribe an address at `/unsubscribe/` and undo it from the emailed
  link. No Mailman list is needed, though "One address" can still post to
  one.
- **SQLite locally / Postgres on SRCF** via `DB_ENGINE`; no ORM features that
  differ between them.
- **Member Markdown is triple-guarded** (core/templatetags/md.py): the source
  is HTML-escaped, rendered with a conservative extension set that excludes
  `attr_list` (which would allow attribute/event-handler injection), then run
  through a dependency-free allow-list sanitizer that keeps only formatting
  tags and drops any `href` that isn't http/https/mailto. Headings, lists,
  links, blockquotes, tables and code survive; scripts and `javascript:` URIs
  do not.

## Security review

The codebase went through an adversarial multi-agent review (permissions,
stored XSS, auth, admin self-harm, correctness). All confirmed findings were
fixed; see the "Security & correctness fixes" commit. Highlights: closed a
stored-XSS vector via Markdown `attr_list`; made supper-club score
aggregation viewer-aware so members-only/cancelled visits never leak into
public scores; row-locked waitlist approval; made the one-time WhatsApp
reveal race-safe with an atomic conditional update. Regression tests cover
each.

## Known limitations / deferred

- **Supper leaderboard N+1**: the leaderboard and homepage call
  `rating_summary()` once per restaurant (~4 queries each). Fine at society
  scale (tens of restaurants); would want a single grouped annotation if the
  list ever grew into the hundreds.
- **WhatsApp** integration is necessarily link-based (share deep-links,
  one-time invite, number export) — there is no group API to automate joins.
- **Bulk email** goes out from the portal itself, bcc'd in batches of 50
  through the SRCF's mail server (see "Mailer" above), rather than through
  a mailing-list service: unsubscribes are the profile checkbox and the
  public /unsubscribe/ page, and bounces return to the From address.
