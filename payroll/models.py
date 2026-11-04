from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q, Sum

MONEY = dict(max_digits=12, decimal_places=2)
DAYS = dict(max_digits=5, decimal_places=1)


class SalaryRevision(models.Model):
    employee = models.ForeignKey("employees.Employee", on_delete=models.CASCADE, related_name="revisions")
    previous_salary = models.DecimalField(null=True, blank=True, **MONEY)
    revised_salary = models.DecimalField(validators=[MinValueValidator(Decimal("0.01"))], **MONEY)
    effective_from = models.DateField(help_text="Always the 1st of a month.")
    reason = models.CharField(max_length=255, blank=True)
    is_initial = models.BooleanField(default=False)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["employee_id", "effective_from"]
        constraints = [
            models.UniqueConstraint(fields=["employee", "effective_from"], name="uniq_revision_per_month"),
            models.CheckConstraint(condition=Q(revised_salary__gt=0), name="revision_salary_positive"),
        ]
        indexes = [models.Index(fields=["employee", "effective_from"])]

    def __str__(self):
        return f"{self.employee.employee_code} {self.effective_from:%b %Y} → {self.revised_salary}"

    @property
    def increment_amount(self):
        return None if self.previous_salary is None else self.revised_salary - self.previous_salary

    @property
    def increment_percent(self):
        if not self.previous_salary:
            return None
        return ((self.revised_salary - self.previous_salary) * 100 / self.previous_salary).quantize(Decimal("0.01"))


class MonthlyPayroll(models.Model):
    DRAFT, FINALIZED = "DRAFT", "FINALIZED"
    STATUS_CHOICES = [(DRAFT, "Draft"), (FINALIZED, "Finalized")]

    employee = models.ForeignKey("employees.Employee", on_delete=models.PROTECT, related_name="payrolls")
    month = models.PositiveSmallIntegerField()
    year = models.PositiveSmallIntegerField()
    total_calendar_days = models.PositiveSmallIntegerField()
    salary_snapshot = models.DecimalField(**MONEY)
    present_days = models.DecimalField(**DAYS)
    absent_days = models.DecimalField(**DAYS)
    half_days = models.DecimalField(**DAYS)
    paid_leave_days = models.DecimalField(**DAYS)
    unpaid_leave_days = models.DecimalField(**DAYS)
    holiday_days = models.DecimalField(default=0, **DAYS)
    pre_joining_days = models.DecimalField(default=0, **DAYS)
    missing_days = models.PositiveSmallIntegerField(default=0)
    payable_days = models.DecimalField(**DAYS)
    gross_salary = models.DecimalField(**MONEY)
    deductions = models.DecimalField(**MONEY)
    net_salary = models.DecimalField(**MONEY)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=DRAFT, db_index=True)
    needs_review = models.BooleanField(default=False, help_text="Attendance/salary changed after calculation.")
    generated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    generated_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    finalized_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    finalized_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-year", "-month", "employee__name"]
        constraints = [
            models.UniqueConstraint(fields=["employee", "year", "month"], name="uniq_payroll_employee_month"),
            models.CheckConstraint(condition=Q(month__gte=1, month__lte=12), name="payroll_month_1_12"),
            models.CheckConstraint(condition=Q(net_salary__gte=0), name="payroll_net_non_negative"),
        ]
        indexes = [models.Index(fields=["year", "month", "status"])]
        permissions = [("finalize_payroll", "Can finalize payroll"), ("unlock_payroll", "Can unlock finalized payroll")]

    def __str__(self):
        return f"{self.employee.employee_code} {self.year}-{self.month:02d}"

    @property
    def period(self):
        import calendar
        return f"{calendar.month_name[self.month]} {self.year}"

    @property
    def is_final(self):
        return self.status == self.FINALIZED

    @property
    def total_paid(self):
        if hasattr(self, "paid") and self.paid is not None:
            return self.paid
        return self.payments.aggregate(s=Sum("amount_paid"))["s"] or Decimal("0.00")

    @property
    def balance(self):
        return self.net_salary - self.total_paid

    @property
    def payment_status(self):
        paid = self.total_paid
        if paid <= 0 and self.net_salary > 0:
            return "Pending"
        return "Paid" if paid >= self.net_salary else "Partially Paid"

    @property
    def per_day_salary(self):
        return (self.salary_snapshot / self.total_calendar_days).quantize(Decimal("0.01"))

    @property
    def unpaid_equivalent_days(self):
        return self.total_calendar_days - self.payable_days


class SalaryPayment(models.Model):
    CASH, BANK, UPI = "CASH", "BANK", "UPI"
    MODE_CHOICES = [(CASH, "Cash"), (BANK, "Bank transfer"), (UPI, "UPI")]

    payroll = models.ForeignKey(MonthlyPayroll, on_delete=models.PROTECT, related_name="payments")
    amount_paid = models.DecimalField(validators=[MinValueValidator(Decimal("0.01"))], **MONEY)
    payment_date = models.DateField()
    payment_mode = models.CharField(max_length=6, choices=MODE_CHOICES)
    transaction_reference = models.CharField(max_length=80, blank=True)
    remarks = models.CharField(max_length=255, blank=True)
    is_adjustment = models.BooleanField(default=False, help_text="Authorized overpayment/adjustment.")
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-payment_date", "-id"]
        constraints = [models.CheckConstraint(condition=Q(amount_paid__gt=0), name="payment_positive")]
        permissions = [("authorize_overpayment", "Can record a payment above the outstanding balance")]

    def __str__(self):
        return f"{self.payroll} ₹{self.amount_paid}"
