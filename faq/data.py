"""Guide toolbox content (who-to-contact starter tree, college and
department pages), distilled from the *Cambridge as a Mature Student — Master
Report & New-Student Handbook* (consolidated master edition, checked
12 July 2026), kept in the repo root as
``Cambridge_Mature_Student_Master_Report_2026.docx``.

Everything here is data, not logic, so the committee can edit answers without
touching a view. The report's own caveat applies throughout and is shown on
every FAQ page: consequential answers (money, visas, housing, assessment)
must be confirmed with your own College and kept in writing.
"""

# --- Who to contact: decision tree --------------------------------------------------
# Nodes keyed by id. A node is either a question with options (each option
# points to another node) or a result. Rendered as an interactive flow AND a
# full visual map.

CONTACT_TREE = {
    "start": {
        "q": "What kind of problem is it?",
        "options": [
            ("🚨 Emergency — danger to someone right now", "emergency"),
            ("📚 Academic — essays, supervisions, deadlines, exams", "academic"),
            ("💙 Health & wellbeing", "welfare"),
            ("💷 Money", "money"),
            ("🏠 Accommodation", "housing"),
            ("👨‍👩‍👧 Family, partner or childcare", "family"),
            ("🛂 Visa or immigration", "visa"),
            ("⚖️ Harassment or discrimination", "harassment"),
            ("🦁 Something about MSS itself", "society"),
        ],
    },
    "emergency": {
        "result": {
            "who": "999 / 112 — then the Porters' Lodge",
            "do": "999 or 112 for immediate danger. NHS 111 for urgent "
                  "medical advice that isn't an emergency. The Porters' "
                  "Lodge for immediate practical college response — it "
                  "operates beyond office hours. Samaritans 116 123 to "
                  "talk, any hour.",
            "escalate": "Once safe, tell your Tutor what they need to know "
                        "so academic protection can start.",
            "keep": "Nothing — act first, record dates and details later.",
        },
    },
    "academic": {
        "q": "What's the academic issue?",
        "options": [
            ("One difficult essay or problem set", "ac_one"),
            ("A repeating pattern of difficulty", "ac_pattern"),
            ("A deadline I will (or did) miss", "ac_deadline"),
            ("Exam access or disability adjustments", "ac_access"),
            ("Timetables, forms or course rules", "ac_admin"),
            ("Conflict with a supervisor", "ac_conflict"),
            ("I'm considering intermitting", "ac_intermit"),
        ],
    },
    "ac_one": {
        "result": {
            "who": "Your supervisor",
            "do": "Clarify the question, the feedback and the next task. "
                  "Bring the exact point where you became uncertain.",
            "escalate": "Your DoS if the pattern repeats or expectations "
                        "between supervisors conflict.",
            "keep": "The work, the comments and your supervision notes.",
        },
    },
    "ac_pattern": {
        "result": {
            "who": "Your Director of Studies",
            "do": "Bring examples, supervisor reports and an honest picture "
                  "of your weekly workload.",
            "escalate": "Your Tutor if health, money or family is "
                        "contributing; the Faculty if it's course-wide.",
            "keep": "Submitted work, marks, feedback and a one-week time log.",
        },
    },
    "ac_deadline": {
        "result": {
            "who": "The person named in the submission rule, plus your supervisor/DoS",
            "do": "Write BEFORE the deadline where possible. State what is "
                  "complete, why, and a realistic new time.",
            "escalate": "Your Tutor and the Faculty administrator if an "
                        "allowance or formal process is needed.",
            "keep": "A timestamped draft, medical/caring evidence if "
                    "relevant, and the submission rule itself.",
        },
    },
    "ac_access": {
        "result": {
            "who": "The ADRC + your college exam office, by the published deadline",
            "do": "Disclose early. Ask for a Student Support Document and a "
                  "named implementation owner. Teaching adjustments do NOT "
                  "automatically create exam arrangements — they are "
                  "separate deadlines.",
            "escalate": "Tutor/Senior Tutor and the Faculty disability "
                        "contact if agreed adjustments aren't implemented.",
            "keep": "Evidence, the SSD, and dates of what was (not) put in place.",
        },
    },
    "ac_admin": {
        "result": {
            "who": "The Faculty administrator",
            "do": "They are the operational expert for timetables, "
                  "submissions, forms and course rules. Ask: “which "
                  "published rule applies, and where is the current form?”",
            "escalate": "The course organiser if it affects the whole "
                        "paper or lab group rather than just you.",
            "keep": "The handbook rule and any written replies.",
        },
    },
    "ac_conflict": {
        "result": {
            "who": "Name the academic issue with the supervisor first; then your DoS",
            "do": "Ask for clarity about expectations. If trust or access "
                  "has broken down, tell your DoS and request a change of "
                  "supervisor or mediation — this is a normal request.",
            "escalate": "A senior academic or the Faculty route if the DoS "
                        "cannot resolve it or has a conflict of interest.",
            "keep": "Work, feedback, dates and agreed expectations.",
        },
    },
    "ac_intermit": {
        "result": {
            "who": "Your Tutor first; DoS in parallel",
            "do": "Map health, conditions of return, housing, finance, visa "
                  "and childcare BEFORE deciding. Ask what intermission "
                  "means for each, in writing.",
            "escalate": "Senior Tutor; International Student Office if you "
                        "hold a visa; SU Advice for independent guidance.",
            "keep": "Medical/support evidence and your academic position.",
        },
    },
    "welfare": {
        "q": "What's going on?",
        "options": [
            ("I'm ill and it's affecting study", "we_ill"),
            ("Mental health, or feeling isolated", "we_mind"),
            ("Disability or a long-term condition", "we_disability"),
            ("The problem is my Tutor themselves", "we_tutor"),
        ],
    },
    "we_ill": {
        "result": {
            "who": "GP / NHS 111 first; then your Tutor and DoS",
            "do": "Seek medical help, then notify your Tutor and DoS that "
                  "study is affected and ask for immediate academic "
                  "protection. Tell teaching staff only what they need.",
            "escalate": "999/111 as medically appropriate; Senior Tutor and "
                        "the intermission route for sustained inability.",
            "keep": "Medical records, dates, and which teaching/exams are hit.",
        },
    },
    "we_mind": {
        "result": {
            "who": "Your GP / the University Counselling Service; your college welfare route",
            "do": "Pick ONE repeated daytime contact (a person, a group, a "
                  "table) and tell your Tutor or welfare rep if mood or "
                  "function is deteriorating. Isolation yields to "
                  "repetition, not to willpower.",
            "escalate": "GP/Student Support for treatment; the JCR/MCR or "
                        "equality rep if the isolation is structural (e.g. "
                        "everything scheduled at 11pm).",
            "keep": "A simple note of what you've tried and what's closed to you.",
        },
    },
    "we_disability": {
        "result": {
            "who": "The Accessibility & Disability Resource Centre (ADRC)",
            "do": "Disclose early enough to arrange evidence, teaching "
                  "adjustments and exam access — these have separate "
                  "deadlines. Ask who implements each adjustment in your "
                  "college and Faculty.",
            "escalate": "Tutor/Senior Tutor and Faculty disability contact "
                        "if recommendations aren't implemented.",
            "keep": "Evidence, the Student Support Document, implementation dates.",
        },
    },
    "we_tutor": {
        "result": {
            "who": "An alternative Tutor, the Senior Tutor, or the published college route",
            "do": "Every college has an alternative route for exactly this. "
                  "Get independent advice from SU Advice — free, "
                  "confidential and outside your college.",
            "escalate": "The college complaints procedure; a University "
                        "route only where jurisdiction applies.",
            "keep": "A chronology: requests, replies, impact, desired remedy.",
        },
    },
    "money": {
        "result": {
            "who": "Your Tutor + the college financial support office — today",
            "do": "Ask about emergency bridge funding, grants, meal credit "
                  "and bill payment plans. Protect essentials first. "
                  "College hardship funds are genuinely usable — apply.",
            "escalate": "The central University hardship scheme, SU Advice, "
                        "and specialist debt/housing advice where needed.",
            "keep": "Bank balance, bills due, your award letter, a short "
                    "cash-flow statement.",
        },
    },
    "housing": {
        "result": {
            "who": "The college accommodation office — in writing",
            "do": "Report the defect or need and request a specific outcome: "
                  "inspection, repair, temporary measure or room move.",
            "escalate": "Tutor/Senior Tutor, the college complaints route, "
                        "SU Advice; council or tenancy advice if it's a "
                        "private landlord.",
            "keep": "Photos, your licence/tenancy, medical or access "
                    "documents, a dated repair log.",
        },
    },
    "family": {
        "q": "Which bit of family life?",
        "options": [
            ("Child or dependant ill — I'm missing teaching", "fa_ill"),
            ("Childcare or school places", "fa_childcare"),
            ("Relationship breakdown", "fa_breakdown"),
        ],
    },
    "fa_ill": {
        "result": {
            "who": "Backup care first; then teaching staff + your Tutor",
            "do": "Notify teaching and your Tutor, and request the SPECIFIC "
                  "missed-session or deadline action you need.",
            "escalate": "DoS/Faculty if compulsory teaching clashes "
                        "repeatedly; financial support if care costs rise.",
            "keep": "School/nursery notices, appointments, dates and tasks affected.",
        },
    },
    "fa_childcare": {
        "result": {
            "who": "Nursery waiting lists + council school admissions — before arrival if possible",
            "do": "Neither childcare nor school places are guaranteed by "
                  "college membership. University nursery lists are long — "
                  "join the day you accept the offer. Ask other MSS parents "
                  "about catchments before renting.",
            "escalate": "The Childcare Grant (85% of eligible costs, capped) "
                        "and college family funds for the money side; the "
                        "Family & Partners tag for the community side.",
            "keep": "Application dates and confirmation emails.",
        },
    },
    "fa_breakdown": {
        "result": {
            "who": "Safety, housing, money and childcare first; your Tutor for urgent support",
            "do": "Tell your Tutor only what is needed for urgent practical "
                  "support (e.g. emergency housing). You do not owe anyone "
                  "the whole story.",
            "escalate": "Domestic-abuse services or police if unsafe; "
                        "college emergency housing; legal advice.",
            "keep": "Tenancy/licence, finances, important documents, "
                    "messages where there is abuse or harassment.",
        },
    },
    "visa": {
        "result": {
            "who": "The International Student Office — immediately",
            "do": "Do NOT rely on peers or social media for immigration "
                  "answers, and avoid any unauthorised work or status "
                  "change while you wait.",
            "escalate": "A qualified immigration adviser as directed; your "
                        "college and the Student Registry for status action.",
            "keep": "Passport, visa/eVisa, CAS, travel dates, all "
                    "correspondence, attendance documents.",
        },
    },
    "harassment": {
        "result": {
            "who": "Specialist harassment/violence support, or the college reporting route",
            "do": "Prioritise safety. Choose informal or formal routes at "
                  "your pace — a short boundary may be enough for a "
                  "one-off; you decide.",
            "escalate": "Formal college/University complaint, SU Advice, "
                        "police where criminal or immediate.",
            "keep": "Messages, screenshots, exact words, dates, places, "
                    "witnesses, and the outcome you want.",
        },
    },
    "society": {
        "q": "What about MSS?",
        "options": [
            ("A club or its events (Supper, History, Coffee…)", "so_club"),
            ("Membership, WhatsApp access or the portal", "so_admin"),
            ("A problem with another member", "so_member"),
        ],
    },
    "so_club": {
        "result": {
            "who": "The tag owner — listed on each club's page",
            "do": "Every club tag page (e.g. Supper Club) names who runs "
                  "it; message them on the portal or catch them at an event.",
            "escalate": "The committee at the society email if the owner "
                        "doesn't respond.",
            "keep": "Nothing formal needed — we're friendly.",
        },
    },
    "so_admin": {
        "result": {
            "who": "Any committee admin",
            "do": "Use the society contact email (in the footer) or message "
                  "an admin on the portal — membership approvals, WhatsApp "
                  "re-invites and portal problems all land with us.",
            "escalate": "The webmaster for anything technical the admins "
                        "can't fix.",
            "keep": "A screenshot if it's a portal bug. We love screenshots.",
        },
    },
    "so_member": {
        "result": {
            "who": "A committee admin, in confidence",
            "do": "You can block any member from their message thread "
                  "yourself; for anything beyond that, tell an admin — "
                  "messages and events have moderation tools and we use them.",
            "escalate": "College/University routes for anything that is "
                        "harassment wherever it happens; we'll support you.",
            "keep": "Screenshots and dates.",
        },
    },
}

