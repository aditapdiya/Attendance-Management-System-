import json
from datetime import date, timedelta

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse

from accounts.testing import make_admin, make_employee
from attendance import services as svc
from attendance.models import Attendance, WeeklyHoliday

MON = date(2025, 1, 6)   # Monday
SUN = date(2025, 1, 5)   # Sunday


class AttendanceServiceTests(TestCase):
    def setUp(self):
        self.user = make_admin()
        self.a = make_employee("Anil", joined=date(2025, 1, 1), user=self.user)
        self.b = make_employee("Bina", joined=date(2025, 1, 1), phone="9876500001", user=self.user)

    def test_sunday_seeded_as_weekly_holiday(self):
        self.assertEqual(svc.holiday_weekdays(), {6})
        self.assertTrue(svc.is_holiday(SUN))
        self.assertFalse(svc.is_holiday(MON))

    def test_no_entry_on_holiday_and_future(self):
        with self.assertRaisesMessage(ValidationError, "holiday"):
            svc.set_attendance(self.a, SUN, "ABSENT", "", self.user)
        with self.assertRaisesMessage(ValidationError, "future"):
            svc.set_attendance(self.a, date.today() + timedelta(days=3), "PRESENT", "", self.user)

    def test_duplicate_prevented_service_and_db(self):
        _, r1 = svc.set_attendance(self.a, MON, "PRESENT", "", self.user)
        _, r2 = svc.set_attendance(self.a, MON, "PRESENT", "", self.user)
        self.assertEqual((r1, r2), ("created", "unchanged"))
        self.assertEqual(Attendance.objects.filter(employee=self.a, attendance_date=MON).count(), 1)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Attendance.objects.create(employee=self.a, attendance_date=MON, status="ABSENT")

    def test_edit_requires_change_permission(self):
        svc.set_attendance(self.a, MON, "PRESENT", "", self.user)
        with self.assertRaisesMessage(ValidationError, "permission"):
            svc.set_attendance(self.a, MON, "ABSENT", "", self.user, can_change=False)
        _, res = svc.set_attendance(self.a, MON, "ABSENT", "sick", self.user, can_change=True)
        self.assertEqual(res, "updated")

    def test_cannot_mark_before_joining_or_inactive(self):
        late = make_employee("Late", joined=date(2025, 1, 10), phone="9876500002", user=self.user)
        with self.assertRaisesMessage(ValidationError, "joined"):
            svc.set_attendance(late, MON, "PRESENT", "", self.user)
        self.b.is_active = False
        self.b.save()
        with self.assertRaisesMessage(ValidationError, "inactive"):
            svc.set_attendance(self.b, MON, "PRESENT", "", self.user)

    def test_mark_all_present_only_fills_gaps(self):
        svc.set_attendance(self.a, MON, "ABSENT", "", self.user)             # manual status must survive
        inactive = make_employee("Gone", joined=date(2025, 1, 1), phone="9876500003", user=self.user)
        inactive.is_active = False
        inactive.save()
        late = make_employee("Later", joined=date(2025, 1, 20), phone="9876500004", user=self.user)
        r = svc.mark_all_present(MON, self.user)
        self.assertEqual(r, {"created": 1, "skipped_existing": 1, "skipped_locked": 0})
        self.assertEqual(Attendance.objects.get(employee=self.a, attendance_date=MON).status, "ABSENT")
        self.assertEqual(Attendance.objects.get(employee=self.b, attendance_date=MON).status, "PRESENT")
        self.assertFalse(Attendance.objects.filter(employee__in=[inactive, late], attendance_date=MON).exists())
        again = svc.mark_all_present(MON, self.user)
        self.assertEqual(again["created"], 0)

    def test_mark_all_present_refused_on_sunday(self):
        with self.assertRaises(ValidationError):
            svc.mark_all_present(SUN, self.user)
        self.assertFalse(Attendance.objects.exists())

    def test_configurable_weekly_holiday(self):
        WeeklyHoliday.objects.create(weekday=5)            # Saturday
        self.assertTrue(svc.is_holiday(date(2025, 1, 4)))
        with self.assertRaises(ValidationError):
            svc.set_attendance(self.a, date(2025, 1, 4), "PRESENT", "", self.user)

    def test_day_counts_not_marked(self):
        svc.set_attendance(self.a, MON, "HALF_DAY", "", self.user)
        c = svc.day_counts(MON)
        self.assertEqual((c["total"], c["HALF_DAY"], c["not_marked"], c["completion"]), (2, 1, 1, 50))


class AttendanceApiTests(TestCase):
    def setUp(self):
        self.admin = make_admin()
        self.e = make_employee("Anil", joined=date(2025, 1, 1), user=self.admin)
        self.client.force_login(self.admin)

    def post(self, items, d="2025-01-06"):
        return self.client.post(reverse("attendance:api_save"), json.dumps({"date": d, "items": items}), content_type="application/json")

    def test_save_and_clear(self):
        r = self.post([{"employee": self.e.pk, "status": "PRESENT", "remarks": "on time"}]).json()
        self.assertTrue(r["ok"])
        self.assertEqual(r["counts"]["PRESENT"], 1)
        r = self.post([{"employee": self.e.pk, "status": ""}]).json()
        self.assertEqual(r["counts"]["not_marked"], 1)
        self.assertFalse(Attendance.objects.exists())

    def test_errors_reported_per_item(self):
        r = self.post([{"employee": self.e.pk, "status": "PRESENT"}], d="2025-01-05").json()      # Sunday
        self.assertFalse(r["ok"])
        self.assertIn("holiday", r["errors"][0]["error"])

    def test_bad_payload(self):
        self.assertEqual(self.client.post(reverse("attendance:api_save"), "nope", content_type="application/json").status_code, 400)

    def test_permissions(self):
        clerk = make_admin("clerk", superuser=False, perms=["attendance.view_attendance", "attendance.add_attendance"])
        self.client.force_login(clerk)
        self.assertTrue(self.post([{"employee": self.e.pk, "status": "PRESENT"}]).json()["ok"])
        r = self.post([{"employee": self.e.pk, "status": "ABSENT"}]).json()           # editing needs change permission
        self.assertFalse(r["ok"])
        viewer = make_admin("viewer", superuser=False, perms=["attendance.view_attendance"])
        self.client.force_login(viewer)
        self.assertEqual(self.post([{"employee": self.e.pk, "status": "PRESENT"}]).status_code, 403)

    def test_pages_render(self):
        for name in ("attendance:daily", "attendance:calendar", "attendance:history"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 200)
        self.assertEqual(self.client.get(reverse("attendance:daily"), {"date": "2025-01-05"}).status_code, 200)
        self.assertEqual(self.client.get(reverse("attendance:calendar"), {"employee": self.e.pk, "year": 2025, "month": 2}).status_code, 200)

    def test_mark_all_present_view(self):
        r = self.client.post(reverse("attendance:mark_all_present"), {"date": "2025-01-06"}, follow=True)
        self.assertContains(r, "1 employee(s) marked present")
