import calendar
from datetime import date
from decimal import Decimal

from django.db.models import Count, F, Sum

from attendance.models import Attendance
from attendance.services import day_counts
from employees.models import Employee
from payroll.models import MonthlyPayroll, SalaryPayment, SalaryRevision


def _months_back(n, today):
    out, y, m = [], today.year, today.month
    for _ in range(n):
        out.append((y, m))
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return list(reversed(out))


def build(today, selected):
    total = Employee.objects.count()
    active = Employee.objects.filter(is_active=True).count()
    t = day_counts(today)
    cur = MonthlyPayroll.objects.filter(year=today.year, month=today.month)
    cur_sum = cur.aggregate(s=Sum("net_salary"), n=Count("id"))
    fin = MonthlyPayroll.objects.filter(status=MonthlyPayroll.FINALIZED).annotate(paid=Sum("payments__amount_paid"))
    pending_amt, pending_n = Decimal("0.00"), 0
    for p in fin:
        b = p.balance
        if b > 0:
            pending_amt += b
            pending_n += 1
    months = _months_back(6, today)
    labels = [f"{calendar.month_abbr[m]} {y}" for y, m in months]
    trend_p, trend_a, spend_net, spend_paid = [], [], [], []
    for y, m in months:
        c = {r["status"]: r["n"] for r in Attendance.objects.filter(attendance_date__year=y, attendance_date__month=m)
             .values("status").annotate(n=Count("id"))}
        trend_p.append(c.get("PRESENT", 0))
        trend_a.append(c.get("ABSENT", 0))
        spend_net.append(float(MonthlyPayroll.objects.filter(year=y, month=m).aggregate(s=Sum("net_salary"))["s"] or 0))
        spend_paid.append(float(SalaryPayment.objects.filter(payroll__year=y, payroll__month=m).aggregate(s=Sum("amount_paid"))["s"] or 0))
    mc = {r["status"]: r["n"] for r in Attendance.objects.filter(attendance_date__year=today.year, attendance_date__month=today.month)
          .values("status").annotate(n=Count("id"))}
    return {
        "stats": {"total": total, "active": active, "inactive": total - active, "present": t["PRESENT"], "absent": t["ABSENT"],
                  "leave": t["PAID_LEAVE"] + t["UNPAID_LEAVE"], "half": t["HALF_DAY"], "holiday": t["holiday"],
                  "payroll_total": cur_sum["s"] or Decimal("0.00"), "payroll_n": cur_sum["n"],
                  "pending_amt": pending_amt, "pending_n": pending_n},
        "sel": day_counts(selected),
        "charts": {"labels": labels, "present": trend_p, "absent": trend_a, "net": spend_net, "paid": spend_paid,
                   "mix": [mc.get("PRESENT", 0), mc.get("ABSENT", 0), mc.get("HALF_DAY", 0), mc.get("PAID_LEAVE", 0), mc.get("UNPAID_LEAVE", 0)]},
        "recent_employees": Employee.objects.order_by("-created_at")[:5],
        "recent_attendance": Attendance.objects.select_related("employee").order_by("-updated_at")[:6],
        "recent_revisions": SalaryRevision.objects.filter(is_initial=False).select_related("employee").order_by("-created_at")[:5],
        "recent_payments": SalaryPayment.objects.select_related("payroll__employee").order_by("-created_at")[:5],
    }
