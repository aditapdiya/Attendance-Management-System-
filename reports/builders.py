"""Report data builders. Each returns a Report; exporters render it to PDF / Excel / HTML.
Aadhaar numbers are never included in any report."""
import calendar
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from django.db.models import Count, Sum

from attendance.models import Attendance
from attendance.services import eligible_employees, holiday_weekdays, month_range
from employees.models import Employee
from payroll.models import MonthlyPayroll, SalaryRevision

Z = Decimal("0.00")
STATUS_LABEL = dict(Attendance.STATUS_CHOICES)


@dataclass
class Report:
    title: str
    subtitle: str
    headers: list
    rows: list
    totals: list = None          # optional final row (same width as headers)
    summary: list = field(default_factory=list)   # [(label, value)]
    slug: str = "report"


def mname(m):
    return calendar.month_name[m]


def daily_attendance(p):
    d = p["date"]
    hw = holiday_weekdays()
    recs = {a.employee_id: a for a in Attendance.objects.filter(attendance_date=d)}
    rows, counts = [], defaultdict(int)
    for e in eligible_employees(d):
        r = recs.get(e.id)
        label = STATUS_LABEL[r.status] if r else ("Weekly holiday" if d.weekday() in hw else "Not marked")
        counts[label] += 1
        rows.append([e.employee_code, e.name, e.designation, label, r.remarks if r else "", r.updated_at.strftime("%d %b %Y %H:%M") if r else ""])
    return Report("Daily Attendance Report", f"Date: {d:%A, %d %B %Y}",
                  ["Employee ID", "Name", "Designation", "Status", "Remarks", "Last updated"], rows,
                  summary=[(k, v) for k, v in counts.items()] + [("Total employees", len(rows))], slug=f"daily_attendance_{d}")


def monthly_attendance(p):
    y, m = p["year"], p["month"]
    first, last = month_range(y, m)
    hw = holiday_weekdays()
    agg = defaultdict(lambda: defaultdict(int))
    marked = defaultdict(set)
    for emp, st, d in Attendance.objects.filter(attendance_date__range=(first, last)).values_list("employee_id", "status", "attendance_date"):
        agg[emp][st] += 1
        marked[emp].add(d)
    today = date.today()
    rows, tot = [], [0] * 7
    for e in Employee.objects.filter(joining_date__lte=last).order_by("name"):
        a = agg.get(e.id)
        if not a and not e.is_active:
            continue
        start = max(first, e.joining_date)
        missing = holidays = 0
        d = start
        while d <= last:
            if d.weekday() in hw:
                holidays += 1
            elif d not in marked[e.id] and d <= today:
                missing += 1
            d += timedelta(days=1)
        a = a or {}
        vals = [a.get("PRESENT", 0), a.get("ABSENT", 0), a.get("HALF_DAY", 0), a.get("PAID_LEAVE", 0), a.get("UNPAID_LEAVE", 0), missing, holidays]
        tot = [x + y_ for x, y_ in zip(tot, vals)]
        rows.append([e.employee_code, e.name] + vals)
    return Report("Monthly Attendance Report", f"{mname(m)} {y}",
                  ["Employee ID", "Name", "Present", "Absent", "Half day", "Paid leave", "Unpaid leave", "Not marked", "Holidays"],
                  rows, totals=["", "TOTAL"] + tot, slug=f"monthly_attendance_{y}_{m:02d}")


def employee_attendance(p):
    e, y, m = p["employee"], p["year"], p["month"]
    first, last = month_range(y, m)
    hw = holiday_weekdays()
    recs = {a.attendance_date: a for a in Attendance.objects.filter(employee=e, attendance_date__range=(first, last))}
    rows, c = [], defaultdict(int)
    d = first
    while d <= last:
        r = recs.get(d)
        if d < e.joining_date:
            label = "Before joining"
        elif d.weekday() in hw:
            label = "Weekly holiday"
        elif r:
            label = STATUS_LABEL[r.status]
        else:
            label = "Not marked" if d <= date.today() else "-"
        c[label] += 1
        rows.append([d, d.strftime("%A"), label, r.remarks if r else ""])
        d += timedelta(days=1)
    return Report("Employee Attendance Report", f"{e.name} ({e.employee_code}) – {mname(m)} {y}",
                  ["Date", "Day", "Status", "Remarks"], rows, summary=list(c.items()), slug=f"attendance_{e.employee_code}_{y}_{m:02d}")


