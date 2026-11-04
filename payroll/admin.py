from django.contrib import admin
from .models import MonthlyPayroll, SalaryPayment, SalaryRevision

class ReadOnly(admin.ModelAdmin):
    """Financial records are changed only through the app (services enforce the rules)."""
    def has_add_permission(self, r): return False
    def has_change_permission(self, r, obj=None): return False
    def has_delete_permission(self, r, obj=None): return False

admin.site.register(MonthlyPayroll, ReadOnly)
admin.site.register(SalaryPayment, ReadOnly)
admin.site.register(SalaryRevision, ReadOnly)
