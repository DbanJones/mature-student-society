"""The words on the site's fixed pages.

The layout of a fixed page (the home page, About, the Winter Ball…) lives
in its template, but every piece of prose on it is a text block listed
here: a key, the page it belongs to, a label for the page editor, a
format, the default wording and whether it may be left empty. When an
admin changes a block in the page editor (Admin → Content → Pages →
"Edit this page") the new text is stored as a ``core.TextBlock`` row with
the same key; save the default wording and the row is removed again.
Templates read blocks with ``{% text "key" %}``.

Four formats:

- ``plain``: one run of plain text, at most ``PLAIN_MAX`` characters.
- ``inline``: one line that may carry a link, bold or italics, at most
  ``INLINE_MAX`` characters. Rendered as rich text without the wrapping
  paragraph.
- ``markdown``: formatted text; the only format that can hold pictures.
- ``lines``: one entry per line, each split on " | " into two parts (a
  heading and its text, a time and what happens, a question and its
  answer).

``LAYOUT`` lists everything on each fixed page in the order it appears:
its text blocks, the parts edited on another admin screen, and the parts
that fill themselves. The page editor draws one row for each. Every block
must appear exactly once, in the layout of its own page.
"""

import hashlib
import re

PLAIN_MAX = 400
INLINE_MAX = 600
FORMATS = ("plain", "inline", "markdown", "lines")

SECTIONS = [
    ("home", "Home"),
    ("calendar", "Event Calendar"),
    ("guide", "Guide"),
    ("about", "About"),
    ("members", "Members portal"),
    ("footer", "Footer"),
]

# Fixed pages, in the order the Pages panel lists them within each section.
PAGES = [
    {"key": "home", "title": "Home", "url": "core:home", "section": "home",
     "note": "The “What we do” cards are edited on the About & committee tab; the events, guide and supper club lists fill themselves.",
     "links": [("What we do cards", "panel:about")]},
    {"key": "calendar", "title": "Event Calendar", "url": "events:calendar", "section": "calendar",
     "note": "Members create events; tag owners and admins promote them."},
    {"key": "guide", "title": "The Guide", "url": "guide:index", "section": "guide",
     "note": "Articles are a community wiki: any member can edit any article on its own page."},
    {"key": "contacts", "title": "Who to contact", "url": "faq:contacts", "section": "guide",
     "links": [("Edit the contact map", "panel:contact_map")]},
    {"key": "colleges", "title": "Colleges", "url": "faq:colleges", "section": "guide",
     "note": "The college descriptions come from the master report and are maintained in code."},
    {"key": "departments", "title": "Departments", "url": "faq:departments", "section": "guide",
     "links": [("Department contacts", "panel:content")]},
    {"key": "about", "title": "About MSS", "url": "core:about", "section": "about",
     "links": [("Committee list", "panel:about")]},
    {"key": "ball", "title": "Winter Ball", "url": "core:winter_ball", "section": "about",
     "note": "The date, venue and booking button come from one event on the calendar.",
     "links": [("Choose the event", "panel:ball_event")]},
    {"key": "wellbeing", "title": "Wellbeing", "url": "core:wellbeing", "section": "about",
     "note": "The main text is a page of its own, edited from the Wellbeing editor."},
    {"key": "testimonials", "title": "Testimonials", "url": "testimonials:index", "section": "about",
     "links": [("Moderate testimonials", "panel:testimonials")]},
    {"key": "policies", "title": "Community policies", "url": "core:policies", "section": "about"},
    {"key": "polls", "title": "Polls", "url": "polls:index", "section": "about",
     "note": "Lists itself from the polls members and organisers run."},
    {"key": "groups", "title": "Groups", "url": "events:groups", "section": "about",
     "note": "Each group's own page is edited by the group's owners from that page."},
    {"key": "supper", "title": "Supper Club", "url": "supper:index", "section": "about",
     "note": "The next supper and the leaderboard fill themselves."},
    {"key": "surveys", "title": "Surveys", "url": "surveys:index", "section": "members",
     "note": "Lists itself from the surveys on the Admin → People → Surveys tab."},
    {"key": "formals", "title": "Formal Exchange", "url": "formals:index", "section": "members",
     "note": "Members add the invitations."},
    {"key": "login", "title": "Log in", "url": "accounts:login", "section": "members"},
    {"key": "waitlist", "title": "Partners and family waitlist", "url": "accounts:waitlist", "section": "members"},
    {"key": "dashboard", "title": "Dashboard", "url": "dashboard:home", "section": "members",
     "note": "Each member's own page; nothing to edit."},
    {"key": "terms", "title": "Terms and conditions", "url": "core:terms", "section": "footer",
     "links": [("Terms versions", "panel:terms")]},
]
PAGES_BY_KEY = {page["key"]: page for page in PAGES}


