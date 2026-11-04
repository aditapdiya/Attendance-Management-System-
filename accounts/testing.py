"""Shared helpers for tests."""
import calendar
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.models import Permission, User

from attendance.models import Attendance, WeeklyHoliday
from employees import crypto
from employees.models import Employee
from payroll import services as pay


def make_admin(username="admin", superuser=True, perms=()):
    u = User.objects.create_user(username, password="pass-12345-xyz", is_staff=True, is_superuser=superuser)
    for code in perms:
        app, codename = code.split(".")
        u.user_permissions.add(Permission.objects.get(content_type__app_label=app, codename=codename))
    return u


def make_employee(name="Asha Patil", salary="15000", joined=date(2025, 1, 1), user=None, **kw):
    e = Employee(name=name, phone_number=kw.pop("phone", "9876543210"), designation=kw.pop("designation", "Salesman"),
                 joining_date=joined, salary=Decimal(salary), **kw)
    e.save()
    pay.create_initial_revision(e, user)
    return e


def mark_month(emp, year, month, default="PRESENT", overrides=None, user=None):
    """Mark every non-Sunday day of the month. overrides = {day_number: status}."""
    overrides = overrides or {}
    n = calendar.monthrange(year, month)[1]
    for d in range(1, n + 1):
        day = date(year, month, d)
        if day.weekday() == 6 or day < emp.joining_date:
            continue
        Attendance.objects.update_or_create(employee=emp, attendance_date=day,
                                            defaults={"status": overrides.get(d, default), "marked_by": user})


def aadhaar_with_checksum(prefix11="23456789012"):
    inv = [0, 4, 3, 2, 1, 5, 6, 7, 8, 9]
    c = 0
    for i, ch in enumerate(reversed(prefix11)):
        c = crypto._D[c][crypto._P[(i + 1) % 8][int(ch)]]
    return prefix11 + str(inv[c])
