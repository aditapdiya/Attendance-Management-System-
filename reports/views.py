from datetime import date, datetime

from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpResponse
from django.shortcuts import render

from accounts.decorators import staff_required
from employees.models import Employee
from . import builders, exporters


@staff_required
def index(request):
    allowed = {k: v for k, v in builders.REGISTRY.items() if request.user.has_perm(v[3])}
    today = date.today()
    return render(request, "reports/index.html", {
        "reports": [(k, v[0], v[2]) for k, v in allowed.items()], "employees": Employee.objects.order_by("name"),
        "today": today, "month_value": today.strftime("%Y-%m"), "crumbs": [("Reports", None)],
    })


def _params(request, needs):
    g = request.GET
    p = {}
    try:
        today = date.today()
        if "date" in needs:
            p["date"] = datetime.strptime(g.get("date") or today.isoformat(), "%Y-%m-%d").date()
        if any(n.startswith("month") for n in needs):
            val = g.get("period") or today.strftime("%Y-%m")
            dt = datetime.strptime(val, "%Y-%m")
            p["year"], p["month"] = dt.year, dt.month
            if "month_optional" in needs and g.get("whole_year") == "1":
                p["month"] = None
        if "year" in needs:
            p["year"] = int(g.get("year") or (datetime.strptime(g["period"], "%Y-%m").year if g.get("period") else today.year))
        if "employee" in needs:
            p["employee"] = Employee.objects.get(pk=int(g["employee"]))
        if "employee_optional" in needs:
            p["employee"] = Employee.objects.filter(pk=g["employee"]).first() if g.get("employee", "").isdigit() else None
    except (KeyError, ValueError, Employee.DoesNotExist):
        raise Http404("Missing or invalid report parameters (check the date, month, year or employee).")
    return p


@staff_required
def download(request):
    key = request.GET.get("type", "")
    if key not in builders.REGISTRY:
        raise Http404("Unknown report.")
    label, fn, needs, perm = builders.REGISTRY[key]
    if not request.user.has_perm(perm):
        raise PermissionDenied
    rep = fn(_params(request, needs))
    fmt = request.GET.get("fmt", "html")
    if fmt == "xlsx":
        resp = HttpResponse(exporters.to_xlsx(rep), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        resp["Content-Disposition"] = f'attachment; filename="{rep.slug}.xlsx"'
        return resp
    if fmt == "pdf":
        resp = HttpResponse(exporters.to_pdf(rep), content_type="application/pdf")
        resp["Content-Disposition"] = f'attachment; filename="{rep.slug}.pdf"'
        return resp
    return render(request, "reports/preview.html", {
        "rep": rep, "rows": [[exporters.fmt(c) for c in r] for r in rep.rows],
        "totals": [exporters.fmt(c) for c in rep.totals] if rep.totals else None,
        "query": request.GET.urlencode(), "crumbs": [("Reports", "reports:index"), (rep.title, None)],
    })
