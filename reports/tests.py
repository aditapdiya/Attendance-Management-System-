from datetime import date
from decimal import Decimal
from io import BytesIO

from django.test import TestCase
from django.urls import reverse
from openpyxl import load_workbook

from accounts.testing import aadhaar_with_checksum, make_admin, make_employee, mark_month
from payroll import services as pay


class ReportTests(TestCase):
    def setUp(self):
        self.admin = make_admin()
        self.client.force_login(self.admin)
        self.e = make_employee("Report Person", "15000", date(2025, 1, 1), user=self.admin)
        self.aad = aadhaar_with_checksum()
        self.e.set_aadhaar(self.aad)
        self.e.save()
        mark_month(self.e, 2025, 1, overrides={6: "ABSENT"})
        p, _ = pay.generate_payroll(self.e, 2025, 1, self.admin)
        pay.finalize(p.pk, self.admin)
        pay.record_payment(p.pk, Decimal("1000"), date(2025, 2, 1), "UPI", "REF1", "", self.admin)

    def get(self, **q):
        return self.client.get(reverse("reports:download"), q)

    def test_every_report_in_every_format(self):
        cases = {
            "daily_attendance": {"date": "2025-01-06"}, "monthly_attendance": {"period": "2025-01"},
            "employee_attendance": {"employee": self.e.pk, "period": "2025-01"}, "attendance_summary": {"period": "2025-01"},
            "monthly_payroll": {"period": "2025-01"}, "employee_salary": {"employee": self.e.pk, "year": 2025},
            "increment_history": {}, "monthly_expenditure": {"year": 2025},
            "pending_payments": {"period": "2025-01"}, "completed_payments": {"period": "2025-01"},
        }
        for key, params in cases.items():
            for fmt, ctype in (("html", "text/html"), ("pdf", "application/pdf"), ("xlsx", "spreadsheetml")):
                r = self.get(type=key, fmt=fmt, **params)
                self.assertEqual(r.status_code, 200, (key, fmt))
                self.assertIn(ctype, r["Content-Type"])
                if fmt == "pdf":
                    self.assertTrue(r.content.startswith(b"%PDF"))

    def test_payroll_excel_content_and_branding_no_aadhaar(self):
        r = self.get(type="monthly_payroll", period="2025-01", fmt="xlsx")
        ws = load_workbook(BytesIO(r.content)).active
        cells = [str(c.value) for row in ws.iter_rows() for c in row if c.value is not None]
        self.assertIn("VIMAL AGENCIES", cells)
        self.assertIn("Report Person", cells)
        self.assertTrue(any("January 2025" in c for c in cells))
        self.assertFalse(any(self.aad in c for c in cells))
        net = [c.value for row in ws.iter_rows() for c in row if isinstance(c.value, Decimal) or isinstance(c.value, (int, float))]
        self.assertIn(Decimal("14516.13"), [Decimal(str(v)) for v in net])

    def test_pending_report_lists_balance(self):
        html = self.get(type="pending_payments", period="2025-01").content.decode()
        self.assertIn("13,516.13", html)
        self.assertNotIn(self.aad, html)

    def test_salary_slip_pdf_and_html(self):
        p = self.e.payrolls.get()
        r = self.client.get(reverse("payroll:slip_pdf", args=[p.pk]))
        self.assertTrue(r.content.startswith(b"%PDF"))
        self.assertContains(self.client.get(reverse("payroll:slip", args=[p.pk])), "NET SALARY")

    def test_bad_params_and_permissions(self):
        self.assertEqual(self.get(type="nope").status_code, 404)
        self.assertEqual(self.get(type="daily_attendance", date="garbage").status_code, 404)
        clerk = make_admin("clerk", superuser=False, perms=["attendance.view_attendance"])
        self.client.force_login(clerk)
        self.assertEqual(self.get(type="monthly_payroll", period="2025-01").status_code, 403)
        self.assertEqual(self.get(type="daily_attendance", date="2025-01-06").status_code, 200)
