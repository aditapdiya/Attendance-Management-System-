from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("django-admin/", admin.site.urls),
    path("", include("dashboard.urls")),
    path("", include("accounts.urls")),
    path("employees/", include("employees.urls")),
    path("attendance/", include("attendance.urls")),
    path("payroll/", include("payroll.urls")),
    path("reports/", include("reports.urls")),
]
handler404 = "accounts.views.error_404"
handler500 = "accounts.views.error_500"
