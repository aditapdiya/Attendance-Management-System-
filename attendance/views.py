import calendar
import json
from collections import defaultdict
from datetime import date, datetime

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from accounts.decorators import perm_required
from employees.models import Employee
from . import services as svc
from .models import Attendance


def parse_date(s, default=None):
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return default or date.today()


@perm_required("attendance.view_attendance")
def daily(request):
    d = parse_date(request.GET.get("date"))
    q = request.GET.get("q", "").strip()
    status = request.GET.get("status", "")
    emps = svc.eligible_employees(d)
    if q:
        emps = emps.filter(Q(name__icontains=q) | Q(employee_code__icontains=q))
    recs = {a.employee_id: a for a in Attendance.objects.filter(attendance_date=d).select_related("marked_by")}
    rows = []
    for e in emps:
        r = recs.get(e.id)
        s = r.status if r else "NOT_MARKED"
        if status and status != s:
            continue
        rows.append({"emp": e, "rec": r, "status": s, "locked": svc.is_locked(e.id, d)})
    page = Paginator(rows, 100).get_page(request.GET.get("page"))
    return render(request, "attendance/daily.html", {
        "d": d, "rows": page, "page": page, "q": q, "status": status, "counts": svc.day_counts(d),
        "statuses": Attendance.STATUS_CHOICES, "is_future": d > date.today(),
        "can_change": request.user.has_perm("attendance.change_attendance"),
        "can_add": request.user.has_perm("attendance.add_attendance"),
        "crumbs": [("Attendance", None)],
    })


@require_POST
@perm_required("attendance.add_attendance")
def api_save(request):
    """JSON: {date, items:[{employee, status, remarks}]}; status '' clears the record."""
    try:
        data = json.loads(request.body)
        d = datetime.strptime(data["date"], "%Y-%m-%d").date()
        items = data["items"]
    except (ValueError, KeyError, TypeError):
        return JsonResponse({"ok": False, "error": "Bad request."}, status=400)
    can_change = request.user.has_perm("attendance.change_attendance")
    emps = {e.id: e for e in Employee.objects.filter(id__in=[i.get("employee") for i in items if isinstance(i, dict)])}
    out, errors = [], []
    for it in items:
        e = emps.get(it.get("employee"))
        if not e:
            errors.append({"employee": it.get("employee"), "error": "Unknown employee."})
            continue
        try:
            if not it.get("status"):
                if not can_change:
                    raise ValidationError("You do not have permission to edit existing attendance.")
                svc.clear_attendance(e, d, request.user)
                out.append({"employee": e.id, "result": "cleared"})
            else:
                rec, res = svc.set_attendance(e, d, it["status"], it.get("remarks", ""), request.user, can_change)
                out.append({"employee": e.id, "result": res, "updated": rec.updated_at.strftime("%d %b %H:%M")})
        except ValidationError as ex:
            errors.append({"employee": e.id, "error": " ".join(ex.messages)})
    return JsonResponse({"ok": not errors, "saved": out, "errors": errors, "counts": svc.day_counts(d)})


@require_POST
@perm_required("attendance.add_attendance")
def mark_all_present(request):
    d = parse_date(request.POST.get("date"))
    try:
        r = svc.mark_all_present(d, request.user)
    except ValidationError as ex:
        messages.error(request, " ".join(ex.messages))
    else:
        msg = f"{r['created']} employee(s) marked present for {d:%d %b %Y}."
        if r["skipped_existing"]:
            msg += f" {r['skipped_existing']} already had a status and were left unchanged."
        if r["skipped_locked"]:
            msg += f" {r['skipped_locked']} skipped (payroll finalized)."
        messages.success(request, msg)
    return redirect(f"{request.POST.get('back') or '/attendance/'}?date={d:%Y-%m-%d}")


@perm_required("attendance.view_attendance")
def calendar_view(request):
    today = date.today()
    try:
        year, month = int(request.GET.get("year", today.year)), int(request.GET.get("month", today.month))
        date(year, month, 1)
    except ValueError:
        year, month = today.year, today.month
    emp = Employee.objects.filter(pk=request.GET.get("employee") or 0).first()
    first, last = svc.month_range(year, month)
    hw = svc.holiday_weekdays()
    recs = Attendance.objects.filter(attendance_date__range=(first, last))
    if emp:
        recs = recs.filter(employee=emp)
    by_day = defaultdict(list)
    for r in recs:
        by_day[r.attendance_date].append(r)
    weeks = []
    for week in calendar.Calendar(0).monthdatescalendar(year, month):
        row = []
        for d in week:
            cell = {"date": d, "in_month": d.month == month, "holiday": d.weekday() in hw, "future": d > today}
            if d.month == month:
                rs = by_day.get(d, [])
                if emp:
                    cell["status"] = rs[0].status if rs else None
                    cell["missing"] = not rs and not cell["holiday"] and not cell["future"] and d >= emp.joining_date
                else:
                    cell["counts"] = {s: sum(1 for r in rs if r.status == s) for s in svc.STATUSES}
                    n = len(svc.eligible_employees(d)) if not cell["holiday"] and not cell["future"] else 0
                    cell["unmarked"] = max(n - len(rs), 0)
            row.append(cell)
        weeks.append(row)
    pm = (first.replace(day=1) - __import__("datetime").timedelta(days=1))
    nm = last + __import__("datetime").timedelta(days=1)
    return render(request, "attendance/calendar.html", {
        "weeks": weeks, "year": year, "month": month, "month_name": calendar.month_name[month], "emp": emp,
        "employees": Employee.objects.order_by("name"), "prev": pm, "next": nm,
        "crumbs": [("Attendance", "attendance:daily"), ("Monthly calendar", None)],
    })


@perm_required("attendance.view_attendance")
def history(request):
    qs = Attendance.objects.select_related("employee", "marked_by")
    f = {k: request.GET.get(k, "") for k in ("from", "to", "employee", "status")}
    if f["from"]:
        qs = qs.filter(attendance_date__gte=parse_date(f["from"]))
    if f["to"]:
        qs = qs.filter(attendance_date__lte=parse_date(f["to"]))
    if f["employee"].isdigit():
        qs = qs.filter(employee_id=f["employee"])
    if f["status"]:
        qs = qs.filter(status=f["status"])
    page = Paginator(qs, 30).get_page(request.GET.get("page"))
    return render(request, "attendance/history.html", {
        "page": page, "f": f, "employees": Employee.objects.order_by("name"),
        "statuses": Attendance.STATUS_CHOICES,
        "crumbs": [("Attendance", "attendance:daily"), ("Records", None)],
    })
