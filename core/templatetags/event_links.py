from django import template

from core import ball

register = template.Library()


@register.simple_tag(takes_context=True)
def event_url(context, event):
    """Where a listing sends people for this event: the Winter Ball page
    for the ball, the event's own page for everything else."""
    if not event:
        return ""
    request = context.get("request")
    if request is None:
        return event.get_absolute_url()
    return ball.event_url(
        event, request.user, request=request, config=context.get("site_config"),
    )
