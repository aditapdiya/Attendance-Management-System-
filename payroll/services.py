"""Payroll business rules. All money uses Decimal; no floats anywhere."""
import calendar
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Count, Sum
from django.utils import timezone

from accounts.audit import audit
from attendance.models import Attendance
from attendance.services import holiday_weekdays
from employees.models import Employee
from .models import MonthlyPayroll, SalaryPayment, SalaryRevision

CENT = Decimal("0.01")
HALF = Decimal("0.5")


def money(x) -> Decimal:
    return Decimal(x).quantize(CENT, rounding=ROUND_HALF_UP)


class PayrollError(ValidationError):
    pass


# ---------------------------------------------------------------- salary history
def first_of_month(d: date) -> date:
    return d.replace(day=1)


def salary_for_month(employee, year, month):
    """Salary in force on the 1st of the month (revisions always start on the 1st)."""
    rev = (SalaryRevision.objects.filter(employee=employee, effective_from__lte=date(year, month, 1))
           .order_by("-effective_from").first())
    return rev.revised_salary if rev else None


def sync_employee_salary(employee):
    rev = (SalaryRevision.objects.filter(employee=employee, effective_from__lte=date.today())
           .order_by("-effective_from").first())
    if rev and employee.salary != rev.revised_salary:
        Employee.objects.filter(pk=employee.pk).update(salary=rev.revised_salary)
        employee.salary = rev.revised_salary


def create_initial_revision(employee, user):
    return SalaryRevision.objects.create(
        employee=employee, previous_salary=None, revised_salary=employee.salary,
        effective_from=first_of_month(employee.joining_date), reason="Initial salary",
        is_initial=True, created_by=user)


def shift_initial_revision(employee):
    new_start = first_of_month(employee.joining_date)
    rev = SalaryRevision.objects.filter(employee=employee, is_initial=True).first()
    if rev and rev.effective_from != new_start and \
            not SalaryRevision.objects.filter(employee=employee, effective_from=new_start).exists():
        rev.effective_from = new_start
        rev.save(update_fields=["effective_from"])


def add_revision(employee, revised_salary, effective_from: date, reason, user):
    revised_salary = money(revised_salary)
    if revised_salary <= 0:
        raise ValidationError("Revised salary must be greater than zero.")
    if effective_from.day != 1:
        raise ValidationError("A salary revision must take effect from the 1st of a month.")
    if effective_from < first_of_month(employee.joining_date):
        raise ValidationError("The revision cannot start before the employee's joining month.")
    with transaction.atomic():
        Employee.objects.select_for_update().get(pk=employee.pk)
        latest = SalaryRevision.objects.filter(employee=employee).order_by("-effective_from").first()
        if latest:
            if effective_from <= latest.effective_from:
                raise ValidationError(
                    f"A revision already exists from {latest.effective_from:%B %Y}. "
                    f"New revisions must take effect after it (no overlapping or back-dated changes).")
            if latest.revised_salary == revised_salary:
                raise ValidationError("The revised salary equals the current salary.")
        if MonthlyPayroll.objects.filter(employee=employee, status=MonthlyPayroll.FINALIZED).filter(
                year__gt=effective_from.year).exists() or MonthlyPayroll.objects.filter(
                employee=employee, status=MonthlyPayroll.FINALIZED, year=effective_from.year,
                month__gte=effective_from.month).exists():
            raise ValidationError("Finalized payroll exists from that month onward; it cannot be changed.")
        rev = SalaryRevision.objects.create(
            employee=employee, previous_salary=latest.revised_salary if latest else None,
            revised_salary=revised_salary, effective_from=effective_from, reason=reason.strip()[:255], created_by=user)
        MonthlyPayroll.objects.filter(employee=employee, status=MonthlyPayroll.DRAFT).filter(
            year__gt=effective_from.year).update(needs_review=True)
        MonthlyPayroll.objects.filter(employee=employee, status=MonthlyPayroll.DRAFT, year=effective_from.year,
                                      month__gte=effective_from.month).update(needs_review=True)
        sync_employee_salary(employee)
        audit(user, "SALARY_REVISION", f"{employee.employee_code}: ₹{rev.previous_salary} → ₹{rev.revised_salary} "
              f"effective {effective_from:%b %Y}. Reason: {rev.reason or '-'}")
    return rev