# The role glossary shown beside the flow.
CONTACT_ROLES = [
    ("Porters' Lodge", "Keys, safety, visitors, post and immediate practical "
     "incidents — the front door that never quite shuts.",
     "“I am a resident student and need immediate practical help with…”"),
    ("Supervisor", "Teaches a particular paper in a small group; detailed "
     "academic feedback on that work.",
     "“I do not see why that follows — can we compare approaches?”"),
    ("Director of Studies", "Your college's academic coordinator: paper "
     "choices, supervisors, structural difficulty.",
     "“The pattern across my last three pieces is…”"),
    ("Tutor", "College pastoral role — illness, money, family, welfare, "
     "intermission. Not usually your subject.",
     "“Something outside my work is affecting my work.”"),
    ("Senior Tutor", "Senior college officer over education and welfare — an "
     "escalation point, not a first contact.",
     "“My Tutor/DoS and I have not been able to resolve…”"),
    ("Faculty administrator", "Operational expert for timetables, forms, "
     "submissions and the current written rule.",
     "“Which published rule applies, and where is the form?”"),
    ("College nurse / welfare officer", "Local health and welfare support "
     "where provided; scope and confidentiality vary — ask first.",
     "“Before I disclose details, what must be shared, and with whom?”"),
    ("JCR / MCR rep", "Student representative for welfare, accommodation, "
     "equality and mature students.",
     "“Is this a known pattern, and what's the formal route?”"),
    ("Cambridge SU Student Advice", "Free, confidential, impartial advice "
     "that is independent of your college.",
     "“I need to understand my options before I respond.”"),
]


