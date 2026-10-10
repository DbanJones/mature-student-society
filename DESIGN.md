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
| notifications | Notification | In-app bell; the important ones are emailed too (`services.notify`) |
| posters | EventPoster, PosterScan | One-button event posters: laid out server-side as SVG, printed and exported in the browser, nothing stored |
| faq | ContactNode, DepartmentContact | Who-to-contact map, college and department pages |
| panel | MailLog, AuditLog | Admin panel (two-tier nav), stats, What's-On mailer, audit trail |
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
cancelled or moved, you come off a waitlist, your testimonial is reviewed,
a page you edit is changed by someone else. Cancellations and waitlist
promotions are emailed as well. The bell in the header and the mobile tab
bar show the unread count.

## Calendar feeds, waitlists and repeats

Every event has an `.ics` download; every member has a token-protected
feed at `/me/calendar.ics` (reset from the dashboard). A full event puts
new RSVPs on a waitlist and promotes the first in line when someone drops
out. Events can be duplicated a week on, or repeated weekly for up to 12
weeks.

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
delete). A fixed page keeps its layout in its template, but every piece
of prose on it is a text block registered in `core/blocks.py` (key, page,
label, format, default wording) and read with `{% text "key" %}`; an
admin's edit is a `core.TextBlock` row with that key, and deleting the row
brings the default back. Formats are markdown, plain, and lines (one
entry per line split on " | ", for the home page numbers, the Winter Ball
timeline, cards and FAQ). Admins see an "Edit text" link on each fixed
page that opens the tree at that page. Custom pages carry a `section`
(About, Guide or Members portal) that decides which menu lists them.

## Pictures and the editor

Admin → Content → Pictures holds pictures for the pages: uploads are
checked and shrunk like event photos, stored under `media/public/` and
served to everyone by `config.media.protected_media` (every other upload
stays members-only). Each picture shows the Markdown line to paste into
a page. Every Markdown editor (pages, text blocks, guide articles, group
pages) gets a toolbar and a live preview from `static/js/editor.js`,
which renders through `core:preview` with the same sanitiser as the
pages, so what the preview shows is what the page will show.

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

## Visual layer

Stats charts are inline SVG from `panel/charts.py` (no JavaScript, no
CDN). The calendar labels Cambridge term weeks from term dates on
SiteConfig and has an agenda view that is the default on phones. Revision
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
  each copy with an unsubscribe line and a List-Unsubscribe header. No
  Mailman list is needed, though "One address" can still post to one.
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
- **Bulk email** goes to the Mailman list, not per-member sends, so
  unsubscribe/deliverability stay Mailman's responsibility.