# ---------------------------------------------------------------- calculation
@dataclass
class PayrollCalc:
    employee: Employee
    year: int
    month: int
    total_calendar_days: int
    salary_snapshot: Decimal
    present_days: Decimal
    absent_days: Decimal
    half_days: Decimal
    paid_leave_days: Decimal
    unpaid_leave_days: Decimal
    holiday_days: Decimal
    pre_joining_days: Decimal
    missing_days: int
    missing_dates: list = field(default_factory=list)
    payable_days: Decimal = Decimal("0")
    gross_salary: Decimal = Decimal("0")
    deductions: Decimal = Decimal("0")
    net_salary: Decimal = Decimal("0")

    @property
    def per_day_salary(self):
        return (self.salary_snapshot / self.total_calendar_days).quantize(CENT, rounding=ROUND_HALF_UP)

    @property
    def unpaid_equivalent_days(self):
        return self.total_calendar_days - self.payable_days

    def values(self):
        keys = ["total_calendar_days", "salary_snapshot", "present_days", "absent_days", "half_days",
                "paid_leave_days", "unpaid_leave_days", "holiday_days", "pre_joining_days", "missing_days",
                "payable_days", "gross_salary", "deductions", "net_salary"]
        return {k: getattr(self, k) for k in keys}


def calculate(employee, year, month, today=None) -> PayrollCalc:
    """
    Per Day Salary          = Monthly Salary / actual calendar days
    Unpaid Equivalent Days  = Absent + Half-days × 0.5 + Unpaid leave + days before joining
    Deduction               = Monthly Salary × Unpaid Equivalent Days / Calendar Days  (rounded once, HALF_UP)
    Net                     = Monthly Salary − Deduction
    Weekly holidays and paid leave are never deducted.
    """
    today = today or date.today()
    first = date(year, month, 1)
    days = calendar.monthrange(year, month)[1]
    last = date(year, month, days)
    if employee.joining_date > last:
        raise PayrollError(f"{employee.name} joined after {calendar.month_name[month]} {year}.")
    salary = salary_for_month(employee, year, month)
    if salary is None:
        raise PayrollError(f"No salary revision is effective for {calendar.month_name[month]} {year}.")

    start = max(first, employee.joining_date)
    pre_days = (start - first).days
    hw = holiday_weekdays()

    counts = {s: 0 for s, _ in Attendance.STATUS_CHOICES}
    marked = set()
    for d, st in Attendance.objects.filter(employee=employee, attendance_date__range=(start, last)).values_list(
            "attendance_date", "status"):
        if d.weekday() in hw:
            continue          # paid holiday: never counted, even if a record exists
        counts[st] += 1
        marked.add(d)

    holidays, missing = 0, []
    d = start
    while d <= last:
        if d.weekday() in hw:
            holidays += 1
        elif d not in marked and d <= today:
            missing.append(d)
        d += timedelta(days=1)

    absent, half = Decimal(counts["ABSENT"]), Decimal(counts["HALF_DAY"])
    unpaid_leave = Decimal(counts["UNPAID_LEAVE"])
    unpaid_equiv = absent + half * HALF + unpaid_leave + Decimal(pre_days)
    deduction = money(salary * unpaid_equiv / Decimal(days))
    deduction = min(deduction, salary)
    return PayrollCalc(
        employee=employee, year=year, month=month, total_calendar_days=days, salary_snapshot=money(salary),
        present_days=Decimal(counts["PRESENT"]), absent_days=absent, half_days=half,
        paid_leave_days=Decimal(counts["PAID_LEAVE"]), unpaid_leave_days=unpaid_leave,
        holiday_days=Decimal(holidays), pre_joining_days=Decimal(pre_days),
        missing_days=len(missing), missing_dates=missing,
        payable_days=Decimal(days) - unpaid_equiv, gross_salary=money(salary),
        deductions=deduction, net_salary=money(salary) - deduction)


def month_ended(year, month, today=None) -> bool:
    today = today or date.today()
    return today >= date(year, month, calendar.monthrange(year, month)[1])


# ---------------------------------------------------------------- lifecycle
def _apply(payroll, calc):
    for k, v in calc.values().items():
        setattr(payroll, k, v)


def generate_payroll(employee, year, month, user):
    """Create a DRAFT payroll. Returns (payroll, created). Never touches an existing record."""
    calc = calculate(employee, year, month)
    with transaction.atomic():
        existing = MonthlyPayroll.objects.filter(employee=employee, year=year, month=month).first()
        if existing:
            return existing, False
        try:
            with transaction.atomic():
                p = MonthlyPayroll(employee=employee, year=year, month=month, generated_by=user)
                _apply(p, calc)
                p.save()
        except IntegrityError:
            return MonthlyPayroll.objects.get(employee=employee, year=year, month=month), False
        audit(user, "PAYROLL_GENERATE", f"Draft payroll {employee.employee_code} {year}-{month:02d}: net ₹{p.net_salary}")
        return p, True


def generate_bulk(year, month, user, employee=None):
    emps = [employee] if employee else list(
        Employee.objects.filter(is_active=True, joining_date__lte=date(year, month, calendar.monthrange(year, month)[1])))
    res = {"created": 0, "existing": 0, "errors": []}
    for e in emps:
        try:
            _, created = generate_payroll(e, year, month, user)
            res["created" if created else "existing"] += 1
        except ValidationError as ex:
            res["errors"].append(f"{e.name}: {' '.join(ex.messages)}")
    return res


