"""The society's Mailman list (SiteConfig.mailing_list_address).

SRCF lists are Mailman 2.1. A list called <soc>-<name> takes mail at
<soc>-<name>@srcf.net and keeps its pages at lists.srcf.net/mailman/<view>/<list>.
Those pages need an SRCF or University login, so anyone else leaves a list
by emailing its -request address with the subject "unsubscribe".
"""

SRCF_DOMAIN = "srcf.net"


def _list_name(address):
    """The list's name for an SRCF list address, else ""."""
    local, _, domain = (address or "").strip().lower().partition("@")
    if domain != SRCF_DOMAIN or not local or not all(char.isalnum() or char == "-" for char in local):
        return ""
    return local


def list_url(address, view="admin", page=""):
    """A page of an SRCF Mailman list: view "admin" (settings and members,
    with ``page`` such as "members/add"), "admindb" (posts waiting for
    approval), "listinfo" and so on. "" for an address that isn't one."""
    name = _list_name(address)
    if not name:
        return ""
    return f"https://lists.srcf.net/mailman/{view}/{name}/{page}".rstrip("/")


def leave_address(address):
    """Where to email "unsubscribe" to leave the list, or ""."""
    name = _list_name(address)
    return f"{name}-request@{SRCF_DOMAIN}" if name else ""


def leave_note(address):
    """One sentence for people stopping the website's mailer: the committee
    takes them off the society's list too when it next tidies it, and how to
    leave straight away. "" when there's no list."""
    if not address:
        return ""
    request = leave_address(address)
    if request:
        # the list's own pages need an SRCF or University login, so not those
        how = (f"email {request} from the address its emails come to, with the subject “unsubscribe”, then reply "
               "to the email that asks you to confirm")
    else:
        how = "follow the instructions at the foot of its emails"
    return (f"The committee also takes you off the society's mailing list, {address}, when it next tidies it; "
            f"to leave that straight away, {how}.")
