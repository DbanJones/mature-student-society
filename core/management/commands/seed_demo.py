"""Seed the database with demo data for local development and screenshots.

Usage:  python manage.py seed_demo
Safe to re-run: existing objects (matched by natural keys) are left alone.
Also seeds the event categories, which ARE wanted in production — run
``python manage.py seed_demo --categories-only`` on SRCF.
"""

import datetime

from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.utils import timezone

from accounts.models import User, WaitlistRequest
from core.models import SiteConfig
from events.models import Category, Event, RSVP
from guide.models import GuidePage
from supper.models import Rating, Restaurant

CATEGORIES = [
    # (name, slug, colour, emoji, has_ratings, sort, description)
    # Mirrors the clubs and event types MSS actually runs (per the old site).
    ("Society-wide", "society-wide", "#1e2a22", "🎓", False, 10,
     "Big society events: welcome drinks, awards night, freshers fair."),
    ("Supper Club", "supper-club", "#b82818", "🍽️", True, 20,
     "Cambridge's international cheap-eats, one restaurant at a time — rated by attendees."),
    ("History Club", "history-club", "#7a5c2e", "🏛️", False, 30,
     "MSSHC: talks, walks and pub-room history for the historically curious."),
    ("Book Club", "book-club", "#5b4a78", "📚", False, 40,
     "MSSBC: one book a month, strong opinions encouraged."),
    ("Pub Nights", "pub-nights", "#8a5a2c", "🍺", False, 50,
     "Exploring Cambridge's pub scene, one snug at a time."),
    ("Formals & Swaps", "formals-swaps", "#313d6b", "🕯️", False, 60,
     "Traditional Cambridge dining — formals and formal-swaps across colleges."),
    ("College Lunches & Tours", "college-lunches", "#4d6141", "🏫", False, 70,
     "Get to know the University's 31 colleges."),
    ("Wellbeing & Coffee", "wellbeing-coffee", "#3e7d8d", "🫖", False, 80,
     "Tea/coffee/cake meets and the weekly Talking Group."),
    ("Coffee Club", "coffee-club", "#7c5a33", "☕", False, 85,
     "Weekly coffee mornings — drop in, no sign-up, all welcome."),
    ("Sports, Walks & Runs", "sports-walks", "#3e7d4f", "🥾", False, 90,
     "The sports hub: walks, runs, punting and days out."),
    ("Networking & Flashtalks", "networking", "#46628a", "🎤", False, 100,
     "Research flashtalks, networking potlucks, careers."),
    ("Family & Partners", "family-partners", "#c2703d", "🧸", False, 110,
     "Events for members with children and partners — prams welcome."),
    ("Partners Club", "partners-club", "#a34d7c", "💞", False, 115,
     "Run by and for partners of mature students — meet-ups, tips and mutual support."),
    ("Study Sessions", "study-sessions", "#4a5d78", "🎯", False, 120,
     "Shared study and co-working sessions."),
]

DEMO_USERS = [
    # (username/crsid, first, last, college, admin, course, bio)
    ("dbj25", "Dennis", "Bailey-Jones", "hughes-hall", True,
     "PhD Computer Science",
     "Webmaster and committee member. Came back to academia after a decade "
     "in industry — ask me about the portal, or about good coffee."),
    ("amk67", "Amara", "Kensington", "wolfson", True,
     "MBA",
     "I run the Supper Club — always hunting for Cambridge's best cheap "
     "eats. Suggestions welcome."),
    ("rt489", "Robert", "Tanaka", "st-edmunds", False,
     "MPhil Early Modern History",
     "History Club convenor. Former secondary school teacher; will talk "
     "about the Civil War at the slightest provocation."),
    ("efw22", "Elena", "Fitzwilliam-Wright", "lucy-cavendish", False,
     "BA English (mature)",
     "Book Club host. Reader, rower (badly), mother of two."),
    ("jm901", "James", "MacAllister", "hughes-hall", False,
     "PGCE",
     "Ex-army, now training to teach physics. Pub night regular."),
    ("pn315", "Priya", "Natarajan", "wolfson", False,
     "PhD Plant Sciences",
     "Family & Partners events — usually found on Jesus Green with a "
     "toddler and a picnic blanket."),
    ("sc777", "Sofia", "Castellanos", "darwin", False,
     "MPhil Development Studies",
     "Coffee Club host. Bilingual chatter, strong flat whites."),
    ("hb244", "Henry", "Blackwood", "st-edmunds", False,
     "BTh",
     "Second-career ordinand. Happy to talk theology or cricket."),
]


