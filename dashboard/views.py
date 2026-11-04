from datetime import date, datetime

from django.shortcuts import render

from accounts.decorators import staff_required
from . import services


@staff_required
def home(request):
    today = date.today()
    try:
        selected = datetime.strptime(request.GET.get("date", ""), "%Y-%m-%d").date()
    except ValueError:
        selected = today
    ctx = services.build(today, selected)
    ctx.update(today=today, selected=selected, crumbs=[])
    return render(request, "dashboard/home.html", ctx)