def _block(page, label, fmt, default, required=False):
    return {"page": page, "label": label, "format": fmt, "default": default, "required": required}


BLOCKS = {
    # --- home ----------------------------------------------------------------
    "home.award_main": _block("home", "Award badge (first)", "plain",
                              "Winner: Outstanding Contribution to University Life 2025"),
    "home.award_second": _block("home", "Award badge (second)", "plain",
                                "Nominated: University Society of the Year 2025"),
    "home.heading": _block("home", "Headline", "plain", "Welcoming Cambridge's mature students",
                           required=True),
    "home.hero": _block("home", "Welcome paragraph", "markdown",
        "As the University's community of mature students continues to grow, we're proud to be "
        "Cambridge's friendliest and most sociable society. The Mature Student Society (MSS) hosted "
        "over 100 events during 2024–25, ranging from pub-nights, Formals and Formal-swaps, college "
        "lunches and tours, to museum/gallery trips, walks, runs and our famous Supper Clubs, "
        "Flashtalks, Networking Potlucks and shared study/work sessions. So far in 2025–26 we've added "
        "a Book Club, a History Club and a Sports hub to ensure that matures fully participate in the "
        "University's myriad sports offerings."),
    "home.what_we_do": _block("home", "“What we do” introduction", "markdown",
        "MSS is home to many active sub-groups, including music lovers, parents, partners as well as "
        "LGBTQ+ and African-Caribbean communities. We even have our own marketplace for selling stuff "
        "and giveaways. Whether you're looking to connect, explore, relax, get inspired or even buy a "
        "bike: there's something for everyone at MSS."),
    "home.what_we_do_more": _block("home", "“What we do” closing line", "markdown",
        "We also place a strong emphasis on well-being, peer support, advice, and advocacy — "
        "[read about our wellbeing support](/wellbeing/)."),
    "home.guide_lede": _block("home", "Guide teaser", "plain",
        "Hard-won knowledge from members who've done it — colleges, money, families, and finding your feet."),
    "home.supper_lede": _block("home", "Supper Club teaser", "plain",
        "Cambridge's international cheap-eats, one restaurant at a time — rated by the members who went."),
    "home.stats": _block("home", "Numbers strip (one per line: number | label)", "lines",
        "1,000 | members (nearly!)\n2024 | founded\n21 | youngest member\n80+ | oldest member"),
    "home.events_heading": _block("home", "“What's on next” heading", "plain", "What's on next"),
    "home.events_empty": _block("home", "Line shown when nothing is planned", "plain",
        "Nothing scheduled just yet — check back soon."),
    "home.what_we_do_heading": _block("home", "“What we do” heading", "plain", "What we do"),
    "home.testimonials_heading": _block("home", "Testimonials heading", "plain", "In members' words"),
    "home.guide_heading": _block("home", "Guide heading", "plain", "The Mature Students Guide"),
    "home.supper_heading": _block("home", "Supper Club heading", "plain", "The Supper Club"),
    # --- about ---------------------------------------------------------------
    "about.title": _block("about", "Page title", "plain", "About Us", required=True),
    "about.lede": _block("about", "Line under the title", "plain", "Welcoming Cambridge's mature students."),
    "about.contact_heading": _block("about", "“Get in touch” heading", "plain", "Get in touch"),
    "about.contact_text": _block("about", "“Get in touch” sentence", "plain",
        "Questions, ideas, or just want to say hello?"),
    "about.associates": _block("about", "Line for partners and family", "inline",
        "Not a Cambridge member but connected to one? "
        "[Request an associate account](/accounts/waitlist/)."),
    "about.body": _block("about", "Introduction", "markdown",
        "Founded in September 2024, the Mature Student Society (MSS) has quickly grown to nearly "
        "1,000 members. Our mission is to make the full Cambridge experience accessible to everyone "
        "— regardless of age, background, or life path."),
    "about.who_for": _block("about", "“Who we're for” card", "markdown",
        "### Who we're for\n\n"
        "- Those returning to academia after a career or caring responsibilities.\n"
        "- Finally pursuing long-held academic ambitions or career-change qualifications.\n"
        "- Or just that little bit older than most of your peers (or just feel older!)."),
    "about.community": _block("about", "“Our community includes” card", "markdown",
        "### Our community includes\n\n"
        "- Undergraduate and postgraduate students (MPhils, PhDs)\n"
        "- Postdocs, research fellows, and university researchers\n"
        "- Professional and Continuing Education students\n"
        "- Faculty and lab staff, librarians, and other university employees\n"
        "- Visiting scholars and researchers\n"
        "- The partners and children of any of the above"),
    "about.committee_heading": _block("about", "Committee heading", "plain", "The committee, 2026–27"),
    # --- wellbeing -----------------------------------------------------------
    "wellbeing.title": _block("wellbeing", "Page title", "plain", "Wellbeing", required=True),
    "wellbeing.team_heading": _block("wellbeing", "Team card heading", "plain", "Reach the MSS wellbeing team"),
    "wellbeing.lede": _block("wellbeing", "Introduction line", "plain",
        "You're not alone — and we can help you find the right door."),
    "wellbeing.team": _block("wellbeing", "“Reach the wellbeing team” card", "markdown",
        "Email us and mark your message for the wellbeing team. We can point you to the right service, "
        "come with you, or speak up on your behalf — but for clinical or crisis support please use the "
        "University and NHS services above."),
    # --- community policies --------------------------------------------------
    "policies.title": _block("policies", "Page title", "plain", "Community Policies", required=True),
    "policies.lede": _block("policies", "Introduction", "markdown",
        "A few simple expectations that keep MSS the friendliest society in Cambridge. These policies "
        "apply to **all MSS spaces** — this portal, our events, and the WhatsApp community (the Open "
        "Forum and every subgroup)."),
    "policies.body": _block("policies", "The policies", "markdown",
        "## How we communicate\n\n"
        "The **Open Forum** is our main WhatsApp group, alongside MSS Notifications, the MSS Marketplace, "
        "and 50+ subgroups. The weekly newsletter rounds up everything that's on — please **check the "
        "newsletter before asking** in the Open Forum; the answer is often already there.\n\n"
        "## WhatsApp guidelines\n\n"
        "- **Post with kindness.** Assume good faith; disagree with ideas, not people.\n"
        "- **Use your real full name** on WhatsApp so members know who they're talking to.\n"
        "- **No unsolicited direct messages.** Ask in the group, or ask permission before DMing someone.\n"
        "- Keep subgroup topics in their subgroup so the Open Forum stays readable for everyone.\n\n"
        "## Political neutrality\n\n"
        "MSS is politically neutral. Political content is restricted to the MSS Notifications channel, "
        "and calls-to-action are removed. As a matter of legal caution as well as community peace, the "
        "society itself takes no position on political questions — our job is to make Cambridge work "
        "for mature students of every background and view.\n\n"
        "## Images\n\n"
        "Be thoughtful when sharing photos: ask before posting pictures in which other members (and "
        "especially their children) are identifiable, and respect any request to take an image down.\n\n"
        "## Marketplace\n\n"
        "Selling stuff and giveaways belong in the **MSS Marketplace** group, not the Open Forum. "
        "Listings are between buyer and seller — the society doesn't vet items and can't arbitrate sales.\n\n"
        "## Membership\n\n"
        "The committee reserves the right to remove people from the community where these policies are "
        "repeatedly or seriously breached. If you have any doubts or concerns, contact the committee.\n\n"
        "*This page summarises the community policies; the full text is available from the committee on request.*"),
    # --- winter ball ---------------------------------------------------------
    "ball.kicker": _block("ball", "Line above the title", "plain", "The Mature Student Society presents"),
    "ball.title": _block("ball", "Page title", "plain", "The Winter Ball", required=True),
    "ball.when_fallback": _block("ball", "Date line before the event is on the calendar", "plain",
        "This December, date to be announced"),
    "ball.where_fallback": _block("ball", "Place line when the event's venue is not shown", "plain", "Cambridge"),
    "ball.when_locked": _block("ball", "Date line for visitors who are not logged in (members-only event)",
                               "plain", "This December"),
    "ball.tickets_note": _block("ball", "Line shown before tickets open", "plain",
        "Tickets open when the date is announced. Watch the calendar and the What's On mailer."),
    "ball.timeline_heading": _block("ball", "“The evening” heading", "plain", "The evening"),
    "ball.faq_heading": _block("ball", "Questions heading", "plain", "Questions, answered"),
    "ball.intro": _block("ball", "Introduction", "markdown",
        "## One night, properly done\n\n"
        "Once a year the society swaps campus cafés and pub snugs for candlelight, white tablecloths and "
        "a string quartet. The Winter Ball is our flagship evening: a welcome drink, a proper three-course "
        "dinner, dancing until the small hours — and the rare pleasure of seeing your seminar neighbours "
        "in black tie.\n\n"
        "It is, emphatically, **not just for students**: partners are warmly invited, and the timing "
        "(school-night-proof, carriages at one) is chosen with families in mind."),
    "ball.timeline": _block("ball", "The evening (one per line: time | what happens)", "lines",
        "19:00 | **Champagne reception** — welcome drinks and canapés by the fire\n"
        "19:45 | **Dinner is served** — three courses, wine on the table, dietary needs catered\n"
        "21:30 | **Speeches & toasts** — short, we promise\n"
        "22:00 | **The band strikes up** — dance floor opens, casino tables and quiet lounge alongside\n"
        "23:30 | **Midnight fare** — bacon rolls and hot chocolate for the survivors\n"
        "01:00 | **Carriages** — taxis pre-bookable at the cloakroom"),
    "ball.cards": _block("ball", "The three cards (one per line: heading | text)", "lines",
        "Dress code | Black tie & evening wear — or the nearest your wardrobe allows. National dress and "
        "vintage are actively encouraged; gowns may be worn. Nobody is turned away over a missing bow tie.\n"
        "Tickets | RSVP on the portal reserves your place; payment details follow by email. Partner "
        "tickets bought the same way — just RSVP for two and note it in the form.\n"
        "Getting home | We finish at 01:00. The cloakroom desk can pre-book taxis from 22:00, and the "
        "venue is a ten-minute walk from the city-centre ranks."),
    "ball.faq": _block("ball", "Questions, answered (one per line: question | answer)", "lines",
        "Can I bring my partner? | Yes — partners and guests are a core part of the night, not an afterthought.\n"
        "I don't dance. Is this still for me? | The quiet lounge and casino tables are open all night, and "
        "the dinner alone is worth the ticket.\n"
        "What about dietary requirements? | The menu covers vegetarian, vegan, gluten-free and halal as "
        "standard; a form circulates two weeks before the night for anything else.\n"
        "Is there parking? | Limited blue-badge parking at the venue — email us and we'll reserve a space. "
        "Otherwise the Grand Arcade car park is open all night.\n"
        "I can no longer make it — can I get a refund? | Full refunds up to a fortnight before; after that "
        "we'll offer your place to the waiting list first."),
    "ball.contact": _block("ball", "Closing line", "markdown",
        "Anything else? Write to the committee — or ask in the Open Forum."),
    # --- the guide and its pages ---------------------------------------------
    "guide.lede": _block("guide", "Introduction", "plain",
        "Written by mature students, for mature students: honest, current answers about colleges, money, "
        "family and life in Cambridge. This is a community wiki — every member can edit any page, so if "
        "something is out of date, fix it."),
    "contacts.lede": _block("contacts", "Introduction", "plain",
        "Cambridge is a federation — a timetable problem, a rent problem and a health problem are three "
        "different doors. Answer the questions and the map hands you the right person, what to say, what "
        "to keep, and a ready-to-fill email."),
    "colleges.lede": _block("colleges", "Introduction", "markdown",
        "A decision map, not a league table. Descriptions are editorial orientation from our master "
        "report; accommodation, eligibility and dining details change — **always verify with the college "
        "itself** before relying on anything here."),
    "departments.lede": _block("departments", "Introduction", "plain",
        "Your Faculty or Department — not your college — controls lectures, labs, handbooks, placements "
        "and coursework rules. What that means day to day differs sharply by School: pick yours."),
    # --- the rest ------------------------------------------------------------
    "testimonials.lede": _block("testimonials", "Introduction", "plain",
        "What being part of MSS has meant to the people in it. Every testimonial comes from a member and "
        "is read by the committee before it appears."),
    "polls.lede": _block("polls", "Introduction", "markdown",
        "Quick votes: where to meet, which date, who can help. Organisers run polls on their events; the "
        "committee asks everyone. For longer questionnaires see [Surveys](/surveys/)."),
    "calendar.lede": _block("calendar", "Introduction", "plain",
        "Suppers, socials, talks and walks — all welcome, partners included."),
    "surveys.lede": _block("surveys", "Introduction", "plain",
        "The committee asks, you answer. Each survey is run by a named member, and you choose whether to "
        "answer with your name or anonymously."),
    "groups.lede": _block("groups", "Introduction", "plain",
        "Clubs and regular meet-ups run by members. Each group has its own page with what's coming up "
        "and who runs it."),
    # --- page titles ---------------------------------------------------------
    "calendar.title": _block("calendar", "Page title", "plain", "Event Calendar", required=True),
    "groups.title": _block("groups", "Page title", "plain", "Groups", required=True),
    "polls.title": _block("polls", "Page title", "plain", "Polls", required=True),
    "surveys.title": _block("surveys", "Page title", "plain", "Surveys", required=True),
    # --- testimonials --------------------------------------------------------
    "testimonials.title": _block("testimonials", "Page title", "plain", "In members' words", required=True),
    "testimonials.share_heading": _block("testimonials", "Heading above the form", "plain",
        "Share your experience", required=True),
    "testimonials.share_note": _block("testimonials", "Note under the form", "plain",
        "Testimonials are reviewed before they are shown. You can remove yours at any time."),
    # --- the guide's front page ----------------------------------------------
    "guide.title": _block("guide", "Page title", "plain", "The Mature Students Guide", required=True),
    "guide.portal_contacts": _block("guide", "“Who to contact” card text", "plain",
        "A decision map: the right person, what to say, and a ready-to-fill email for any problem."),
    "guide.portal_colleges": _block("guide", "“College-specific info” card text", "plain",
        "All 31 colleges: character, what to verify, formal hall rhythm, mature-student notes."),
    "guide.portal_departments": _block("guide", "“Department-specific info” card text", "plain",
        "Workload, contacts and pitfalls by School — labs, essays, placements, group projects."),
    "guide.invite": _block("guide", "Line for visitors who are not logged in", "inline",
        "Spotted something missing or out of date? [Log in](/accounts/login/) to contribute — any "
        "member can write or improve a page."),
    # --- who to contact ------------------------------------------------------
    "contacts.title": _block("contacts", "Page title", "plain", "Who do I contact?", required=True),
    "contacts.map_heading": _block("contacts", "“The whole map” heading", "plain", "The whole map"),
    "contacts.map_text": _block("contacts", "Line under “The whole map”", "plain",
        "Scroll sideways for the full hierarchy — or use the flow above for the details behind each box."),
    "contacts.confidentiality_heading": _block("contacts", "Confidentiality heading", "plain",
        "Confidentiality: ask, don't guess"),
    "contacts.confidentiality": _block("contacts", "Confidentiality text", "inline",
        "Tutors, nurses, counsellors, porters and reps have different duties. Before disclosing "
        "something sensitive, ask: *“What will you record? Who can see it? What would require you to "
        "share it?”* In immediate-risk situations, safety overrides normal confidentiality."),
    # --- colleges ------------------------------------------------------------
    "colleges.title": _block("colleges", "Page title", "plain", "College-specific information", required=True),
    "colleges.mature_heading": _block("colleges", "“Mature colleges” heading", "plain",
        "The three mature colleges"),
    "colleges.mature_text": _block("colleges", "“Mature colleges” text", "inline",
        "Undergraduate intake restricted to mature students (21+). An open mature application is "
        "allocated to one of these — but mature applicants may apply to *any* undergraduate college, "
        "and a mature college is not automatically quieter, cheaper or better for families. Compare "
        "the actual room licence, charges and family rules."),
    # --- departments ---------------------------------------------------------
    "departments.title": _block("departments", "Page title", "plain", "Department-specific information",
                                required=True),
    "departments.tips_heading": _block("departments", "Advice card heading", "plain", "Whatever your subject"),
    "departments.tips": _block("departments", "Advice card text", "markdown",
        "- The **Faculty administrator** is the operational expert: timetables, forms, submission "
        "rules, the current written rule.\n"
        "- The **course organiser** is for anything affecting a whole paper, lab or placement group.\n"
        "- The **subject librarian** will save you hours — ask for a five-minute route through the "
        "catalogue.\n"
        "- Ask early: “Is this a University rule, a Faculty rule, or my college's practice?” The "
        "answer decides who can fix it."),
    # --- supper club ---------------------------------------------------------
    "supper.title": _block("supper", "Page title", "plain", "🍽️ The MSS Supper Club", required=True),
    "supper.lede": _block("supper", "Introduction", "plain",
        "Eating our way through Cambridge's international cheap-eats, one restaurant at a time. Every "
        "attendee scores the visit — food, service, atmosphere and value for money — and the "
        "leaderboard below settles the arguments."),
    "supper.note": _block("supper", "Note about voting", "plain",
        "Everyone can browse the scores; voting is reserved for members who were at the table."),
    "supper.none_heading": _block("supper", "Heading when no supper is planned", "plain",
        "🍽️ No supper on the books yet"),
    "supper.none_text": _block("supper", "Line when no supper is planned", "inline",
        "Watch the [calendar](/events/) — the next outing won't be far away."),
    "supper.board_heading": _block("supper", "Leaderboard heading", "plain", "The leaderboard"),
    "supper.unrated_heading": _block("supper", "“Not yet rated” heading", "plain", "Not yet rated"),
    "supper.unrated_text": _block("supper", "Line under “Not yet rated”", "plain",
        "On the hit list — awaiting a visit, or the scores from one."),
    # --- formal exchange -----------------------------------------------------
    "formals.lede": _block("formals", "Line under the title", "plain", "Find a dinner. Bring your friends."),
    "formals.footnote": _block("formals", "Note at the foot of the page", "plain",
        "No obligation = just enjoy the dinner. Swap = host in return. Each invitation shows how many "
        "return places the host would like."),
    # --- log in and the waitlist: plain text only, on purpose -----------------
    "login.title": _block("login", "Page title", "plain", "Welcome to the members' portal", required=True),
    "login.lede": _block("login", "Line under the title", "plain", "The kettle's on. Pick your door below."),
    "login.members_text": _block("login", "University members card text", "plain",
        "Any current University of Cambridge student or staff member with a CRSid can sign in with "
        "their University account — no separate registration needed."),
    "login.members_note": _block("login", "University members card note", "plain",
        "First time here? Signing in creates your portal account automatically."),
    "login.associates_text": _block("login", "Partners and family card text", "plain",
        "Partners and family members with an approved associate account log in with their email "
        "address and password."),
    "waitlist.title": _block("waitlist", "Page title", "plain", "Partners & family — join the waitlist",
                             required=True),
    "waitlist.lede": _block("waitlist", "Introduction", "plain",
        "MSS is for whole households, not just the person with the University card. If you're the "
        "partner or family member of a Cambridge mature student (or staff member) and don't have a "
        "CRSid of your own, ask for an associate account here."),
    "waitlist.note": _block("waitlist", "Note about what happens next", "plain",
        "An admin reviews each request — usually within a few days. If approved, you'll get an email "
        "with a link to set your password."),
}


