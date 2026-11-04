import re
from decimal import Decimal, InvalidOperation

from django import template
from django.urls import reverse
from django.utils.html import format_html

register = template.Library()


@register.filter
def inr(value):
    """₹1,23,456.00 with Indian digit grouping."""
    if value in (None, ""):
        return "—"
    try:
        v = Decimal(value).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError):
        return value
    whole, frac = f"{abs(v):.2f}".split(".")
    if len(whole) > 3:
        whole = re.sub(r"(\d)(?=(\d\d)+$)", r"\1,", whole[:-3]) + "," + whole[-3:]
    return f"{'-' if v < 0 else ''}₹{whole}.{frac}"


@register.filter
def days(value):
    """5.0 -> 5 ; 2.5 -> 2.5"""
    try:
        d = Decimal(value)
    except (InvalidOperation, TypeError):
        return value
    return f"{d.normalize():f}" if d == d.to_integral() else f"{d:.1f}"


@register.simple_tag
def crumb_url(target):
    if isinstance(target, (tuple, list)):
        return reverse(target[0], args=target[1:])
    return reverse(target)


BADGES = {
    "PRESENT": ("success", "Present"), "ABSENT": ("danger", "Absent"), "HALF_DAY": ("warning", "Half day"),
    "PAID_LEAVE": ("primary", "Paid leave"), "UNPAID_LEAVE": ("secondary", "Unpaid leave"),
    "NOT_MARKED": ("light text-muted border", "Not marked"),
    "DRAFT": ("warning", "Draft"), "FINALIZED": ("success", "Finalized"),
    "Pending": ("danger", "Pending"), "Partially Paid": ("warning", "Partially paid"), "Paid": ("success", "Paid"),
    "Active": ("success", "Active"), "Inactive": ("secondary", "Inactive"),
}


@register.simple_tag
def badge(key):
    cls, label = BADGES.get(key, ("secondary", key))
    return format_html('<span class="badge rounded-pill text-bg-{}">{}</span>', cls, label)


@register.simple_tag(takes_context=True)
def url_replace(context, **kwargs):
    q = context["request"].GET.copy()
    for k, v in kwargs.items():
        q[k] = v
    return q.urlencode()


@register.filter
def get_item(d, key):
    return d.get(key, 0) if d else 0


@register.filter
def status_label(value):
    return str(value).replace("_", " ").capitalize()
