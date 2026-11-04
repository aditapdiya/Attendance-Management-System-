from django.urls import path
from . import views

app_name = "accounts"
urlpatterns = [
    path("login/", views.AdminLoginView.as_view(), name="login"),
    path("logout/", views.AdminLogoutView.as_view(), name="logout"),
    path("password-change/", views.password_change, name="password_change"),
    path("settings/", views.settings_page, name="settings"),
    path("audit-log/", views.audit_log, name="audit_log"),
]
