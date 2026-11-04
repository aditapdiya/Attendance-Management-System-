from django.conf import settings
from django.db import models


class WeeklyHoliday(models.Model):
    """Weekdays (0=Monday … 6=Sunday) that are paid weekly holidays. Seeded with Sunday."""
    weekday = models.PositiveSmallIntegerField(unique=True)

    class Meta:
        ordering = ["weekday"]
        constraints = [models.CheckConstraint(condition=models.Q(weekday__gte=0, weekday__lte=6), name="weekday_0_6")]

    def __str__(self):
        return ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"][self.weekday]


class Attendance(models.Model):
    PRESENT, ABSENT, HALF_DAY, PAID_LEAVE, UNPAID_LEAVE = "PRESENT", "ABSENT", "HALF_DAY", "PAID_LEAVE", "UNPAID_LEAVE"
    STATUS_CHOICES = [(PRESENT, "Present"), (ABSENT, "Absent"), (HALF_DAY, "Half day"),
                      (PAID_LEAVE, "Paid leave"), (UNPAID_LEAVE, "Unpaid leave")]

    employee = models.ForeignKey("employees.Employee", on_delete=models.PROTECT, related_name="attendance_records")
    attendance_date = models.DateField(db_index=True)
    status = models.CharField(max_length=12, choices=STATUS_CHOICES)
    remarks = models.CharField(max_length=255, blank=True)
    marked_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-attendance_date", "employee__name"]
        constraints = [models.UniqueConstraint(fields=["employee", "attendance_date"], name="uniq_attendance_per_day")]
        indexes = [models.Index(fields=["attendance_date", "status"])]

    def __str__(self):
        return f"{self.employee.employee_code} {self.attendance_date} {self.status}"
