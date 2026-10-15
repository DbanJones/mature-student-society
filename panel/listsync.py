"""Keeping the website's copy of the society's Mailman list.

The site can't ask Mailman who is on the list: SRCF lists are Mailman 2.1,
with no API and no export, and their pages sit behind the SRCF login. So a
list admin copies the members from Mailman's own pages and pastes them into
Admin > People > Mailing list. This module reads the addresses out of
whatever was pasted, and decides which website accounts are on the list.

Addresses are compared as mailboxes: any case, and a Cambridge address's
two spellings (crsid@cam.ac.uk and crsid@hermes.cam.ac.uk) are one mailbox.
"""

import hashlib
import re
import threading
from contextlib import contextmanager
from types import SimpleNamespace

CAMBRIDGE = ("cam.ac.uk", "hermes.cam.ac.uk")
CRSID = re.compile(r"[a-z]{2,6}[0-9]{1,5}")
# A plain address, or Mailman's obscured form "someone at example.com". The
# last part of the domain must be letters, so "lunch at 12.30" isn't one.
# Each part has a length limit (an address's own), and an address starts
# where its name does: without those, a long run of letters with no @ in it
# takes minutes to search rather than milliseconds.
LOCAL_CHARS = "A-Za-z0-9.!#$%&'*+/=?^_`{|}~-"
LOCAL = rf"(?<![{LOCAL_CHARS}])[{LOCAL_CHARS}]{{1,64}}"
DOMAIN = r"[A-Za-z0-9-]{1,63}(?:\.[A-Za-z0-9-]{1,63})*\.[A-Za-z]{2,24}"
PLAIN = re.compile(LOCAL + "@" + DOMAIN)
OBSCURED = re.compile(rf"({LOCAL}) at ({DOMAIN})\b")
MAX_PASTE = 2_000_000  # characters; a list of a few thousand fits many times over

# What Mailman's pages say about how many members there are: the subscriber
# list ("12 Non-digested Members of Mss-members:", and "(2 private members
# not shown)" when it hides some), and the Membership List ("240 members
# total, 30 shown" when it is split into pages).
ROSTER_COUNT = re.compile(r"(?<!\d)(\d{1,7})\s+(Non-digested|Digested)\s+Members\s+of\b", re.IGNORECASE)
HIDDEN_COUNT = re.compile(r"\((\d{1,7})\s+private\s+members?\s+not\s+shown\)", re.IGNORECASE)
MEMBERS_TOTAL = re.compile(r"(?<!\d)(\d{1,7})\s+members\s+total(?:,\s*(\d{1,7})\s+shown)?", re.IGNORECASE)


def mailbox_key(email):
    """The one name a mailbox goes by here: lower case, and a Cambridge
    address under its @cam.ac.uk spelling."""
    email = (email or "").strip().lower()
    local, _, domain = email.partition("@")
    if domain in CAMBRIDGE and CRSID.fullmatch(local):
        return f"{local}@cam.ac.uk"
    return email


def spellings(email):
    """Every spelling Mailman may hold a mailbox under: both of a Cambridge
    address's (Mailman counts them as two addresses), anything else as it is."""
    key = mailbox_key(email)
    local, _, domain = key.partition("@")
    if domain == "cam.ac.uk" and CRSID.fullmatch(local):
        return [key, f"{local}@hermes.cam.ac.uk"]
    return [key] if key else []


def not_a_member(address, list_address):
    """Is this one of the addresses on Mailman's pages that isn't a member?
    That's the list's own (mss-members, and mss-members-owner, which every
    page's footer gives as "mss-members-owner at srcf.net", -request,
    -bounces...), at the list's domain or a host under it such as
    lists.srcf.net, and Mailman's. The society's other addresses, such as
    mss-admins, are kept: on the list they are a problem the page points out."""
    name, _, host = address.partition("@")
    if name == "mailman" or name.startswith("mailman-"):
        return True
    list_name, _, domain = (list_address or "").strip().lower().partition("@")
    if not list_name or not (host == domain or host.endswith("." + domain)):
        return False
    return name == list_name or name.startswith(list_name + "-")


def addresses_in(text, list_address=""):
    """Every member address in pasted text, lower-cased, each once, in the
    order they first appear. Mailman's obscured "user at example.com" counts."""
    text = (text or "")[:MAX_PASTE]
    found, seen = [], set()
    candidates = [(match.start(), match.group(0)) for match in PLAIN.finditer(text)]
    candidates += [(match.start(), f"{match.group(1)}@{match.group(2)}") for match in OBSCURED.finditer(text)]
    for _, address in sorted(candidates):
        address = address.strip(".").lower()
        local, _, host = address.partition("@")
        if not local or "." not in host or len(address) > 254 or not_a_member(address, list_address):
            continue
        if address not in seen:
            seen.add(address)
            found.append(address)
    return found