# --- College-specific information ---------------------------------------------------
# Keyed by the slugs used in accounts.models.COLLEGES so profiles link up.
# status: admissions position · character: editorial orientation ·
# verify: what to check with the college itself · formal: dining rhythm ·
# mature_note: anything specifically for mature students/families.

COLLEGES_INFO = {
    "christs": dict(
        name="Christ's", area="Central", status="All-age undergraduate",
        character="Traditional central college with a strong undergraduate identity.",
        verify="Vacation stay, kitchen level and family guest rules.",
        formal="Bookable term-time Formals (exact dates on the intranet); three "
               "courses, gown required, dress smart — plus Halloween/Christmas "
               "“super halls”. Food: strong traditional.",
        mature_note="Often suggested as a relaxed first Formal.",
    ),
    "churchill": dict(
        name="Churchill", area="West", status="All-age; substantial postgraduate community",
        character="Modern, spacious campus with a science and technology culture.",
        verify="West Cambridge location travel times; couples/family eligibility "
               "for undergraduates.",
        formal="At least twice weekly in recent descriptions; ordinary Formals "
               "relatively relaxed — gowns mainly for feasts. Food: solid/community.",
        mature_note="Less formality by default, which some adults prefer.",
    ),
    "clare": dict(
        name="Clare", area="Centre / west", status="All-age undergraduate",
        character="Central Old Court plus Memorial Court; strong dining and community.",
        verify="Rooms and access are split across sites; kitchen and vacation terms.",
        formal="Four times a week in term; three courses, gowns usual. Food: "
               "destination/strong — a classic food-first guest Formal.",
    ),
    "clare-hall": dict(
        name="Clare Hall", area="West", status="Postgraduate college",
        character="Small, informal, international postgraduate community with a "
                  "long-standing family-friendly reputation; no undergraduates, "
                  "no High Table culture.",
        verify="Family housing stock, partner access and current dining pattern — "
               "postgraduate colleges sit outside the undergraduate tables in "
               "our source report.",
        formal="Regular informal member dining rather than a classic undergraduate "
               "Formal rhythm; verify with the college.",
        mature_note="Postgraduate-only: relevant if you're applying for a "
                    "postgraduate course rather than a mature undergraduate place.",
    ),
    "corpus": dict(
        name="Corpus Christi", area="Central", status="All-age undergraduate",
        character="Small, central and close-knit.",
        verify="Limited scale can mean fewer room types; ask about living out.",
        formal="Exact public schedule not located — use the member booking "
               "calendar; likely gown/smart. Food: insufficient public evidence.",
    ),
    "darwin": dict(
        name="Darwin", area="Central / riverside", status="Postgraduate college",
        character="Cambridge's first graduate college and the first to admit men "
                  "and women; informal, international, riverside site.",
        verify="Room and couples/family stock, dining pattern and society access.",
        formal="Regular member dining and occasional formals in an intentionally "
               "informal register; verify current pattern.",
        mature_note="Postgraduate-only, but a large share of MSS's postgraduate "
                    "members call it home.",
    ),
    "downing": dict(
        name="Downing", area="Central-south", status="All-age undergraduate",
        character="Large neoclassical site; candlelit Hall is a big part of the appeal.",
        verify="Room band, cooking facilities and annual contract length.",
        formal="Commonly several nights a week in Full Term; gown and smart dress "
               "expected. Food: strong traditional — good candlelit atmosphere pick.",
    ),
    "emmanuel": dict(
        name="Emmanuel", area="Central", status="All-age undergraduate",
        character="Central with an active undergraduate community (and famous ducks).",
        verify="Room allocation and vacation residence.",
        formal="Regular term-time Formal; gown/smart normally expected. Food: "
               "strong traditional.",
    ),
    "fitzwilliam": dict(
        name="Fitzwilliam", area="North-west", status="All-age undergraduate",
        character="Modern/traditional mix on the hill, near the mature colleges.",
        verify="Hill location; family and partner accommodation.",
        formal="Usually Wednesday plus selected Fridays; smart dress, gown rules "
               "differ by dinner. Food: solid/community — a relaxed first Formal.",
    ),
    "girton": dict(
        name="Girton", area="North-west edge", status="All-age undergraduate",
        character="Large site and community, noticeably further from the centre.",
        verify="Travel time is material; on-site facilities and vacation stay.",
        formal="Usually a weekly or selected-night pattern; gown and smart/formal "
               "often used. Food: solid/community. Plan the journey home after dinner.",
    ),
    "gonville-caius": dict(
        name="Gonville & Caius", area="Central / west", status="All-age undergraduate",
        character="Historic central courts; strong dining tradition.",
        verify="Site allocation and kitchens.",
        formal="Traditionally one of the highest-frequency Formals — often six "
               "evenings with sittings; gown and smart expected. Food: "
               "destination/strong; great for swaps.",
    ),
    "homerton": dict(
        name="Homerton", area="South-east", status="All-age undergraduate",
        character="Large, diverse community; the biggest college by numbers.",
        verify="Long trip to west-Cambridge sites; don't assume central-college "
               "conventions.",
        formal="Regular Formals and special dinners; invitation-specific rules. "
               "Food: solid/community.",
        mature_note="Well-placed for Addenbrooke's-based courses.",
    ),
    "hughes-hall": dict(
        name="Hughes Hall", area="Central-east", status="Mature-only undergraduate (21+)",
        character="Central-east, mixed undergraduate/postgraduate and "
                  "international — visibly adult college culture.",
        verify="Undergraduate room guarantee, contract length, family/couple "
               "stock, kitchens and vacation residence.",
        formal="Regular community dinners and special Formals; often formal dress "
               "with event-specific gown guidance. Food: solid/community — "
               "comfortable for partners and older guests.",
        mature_note="One of the three mature colleges. Its June Event ran at £85 "
                    "in 2026 — good value, mixed-age crowd.",
    ),
    "jesus": dict(
        name="Jesus", area="Central-north", status="All-age undergraduate",
        character="Large green site with active clubs.",
        verify="Room band and kitchen provision.",
        formal="Five nights a week in widely published descriptions; gown plus "
               "formal/smart, three courses. Food: destination/strong; guest "
               "places competitive.",
    ),
    "kings": dict(
        name="King's", area="Central", status="All-age undergraduate",
        character="Central, international, with a distinctive social and political "
                  "culture — and the tourists to match.",
        verify="Access, room and dining details; tourist-heavy setting.",
        formal="Regular Formals; event-specific rules — read the ticket wording. "
               "Food: strong traditional. Grand-setting pick.",
    ),
    "lucy-cavendish": dict(
        name="Lucy Cavendish", area="North-west", status="All-age undergraduate since 2021",
        character="Historically the mature/women's college, now all-age and "
                  "all-gender with a strong non-traditional heritage.",
        verify="The current age mix is changing fast — check it matches older "
               "descriptions before relying on them; family and accommodation offer.",
        formal="Regular dining and selected Formals; usually smart with "
               "event-specific gown guidance. Food: solid/community.",
        mature_note="Its 2026 Garden Party was the cheapest event of May Week "
                    "(£26) — a genuinely low-cost, community option.",
    ),
    "magdalene": dict(
        name="Magdalene", area="North", status="All-age undergraduate",
        character="Small historic river college; traditional, candlelit dining.",
        verify="Old buildings and access; limited room stock.",
        formal="High-frequency Formal pattern, often most weekday evenings; gown "
               "and smart/formal, some dinners approach white-tie tradition. "
               "Food: destination/strong — the atmosphere pick.",
    ),
    "murray-edwards": dict(
        name="Murray Edwards", area="North-west", status="Women students; all-age",
        character="Modern, women-centred, gardens; kitchens often praised.",
        verify="Current gender eligibility policy and exact accommodation.",
        formal="A regular weekly Formal, commonly Tuesday in the Dome; smart "
               "dress. Food: solid/community.",
    ),
    "newnham": dict(
        name="Newnham", area="West", status="Women students; all-age",
        character="Large gardens, strong women's history and community.",
        verify="Current gender eligibility, room contract and cooking.",
        formal="Regular Formals and special dinners; pattern not consistently "
               "public. Food: strong traditional.",
    ),
    "pembroke": dict(
        name="Pembroke", area="Central", status="All-age undergraduate",
        character="Central, compact, with a strong dining tradition.",
        verify="Room site and vacation availability.",
        formal="Commonly four evenings a week in Full Term; gown, smart dress and "
               "Latin grace are usual. Food: destination/strong — classic guest "
               "Formal target.",
    ),
    "peterhouse": dict(
        name="Peterhouse", area="Central-south", status="All-age undergraduate",
        character="The smallest and oldest college; deliberately traditional.",
        verify="Traditional culture may delight or constrain — inspect access and "
               "room rules.",
        formal="Regular traditional Formal, sometimes multiple sittings; gown and "
               "smart formalwear. Food: destination/strong; candlelit atmosphere pick.",
    ),
    "queens": dict(
        name="Queens'", area="Central", status="All-age undergraduate",
        character="Central river college with a busy undergraduate community.",
        verify="Old/new courts vary a lot — accessibility and cooking.",
        formal="Regular term-time Formals; gown/smart normally expected. Food: "
               "strong traditional; the river setting adds atmosphere.",
    ),
    "robinson": dict(
        name="Robinson", area="West", status="All-age undergraduate",
        character="The newest college: modern red-brick, integrated site.",
        verify="Family stock and summer use.",
        formal="Regular Formals on selected nights; smart dress with "
               "event-specific gown rules. Food: solid/community — judge by menu "
               "and company, not antiquity.",
    ),
    "selwyn": dict(
        name="Selwyn", area="West", status="All-age undergraduate",
        character="Residential and sociable, right by the Sidgwick site.",
        verify="Room and vacation terms.",
        formal="Tuesday and Thursday at about 19:30 in recent information; gown "
               "and smart dress; guest allowance commonly limited. Food: strong "
               "traditional — an accessible first Formal, ideal for humanities "
               "students.",
    ),
    "sidney-sussex": dict(
        name="Sidney Sussex", area="Central", status="All-age undergraduate",
        character="Small, very central and intimate (opposite Sainsbury's, as "
                  "everyone will tell you).",
        verify="City-centre noise and space; room allocation and kitchens.",
        formal="Regular term-time Formal; gown/smart normally expected. Food: "
               "solid/community.",
    ),
    "st-catharines": dict(
        name="St Catharine's", area="Central", status="All-age undergraduate",
        character="Central, compact, active community.",
        verify="Accommodation site, formalwear and access.",
        formal="Regular Formal with the pattern in the booking system; gown plus "
               "smart formal clothing, with gender-neutral dress guidance. Food: "
               "strong traditional; active swap culture.",
    ),
    "st-edmunds": dict(
        name="St Edmund's", area="North-west", status="Mature-only undergraduate (21+)",
        character="Small mature-undergraduate presence inside an international "
                  "mixed community — a college built around adults.",
        verify="Accommodation availability, the hill journey, family provision "
               "and vacation operation.",
        formal="Regular Formals and feasts; academic gown required — and "
               "distinctively, no separate High Table division at dinner. Food: "
               "solid/community; the mixed mature/postgraduate table is the draw.",
        mature_note="One of the three mature colleges.",
    ),
    "st-johns": dict(
        name="St John's", area="Central-north", status="All-age undergraduate",
        character="Large, wealthy, extensive grounds and facilities.",
        verify="Room contract and guest rules.",
        formal="Traditionally six evenings a week in Full Term; gown and smart "
               "dress, feasts stricter. Food: destination/strong; a common guest "
               "target. Its 2026 May Ball ran at flagship prices.",
    ),
    "trinity": dict(
        name="Trinity", area="Central", status="All-age undergraduate",
        character="Large, central, with extensive resources and tradition.",
        verify="Room location, dining costs and vacation residence.",
        formal="Frequent Formals and special dinners; gown and smart normally, "
               "feasts much stricter. Food: destination/strong. (The May Ball — "
               "£190–£290 base in 2026 — is a different beast from an ordinary "
               "Formal.)",
    ),
    "trinity-hall": dict(
        name="Trinity Hall", area="Central", status="All-age undergraduate",
        character="Small central river college; close community.",
        verify="Room years and the living-out route.",
        formal="Regular selected-night Formals; gown/smart generally expected. "
               "Food: strong traditional; booking availability matters more than "
               "ranking.",
    ),
    "wolfson": dict(
        name="Wolfson", area="West", status="Mature-only undergraduate (21+)",
        character="Egalitarian, international, mature/postgraduate-heavy "
                  "community in west Cambridge — less undergraduate theatre.",
        verify="Exact undergraduate numbers, family flats, room contract and the "
               "journey to your teaching site.",
        formal="Regular Formal Hall and special dinners; gowns encouraged or "
               "required for particular occasions rather than uniformly — no "
               "rigid High Table separation. Food: solid/community.",
        mature_note="One of the three mature colleges; particularly relevant to "
                    "older and family applicants.",
    ),
}

