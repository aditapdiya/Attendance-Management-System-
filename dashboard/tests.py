from datetime import date
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from accounts.testing import make_admin, make_employee, mark_month
from attendance import services as att
from payroll import services as pay


class DashboardTests(TestCase):
    def setUp(self):
        self.admin = make_admin()
        self.client.force_login(self.admin)

    def test_empty_state_has_no_fake_data(self):
        r = self.client.get(reverse("dashboard:home"))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.context["stats"]["total"], 0)
        self.assertContains(r, "No employees yet")

    def test_stats_come_from_database(self):
        a = make_employee("A", joined=date(2025, 1, 1), user=self.admin)
        b = make_employee("B", phone="9000000002", joined=date(2025, 1, 1), user=self.admin)
        c = make_employee("C", phone="9000000003", joined=date(2025, 1, 1), user=self.admin)
        c.is_active = False
        c.save()
        d = date(2025, 1, 6)
        att.set_attendance(a, d, "PRESENT", "", self.admin)
        att.set_attendance(b, d, "HALF_DAY", "", self.admin)
        r = self.client.get(reverse("dashboard:home"), {"date": "2025-01-06"})
        self.assertEqual((r.context["stats"]["total"], r.context["stats"]["active"], r.context["stats"]["inactive"]), (3, 2, 1))
        sel = r.context["sel"]
        self.assertEqual((sel["total"], sel["PRESENT"], sel["HALF_DAY"], sel["not_marked"], sel["completion"]), (2, 1, 1, 0, 100))

    def test_pending_payments_total(self):
        e = make_employee(joined=date(2025, 1, 1), user=self.admin)
        mark_month(e, 2025, 1)
        p, _ = pay.generate_payroll(e, 2025, 1, self.admin)
        pay.finalize(p.pk, self.admin)
        pay.record_payment(p.pk, Decimal("5000"), date(2025, 2, 1), "CASH", "", "", self.admin)
        r = self.client.get(reverse("dashboard:home"))
        self.assertEqual(r.context["stats"]["pending_amt"], Decimal("10000.00"))
        self.assertEqual(r.context["stats"]["pending_n"], 1)