def attendance_summary(p):
    y, m = p["year"], p["month"]
    first, last = month_range(y, m)
    hw = holiday_weekdays()
    per = defaultdict(lambda: defaultdict(int))
    for d, st in Attendance.objects.filter(attendance_date__range=(first, last)).values_list("attendance_date", "status"):
        per[d][st] += 1
    rows, tot = [], [0] * 6
    d = first
    while d <= last:
        if d.weekday() in hw:
            rows.append([d, d.strftime("%a"), "Weekly holiday", "", "", "", "", "", ""])
        elif d <= date.today():
            c = per.get(d, {})
            total = eligible_employees(d).count()
            vals = [c.get("PRESENT", 0), c.get("ABSENT", 0), c.get("HALF_DAY", 0), c.get("PAID_LEAVE", 0), c.get("UNPAID_LEAVE", 0)]
            nm = max(total - sum(vals), 0)
            vals.append(nm)
            tot = [a + b for a, b in zip(tot, vals)]
            rows.append([d, d.strftime("%a"), ""] + vals)
        d += timedelta(days=1)
    return Report("Present & Absent Summary", f"{mname(m)} {y}",
                  ["Date", "Day", "Note", "Present", "Absent", "Half day", "Paid leave", "Unpaid leave", "Not marked"],
                  rows, totals=["TOTAL", "", ""] + tot, slug=f"attendance_summary_{y}_{m:02d}")


PAYROLL_HEADERS = ["Employee ID", "Name", "Monthly salary", "Cal. days", "Present", "Absent", "Half days", "Paid leave",
                   "Unpaid leave", "Deductions", "Net salary", "Status"]


def _payroll_row(p):
    return [p.employee.employee_code, p.employee.name, p.salary_snapshot, p.total_calendar_days, p.present_days,
            p.absent_days, p.half_days, p.paid_leave_days, p.unpaid_leave_days, p.deductions, p.net_salary, p.get_status_display()]


def monthly_payroll(p):
    y, m = p["year"], p["month"]
    qs = MonthlyPayroll.objects.filter(year=y, month=m).select_related("employee").order_by("employee__name")
    rows = [_payroll_row(x) for x in qs]
    tot = ["", "TOTAL", sum((x.salary_snapshot for x in qs), Z), "", "", "", "", "", "", sum((x.deductions for x in qs), Z), sum((x.net_salary for x in qs), Z), ""]
    return Report("Monthly Payroll Report", f"{mname(m)} {y}", PAYROLL_HEADERS, rows, totals=tot, slug=f"payroll_{y}_{m:02d}")


def employee_salary(p):
    e, y = p["employee"], p["year"]
    qs = MonthlyPayroll.objects.filter(employee=e, year=y).annotate(paid=Sum("payments__amount_paid")).order_by("month")
    rows = [[f"{mname(x.month)} {x.year}", x.salary_snapshot, x.deductions, x.net_salary, x.total_paid,
             x.balance if x.is_final else "", x.payment_status if x.is_final else "Draft"] for x in qs]
    tot = ["TOTAL", sum((x.salary_snapshot for x in qs), Z), sum((x.deductions for x in qs), Z), sum((x.net_salary for x in qs), Z),
           sum((x.total_paid for x in qs), Z), "", ""]
    return Report("Employee Salary Report", f"{e.name} ({e.employee_code}) – {y}",
                  ["Month", "Monthly salary", "Deductions", "Net salary", "Paid", "Balance", "Payment status"], rows, totals=tot,
                  slug=f"salary_{e.employee_code}_{y}")


def increment_history(p):
    qs = SalaryRevision.objects.select_related("employee", "created_by").order_by("employee__name", "effective_from")
    if p.get("employee"):
        qs = qs.filter(employee=p["employee"])
    rows = [[r.employee.employee_code, r.employee.name, r.effective_from, r.previous_salary if r.previous_salary is not None else "-",
             r.revised_salary, r.increment_amount if r.increment_amount is not None else "-",
             f"{r.increment_percent}%" if r.increment_percent is not None else "-", r.reason, r.created_by.username if r.created_by else ""] for r in qs]
    sub = p["employee"].name if p.get("employee") else "All employees"
    return Report("Salary Increment History", sub, ["Employee ID", "Name", "Effective from", "Previous", "Revised", "Increment", "%", "Reason", "By"],
                  rows, slug="increment_history")


