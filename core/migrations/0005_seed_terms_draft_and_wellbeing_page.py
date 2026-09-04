"""Seed a starter terms draft and move the Wellbeing copy into an editable page.

The terms are seeded UNPUBLISHED on purpose. Publishing turns on the
acceptance gate for the whole membership, and the committee should read and
adapt the wording — it is a legal document, not boilerplate — before that
happens. Panel → Terms shows a banner while nothing is published.

The Wellbeing body becomes a SitePage so admins can edit it without a deploy.
The old template claimed some of the wellbeing team were trained in
counselling and mental health first aid; that claim is removed here and
replaced with the University's official support routes, because MSS has no
trained practitioners and shouldn't imply otherwise.
"""

from django.db import migrations

WELLBEING_SLUG = "wellbeing"

WELLBEING_CONTENT = """At MSS we know that mature students at Cambridge face particular pressures — often juggling study with careers, family responsibilities or major life changes. Many are new to the city, to the University's way of doing things, or to the UK itself. It is easy to feel isolated or overwhelmed, and asking early always beats asking late.

## Where to get support

**MSS is a peer community, not a counselling service.** Nobody on the committee is a trained mental health practitioner. If you are struggling, please use the University's official services below — they are free, confidential, and staffed by qualified professionals.

* **In an emergency, or if someone is in immediate danger — call 999.**
* **University Student Wellbeing Service**, including the University Counselling Service — the University's official support service for students: [studentwellbeing.admin.cam.ac.uk](https://www.studentwellbeing.admin.cam.ac.uk/)
* **NHS 111** — free, 24/7. Select the mental health option for urgent help.
* **Samaritans — 116 123** — free, 24/7, any time and any reason.
* **Your College Tutor or College Nurse** — your first port of call inside college for anything affecting your work or your welfare.
* **Cambridge SU Student Advice Service** — free, confidential and independent of your college: [cambridgesu.co.uk/support/advice](https://www.cambridgesu.co.uk/support/advice/)

Not sure which door to knock on? The Guide's [Who do I contact?](/faq/who-to-contact/) map walks you through it.

## What MSS can offer

* **A Mature Students Talking Group** — a regular in-person meet-up: a friendly space to talk about academic pressure, isolation, or adjusting to a new environment, among people in the same boat.
* **Practical advice** — housing, healthcare, childcare, course deadlines: peer-to-peer guidance and hive-mind solutions from people who have been there.
* **Advocacy** — help finding the right channel for a department or college issue, or speaking up on your behalf.
* **Company** — someone to walk to an appointment with, and someone to check in on you afterwards.

These are things members do for each other. They sit alongside professional support — never in place of it.
"""

TERMS_TITLE = "Terms and Conditions"

TERMS_CONTENT = """These terms cover your use of the University of Cambridge Mature Student Society (MSS) members' portal, and your conduct in MSS spaces — this website, our events, and our WhatsApp community.

**This is a starting draft.** The committee should review, adapt and publish it before it is put to members.

## 1. Who can join

Membership is open to mature students of the University of Cambridge, and to the partners and family members of mature students through an associate account approved by the committee. You agree to give accurate details when you register and to keep them up to date.

## 2. Your account

Your account is personal to you. Do not share your login, and tell the committee promptly if you think someone else has access to it. Accounts obtained by giving false information may be removed.

## 3. How you treat other members

You agree to follow the MSS Community Policies. In short: post with kindness, use your real name, do not send unsolicited direct messages, and disagree with ideas rather than with people. Harassment, discrimination and abuse have no place in MSS and may lead to your membership being suspended or ended.

## 4. Other members' information

The member directory and event attendee lists exist so members can recognise and contact each other. You agree to use that information only for MSS purposes — never to market anything, to build a mailing list, or to pass on to anyone outside the society. Mobile numbers are shown only to admins and to the organisers of events you RSVP to; treat them as confidential.

## 5. What you post

You keep ownership of what you write, and you grant MSS permission to display it in the portal and in society communications. Do not post anything unlawful, defamatory, or that infringes someone else's rights. Admins may edit or remove content, and the Guide is a shared wiki that other members may edit.

## 6. Events

RSVPs help hosts plan, and some events cost the society money per head. Please cancel if your plans change. Member-run events are organised by members, not by the society; MSS is not responsible for them.

## 7. Wellbeing

MSS is a peer community. We are not a counselling, medical, legal or financial service, and nothing offered by members or through this portal is professional advice. For support, please use the University and NHS services listed on our Wellbeing page.

## 8. Your data

We hold the details you give us in order to run the society: your name, contact details, college and course, your profile, and your event RSVPs. We do not sell your data or pass it to third parties for marketing. You can view and change your details on your profile page at any time, and you can ask the committee to delete your account.

We record the date and time you accept these terms, and the version you accepted, so we can show what was agreed and when.

## 9. Ending your membership

You may leave at any time by asking the committee to close your account. We may suspend or end membership where these terms or the Community Policies are broken.

## 10. Changes to these terms

We may update these terms. When we publish a new version you will be asked to accept it the next time you use the portal, and the date of your acceptance is recorded.

## 11. Contact

Questions about these terms go to the committee at the society's contact address.
"""


def seed(apps, schema_editor):
    SitePage = apps.get_model("core", "SitePage")
    TermsVersion = apps.get_model("core", "TermsVersion")

    if not SitePage.objects.filter(slug=WELLBEING_SLUG).exists():
        SitePage.objects.create(
            title="Wellbeing",
            slug=WELLBEING_SLUG,
            content=WELLBEING_CONTENT,
            is_published=True,
            # Wellbeing already has its own entry in the main navigation, so
            # this page must not add a second one.
            nav_label="",
            nav_visibility="public",
            sort_order=50,
        )

    if not TermsVersion.objects.exists():
        TermsVersion.objects.create(
            number=1,
            title=TERMS_TITLE,
            content=TERMS_CONTENT,
            change_note="Starter draft — review and publish before use.",
            is_published=False,
        )


def unseed(apps, schema_editor):
    apps.get_model("core", "SitePage").objects.filter(
        slug=WELLBEING_SLUG
    ).delete()
    apps.get_model("core", "TermsVersion").objects.filter(
        number=1, is_published=False
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0004_termsversion_termsrevision_termsacceptance"),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
