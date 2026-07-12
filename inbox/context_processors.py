from .models import DirectMessage


def unread_messages(request):
    """Unread DM count for the nav badge (0 for anonymous visitors)."""
    if not request.user.is_authenticated:
        return {"unread_messages": 0}
    return {"unread_messages": DirectMessage.unread_count_for(request.user)}
