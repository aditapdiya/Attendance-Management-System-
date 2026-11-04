from django.urls import path
from . import views

app_name = "payroll"
urlpatterns = [
    path("", views.payroll_list, name="list"),
    path("generate/", views.generate, name="generate"),
    path("finalize-all/", views.finalize_all, name="finalize_all"),
    path("<int:pk>/", views.detail, name="detail"),
    path("<int:pk>/recalculate/", views.recalculate, name="recalculate"),
    path("<int:pk>/finalize/", views.finalize, name="finalize"),
    path("<int:pk>/unlock/", views.unlock, name="unlock"),
    path("<int:pk>/slip/", views.slip, name="slip"),
    path("<int:pk>/slip.pdf", views.slip_pdf, name="slip_pdf"),
    path("<int:pk>/pay/", views.pay, name="pay"),
    path("increments/", views.increments, name="increments"),
    path("salary-history/", views.salary_history, name="salary_history"),
    path("payments/", views.payments, name="payments"),
]
