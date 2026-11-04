import uuid
from decimal import Decimal

from django.core.validators import MinValueValidator, RegexValidator
from django.db import models, transaction

from . import crypto

phone_validator = RegexValidator(r"^[6-9]\d{9}$", "Enter a valid 10-digit Indian mobile number.")


class Employee(models.Model):
    employee_code = models.CharField(max_length=20, unique=True, editable=False)
    name = models.CharField(max_length=120)
    aadhaar_encrypted = models.TextField(blank=True, editable=False)
    aadhaar_last4 = models.CharField(max_length=4, blank=True, editable=False)
    aadhaar_hash = models.CharField(max_length=64, unique=True, null=True, blank=True, editable=False)
    phone_number = models.CharField(max_length=10, validators=[phone_validator], db_index=True)
    address = models.TextField(blank=True)
    designation = models.CharField(max_length=80)
    department = models.CharField(max_length=80, blank=True)
    joining_date = models.DateField()
    salary = models.DecimalField("Current monthly salary", max_digits=10, decimal_places=2,
                                 validators=[MinValueValidator(Decimal("0.01"))],
                                 help_text="Kept in sync with the latest salary revision.")
    is_active = models.BooleanField(default=True, db_index=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name", "id"]
        constraints = [models.CheckConstraint(condition=models.Q(salary__gt=0), name="employee_salary_positive")]
        indexes = [models.Index(fields=["is_active", "name"])]

    def __str__(self):
        return f"{self.name} ({self.employee_code})"

    # --- code generation -------------------------------------------------
    def save(self, *args, **kwargs):
        if self.employee_code:
            return super().save(*args, **kwargs)
        self.employee_code = "TMP" + uuid.uuid4().hex[:15]
        with transaction.atomic():
            super().save(*args, **kwargs)
            self.employee_code = f"VA{self.pk:05d}"
            Employee.objects.filter(pk=self.pk).update(employee_code=self.employee_code)

    # --- Aadhaar -----------------------------------------------------------
    def set_aadhaar(self, number: str):
        n = crypto.validate_aadhaar(number)
        self.aadhaar_encrypted = crypto.encrypt(n)
        self.aadhaar_last4 = n[-4:]
        self.aadhaar_hash = crypto.blind_index(n)

    @property
    def masked_aadhaar(self):
        return f"XXXX XXXX {self.aadhaar_last4}" if self.aadhaar_last4 else "—"

    def reveal_aadhaar(self):
        return crypto.decrypt(self.aadhaar_encrypted) if self.aadhaar_encrypted else ""

    def has_history(self):
        return self.attendance_records.exists() or self.payrolls.exists()

    def delete(self, *args, **kwargs):
        if self.has_history():
            raise models.ProtectedError("Employee has attendance/payroll history and cannot be deleted.", {self})
        return super().delete(*args, **kwargs)