def recalculate(payroll_id, user):
    with transaction.atomic():
        p = MonthlyPayroll.objects.select_for_update().select_related("employee").get(pk=payroll_id)
        if p.is_final:
            raise ValidationError("Finalized payroll cannot be recalculated. Unlock it first.")
        _apply(p, calculate(p.employee, p.year, p.month))
        p.needs_review = False
        p.save()
        audit(user, "PAYROLL_RECALC", f"Recalculated {p.employee.employee_code} {p.year}-{p.month:02d}: net ₹{p.net_salary}")
    return p


def finalize(payroll_id, user):
    with transaction.atomic():
        p = MonthlyPayroll.objects.select_for_update().select_related("employee").get(pk=payroll_id)
        if p.is_final:
            raise ValidationError("Payroll is already finalized.")
        if not month_ended(p.year, p.month):
            raise ValidationError(f"{p.period} has not ended yet; payroll can be finalized once the month is complete.")
        if p.needs_review:
            raise ValidationError("Attendance or salary changed after this payroll was calculated. Review and recalculate it first.")
        fresh = calculate(p.employee, p.year, p.month)
        if fresh.missing_days:
            raise ValidationError(f"{fresh.missing_days} working day(s) have no attendance "
                                  f"(first: {fresh.missing_dates[0]:%d %b}). Mark attendance before finalizing.")
        if fresh.values() != {k: getattr(p, k) for k in fresh.values()}:
            raise ValidationError("Stored figures differ from current attendance. Recalculate the payroll first.")
        p.status, p.finalized_by, p.finalized_at = MonthlyPayroll.FINALIZED, user, timezone.now()
        p.save()
        audit(user, "PAYROLL_FINALIZE", f"Finalized {p.employee.employee_code} {p.year}-{p.month:02d}: net ₹{p.net_salary}")
    return p


def finalize_bulk(year, month, user):
    res = {"done": 0, "errors": []}
    for p in MonthlyPayroll.objects.filter(year=year, month=month, status=MonthlyPayroll.DRAFT).select_related("employee"):
        try:
            finalize(p.pk, user)
            res["done"] += 1
        except ValidationError as ex:
            res["errors"].append(f"{p.employee.name}: {' '.join(ex.messages)}")
    return res


def unlock(payroll_id, user, reason):
    reason = (reason or "").strip()
    if len(reason) < 5:
        raise ValidationError("Please give a reason (at least 5 characters) for unlocking.")
    with transaction.atomic():
        p = MonthlyPayroll.objects.select_for_update().select_related("employee").get(pk=payroll_id)
        if not p.is_final:
            raise ValidationError("Only finalized payroll can be unlocked.")
        if p.payments.exists():
            raise ValidationError("Salary payments are already recorded against this payroll; it cannot be unlocked.")
        p.status, p.needs_review, p.finalized_by, p.finalized_at = MonthlyPayroll.DRAFT, True, None, None
        p.save()
        audit(user, "PAYROLL_UNLOCK", f"Unlocked {p.employee.employee_code} {p.year}-{p.month:02d} "
              f"(net was ₹{p.net_salary}). Reason: {reason}")
    return p


def record_payment(payroll_id, amount, pay_date, mode, reference, remarks, user, allow_overpay=False):
    amount = money(amount)
    if amount <= 0:
        raise ValidationError("Payment amount must be greater than zero.")
    if pay_date > date.today():
        raise ValidationError("Payment date cannot be in the future.")
    with transaction.atomic():
        p = MonthlyPayroll.objects.select_for_update().select_related("employee").get(pk=payroll_id)
        if not p.is_final:
            raise ValidationError("Payments can only be recorded against finalized payroll.")
        paid = p.payments.aggregate(s=Sum("amount_paid"))["s"] or Decimal("0")
        balance = p.net_salary - paid
        over = amount > balance
        if over and not allow_overpay:
            raise ValidationError(f"Amount exceeds the outstanding balance of ₹{balance}. "
                                  f"Only an authorized adjustment can exceed it.")
        if over and not (remarks or "").strip():
            raise ValidationError("Remarks are required when recording an adjustment above the balance.")
        pay = SalaryPayment.objects.create(
            payroll=p, amount_paid=amount, payment_date=pay_date, payment_mode=mode,
            transaction_reference=(reference or "").strip()[:80], remarks=(remarks or "").strip()[:255],
            is_adjustment=over, recorded_by=user)
        audit(user, "PAYMENT" + ("_ADJUSTMENT" if over else ""),
              f"₹{amount} {mode} for {p.employee.employee_code} {p.year}-{p.month:02d}")
    return pay