def mailman_counts(text):
    """What the pasted page says about its members, where it says it:
    ``total`` (everyone Mailman counted), ``shown`` (on this page of a
    Membership List split into pages) and ``hidden`` (members the subscriber
    list left out, because the viewer was signed in as a member too)."""
    text = (text or "")[:MAX_PASTE]
    counts = {}
    roster = {}
    for match in ROSTER_COUNT.finditer(text):
        roster.setdefault(match.group(2).lower(), int(match.group(1)))
    if roster:
        counts["total"] = sum(roster.values())
    hidden = HIDDEN_COUNT.search(text)
    if hidden:
        counts["hidden"] = int(hidden.group(1))
    total = MEMBERS_TOTAL.search(text)
    if total and "total" not in counts:
        counts["total"] = int(total.group(1))
        if total.group(2) and int(total.group(2)) < counts["total"]:
            counts["shown"] = int(total.group(2))
    return counts


def on_list(user_email, keys):
    """Is the address the site mails this account at on the list (either
    spelling of a Cambridge address)? Another address of the same person,
    say their Cambridge one after they moved to a personal address, doesn't
    count: that's not where they want the mail."""
    return bool(user_email) and mailbox_key(user_email) in keys


# --- the site's copy, against the website's accounts ----------------------------------------------

def list_keys():
    """The mailboxes on the list, as the site last knew it."""
    from panel.models import ListMember

    return set(ListMember.objects.values_list("key", flat=True))


def gets_mailer(user):
    """Does the website's own mailer reach this account?"""
    return bool(user.is_active and not user.is_banned and user.wants_mailer and user.email)


def reached_keys():
    """The mailboxes the society's mail should reach: the mailer's "both"
    audience (members, and old-list people not on the website). Nothing in
    here is ever to come off the list. Not the old list as a whole: that
    still holds the old address of a member who moved to a new one, which
    is exactly what should come off."""
    from panel.services import mailer_recipients

    members, old = mailer_recipients("both")
    return {mailbox_key(email) for email in members + old}


def stopped_keys(reached=None):
    """The mailboxes that must not get the What's On, so come off the list:
    those of the Mailer's "People who stopped the mailer" (accounts opted
    out, banned or closed, and old-list people who unsubscribed or belong to
    such an account). A mailbox the mailer still reaches isn't one."""
    from panel.services import stopped_rows

    return {mailbox_key(row["email"]) for row in stopped_rows(reached)}


def owed(reached=None):
    """What waits to come off the list because accounts went or moved, or
    someone asked to leave: ``(batches, addresses)``. Each address is checked
    again now, so one the mailer reaches again (a returning member, an
    address moved back) is left out, and so is a batch with nothing left."""
    from panel.models import ListSync

    reached = reached_keys() if reached is None else reached
    batches, addresses = [], set()
    for batch in (ListSync.objects.filter(kind=ListSync.Kind.REMOVE, done_at__isnull=True).exclude(note="")
                  .order_by("-created_at")):
        still = {address for address in batch.addresses if mailbox_key(address) not in reached}
        if still:
            batches.append(batch)
            addresses |= still
    return batches, sorted(addresses)


def close_owed(addresses, reached, actor, when):
    """A take-off file was done on Mailman: the waiting batches it covered
    are done too, and so is any whose addresses the mailer reaches again.
    A batch queued since, with an address the file didn't have, still waits."""
    from panel.models import ListSync

    done = set(addresses)
    covered = [batch.pk for batch in ListSync.objects.filter(kind=ListSync.Kind.REMOVE, done_at__isnull=True)
               .exclude(note="")
               if all(address in done or mailbox_key(address) in reached for address in batch.addresses)]
    return ListSync.objects.filter(pk__in=covered, done_at__isnull=True).update(done_at=when, done_by=actor)


def paste_digest(addresses):
    """A short fingerprint of a paste's addresses, so that confirming a
    paste much smaller than the copy confirms that paste and no other."""
    return hashlib.sha256("\n".join(sorted(addresses)).encode()).hexdigest()[:16]


def account_rows(keys, stop=None):
    """Every website account with an address, by name: whether it is on the
    list, whether the mailer reaches it, and whether it is on the list but
    must come off (``stopped``; see stopped_keys)."""
    from accounts.models import User

    stop = stopped_keys() if stop is None else stop
    users = (User.objects.exclude(email="").order_by("first_name", "last_name", "username")
             .only("id", "username", "first_name", "last_name", "email", "crsid", "is_active", "is_banned",
                   "wants_mailer"))
    rows = []
    for user in users:
        on = on_list(user.email, keys)
        rows.append({"user": user, "on_list": on, "wants": gets_mailer(user),
                     "stopped": on and mailbox_key(user.email) in stop})
    return rows


def _account_keys(user):
    return {mailbox_key(user.email)} if user.email else set()


