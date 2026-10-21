"""A shareable invitation summary; membership still protects the linked page."""
from urllib.parse import quote

from django.utils import timezone


def whatsapp_share_url(request, listing):
    lines = [
        f"{listing.event.title} · {listing.get_college_display()}",
        timezone.localtime(listing.event.start).strftime("%a %d %b %Y, %H:%M"),
        listing.price_text or "Ask the host about the cost.",
    ]
    if listing.invitation_type == "reciprocal":
        places = f"{listing.return_places} place{'s' if listing.return_places != 1 else ''} per booking" if listing.return_places else "matching places"
        deadline = f" by {listing.return_by:%d %b %Y}" if listing.return_by else ""
        lines.append(f"Swap: {places} in return{deadline}.")
        if listing.allowed_return_colleges:
            lines.append("Return colleges: " + ", ".join(listing.allowed_return_college_names))
    else:
        lines.append("No return invitation needed.")
    lines += ["MSS members: sign in to see availability and book.", request.build_absolute_uri(listing.get_absolute_url())]
    return "https://wa.me/?text=" + quote("\n".join(lines), safe="")
