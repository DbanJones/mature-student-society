"""Seed the database with demo data for local development and screenshots.

Usage:  python manage.py seed_demo
Safe to re-run: existing objects (matched by natural keys) are left alone.
Also seeds the event categories, which ARE wanted in production — run
``python manage.py seed_demo --categories-only`` on SRCF.
"""

import datetime

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
    ("Wellbeing & Coffee", "wellbeing-coffee", "#3e7d8d", "☕", False, 80,
     "Tea/coffee/cake meets and the weekly Talking Group."),
    ("Sports, Walks & Runs", "sports-walks", "#3e7d4f", "🥾", False, 90,
     "The sports hub: walks, runs, punting and days out."),
    ("Networking & Flashtalks", "networking", "#46628a", "🎤", False, 100,
     "Research flashtalks, networking potlucks, careers."),
    ("Family & Partners", "family-partners", "#c2703d", "🧸", False, 110,
     "Events for members with children and partners — prams welcome."),
    ("Study Sessions", "study-sessions", "#4a5d78", "🎯", False, 120,
     "Shared study and co-working sessions."),
]

DEMO_USERS = [
    # (username/crsid, first, last, college, admin)
    ("dbj25", "Dennis", "Bailey-Jones", "hughes-hall", True),
    ("amk67", "Amara", "Kensington", "wolfson", True),
    ("rt489", "Robert", "Tanaka", "st-edmunds", False),
    ("efw22", "Elena", "Fitzwilliam-Wright", "lucy-cavendish", False),
    ("jm901", "James", "MacAllister", "hughes-hall", False),
    ("pn315", "Priya", "Natarajan", "wolfson", False),
    ("sc777", "Sofia", "Castellanos", "darwin", False),
    ("hb244", "Henry", "Blackwood", "st-edmunds", False),
]


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
        self.seed_guide(users)
        self.seed_waitlist()
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
        for crsid, first, last, college, is_admin in DEMO_USERS:
            user, created = User.objects.get_or_create(
                username=crsid,
                defaults=dict(
                    crsid=crsid, first_name=first, last_name=last,
                    college=college, account_type=User.AccountType.RAVEN,
                    email=f"{crsid}@cam.ac.uk", mobile="+44 7700 900123",
                    is_portal_admin=is_admin,
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
            ),
        )
        if created:
            assoc.set_password("demo-password")
            assoc.save()
        users["associate"] = assoc
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
                is_official=official,
                members_only=members_only,
                capacity=capacity,
                restaurant=restaurant,
            )
            events[title] = event

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