# --- Department / faculty-specific information ---------------------------------------
# Cambridge organises subjects under six Schools. The report's guidance is
# structured by *type* of course, so each entry translates it for that School.

DEPARTMENTS_INFO = {
    "arts-humanities": dict(
        name="Arts & Humanities", emoji="🏛️",
        examples="English, History, Modern & Medieval Languages, Classics, "
                 "Philosophy, Music, Architecture, Asian & Middle Eastern Studies…",
        shape="Fewer fixed contact hours, but long independent reading and a "
              "relentless weekly essay cycle. The Sidgwick Site is the centre of "
              "gravity for most of these faculties. Languages add intensive "
              "classes and a year abroad; Architecture adds studio work that "
              "behaves more like a lab science timetable.",
        contacts="Your Faculty administrator for timetables, submission rules "
                 "and forms; the paper coordinator for anything affecting a "
                 "whole paper; the Faculty library's subject librarian — ask "
                 "them for a five-minute route through the catalogue rather "
                 "than losing an hour.",
        mature="Reading after a long gap is the classic returner challenge: "
               "start from the question, preview headings and conclusions, and "
               "stop when you can answer the task — not when the library is "
               "exhausted. Twenty open tabs and guilt is the warning sign.",
        verify="Which papers are examined by coursework vs. exam; the essay "
               "cycle per term; year-abroad or dissertation requirements; how "
               "supervisions are distributed across the term.",
    ),
    "social-sciences": dict(
        name="Humanities & Social Sciences", emoji="⚖️",
        examples="Law, Economics, Human, Social & Political Sciences, "
                 "Psychological & Behavioural Sciences, Education, Land Economy, "
                 "Archaeology…",
        shape="A hybrid: lecture series plus heavy reading lists plus, for some "
              "courses, statistics or methods classes with problem-set "
              "deadlines. Education includes placements, which make generic "
              "timetables misleading — placement rules come from the "
              "department, not the college.",
        contacts="Faculty administrator for rules and forms; the course "
                 "organiser for placement or methods-class issues; your DoS "
                 "for paper choices, which in these subjects can materially "
                 "change your workload shape.",
        mature="Life experience genuinely helps in discussion-led subjects — "
               "use it, but pair it with the citation and methods conventions "
               "the discipline expects. Practise source-recording from week 1.",
        verify="Methods/statistics requirements and support classes; placement "
               "obligations and travel; coursework vs. exam weighting per paper.",
    ),
    "physical-sciences": dict(
        name="Physical Sciences", emoji="🔭",
        examples="Natural Sciences (physical), Mathematics, Physics, Chemistry, "
                 "Earth Sciences, Astronomy, Materials Science…",
        shape="Fixed lectures plus regular problem sheets with hard deadlines "
              "and, for experimental subjects, scheduled practicals you cannot "
              "move. The workload is front-loaded and cumulative: a missed "
              "fortnight compounds faster than in essay subjects.",
        contacts="The department's teaching office / Faculty administrator for "
                 "practical schedules and submission rules; the practical "
                 "class organiser for lab clashes; your supervisor for problem "
                 "sheets; your DoS when the pattern breaks down.",
        mature="Quantitative confidence is the thing returners most often need "
               "to rebuild: run a diagnostic before arrival, pick two priority "
               "gaps, and do one timed problem set with feedback. Don't try to "
               "re-derive your whole school mathematics in week 1.",
        verify="Which practicals are compulsory and how absence is handled; "
               "problem-sheet submission rules; exam structure per paper.",
    ),
    "biological-sciences": dict(
        name="Biological Sciences", emoji="🧬",
        examples="Natural Sciences (biological), Veterinary Medicine "
                 "(pre-clinical), Psychology, Plant Sciences, Zoology, "
                 "Biochemistry, Genetics…",
        shape="Lectures plus fixed laboratory practicals and field elements. "
              "Labs are timetabled by the department and attendance is usually "
              "compulsory — childcare clashes need raising with the course "
              "organiser early, not after three missed sessions.",
        contacts="Departmental teaching office for lab schedules; the course "
                 "organiser for anything affecting the whole lab group; the "
                 "ADRC plus the department's disability contact for lab "
                 "adjustments (they need implementation time).",
        mature="If you have caring responsibilities, map every compulsory lab "
               "slot against your care plan in week 0 and flag clashes in "
               "writing immediately — repeated compulsory-teaching clashes are "
               "a DoS/Faculty matter, and there is a route for them.",
        verify="Compulsory practical/field requirements; safety training dates; "
               "how missed labs are made up.",
    ),
    "clinical-medicine": dict(
        name="Clinical Medicine & Health", emoji="🩺",
        examples="Medicine (clinical school), Clinical Veterinary Medicine, "
                 "and the clinical years of related courses.",
        shape="Placements dominate: hospital and community rotations run to NHS "
              "rhythms, not university terms, and can involve early starts, "
              "evenings and travel. The generic '35 hours' guidance "
              "understates clinical years.",
        contacts="The clinical school office is the operational authority — "
                 "placement allocation, absence rules, occupational health. "
                 "College still owns your housing and pastoral support, which "
                 "is exactly why clinical students need both doors.",
        mature="Many clinical students are already mature students — you'll be "
               "less unusual here than anywhere else. Ask early about "
               "placement travel funding, and about how caring "
               "responsibilities are factored into rotation allocation.",
        verify="Rotation locations and travel; absence and mitigation "
               "procedures; occupational health and DBS timelines; what "
               "happens to placements during intermission.",
    ),
    "technology": dict(
        name="Technology", emoji="⚙️",
        examples="Engineering, Computer Science, Chemical Engineering & "
                 "Biotechnology, Management (Judge Business School)…",
        shape="High fixed contact hours: lectures, labs, coursework projects "
              "and (for Engineering) drawing/computing sessions, with group "
              "project work that requires coordinating other people's "
              "timetables — plan around that if you commute or do school runs.",
        contacts="The department teaching office for lab and project "
                 "scheduling; project supervisors for group-work issues; the "
                 "Faculty administrator for coursework submission rules, which "
                 "are strict and written down.",
        mature="Group projects are where commuting/parenting mature students "
               "feel the friction most: propose meeting patterns early "
               "(daytime, hybrid) rather than absorbing a default of 10pm "
               "sessions. Industrial experience is an asset here — say so.",
        verify="Coursework weighting and late-submission rules; lab/project "
               "attendance requirements; any industrial placement elements.",
    ),
}
