from .models import Notification


def unread_notifications(request):
    if not request.user.is_authenticated:
        return {"unread_notifications": 0}
    return {"unread_notifications": Notification.unread_count_for(request.user)}
