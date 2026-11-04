from django.urls import path
from . import views

app_name = "employees"
urlpatterns = [
    path("", views.employee_list, name="list"),
    path("add/", views.employee_add, name="add"),
    path("<int:pk>/", views.employee_profile, name="profile"),
    path("<int:pk>/edit/", views.employee_edit, name="edit"),
    path("<int:pk>/toggle/", views.employee_toggle, name="toggle"),
    path("<int:pk>/delete/", views.employee_delete, name="delete"),
]