def _t(key):
    return {"kind": "text", "key": key}


def _link(label, url_name, button, why="Edited on its own screen."):
    return {"kind": "link", "label": label, "url_name": url_name, "button": button, "why": why}


def _auto(label, why):
    return {"kind": "auto", "label": label, "why": why}


def _body(label, slug):
    return {"kind": "body", "label": label, "slug": slug}


_ADDRESS = "Always shows the society's contact address."
_BALL = "Comes from the Winter Ball event on the calendar."

# Everything on each fixed page, in the order it appears there.
LAYOUT = {
    "home": [
        _t("home.award_main"), _t("home.award_second"), _t("home.heading"), _t("home.hero"),
        _auto("Buttons under the headline", "These change with whether the visitor is logged in."),
        _t("home.events_heading"),
        _auto("Next events", "Fills itself from the calendar."),
        _t("home.events_empty"),
        _t("home.what_we_do_heading"), _t("home.what_we_do"),
        _link("“What we do” cards", "panel:about", "Edit the cards"),
        _t("home.what_we_do_more"),
        _t("home.testimonials_heading"),
        _link("Testimonials shown", "panel:testimonials", "Choose testimonials"),
        _t("home.guide_heading"), _t("home.guide_lede"),
        _auto("Latest guide articles", "Fills itself from the Guide."),
        _t("home.supper_heading"), _t("home.supper_lede"),
        _auto("Top restaurants", "Fills itself from the Supper Club ratings."),
        _t("home.stats"),
    ],
    "calendar": [
        _t("calendar.title"), _t("calendar.lede"),
        _auto("The calendar and its filters", "Events are added by members."),
    ],
    "guide": [
        _t("guide.title"), _t("guide.lede"),
        _t("guide.portal_contacts"), _t("guide.portal_colleges"), _t("guide.portal_departments"),
        _t("guide.invite"),
        _auto("The articles", "Any member can edit an article on its own page."),
    ],
    "contacts": [
        _t("contacts.title"), _t("contacts.lede"),
        _link("The contact map", "panel:contact_map", "Edit the contact map"),
        _t("contacts.map_heading"), _t("contacts.map_text"),
        _auto("Cast of characters", "The list of roles is part of the site's reference data."),
        _t("contacts.confidentiality_heading"), _t("contacts.confidentiality"),
    ],
    "colleges": [
        _t("colleges.title"), _t("colleges.lede"),
        _t("colleges.mature_heading"), _t("colleges.mature_text"),
        _auto("The college list", "The college descriptions are part of the site's reference data."),
    ],
    "departments": [
        _t("departments.title"), _t("departments.lede"),
        _link("Department contacts", "panel:content", "Edit department contacts"),
        _t("departments.tips_heading"), _t("departments.tips"),
    ],
    "about": [
        _t("about.title"), _t("about.lede"), _t("about.body"),
        _t("about.who_for"), _t("about.community"),
        _t("about.committee_heading"),
        _link("Committee list", "panel:about", "Edit the committee"),
        _t("about.contact_heading"), _t("about.contact_text"),
        _auto("“Write to us at” line", _ADDRESS),
        _t("about.associates"),
    ],
    "ball": [
        _t("ball.kicker"), _t("ball.title"),
        _link("Date and venue", "panel:ball_event", "Choose the event", _BALL),
        _t("ball.when_fallback"), _t("ball.where_fallback"), _t("ball.when_locked"),
        _auto("Ticket buttons and places left", _BALL),
        _t("ball.tickets_note"), _t("ball.intro"),
        _t("ball.timeline_heading"), _t("ball.timeline"),
        _t("ball.cards"),
        _t("ball.faq_heading"), _t("ball.faq"),
        _t("ball.contact"),
        _auto("“Email” line", _ADDRESS),
    ],
    "wellbeing": [
        _t("wellbeing.title"), _t("wellbeing.lede"),
        _body("Main text", "wellbeing"),
        _t("wellbeing.team_heading"), _t("wellbeing.team"),
        _auto("“Write to” line", _ADDRESS),
    ],
    "testimonials": [
        _t("testimonials.title"), _t("testimonials.lede"),
        _link("The testimonials", "panel:testimonials", "Moderate testimonials"),
        _t("testimonials.share_heading"), _t("testimonials.share_note"),
    ],
    "policies": [
        _t("policies.title"), _t("policies.lede"), _t("policies.body"),
        _auto("“Contact the committee” line", _ADDRESS),
    ],
    "polls": [
        _t("polls.title"), _t("polls.lede"),
        _auto("The polls", "Lists itself from the polls members and organisers run."),
    ],
    "groups": [
        _t("groups.title"), _t("groups.lede"),
        _auto("The groups", "Each group's own page is edited by its owners from that page."),
    ],
    "supper": [
        _t("supper.title"), _t("supper.lede"), _t("supper.note"),
        _auto("Next supper", "Comes from the next Supper Club event on the calendar."),
        _t("supper.none_heading"), _t("supper.none_text"),
        _t("supper.board_heading"),
        _auto("Leaderboard", "Fills itself from members' ratings."),
        _t("supper.unrated_heading"), _t("supper.unrated_text"),
    ],
    "surveys": [
        _t("surveys.title"), _t("surveys.lede"),
        _auto("The surveys", "Lists itself from the Surveys tab."),
    ],
    "formals": [
        _auto("Page title", "“Formal Exchange” is the feature's name, used in the menu and in emails."),
        _t("formals.lede"),
        _auto("The invitations", "Added by members."),
        _t("formals.footnote"),
    ],
    "login": [
        _t("login.title"), _t("login.lede"),
        _t("login.members_text"), _t("login.members_note"), _t("login.associates_text"),
        _auto("Headings, buttons and the form", "These stay fixed so logging in always works the same way."),
    ],
    "waitlist": [
        _t("waitlist.title"), _t("waitlist.lede"), _t("waitlist.note"),
        _auto("The request form", "Stays fixed."),
    ],
    "dashboard": [_auto("The dashboard", "Each member's own page; nothing to edit.")],
    "terms": [_link("The terms", "panel:terms", "Edit the terms")],
}