def monthly_expenditure(p):
    y = p["year"]
    net = {x["month"]: x for x in MonthlyPayroll.objects.filter(year=y).values("month").annotate(
        n=Count("id"), g=Sum("gross_salary"), d=Sum("deductions"), net=Sum("net_salary"))}
    paid = {x["payroll__month"]: x["s"] for x in SalaryPayment_qs(y)}
    rows, tot = [], [0, Z, Z, Z, Z]
    for m in range(1, 13):
        x = net.get(m)
        if not x:
            continue
        vals = [x["n"], x["g"], x["d"], x["net"], paid.get(m) or Z]
        tot = [a + b for a, b in zip(tot, vals)]
        rows.append([mname(m)] + vals)
    return Report("Monthly Salary Expenditure", f"Year {y}", ["Month", "Employees", "Gross", "Deductions", "Net payroll", "Paid so far"],
                  rows, totals=["TOTAL"] + tot, slug=f"expenditure_{y}")


def SalaryPayment_qs(y):
    from payroll.models import SalaryPayment
    return SalaryPayment.objects.filter(payroll__year=y).values("payroll__month").annotate(s=Sum("amount_paid"))


def _payment_rows(p, want_paid):
    qs = MonthlyPayroll.objects.filter(status=MonthlyPayroll.FINALIZED).select_related("employee").annotate(paid=Sum("payments__amount_paid"))
    if p.get("month"):
        qs = qs.filter(year=p["year"], month=p["month"])
    elif p.get("year"):
        qs = qs.filter(year=p["year"])
    out = [x for x in qs.order_by("year", "month", "employee__name") if (x.payment_status == "Paid") == want_paid]
    return out


def pending_payments(p):
    items = _payment_rows(p, False)
    rows = [[x.employee.employee_code, x.employee.name, x.period, x.net_salary, x.total_paid, x.balance, x.payment_status] for x in items]
    tot = ["", "TOTAL", "", sum((x.net_salary for x in items), Z), sum((x.total_paid for x in items), Z), sum((x.balance for x in items), Z), ""]
    return Report("Pending Salary Payments", _period_label(p), ["Employee ID", "Name", "Period", "Net salary", "Paid", "Balance", "Status"], rows, totals=tot, slug="pending_payments")


def completed_payments(p):
    items = _payment_rows(p, True)
    rows = []
    for x in items:
        last = x.payments.first()
        rows.append([x.employee.employee_code, x.employee.name, x.period, x.net_salary, x.total_paid, last.payment_date if last else "", last.get_payment_mode_display() if last else ""])
    tot = ["", "TOTAL", "", sum((x.net_salary for x in items), Z), sum((x.total_paid for x in items), Z), "", ""]
    return Report("Completed Salary Payments", _period_label(p), ["Employee ID", "Name", "Period", "Net salary", "Paid", "Last payment", "Mode"], rows, totals=tot, slug="completed_payments")


def _period_label(p):
    if p.get("month"):
        return f"{mname(p['month'])} {p['year']}"
    return f"Year {p['year']}" if p.get("year") else "All periods"


# key: (label, builder, needs, permission)
REGISTRY = {
    "daily_attendance": ("Daily attendance report", daily_attendance, ["date"], "attendance.view_attendance"),
    "monthly_attendance": ("Monthly attendance report", monthly_attendance, ["month"], "attendance.view_attendance"),
    "employee_attendance": ("Employee-wise attendance report", employee_attendance, ["employee", "month"], "attendance.view_attendance"),
    "attendance_summary": ("Present & absent summary", attendance_summary, ["month"], "attendance.view_attendance"),
    "monthly_payroll": ("Monthly payroll report", monthly_payroll, ["month"], "payroll.view_monthlypayroll"),
    "employee_salary": ("Employee-wise salary report", employee_salary, ["employee", "year"], "payroll.view_monthlypayroll"),
    "increment_history": ("Salary increment history", increment_history, ["employee_optional"], "payroll.view_salaryrevision"),
    "monthly_expenditure": ("Monthly salary expenditure", monthly_expenditure, ["year"], "payroll.view_monthlypayroll"),
    "pending_payments": ("Pending payments", pending_payments, ["month_optional"], "payroll.view_salarypayment"),
    "completed_payments": ("Completed payments", completed_payments, ["month_optional"], "payroll.view_salarypayment"),
}
