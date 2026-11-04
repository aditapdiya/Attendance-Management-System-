"""End-to-end: login -> add employee -> attendance -> payroll -> finalize -> pay -> increment -> reports."""
import json
from datetime import date
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from accounts.testing import make_admin
from employees.models import Employee
from payroll.models import MonthlyPayroll, SalaryPayment


class FullWorkflowTest(TestCase):
    def setUp(self):
        make_admin("owner")
        self.client.login(username="owner", password="pass-12345-xyz")

    def test_workflow(self):
        c = self.client
        c.post(reverse("employees:add"), {"name": "Kiran More", "phone_number": "9012345678", "designation": "Driver", "department": "Logistics",
                                          "joining_date": "2025-01-01", "address": "Latur", "notes": "", "initial_salary": "15000"})
        e = Employee.objects.get()
        for d in range(1, 32):
            day = date(2025, 1, d)
            if day.weekday() == 6:
                continue
            st = "ABSENT" if d == 6 else ("HALF_DAY" if d == 7 else "PRESENT")
            res = c.post(reverse("attendance:api_save"), json.dumps({"date": day.isoformat(), "items": [{"employee": e.pk, "status": st}]}),
                         content_type="application/json").json()
            self.assertTrue(res["ok"], res)
        self.assertContains(c.get(reverse("payroll:list"), {"year": 2025, "month": 1}), "Preview")
        self.assertEqual(MonthlyPayroll.objects.count(), 0)
        c.post(reverse("payroll:generate"), {"year": 2025, "month": 1})
        p = MonthlyPayroll.objects.get()
        self.assertEqual((p.deductions, p.net_salary, p.status), (Decimal("725.81"), Decimal("14274.19"), "DRAFT"))   # 15000*1.5/31
        c.post(reverse("payroll:finalize", args=[p.pk]))
        p.refresh_from_db()
        self.assertEqual(p.status, "FINALIZED")
        url = reverse("payroll:pay", args=[p.pk])
        base = {"payment_date": "2025-02-01", "payment_mode": "BANK", "transaction_reference": "UTR1", "remarks": ""}
        c.post(url, {**base, "amount_paid": "10000"})
        c.post(url, {**base, "amount_paid": "5000"})            # over balance, no remarks -> refused
        self.assertEqual(SalaryPayment.objects.count(), 1)
        c.post(url, {**base, "amount_paid": "4274.19"})
        p = MonthlyPayroll.objects.get(pk=p.pk)
        self.assertEqual((p.balance, p.payment_status), (Decimal("0.00"), "Paid"))

        # increment from April, then history + pages + reports
        c.post(reverse("payroll:increments"), {"employee": e.pk, "revised_salary": "17000", "effective_month": "2025-04", "reason": "Annual"})
        e.refresh_from_db()
        self.assertEqual(e.salary, Decimal("17000.00"))
        p.refresh_from_db()
        self.assertEqual(p.salary_snapshot, Decimal("15000.00"))
        for name, args in (("payroll:detail", [p.pk]), ("payroll:slip", [p.pk]), ("payroll:payments", []), ("payroll:increments", []),
                           ("payroll:salary_history", []), ("accounts:audit_log", []), ("reports:index", []), ("accounts:settings", []),
                           ("employees:profile", [e.pk]), ("dashboard:home", [])):
            self.assertEqual(c.get(reverse(name, args=args)).status_code, 200, name)
        self.assertEqual(c.get(reverse("payroll:salary_history"), {"employee": e.pk}).status_code, 200)

    def test_unlock_requires_permission(self):
        c = self.client
        c.post(reverse("employees:add"), {"name": "K", "phone_number": "9012345678", "designation": "D", "joining_date": "2025-01-01", "initial_salary": "15000"})
        e = Employee.objects.get()
        from accounts.testing import mark_month
        from payroll import services as svc
        mark_month(e, 2025, 1)
        p, _ = svc.generate_payroll(e, 2025, 1, None)
        svc.finalize(p.pk, None)
        finalizer = make_admin("fin", superuser=False, perms=["payroll.view_monthlypayroll", "payroll.finalize_payroll"])
        c.force_login(finalizer)
        self.assertEqual(c.post(reverse("payroll:unlock", args=[p.pk]), {"reason": "because"}).status_code, 403)
        unlocker = make_admin("unl", superuser=False, perms=["payroll.view_monthlypayroll", "payroll.unlock_payroll"])
        c.force_login(unlocker)
        c.post(reverse("payroll:unlock", args=[p.pk]), {"reason": "fix wrong entry"})
        p.refresh_from_db()
        self.assertEqual(p.status, "DRAFT")
