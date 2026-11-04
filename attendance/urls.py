from django.urls import path
from . import views

app_name = "attendance"
urlpatterns = [
    path("", views.daily, name="daily"),
    path("api/save/", views.api_save, name="api_save"),
    path("mark-all-present/", views.mark_all_present, name="mark_all_present"),
    path("calendar/", views.calendar_view, name="calendar"),
    path("records/", views.history, name="history"),
]
