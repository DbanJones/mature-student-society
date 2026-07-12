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
| core | SiteConfig (singleton) | Society-wide settings, public pages |
| panel | MailLog, AuditLog | Admin panel, stats, What's-On mailer, audit trail |
| dashboard | — | Member dashboard (views only) |

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

## Deliberate simplifications

- **WhatsApp**: there is no usable WhatsApp API for community groups, so
  integration is deep links (wa.me share), the one-time invite link, and a
  copy-paste/CSV export of attendee numbers for the group admin.
- **Mailer** sends to the Mailman list address, not to individual members —
  deliverability and unsubscribe handling stay Mailman's job.
- **SQLite locally / Postgres on SRCF** via `DB_ENGINE`; no ORM features that
  differ between them.
- Markdown from members is HTML-escaped before rendering (core/templatetags/
  md.py) — no raw HTML, no XSS, at the cost of blockquote syntax.