def old_list_to_add(keys):
    """Old-list addresses of people not yet on the website that the mailer
    reaches and the list doesn't have."""
    from panel.services import mailer_recipients

    return [email for email in mailer_recipients("old_new")[1] if mailbox_key(email) not in keys]


def to_add(rows, keys, old_list=False):
    """The addresses to put on the list: accounts that get the mailer and
    aren't on it (each mailbox once), and with ``old_list`` the old list's
    people who haven't joined the website."""
    out, seen = [], set(keys)
    for row in rows:
        if row["wants"] and not row["on_list"]:
            key = mailbox_key(row["user"].email)
            if key not in seen:
                seen.add(key)
                out.append(row["user"].email.strip())
    if old_list:
        for email in old_list_to_add(keys):
            if mailbox_key(email) not in seen:
                seen.add(mailbox_key(email))
                out.append(email)
    return out


def to_remove(stop=None):
    """Every address on the site's copy whose mailbox must come off (see
    stopped_keys), as spelled on the list, so Mailman's Mass Removal finds
    it: accounts and old-list people alike."""
    from panel.models import ListMember

    stop = stopped_keys() if stop is None else stop
    return list(ListMember.objects.filter(key__in=stop).order_by("email").values_list("email", flat=True))


def summary(rows, keys, removing):
    """The page's counts. Each tile's number is the number of rows its link
    shows; ``to_remove`` is the size of the take-off download."""
    account_keys = set()
    for row in rows:
        account_keys |= _account_keys(row["user"])
    return {
        "on": sum(1 for row in rows if row["on_list"]),
        "missing": sum(1 for row in rows if row["wants"] and not row["on_list"]),
        "stopped": sum(1 for row in rows if row["stopped"]),
        "to_remove": len(removing),
        "outside": len(keys - account_keys),
        "old_missing": len(old_list_to_add(keys)),
    }


def queue_removal(users, actor, note, addresses=None):
    """Accounts deleted, or ``addresses`` that are nobody's any more (a
    member moved to a new one): every spelling Mailman may hold their
    mailboxes under goes into a batch to take off the list, shown on the
    Mailing list page until an admin says it's done. A deleted account's go
    whether or not the site's copy has them (the copy can be out of date,
    and Mass Removal passes over addresses that aren't on the list), and a
    deleted Raven account's Cambridge address goes too (it's the same
    person). An old address goes only if the list may have it. A mailbox
    the mailer still reaches, through another account or the old list,
    stays. Call it once the accounts have gone. Returns the addresses
    queued."""
    from panel.models import ListSync

    keys = {mailbox_key(address) for address in (addresses or []) if address}
    for user in users:
        keys |= _account_keys(user)
        if addresses is None and getattr(user, "crsid", ""):
            keys.add(f"{user.crsid.strip().lower()}@cam.ac.uk")
    if keys:
        keys -= reached_keys()
    if addresses is not None and keys:
        keys &= _may_be_listed(keys)
    queued = sorted({spelling for key in keys for spelling in spellings(key)})
    if queued:
        ListSync.objects.create(kind=ListSync.Kind.REMOVE, created_by=actor, count=len(queued), addresses=queued,
                                note=note[:200])
    return queued


def _may_be_listed(keys):
    """Which of these mailboxes the list may have: all of them while the site
    has never had a whole copy; otherwise those on it, or downloaded to add
    and not yet marked as added."""
    from panel.models import ListMember, ListSync

    if not ListSync.objects.filter(kind=ListSync.Kind.UPDATE, note="").exists():
        return set(keys)
    listed = set(ListMember.objects.filter(key__in=keys).values_list("key", flat=True))
    for batch in (ListSync.objects.filter(kind=ListSync.Kind.ADD, done_at__isnull=True)
                  .values_list("addresses", flat=True)):
        listed |= {mailbox_key(address) for address in batch} & keys
    return listed


# --- accounts deleted, however that happens -------------------------------------------------------

_deleting = threading.local()


@contextmanager
def deleting(actor, note):
    """Accounts deleted inside the block are queued to come off the list in
    one batch, once they've gone; the list it yields then holds the
    addresses queued. Nothing is queued if the block fails."""
    gone, queued = [], []
    outer = getattr(_deleting, "gone", None)
    _deleting.gone = gone
    try:
        yield queued
    finally:
        _deleting.gone = outer
    queued.extend(queue_removal(gone, actor, note))


def account_deleted(sender, instance, **kwargs):
    """post_delete for accounts, whatever deleted them (a panel page, the
    Django admin, a shell): their addresses are queued to take off the list,
    with the rest of a ``deleting`` block's or on their own."""
    gone = SimpleNamespace(pk=instance.pk, email=instance.email or "", crsid=getattr(instance, "crsid", "") or "")
    collecting = getattr(_deleting, "gone", None)
    if collecting is not None:
        collecting.append(gone)
    else:
        queue_removal([gone], None, "an account was deleted")
