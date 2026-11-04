from datetime import date
from decimal import Decimal

from django.db import connection
from django.test import TestCase
from django.urls import reverse

from accounts.models import AuditLog
from accounts.testing import aadhaar_with_checksum, make_admin, make_employee, mark_month
from employees import crypto
from employees.models import Employee
from payroll import services as pay
from payroll.models import SalaryRevision

VALID = aadhaar_with_checksum()


class EmployeeTests(TestCase):
    def setUp(self):
        self.admin = make_admin()
        self.client.force_login(self.admin)

    def data(self, **kw):
        d = {"name": "  Meena   Joshi ", "phone_number": "9123456780", "designation": "Accountant", "department": "Office",
             "joining_date": "2025-01-01", "address": "Pune", "notes": "", "initial_salary": "18000.00", "aadhaar_number": VALID}
        d.update(kw)
        return d

    def test_create_generates_code_initial_revision_and_audit(self):
        r = self.client.post(reverse("employees:add"), self.data())
        e = Employee.objects.get()
        self.assertRedirects(r, reverse("employees:profile", args=[e.pk]))
        self.assertRegex(e.employee_code, r"^VA\d{5}$")
        self.assertEqual(e.name, "Meena Joshi")
        rev = SalaryRevision.objects.get(employee=e)
        self.assertEqual((rev.revised_salary, rev.effective_from, rev.is_initial), (Decimal("18000.00"), date(2025, 1, 1), True))
        self.assertTrue(AuditLog.objects.filter(action="EMPLOYEE_CREATE").exists())

    def test_aadhaar_encrypted_at_rest_masked_and_not_logged(self):
        self.client.post(reverse("employees:add"), self.data())
        e = Employee.objects.get()
        with connection.cursor() as c:
            c.execute("SELECT aadhaar_encrypted, aadhaar_last4, aadhaar_hash FROM employees_employee")
            enc, last4, h = c.fetchone()
        self.assertNotIn(VALID, enc)
        self.assertNotIn(VALID, h)
        self.assertEqual(crypto.decrypt(enc), VALID)
        self.assertEqual(e.masked_aadhaar, f"XXXX XXXX {VALID[-4:]}")
        page = self.client.get(reverse("employees:profile", args=[e.pk])).content.decode()
        self.assertIn("XXXX XXXX", page)
        self.assertNotIn(VALID, page)
        self.assertFalse(AuditLog.objects.filter(description__contains=VALID).exists())

    def test_aadhaar_validation(self):
        for bad in ("123456789012", "12345", "abcdefghijkl", VALID[:-1] + str((int(VALID[-1]) + 1) % 10)):
            r = self.client.post(reverse("employees:add"), self.data(aadhaar_number=bad))
            self.assertEqual(r.status_code, 200, bad)
            self.assertFalse(Employee.objects.exists(), bad)

    def test_aadhaar_optional_and_unique(self):
        self.assertEqual(self.client.post(reverse("employees:add"), self.data(aadhaar_number="")).status_code, 302)
        self.assertEqual(self.client.post(reverse("employees:add"), self.data(aadhaar_number="", name="Second")).status_code, 302)
        self.client.post(reverse("employees:add"), self.data(name="Third"))
        r = self.client.post(reverse("employees:add"), self.data(name="Fourth"))
        self.assertContains(r, "already exists")

    def test_field_validation(self):
        for field, val in (("phone_number", "12345"), ("joining_date", "2999-01-01"), ("initial_salary", "0")):
            self.assertEqual(self.client.post(reverse("employees:add"), self.data(**{field: val})).status_code, 200, field)
        self.assertFalse(Employee.objects.exists())

    def test_edit_keeps_aadhaar_when_blank_and_cannot_change_salary(self):
        self.client.post(reverse("employees:add"), self.data())
        e = Employee.objects.get()
        d = self.data(aadhaar_number="", name="Meena J", initial_salary="99999")
        self.client.post(reverse("employees:edit", args=[e.pk]), d)
        e.refresh_from_db()
        self.assertEqual((e.name, e.salary, e.aadhaar_last4), ("Meena J", Decimal("18000.00"), VALID[-4:]))

    def test_deactivate_reactivate_and_search_filter(self):
        a = make_employee("Zed One", user=self.admin)
        b = make_employee("Amy Two", phone="9000000001", user=self.admin)
        self.client.post(reverse("employees:toggle", args=[a.pk]))
        a.refresh_from_db()
        self.assertFalse(a.is_active)
        r = self.client.get(reverse("employees:list"), {"status": "inactive"})
        self.assertContains(r, "Zed One")
        self.assertNotContains(r, "Amy Two")
        self.assertContains(self.client.get(reverse("employees:list"), {"q": b.employee_code}), "Amy Two")
        self.assertContains(self.client.get(reverse("employees:list"), {"q": "9000000001"}), "Amy Two")
        self.client.post(reverse("employees:toggle", args=[a.pk]))
        a.refresh_from_db()
        self.assertTrue(a.is_active)

    def test_delete_blocked_with_history(self):
        e = make_employee(user=self.admin)
        mark_month(e, 2025, 1)
        self.client.post(reverse("employees:delete", args=[e.pk]))
        self.assertTrue(Employee.objects.filter(pk=e.pk).exists())
        pay.generate_payroll(e, 2025, 1, self.admin)
        with self.assertRaises(Exception):
            e.delete()

    def test_delete_allowed_without_history(self):
        e = make_employee(user=self.admin)
        self.client.post(reverse("employees:delete", args=[e.pk]))
        self.assertFalse(Employee.objects.exists())
        self.assertFalse(SalaryRevision.objects.exists())

    def test_profile_renders_all_sections(self):
        e = make_employee(user=self.admin)
        mark_month(e, 2025, 1)
        pay.add_revision(e, Decimal("17000"), date(2025, 4, 1), "inc", self.admin)
        r = self.client.get(reverse("employees:profile", args=[e.pk]))
        self.assertContains(r, "Salary revision timeline")
        self.assertContains(r, "₹17,000.00")