# Profile colour for the demo members: conversation starters, background and
# interests, keyed by username.
EXTRA_PROFILE = {
    "dbj25": ("the portal, single-origin coffee, coming back to code after management",
              "A decade in software, latterly engineering management",
              "espresso, bouldering, mechanical keyboards"),
    "amk67": ("Cambridge's best cheap eats — fight me",
              "Ex-restaurant manager turned MBA",
              "food markets, supper clubs, salsa"),
    "rt489": ("the Civil War at the slightest provocation",
              "Former secondary-school history teacher",
              "archives, battlefield walks, real ale"),
    "efw22": ("whatever the Book Club is reading, rowing badly",
              "Raised two kids, ran the school library, now reading English",
              "novels, rivers, second-hand bookshops"),
    "jm901": ("physics teaching, army stories, pub quizzes",
              "Twelve years in the Royal Engineers",
              "rugby, quizzes, DIY"),
    "pn315": ("plant science, toddler logistics, picnic spots",
              "Research assistant before the PhD",
              "gardening, picnics, wild swimming"),
    "sc777": ("flat whites, development economics, café recommendations",
              "NGO field work in three countries",
              "coffee, languages, photography"),
    "hb244": ("theology or cricket — ideally both",
              "Twenty years as a parish administrator",
              "cricket, choral music, walking"),
    "marta.tanaka@example.com": ("moving to Cambridge as a partner, Japanese cooking",
                                 "Graphic designer, freelancing from home",
                                 "illustration, cooking, park runs"),
}


