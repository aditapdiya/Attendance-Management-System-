from django.contrib import admin
from .models import Employee

@admin.register(Employee)
class EmployeeAdmin(admin.ModelAdmin):
    list_display = ("employee_code", "name", "designation", "is_active")
    search_fields = ("name", "employee_code", "phone_number")
    exclude = ("aadhaar_encrypted", "aadhaar_hash")   # never show encrypted/blind data
    readonly_fields = ("employee_code", "aadhaar_last4", "salary")