def layout_for(page_key):
    return LAYOUT.get(page_key, [])


def stored_texts():
    """Every edited block, key to text: one small query. The template tag
    asks once per page render, so there is nothing to cache and nothing
    to go stale between gunicorn workers."""
    from .models import TextBlock

    return dict(TextBlock.objects.values_list("key", "text"))


def forget_texts():
    """Nothing is cached any more; kept so callers need not change."""


def value_of(key):
    """The current wording of a block: the admin's, or the default."""
    return stored_texts().get(key, BLOCKS[key]["default"])


def normalise(fmt, text):
    """The form a wording is compared and stored in."""
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    if fmt in ("plain", "inline"):
        return " ".join(text.split())
    return text.rstrip().lstrip("\n")


def token(text):
    """A short fingerprint of a wording, to notice when it changed underneath an editor."""
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


def problem_with(text_block, text):
    """Why this wording can't be saved, or None. `text` is already normalised."""
    from .richtext import richtext_problem

    fmt = text_block["format"]
    if text_block["required"] and not text:
        return "this one can't be left empty"
    if fmt == "plain":
        return f"keep it to {PLAIN_MAX} characters or fewer" if len(text) > PLAIN_MAX else None
    if fmt == "inline" and len(text) > INLINE_MAX:
        return f"keep it to {INLINE_MAX} characters or fewer"
    if fmt == "lines":
        # Without the bar the whole line becomes the first part: on the home
        # page that is a sentence in the huge type meant for a number.
        for number, line in enumerate(text.splitlines(), start=1):
            if line.strip() and "|" not in line:
                return f"line {number} needs a | between its two parts"
    return richtext_problem(text)


def render_value(block, text):
    """What a template gets: rich HTML for markdown, one line of rich HTML
    for inline, a string for plain text, a list of [first, second] pairs
    for lines."""
    from .templatetags.md import inline_richtext, richtext_filter

    if block["format"] == "markdown":
        return richtext_filter(text)
    if block["format"] == "lines":
        rows = []
        for line in text.splitlines():
            if not line.strip():
                continue
            parts = [part.strip() for part in re.split(r"\s*\|\s*", line, maxsplit=1)]
            parts += [""] * (2 - len(parts))
            rows.append([inline_richtext(parts[0]), inline_richtext(parts[1])])
        return rows
    if block["format"] == "inline":
        return inline_richtext(" ".join(text.split()))
    return " ".join(text.split())
