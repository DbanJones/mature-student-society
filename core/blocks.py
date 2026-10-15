"""The words on the site's fixed pages.

The layout of a fixed page (the home page, About, the Winter Ball…) lives
in its template, but every piece of prose on it is a text block listed
here: a key, the page it belongs to, a label for the Pages panel, a
format and the default wording. When an admin edits a block on the Pages
panel the new text is stored as a ``core.TextBlock`` row with the same
key; delete the row and the default comes back. Templates read blocks
with ``{% text "key" %}``.

Formats: ``markdown`` renders as rich text; ``plain`` is one line of text;
``lines`` is one entry per line, each split on " | " into two parts (a
heading and its text, a time and what happens, a question and its
answer).
"""

import re

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
     "note": "The date, venue and ticket count come from the Winter Ball event on the calendar."},
    {"key": "wellbeing", "title": "Wellbeing", "url": "core:wellbeing", "section": "about",
     "note": "The body is the “wellbeing” page below; the crisis-support panel stays in code so it can't be deleted by accident."},
    {"key": "testimonials", "title": "Testimonials", "url": "testimonials:index", "section": "about",
     "links": [("Moderate testimonials", "panel:testimonials")]},
    {"key": "policies", "title": "Community policies", "url": "core:policies", "section": "about"},
    {"key": "polls", "title": "Polls", "url": "polls:index", "section": "about",
     "note": "Lists itself from the polls members and organisers run."},
    {"key": "groups", "title": "Groups", "url": "events:groups", "section": "about",
     "note": "Each group's own page is edited by the group's owners from that page."},
    {"key": "surveys", "title": "Surveys", "url": "surveys:index", "section": "members",
     "note": "Lists itself from the surveys on the Admin → People → Surveys tab."},
    {"key": "dashboard", "title": "Dashboard", "url": "dashboard:home", "section": "members",
     "note": "Each member's own page; nothing to edit."},
    {"key": "terms", "title": "Terms and conditions", "url": "core:terms", "section": "footer",
     "links": [("Terms versions", "panel:terms")]},
]
PAGES_BY_KEY = {page["key"]: page for page in PAGES}


def _block(page, label, fmt, default):
    return {"page": page, "label": label, "format": fmt, "default": default}


BLOCKS = {
    # --- home ----------------------------------------------------------------
    "home.award_main": _block("home", "Award badge (first)", "plain",
                              "Winner: Outstanding Contribution to University Life 2025"),
    "home.award_second": _block("home", "Award badge (second)", "plain",
                                "Nominated: University Society of the Year 2025"),
    "home.heading": _block("home", "Headline", "plain", "Welcoming Cambridge's mature students"),
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
    # --- about ---------------------------------------------------------------
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
    "wellbeing.lede": _block("wellbeing", "Introduction line", "plain",
        "You're not alone — and we can help you find the right door."),
    "wellbeing.team": _block("wellbeing", "“Reach the wellbeing team” card", "markdown",
        "Email us and mark your message for the wellbeing team. We can point you to the right service, "
        "come with you, or speak up on your behalf — but for clinical or crisis support please use the "
        "University and NHS services above."),
    # --- community policies --------------------------------------------------
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
}


def blocks_for(page_key):
    return [key for key, block in BLOCKS.items() if block["page"] == page_key]


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


def render_value(block, text):
    """What a template gets: rich HTML for markdown, a string for plain
    text, a list of [first, second] pairs for lines."""
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
    return " ".join(text.split())
