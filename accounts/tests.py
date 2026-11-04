from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from accounts.models import AuditLog
from accounts.testing import make_admin, make_employee


class AuthTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_every_internal_page_requires_login(self):
        for name in ("dashboard:home", "employees:list", "attendance:daily", "payroll:list", "reports:index",
                     "accounts:audit_log", "payroll:increments", "payroll:payments", "accounts:settings"):
            r = self.client.get(reverse(name))
            self.assertEqual(r.status_code, 302, name)
            self.assertIn("/login/", r["Location"])

    def test_login_logout_and_audit(self):
        make_admin("boss")
        r = self.client.post(reverse("accounts:login"), {"username": "boss", "password": "pass-12345-xyz"})
        self.assertRedirects(r, reverse("dashboard:home"))
        self.assertEqual(self.client.get(reverse("dashboard:home")).status_code, 200)
        self.client.post(reverse("accounts:logout"))
        self.assertEqual(self.client.get(reverse("dashboard:home")).status_code, 302)
        self.assertTrue(AuditLog.objects.filter(action="LOGIN").exists())

    def test_wrong_password_message(self):
        make_admin("boss")
        r = self.client.post(reverse("accounts:login"), {"username": "boss", "password": "nope"})
        self.assertContains(r, "Incorrect username or password")

    def test_non_staff_cannot_log_in(self):
        User.objects.create_user("plain", password="pass-12345-xyz")
        r = self.client.post(reverse("accounts:login"), {"username": "plain", "password": "pass-12345-xyz"})
        self.assertContains(r, "not authorised")

    def test_lockout_after_repeated_failures(self):
        make_admin("boss")
        for _ in range(5):
            self.client.post(reverse("accounts:login"), {"username": "boss", "password": "bad"})
        r = self.client.post(reverse("accounts:login"), {"username": "boss", "password": "pass-12345-xyz"})
        self.assertContains(r, "Too many failed attempts")

    def test_logout_requires_post(self):
        make_admin("boss")
        self.client.login(username="boss", password="pass-12345-xyz")
        self.assertEqual(self.client.get(reverse("accounts:logout")).status_code, 405)

    def test_no_public_registration_route(self):
        for path in ("/register/", "/signup/", "/accounts/register/"):
            self.assertEqual(self.client.get(path).status_code, 302)       # login redirect, no page exists

    def test_csrf_enforced(self):
        from django.test import Client
        make_admin("boss")
        c = Client(enforce_csrf_checks=True)
        c.login(username="boss", password="pass-12345-xyz")
        self.assertEqual(c.post(reverse("employees:add"), {}).status_code, 403)

    def test_permission_separation(self):
        clerk = make_admin("clerk", superuser=False, perms=["attendance.view_attendance", "attendance.add_attendance"])
        self.client.force_login(clerk)
        for name in ("payroll:list", "payroll:increments", "payroll:payments", "employees:list", "accounts:audit_log", "accounts:settings"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 403, name)
        self.assertEqual(self.client.get(reverse("attendance:daily")).status_code, 200)

    def test_password_change(self):
        u = make_admin("boss")
        self.client.force_login(u)
        r = self.client.post(reverse("accounts:password_change"), {
            "old_password": "pass-12345-xyz", "new_password1": "Another-Str0ng-Pass", "new_password2": "Another-Str0ng-Pass"})
        self.assertRedirects(r, reverse("dashboard:home"))
        u.refresh_from_db()
        self.assertTrue(u.check_password("Another-Str0ng-Pass"))

    def test_weekly_holiday_settings(self):
        self.client.force_login(make_admin("boss"))
        self.client.post(reverse("accounts:settings"), {"weekdays": ["5", "6"]})
        from attendance.services import holiday_weekdays
        self.assertEqual(holiday_weekdays(), {5, 6})