class Command(BaseCommand):
    help = "Seed demo data (categories, users, events, guide pages, ratings)."

    def add_arguments(self, parser):
        parser.add_argument("--categories-only", action="store_true",
                            help="Only create event categories (for production).")

    def handle(self, *args, **options):
        self.seed_categories()
        if options["categories_only"]:
            self.stdout.write(self.style.SUCCESS("Categories seeded."))
            return
        self.seed_site_config()
        users = self.seed_users()
        self.seed_events(users)
        self.seed_tag_pages()
        self.seed_guide(users)
        call_command("seed_guide")  # the Master Report pages
        self.seed_waitlist()
        self.seed_messages(users)
        self.seed_polls_and_testimonials(users)
        self.seed_surveys(users)
        self.stdout.write(self.style.SUCCESS(
            "Demo data seeded. Dev logins: any demo user via the dev login page "
            "(RAVEN_ENABLED=false), or password 'demo-password' for all of them."
        ))

    def seed_categories(self):
        for name, slug, color, emoji, has_ratings, sort, desc in CATEGORIES:
            Category.objects.get_or_create(
                slug=slug,
                defaults=dict(name=name, color=color, emoji=emoji,
                              has_restaurant_ratings=has_ratings,
                              sort_order=sort, description=desc),
            )

    def seed_site_config(self):
        config = SiteConfig.get()
        if not config.about_text:
            # Adapted from the About Us copy on the previous site.
            config.about_text = (
                "Founded in September 2024, the **Mature Student Society (MSS)** has "
                "quickly grown to nearly 1,000 members. Our mission is to make the "
                "full Cambridge experience accessible to everyone — regardless of "
                "age, background, or life path.\n\n"
                "MSS exists to support and connect those who may not follow the "
                "traditional student journey. You'll fit right in if you're returning "
                "to academia after a career or caring responsibilities, finally "
                "pursuing long-held academic ambitions, or just that little bit older "
                "than most of your peers (or just feel older!). Our youngest member "
                "is 21, our oldest over 80.\n\n"
                "**Our community includes** undergraduates and postgraduates, "
                "postdocs and research fellows, professional and continuing-education "
                "students, university staff, visiting scholars — and the partners and "
                "children of any of the above."
            )
            config.whatsapp_group_link = "https://chat.whatsapp.com/DEMO-OPEN-FORUM-LINK"
            config.save()

    def seed_users(self):
        users = {}
        for crsid, first, last, college, is_admin, course, bio in DEMO_USERS:
            user, created = User.objects.get_or_create(
                username=crsid,
                defaults=dict(
                    crsid=crsid, first_name=first, last_name=last,
                    college=college, account_type=User.AccountType.RAVEN,
                    email=f"{crsid}@cam.ac.uk", mobile="+44 7700 900123",
                    is_portal_admin=is_admin, course=course, bio=bio,
                    # The webmaster is the super admin.
                    is_super_admin=(crsid == "dbj25"),
                ),
            )
            if created:
                user.set_password("demo-password")
                user.save()
            users[crsid] = user
        # One approved associate (partner) account.
        assoc, created = User.objects.get_or_create(
            username="marta.tanaka@example.com",
            defaults=dict(
                first_name="Marta", last_name="Tanaka",
                email="marta.tanaka@example.com",
                account_type=User.AccountType.ASSOCIATE,
                college="other", mobile="+44 7700 900456",
                course="Partner member",
                bio="Partner of Robert (St Edmund's). I help run the Partners "
                    "Club — say hello at a coffee morning!",
            ),
        )
        if created:
            assoc.set_password("demo-password")
            assoc.save()
        users["associate"] = assoc

        # Conversation starters / work / interests (idempotent backfill: only
        # fills profiles that haven't set their own).
        for username, (talk, work, interests) in EXTRA_PROFILE.items():
            User.objects.filter(
                username=username, talk_to_me_about="", work="", interests="",
            ).update(talk_to_me_about=talk, work=work, interests=interests)

        # Tag owners: the members who run each club.
        owner_plan = {
            "supper-club": ["amk67"],
            "history-club": ["rt489"],
            "book-club": ["efw22"],
            "coffee-club": ["sc777"],
            "partners-club": ["associate", "pn315"],
        }
        for slug, ids in owner_plan.items():
            tag = Category.objects.filter(slug=slug).first()
            if tag:
                tag.owners.add(*[users[uid] for uid in ids])
        return users

    def seed_events(self, users):
        if Event.objects.exists():
            return
        now = timezone.localtime()
        categories = {c.slug: c for c in Category.objects.all()}

        def at(days, hour, minute=0):
            return (now + datetime.timedelta(days=days)).replace(
                hour=hour, minute=minute, second=0, microsecond=0
            )

        # Restaurants — real Supper Club venues from the old site (two past
        # visits already rated, one upcoming).
        tiffin = Restaurant.objects.create(
            name="The Tiffin Truck", cuisine="Indian street food", area="Regent Street",
            added_by=users["amk67"],
        )
        limoncello = Restaurant.objects.create(
            name="Limoncello", cuisine="Italian", area="Mill Road",
            added_by=users["rt489"],
        )
        vedanta = Restaurant.objects.create(
            name="Vedanta", cuisine="Indian fine dining", area="Regent Street",
            added_by=users["amk67"],
        )

        spec = [
            # (title, cat, days, hour, official, members_only, creator, location, capacity, restaurant)
            ("Michaelmas Welcome Drinks", "society-wide", 3, 19, True, False, "dbj25",
             "The Granta, 14 Newnham Road", None, None),
            ("Supper Club at Vedanta", "supper-club", 6, 19, True, False, "amk67",
             "Vedanta, Regent Street", 16, vedanta),
            ("MSS History Club: the Bolt Hole", "history-club", 8, 19, False, False, "rt489",
             "The Castle Inn, 38 Castle Street (the Bolt Hole room)", 14, None),
            ("MSS Book Club: 'The Old Ways'", "book-club", 10, 19, False, False, "efw22",
             "Emmanuel College, St Andrew's Street", None, None),
            ("Family picnic on Jesus Green", "family-partners", 12, 11, False, False, "pn315",
             "Jesus Green (by the tennis courts)", None, None),
            ("Tea/Coffee/Cake", "wellbeing-coffee", 13, 14, False, False, "sc777",
             "Fitzwilliam Museum Courtyard Café, Trumpington Street", None, None),
            ("Pub Night at The Free Press", "pub-nights", 16, 19, False, True, "jm901",
             "The Free Press, 7 Prospect Row", None, None),
            ("Punting & picnic day", "sports-walks", 21, 12, True, False, "amk67",
             "Mill Lane punt station", 20, None),
            ("Coffee Club at Hot Numbers", "coffee-club", 5, 10, False, False, "sc777",
             "Hot Numbers, Gwydir Street", None, None),
            ("Partners Club brunch", "partners-club", 9, 11, False, False, "pn315",
             "Stir Bakery, Chesterton Road", 12, None),
            # Past events for stats/ratings.
            ("Supper Club at The Tiffin Truck", "supper-club", -12, 19, True, False, "amk67",
             "The Tiffin Truck, 22 Regent Street", 14, tiffin),
            ("Supper Club at Limoncello", "supper-club", -40, 19, True, False, "amk67",
             "Limoncello, 212 Mill Road", 12, limoncello),
            ("History Club: Cambridge in the Civil War", "history-club", -20, 18, False, False, "rt489",
             "Mill Lane lecture rooms", None, None),
        ]
        events = {}
        for row in spec:
            (title, cat, days, hour, official, members_only,
             creator, location, capacity, restaurant) = row
            minute = 0
            event = Event.objects.create(
                title=title,
                description=f"Join us for **{title}** — all mature students and partners welcome. "
                            "RSVP below so we know numbers.",
                category=categories[cat],
                location=location,
                start=at(days, hour, minute),
                end=at(days, hour + 2, minute),
                created_by=users[creator],
                host=users[creator],
                is_official=official,
                members_only=members_only,
                capacity=capacity,
                restaurant=restaurant,
            )
            events[title] = event

        # Attendee-only extras on one upcoming event, to demo the post-RSVP card.
        vedanta_supper = events["Supper Club at Vedanta"]
        vedanta_supper.group_chat_link = "https://chat.whatsapp.com/DEMO-VEDANTA-GROUP"
        vedanta_supper.attendee_info = (
            "We've got the long table at the back — ask for the MSS booking.\n\n"
            "- Arrive from **18:45**; we order at 19:15 sharp.\n"
            "- Set menu is £24pp, pay the organiser on the night (card OK).\n"
            "- Running late? Message Amara in the group chat."
        )
        vedanta_supper.save()

        # The Winter Ball — the society's flagship night (12 December).
        ball_start = timezone.make_aware(
            datetime.datetime(now.year, 12, 12, 19, 30)
        )
        if ball_start < now:
            ball_start = ball_start.replace(year=now.year + 1)
        ball = Event.objects.create(
            title=f"MSS Winter Ball {ball_start.year}",
            description=(
                "**The Mature Student Society Winter Ball** — our flagship "
                "black-tie evening of dinner, dancing and midwinter sparkle, "
                "open to members and their partners.\n\n"
                "See the [Winter Ball page](/winter-ball/) for the full "
                "programme, dress code and FAQs."
            ),
            category=categories["society-wide"],
            location="The Old Hall, Queens' College, Silver Street",
            start=ball_start,
            end=ball_start + datetime.timedelta(hours=5, minutes=30),
            created_by=users["dbj25"],
            host=users["dbj25"],
            is_official=True,
            is_super=True,  # the flagship: demonstrates the super-event tier
            capacity=150,
            attendee_info=(
                "Doors from **19:00**, carriages at **01:00**.\n\n"
                "- Dietary requirements: fill in the form emailed a fortnight "
                "before the night.\n"
                "- Cloakroom available; bring your ticket QR code."
            ),
        )
        events[ball.title] = ball
        for uid in ["dbj25", "amk67", "efw22", "pn315", "sc777", "jm901"]:
            RSVP.objects.get_or_create(event=ball, user=users[uid])
        RSVP.objects.get_or_create(event=ball, user=users["associate"])

        member_ids = ["dbj25", "amk67", "rt489", "efw22", "jm901", "pn315", "sc777", "hb244"]
        rsvp_plan = {
            "Michaelmas Welcome Drinks": member_ids,
            "Supper Club at Vedanta": ["dbj25", "amk67", "efw22", "pn315", "sc777"],
            "MSS History Club: the Bolt Hole": ["rt489", "hb244", "dbj25"],
            "MSS Book Club: 'The Old Ways'": ["efw22", "sc777", "pn315"],
            "Family picnic on Jesus Green": ["pn315", "rt489", "jm901"],
            "Punting & picnic day": ["amk67", "dbj25", "efw22", "hb244", "jm901"],
            "Supper Club at The Tiffin Truck": ["dbj25", "amk67", "rt489", "efw22", "pn315"],
            "Supper Club at Limoncello": ["amk67", "rt489", "jm901", "sc777"],
            "History Club: Cambridge in the Civil War": ["rt489", "dbj25", "hb244"],
        }
        for title, ids in rsvp_plan.items():
            for uid in ids:
                RSVP.objects.get_or_create(event=events[title], user=users[uid])
        RSVP.objects.get_or_create(
            event=events["Family picnic on Jesus Green"], user=users["associate"]
        )

        # Ratings for the two past supper clubs.
        ratings = {
            "Supper Club at The Tiffin Truck": {
                "dbj25": (5, 4, 4, 5, "Railway-station thali done properly. Loud but fun."),
                "amk67": (5, 5, 4, 4, "Best value curry in Cambridge, book the long table."),
                "rt489": (4, 4, 4, 4, ""),
                "efw22": (5, 4, 4, 5, "Great for a big group — sharing plates worked well."),
            },
            "Supper Club at Limoncello": {
                "amk67": (4, 3, 4, 4, "Deli up front, lovely trattoria feel out back."),
                "rt489": (4, 3, 3, 4, ""),
                "jm901": (5, 4, 4, 4, "The lemon tart. That is all."),
            },
        }
        for title, by_user in ratings.items():
            for uid, (food, service, atmosphere, value, comment) in by_user.items():
                Rating.objects.get_or_create(
                    event=events[title], user=users[uid],
                    defaults=dict(food=food, service=service, atmosphere=atmosphere,
                                  value=value, comment=comment),
                )

    def seed_tag_pages(self):
        """Starter page content for the club tags (owners can edit these)."""
        pages = {
            "supper-club": (
                "## How Supper Club works\n\n"
                "One restaurant a month, always somewhere **cheap, cheerful and "
                "international**. RSVP on the event page — numbers matter for "
                "bookings — and rate the restaurant afterwards.\n\n"
                "- We order to share where the menu allows it.\n"
                "- Budget: roughly £15–25 a head including a drink.\n"
                "- Partners always welcome.\n\n"
                "Check the ratings board on the Supper Club page to see where "
                "we've been and what we thought."
            ),
            "history-club": (
                "## MSS History Club\n\n"
                "Talks, walks and pub-room history for the historically "
                "curious — no prior knowledge needed, strong opinions optional "
                "but traditional.\n\n"
                "We meet roughly fortnightly in term. Suggestions for talks and "
                "walking routes are always welcome."
            ),
            "coffee-club": (
                "## Coffee Club\n\n"
                "The lowest-commitment club in the society: **turn up, drink "
                "coffee, talk to people**. No sign-up, no agenda, prams and "
                "laptops equally welcome.\n\n"
                "We rotate around Cambridge's independent cafés — see the "
                "upcoming events below for the next one."
            ),
            "partners-club": (
                "## Partners Club\n\n"
                "Run **by partners, for partners** of mature students. Moving "
                "to a new city where your other half promptly disappears into "
                "a library is hard — this club is the antidote.\n\n"
                "- Regular brunches and playground meet-ups.\n"
                "- A friendly WhatsApp group (ask at any event).\n"
                "- Practical help: schools, GP registration, work visas."
            ),
        }
        for slug, content in pages.items():
            Category.objects.filter(slug=slug, page_content="").update(
                page_content=content
            )

    def seed_messages(self, users):
        from inbox.models import DirectMessage

        if DirectMessage.objects.exists():
            return
        script = [
            ("amk67", "dbj25",
             "Dennis — can you make the Vedanta supper official when you get a "
             "sec? Table's confirmed for 16."),
            ("dbj25", "amk67",
             "Done, and pinned it to the What's On mailer. Looking forward to it!"),
            ("rt489", "dbj25",
             "Thinking of a Civil War walking tour for week 5 — too niche?"),
            ("dbj25", "rt489",
             "Not at all, the last one filled up in a day. Put it on the "
             "calendar and I'll share it to the group."),
        ]
        for sender, recipient, body in script:
            DirectMessage.objects.create(
                sender=users[sender], recipient=users[recipient], body=body
            )

    def seed_guide(self, users):
        if GuidePage.objects.exists():
            return
        pages = [
            ("Choosing a mature college", "colleges", "efw22",
             "Four colleges admit mature undergraduates only: **Hughes Hall**, "
             "**St Edmund's**, **Wolfson** and (for postgraduates of any age) "
             "**Darwin**. Lucy Cavendish historically admitted mature women and "
             "now admits everyone.\n\n"
             "## Things people wish they'd known\n\n"
             "- Mature colleges have far more couples' and family accommodation — "
             "ask the accommodation office *early*.\n"
             "- Formal halls at mature colleges are relaxed about partners.\n"
             "- Distance to your department matters more than prestige when "
             "you're doing a school run."),
            ("Money, funding and part-time work", "money", "rt489",
             "Mature students can access the standard student finance package "
             "plus, in many cases, the **Cambridge Bursary**. Parents' income "
             "isn't assessed if you're over 25 or estranged.\n\n"
             "- The university hardship funds are genuinely usable — apply.\n"
             "- College fees are covered for home undergraduates by the tuition "
             "fee loan.\n"
             "- Working during term is formally discouraged; vacation work is "
             "normal and colleges can help find it."),
            ("Bringing a partner or family", "family", "pn315",
             "You are not the only one doing this with a family. Really.\n\n"
             "- **University nursery** places have long waitlists; apply the day "
             "you accept your offer.\n"
             "- Partners can get a university card via some colleges — ask the "
             "tutorial office.\n"
             "- Our society events marked *Family & Kids* are exactly that; prams "
             "welcome.\n"
             "- Schools: the catchment system matters, talk to other members "
             "before renting."),
            ("Surviving supervisions as a mature student", "study", "dbj25",
             "Supervisions can feel intimidating when your supervisor is younger "
             "than you. Three things help:\n\n"
             "1. Your life experience is an asset in discussion — use it.\n"
             "2. Ask for reading lists early; you may be juggling childcare.\n"
             "3. Be honest about time constraints — supervisors adapt if told."),
            ("Where to actually live", "living", "amk67",
             "College accommodation, the private market, or living out in the "
             "villages — each works for different situations.\n\n"
             "- **College**: cheapest, zero admin, but variable for families.\n"
             "- **Private in town**: expensive; look at Mill Road, Chesterton, "
             "Romsey.\n"
             "- **Villages**: much cheaper, needs a car or a good bike; "
             "Waterbeach and Histon have trains/busway."),
        ]
        for title, section, uid, content in pages:
            page = GuidePage.objects.create(
                title=title, slug=title.lower().replace(" ", "-").replace(",", "").replace("'", ""),
                section=section, content=content,
                created_by=users[uid], updated_by=users[uid],
            )
            page.save_revision(users[uid])

    def seed_waitlist(self):
        WaitlistRequest.objects.get_or_create(
            email="alex.morgan@example.com",
            defaults=dict(
                first_name="Alex", last_name="Morgan",
                mobile="+44 7700 900789",
                connection="Partner of Priya Natarajan (Wolfson). Moving to Cambridge in September.",
            ),
        )
        WaitlistRequest.objects.get_or_create(
            email="claire.dubois@example.com",
            defaults=dict(
                first_name="Claire", last_name="Dubois",
                connection="Partner of a incoming Hughes Hall MBA student.",
            ),
        )

    def seed_surveys(self, users):
        """One open survey, run by two members, with a few answers in."""
        from surveys.models import Answer, Choice, Participation, Question, Response, Survey

        if Survey.objects.exists():
            return
        now = timezone.now()
        survey = Survey.objects.create(
            title="Michaelmas temperature check",
            intro="Five minutes on how this term is going, so the committee can plan Lent. "
                  "Answer with your name or anonymously, whichever you prefer.",
            created_by=users["dbj25"], status=Survey.Status.OPEN,
            closes_at=now + datetime.timedelta(days=10),
            results_visibility=Survey.Results.RESPONDENTS,
        )
        survey.admins.set([users["amk67"], users["efw22"]])
        mood = Question.objects.create(
            survey=survey, prompt="How are you finding this term so far?", kind="scale",
            scale_low="Struggling", scale_high="Thriving", sort_order=1,
        )
        kinds = Question.objects.create(
            survey=survey, prompt="Which kinds of event would you come to?", kind="multi",
            help_text="Tick as many as you like.", sort_order=2,
        )
        options = [
            Choice.objects.create(question=kinds, label=label, sort_order=i)
            for i, label in enumerate(["Pub nights", "Formals and swaps", "Walks and runs", "Study sessions", "Family-friendly days"])
        ]
        fair = Question.objects.create(
            survey=survey, prompt="Would you volunteer at the Freshers Fair next year?",
            kind="yesno", required=False, sort_order=3,
        )
        more = Question.objects.create(
            survey=survey, prompt="Anything the committee should know?", kind="long",
            required=False, sort_order=4,
        )

        def answer(user, anonymous, score, picks, yes, text):
            response = Response.objects.create(
                survey=survey, respondent=None if anonymous else user, is_anonymous=anonymous,
                submitted_at=None if anonymous else now,
            )
            Answer.objects.create(response=response, question=mood, value=score)
            picked = Answer.objects.create(response=response, question=kinds)
            picked.choices.set([options[i] for i in picks])
            Answer.objects.create(response=response, question=fair, value=yes)
            if text:
                Answer.objects.create(response=response, question=more, text=text)
            Participation.objects.create(survey=survey, user=user)

        answer(users["sc777"], False, "4", [0, 2], "yes", "More daytime events for those of us with children, please.")
        answer(users["pn315"], True, "2", [3], "no", "The WhatsApp group is a lot. A weekly digest would help.")
        answer(users["jm901"], False, "5", [0, 1, 4], "yes", "")

    def seed_polls_and_testimonials(self, users):
        """A venue poll, a Freshers Fair volunteer rota, a general poll, a few
        testimonials, term dates, and one member with messaging enabled."""
        from polls.models import Poll, PollOption, PollVote
        from testimonials.models import Testimonial

        config = SiteConfig.get()
        if config.michaelmas_start is None:
            config.michaelmas_start = datetime.date(2026, 10, 6)
            config.lent_start = datetime.date(2027, 1, 19)
            config.easter_start = datetime.date(2027, 4, 27)
            config.save()
        User.objects.filter(username="amk67").update(messaging=User.Messaging.ENABLED)

        if not Poll.objects.exists():
            now = timezone.now()
            book_club = Event.objects.filter(title__startswith="MSS Book Club").first()
            if book_club:
                poll = Poll.objects.create(
                    event=book_club, kind=Poll.Kind.VENUE, created_by=users["efw22"],
                    question="Where shall we meet next month?",
                    closes_at=now + datetime.timedelta(days=3),
                )
                options = [
                    PollOption.objects.create(poll=poll, label=label, sort_order=i)
                    for i, label in enumerate(["Emmanuel College bar", "Heffers café", "The Eagle"])
                ]
                for uid, option in (("efw22", 0), ("sc777", 0), ("pn315", 1), ("dbj25", 2)):
                    PollVote.objects.create(poll=poll, option=options[option], user=users[uid])

            fair_start = (now + datetime.timedelta(days=12)).replace(hour=10, minute=0, second=0, microsecond=0)
            fair = Event.objects.create(
                title="Freshers Fair stall", category=Category.objects.get(slug="society-wide"),
                description="Our stall at the Freshers Fair. **Volunteers needed** for the slots below.",
                location="Kelsey Kerridge Sports Hall", start=fair_start,
                end=fair_start + datetime.timedelta(hours=5), created_by=users["dbj25"],
                host=users["dbj25"], is_official=True,
            )
            rota = Poll.objects.create(
                event=fair, kind=Poll.Kind.VOLUNTEERS, created_by=users["dbj25"],
                question="Who can staff the stall?", allow_multiple=True,
                closes_at=fair_start - datetime.timedelta(days=1),
            )
            slots = [
                PollOption.objects.create(poll=rota, label=label, capacity=cap, sort_order=i)
                for i, (label, cap) in enumerate([
                    ("Set-up and 10:00 to 12:00", 2), ("12:00 to 14:00", 2), ("14:00 to pack-down", 3),
                ])
            ]
            PollVote.objects.create(poll=rota, option=slots[0], user=users["amk67"])
            PollVote.objects.create(poll=rota, option=slots[1], user=users["jm901"])

            general = Poll.objects.create(
                kind=Poll.Kind.GENERAL, created_by=users["amk67"],
                question="Which should we run next term?", allow_multiple=True,
                closes_at=now + datetime.timedelta(days=7),
                description="Pick everything you'd come to.",
            )
            for i, label in enumerate(["Garden party", "Punting afternoon", "Quiz night", "Day trip to Ely"]):
                PollOption.objects.create(poll=general, label=label, sort_order=i)

        if not Testimonial.objects.exists():
            Testimonial.objects.create(
                author=users["rt489"], author_name="Robert Tanaka", author_college="St Edmund's",
                body="I came back to study at 48 convinced I'd be the odd one out. MSS made Cambridge feel like somewhere I belonged within a fortnight.",
                status=Testimonial.Status.APPROVED, is_featured=True,
                reviewed_by=users["dbj25"], reviewed_at=timezone.now(),
            )
            Testimonial.objects.create(
                author=users["pn315"], author_name="Priya Natarajan", author_college="Wolfson",
                body="Doing a PhD with a toddler is hard. The family picnics and the parents' group meant I never had to choose between the two.",
                is_anonymous=True, status=Testimonial.Status.APPROVED,
                reviewed_by=users["dbj25"], reviewed_at=timezone.now(),
            )
            Testimonial.objects.create(
                author=users["hb244"], author_name="Henry Blackwood", author_college="St Edmund's",
                body="The Supper Club alone is worth the membership. Also the cricket chat.",
            )
