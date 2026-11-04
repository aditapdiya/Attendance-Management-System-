"""Attendance business logic (no HTTP here)."""
import calendar
from datetime import date

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from accounts.audit import audit
from employees.models import Employee
from .models import Attendance, WeeklyHoliday

STATUSES = {c for c, _ in Attendance.STATUS_CHOICES}


def holiday_weekdays():
    return set(WeeklyHoliday.objects.values_list("weekday", flat=True))


def is_holiday(d: date, weekdays=None) -> bool:
    return d.weekday() in (holiday_weekdays() if weekdays is None else weekdays)


def eligible_employees(d: date):
    """Active employees who had already joined on date d."""
    return Employee.objects.filter(is_active=True, joining_date__lte=d).order_by("name", "id")


def _payroll_model():
    from payroll.models import MonthlyPayroll  # lazy: avoids circular import
    return MonthlyPayroll


def is_locked(employee_id: int, d: date) -> bool:
    M = _payroll_model()
    return M.objects.filter(employee_id=employee_id, year=d.year, month=d.month, status=M.FINALIZED).exists()


def _flag_payroll_review(employee_ids, d: date):
    M = _payroll_model()
    M.objects.filter(employee_id__in=employee_ids, year=d.year, month=d.month, status=M.DRAFT).update(needs_review=True)


def validate_day(d: date):
    if d > date.today():
        raise ValidationError("Attendance cannot be recorded for a future date.")
    if is_holiday(d):
        raise ValidationError(f"{d:%A} is a paid weekly holiday; no attendance entry is needed.")


def set_attendance(employee, d, status, remarks, user, can_change=True):
    """Create or update one record. Returns (record, 'created'|'updated'|'unchanged')."""
    if status not in STATUSES:
        raise ValidationError("Invalid attendance status.")
    validate_day(d)
    if d < employee.joining_date:
        raise ValidationError(f"{employee.name} joined on {employee.joining_date:%d %b %Y}.")
    if is_locked(employee.id, d):
        raise ValidationError(f"Payroll for {d:%B %Y} is finalized for {employee.name}. Unlock it first.")
    remarks = (remarks or "").strip()[:255]
    with transaction.atomic():
        rec = Attendance.objects.select_for_update().filter(employee=employee, attendance_date=d).first()
        if rec is None:
            if not employee.is_active:
                raise ValidationError(f"{employee.name} is inactive.")
            try:
                with transaction.atomic():
                    rec = Attendance.objects.create(employee=employee, attendance_date=d, status=status,
                                                    remarks=remarks, marked_by=user)
            except IntegrityError:   # concurrent duplicate – treat as existing
                rec = Attendance.objects.get(employee=employee, attendance_date=d)
            else:
                _flag_payroll_review([employee.id], d)
                audit(user, "ATTENDANCE_MARK", f"{employee.employee_code} {d:%Y-%m-%d} {status}")
                return rec, "created"
        if rec.status == status and rec.remarks == remarks:
            return rec, "unchanged"
        if not can_change:
            raise ValidationError("You do not have permission to edit existing attendance.")
        old = rec.status
        rec.status, rec.remarks, rec.marked_by = status, remarks, user
        rec.save()
        _flag_payroll_review([employee.id], d)
        audit(user, "ATTENDANCE_EDIT", f"{employee.employee_code} {d:%Y-%m-%d} {old} -> {status}")
        return rec, "updated"


def clear_attendance(employee, d, user):
    if is_locked(employee.id, d):
        raise ValidationError("Payroll for this month is finalized. Unlock it first.")
    with transaction.atomic():
        deleted, _ = Attendance.objects.filter(employee=employee, attendance_date=d).delete()
        if deleted:
            _flag_payroll_review([employee.id], d)
            audit(user, "ATTENDANCE_CLEAR", f"{employee.employee_code} {d:%Y-%m-%d} cleared")
    return deleted


def mark_all_present(d, user):
    """Create PRESENT for eligible employees with no record. Never overwrites."""
    validate_day(d)
    M = _payroll_model()
    with transaction.atomic():
        emps = list(eligible_employees(d))
        existing = set(Attendance.objects.filter(attendance_date=d, employee__in=emps).values_list("employee_id", flat=True))
        locked = set(M.objects.filter(year=d.year, month=d.month, status=M.FINALIZED,
                                      employee__in=emps).values_list("employee_id", flat=True))
        todo = [e for e in emps if e.id not in existing and e.id not in locked]
        Attendance.objects.bulk_create([
            Attendance(employee=e, attendance_date=d, status=Attendance.PRESENT, marked_by=user,
                       remarks="Marked via Mark All Present") for e in todo], ignore_conflicts=True)
        _flag_payroll_review([e.id for e in todo], d)
        result = {"created": len(todo), "skipped_existing": len(existing), "skipped_locked": len(locked - existing)}
        audit(user, "ATTENDANCE_MARK_ALL", f"Mark All Present {d:%Y-%m-%d}: {result['created']} created, "
              f"{result['skipped_existing']} already marked, {result['skipped_locked']} locked")
    return result


def day_counts(d: date):
    emps = list(eligible_employees(d).values_list("id", flat=True))
    rows = Attendance.objects.filter(attendance_date=d, employee_id__in=emps)
    c = {s: 0 for s in STATUSES}
    for st in rows.values_list("status", flat=True):
        c[st] += 1
    marked = sum(c.values())
    total = len(emps)
    c.update(total=total, not_marked=total - marked, marked=marked,
             completion=round(marked * 100 / total) if total else 0, holiday=is_holiday(d))
    return c


def month_range(year, month):
    return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])
