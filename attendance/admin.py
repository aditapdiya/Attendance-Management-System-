from django.contrib import admin
from .models import Attendance, WeeklyHoliday

admin.site.register(WeeklyHoliday)

@admin.register(Attendance)
class AttendanceAdmin(admin.ModelAdmin):
    list_display = ("attendance_date", "employee", "status")
    list_filter = ("status",)
    def has_add_permission(self, r): return False
    def has_change_permission(self, r, obj=None): return False
    def has_delete_permission(self, r, obj=None): return False
