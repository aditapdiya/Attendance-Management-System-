from datetime import date
from decimal import Decimal as D

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from accounts.models import AuditLog
from accounts.testing import make_admin, make_employee, mark_month
from attendance.models import Attendance
from payroll import services as svc
from payroll.models import MonthlyPayroll, SalaryPayment, SalaryRevision


class CalculationTests(TestCase):
    """Every expected value below was calculated by hand."""

    def setUp(self):
        self.user = make_admin()

    def calc(self, emp, y, m):
        return svc.calculate(emp, y, m, today=date(2026, 11, 4))

    def test_leap_february_mixed_deductions(self):
        # Feb 2024 = 29 days, salary 29,000 -> per day 1,000.
        # 2 absent + 1 half-day (0.5) + 1 unpaid leave = 3.5 days -> 3,500 deducted.
        e = make_employee(salary="29000", joined=date(2024, 1, 1))
        mark_month(e, 2024, 2, overrides={5: "ABSENT", 6: "ABSENT", 7: "HALF_DAY", 8: "UNPAID_LEAVE", 9: "PAID_LEAVE"})
        c = self.calc(e, 2024, 2)
        self.assertEqual(c.total_calendar_days, 29)
        self.assertEqual(c.per_day_salary, D("1000.00"))
        self.assertEqual((c.absent_days, c.half_days, c.unpaid_leave_days, c.paid_leave_days), (2, 1, 1, 1))
        self.assertEqual(c.unpaid_equivalent_days, D("3.5"))
        self.assertEqual(c.deductions, D("3500.00"))
        self.assertEqual(c.net_salary, D("25500.00"))
        self.assertEqual(c.payable_days, D("25.5"))
        self.assertEqual(c.missing_days, 0)

    def test_non_leap_february_28_days(self):
        e = make_employee(salary="28000", joined=date(2025, 1, 1))
        mark_month(e, 2025, 2, overrides={3: "ABSENT"})
        c = self.calc(e, 2025, 2)
        self.assertEqual(c.total_calendar_days, 28)
        self.assertEqual((c.deductions, c.net_salary), (D("1000.00"), D("27000.00")))

    def test_thirty_day_month_rounding(self):
        # April: 17000 * 2 / 30 = 1133.333... -> 1133.33 ; net 15866.67
        e = make_employee(salary="17000", joined=date(2025, 1, 1))
        mark_month(e, 2025, 4, overrides={2: "ABSENT", 3: "ABSENT"})
        c = self.calc(e, 2025, 4)
        self.assertEqual((c.deductions, c.net_salary), (D("1133.33"), D("15866.67")))
        self.assertEqual(c.deductions + c.net_salary, D("17000.00"))

    def test_half_day_31_day_month(self):
        # 15000 * 0.5 / 31 = 241.9354... -> 241.94
        e = make_employee(salary="15000")
        mark_month(e, 2025, 1, overrides={10: "HALF_DAY"})
        c = self.calc(e, 2025, 1)
        self.assertEqual(c.total_calendar_days, 31)
        self.assertEqual((c.deductions, c.net_salary), (D("241.94"), D("14758.06")))

    def test_paid_leave_and_sundays_never_deducted(self):
        e = make_employee(salary="15000")
        mark_month(e, 2025, 1, overrides={8: "PAID_LEAVE", 9: "PAID_LEAVE"})
        c = self.calc(e, 2025, 1)
        self.assertEqual(c.deductions, D("0.00"))
        self.assertEqual(c.net_salary, D("15000.00"))
        self.assertEqual(c.holiday_days, 4)         # Jan 2025 has 4 Sundays (5, 12, 19, 26)

    def test_sunday_absent_record_is_ignored(self):
        e = make_employee(salary="15000")
        mark_month(e, 2025, 1)
        Attendance.objects.create(employee=e, attendance_date=date(2025, 1, 5), status="ABSENT")   # a Sunday
        c = self.calc(e, 2025, 1)
        self.assertEqual(c.absent_days, 0)
        self.assertEqual(c.net_salary, D("15000.00"))

    def test_unmarked_working_days_are_missing_not_present_or_absent(self):
        e = make_employee(salary="15000")
        c = self.calc(e, 2025, 1)            # nothing marked
        self.assertEqual(c.missing_days, 27)  # 31 days - 4 Sundays
        self.assertEqual((c.present_days, c.absent_days), (0, 0))
        self.assertEqual(c.net_salary, D("15000.00"))   # missing is not auto-deducted

    def test_mid_month_joining(self):
        # joins 11 Mar 2025 (Mar = 31 days): 10 days before joining unpaid -> 31000*10/31 = 10000
        e = make_employee(salary="31000", joined=date(2025, 3, 11))
        mark_month(e, 2025, 3)
        c = self.calc(e, 2025, 3)
        self.assertEqual(c.pre_joining_days, 10)
        self.assertEqual((c.deductions, c.net_salary), (D("10000.00"), D("21000.00")))
        self.assertEqual(c.missing_days, 0)

    def test_joined_after_month_not_eligible(self):
        e = make_employee(joined=date(2025, 5, 1))
        with self.assertRaises(ValidationError):
            self.calc(e, 2025, 4)

    def test_all_decimal_no_float(self):
        e = make_employee(salary="12345.67")
        mark_month(e, 2025, 1, overrides={7: "ABSENT"})
        c = self.calc(e, 2025, 1)
        for v in (c.deductions, c.net_salary, c.gross_salary, c.salary_snapshot):
            self.assertIsInstance(v, D)
        self.assertEqual(c.deductions, svc.money(D("12345.67") / 31))


class SalaryRevisionTests(TestCase):
    def setUp(self):
        self.user = make_admin()
        self.e = make_employee(salary="15000", joined=date(2025, 1, 1), user=self.user)
        svc.add_revision(self.e, D("17000"), date(2025, 4, 1), "Annual", self.user)
        svc.add_revision(self.e, D("20000"), date(2025, 7, 1), "Promotion", self.user)

    def test_salary_applicable_to_each_month(self):
        s = lambda y, m: svc.salary_for_month(self.e, y, m)
        for m in (1, 2, 3):
            self.assertEqual(s(2025, m), D("15000.00"))
        for m in (4, 5, 6):
            self.assertEqual(s(2025, m), D("17000.00"))
        for m in (7, 12):
            self.assertEqual(s(2025, m), D("20000.00"))
        self.assertEqual(s(2026, 3), D("20000.00"))
        self.assertIsNone(svc.salary_for_month(self.e, 2024, 12))

    def test_history_is_preserved_and_chained(self):
        revs = list(SalaryRevision.objects.filter(employee=self.e).order_by("effective_from"))
        self.assertEqual([r.revised_salary for r in revs], [D("15000"), D("17000"), D("20000")])
        self.assertEqual([r.previous_salary for r in revs], [None, D("15000"), D("17000")])
        self.assertEqual(revs[1].increment_amount, D("2000"))
        self.assertEqual(revs[1].increment_percent, D("13.33"))
        self.assertEqual(revs[2].created_by, self.user)

    def test_payroll_uses_month_salary(self):
        for m, expect in ((3, "15000"), (4, "17000"), (7, "20000")):
            mark_month(self.e, 2025, m)
            p, _ = svc.generate_payroll(self.e, 2025, m, self.user)
            self.assertEqual(p.salary_snapshot, D(expect))
            self.assertEqual(p.net_salary, D(expect))

    def test_conflicting_revisions_rejected(self):
        for when in (date(2025, 7, 1), date(2025, 5, 1), date(2025, 2, 1)):   # same / between / before
            with self.assertRaises(ValidationError):
                svc.add_revision(self.e, D("25000"), when, "x", self.user)
        with self.assertRaises(ValidationError):
            svc.add_revision(self.e, D("25000"), date(2025, 9, 15), "not first of month", self.user)
        with self.assertRaises(ValidationError):
            svc.add_revision(self.e, D("20000"), date(2025, 9, 1), "same salary", self.user)
        with self.assertRaises(ValidationError):
            svc.add_revision(self.e, D("0"), date(2025, 9, 1), "zero", self.user)
        self.assertEqual(SalaryRevision.objects.filter(employee=self.e).count(), 3)

    def test_db_enforces_one_revision_per_month(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            SalaryRevision.objects.create(employee=self.e, revised_salary=D("1"), effective_from=date(2025, 4, 1))

    def test_finalized_payroll_never_changes_after_increment(self):
        e = make_employee("Ravi", "15000", date(2025, 1, 1), user=self.user)
        mark_month(e, 2025, 3)
        p, _ = svc.generate_payroll(e, 2025, 3, self.user)
        svc.finalize(p.pk, self.user)
        svc.add_revision(e, D("17000"), date(2025, 4, 1), "inc", self.user)
        p.refresh_from_db()
        self.assertEqual((p.salary_snapshot, p.net_salary, p.status), (D("15000"), D("15000"), "FINALIZED"))
        # a revision reaching back into a finalized month is refused
        with self.assertRaises(ValidationError):
            svc.add_revision(e, D("19000"), date(2025, 3, 1), "backdated", self.user)

    def test_employee_current_salary_synced(self):
        self.e.refresh_from_db()
        self.assertEqual(self.e.salary, D("20000.00"))


class PayrollLifecycleTests(TestCase):
    def setUp(self):
        self.user = make_admin()
        self.e = make_employee(salary="15000", joined=date(2025, 1, 1), user=self.user)
        mark_month(self.e, 2025, 1, overrides={6: "ABSENT"})

    def test_duplicate_prevented(self):
        p1, c1 = svc.generate_payroll(self.e, 2025, 1, self.user)
        p2, c2 = svc.generate_payroll(self.e, 2025, 1, self.user)
        self.assertTrue(c1)
        self.assertFalse(c2)
        self.assertEqual(p1.pk, p2.pk)
        with self.assertRaises(IntegrityError), transaction.atomic():
            MonthlyPayroll.objects.create(employee=self.e, year=2025, month=1, total_calendar_days=31, salary_snapshot=1,
                                          present_days=0, absent_days=0, half_days=0, paid_leave_days=0, unpaid_leave_days=0,
                                          payable_days=0, gross_salary=1, deductions=0, net_salary=1)

    def test_finalize_and_protection(self):
        p, _ = svc.generate_payroll(self.e, 2025, 1, self.user)
        self.assertEqual(p.net_salary, D("14516.13"))      # 15000 - 15000/31
        svc.finalize(p.pk, self.user)
        p.refresh_from_db()
        self.assertTrue(p.is_final)
        self.assertEqual(p.finalized_by, self.user)
        with self.assertRaises(ValidationError):
            svc.recalculate(p.pk, self.user)
        with self.assertRaises(ValidationError):
            svc.finalize(p.pk, self.user)
        again, created = svc.generate_payroll(self.e, 2025, 1, self.user)     # does not overwrite
        self.assertFalse(created)
        self.assertEqual(again.net_salary, D("14516.13"))

    def test_attendance_change_after_generation_requires_recalculation(self):
        from attendance import services as att
        p, _ = svc.generate_payroll(self.e, 2025, 1, self.user)
        att.set_attendance(self.e, date(2025, 1, 7), "ABSENT", "", self.user)
        p.refresh_from_db()
        self.assertTrue(p.needs_review)
        self.assertEqual(p.net_salary, D("14516.13"))               # not silently changed
        with self.assertRaisesMessage(ValidationError, "recalculate"):
            svc.finalize(p.pk, self.user)
        svc.recalculate(p.pk, self.user)
        p.refresh_from_db()
        self.assertFalse(p.needs_review)
        self.assertEqual(p.net_salary, D("15000") - svc.money(D("15000") * 2 / 31))
        svc.finalize(p.pk, self.user)

    def test_cannot_finalize_with_unmarked_days(self):
        e2 = make_employee("Sunil", "15000", date(2025, 1, 1), user=self.user)
        p, _ = svc.generate_payroll(e2, 2025, 1, self.user)
        with self.assertRaisesMessage(ValidationError, "no attendance"):
            svc.finalize(p.pk, self.user)

    def test_cannot_finalize_month_in_progress(self):
        today = date.today()
        e = make_employee("New", "15000", today.replace(day=1), user=self.user)
        p, _ = svc.generate_payroll(e, today.year, today.month, self.user)
        with self.assertRaisesMessage(ValidationError, "not ended"):
            svc.finalize(p.pk, self.user)

    def test_attendance_locked_after_finalization(self):
        from attendance import services as att
        p, _ = svc.generate_payroll(self.e, 2025, 1, self.user)
        svc.finalize(p.pk, self.user)
        with self.assertRaisesMessage(ValidationError, "finalized"):
            att.set_attendance(self.e, date(2025, 1, 8), "ABSENT", "", self.user)

    def test_unlock_audited_and_recalculable(self):
        p, _ = svc.generate_payroll(self.e, 2025, 1, self.user)
        svc.finalize(p.pk, self.user)
        with self.assertRaises(ValidationError):
            svc.unlock(p.pk, self.user, "")
        svc.unlock(p.pk, self.user, "Correct attendance for 6 Jan")
        p.refresh_from_db()
        self.assertEqual((p.status, p.needs_review, p.finalized_by), ("DRAFT", True, None))
        self.assertTrue(AuditLog.objects.filter(action="PAYROLL_UNLOCK", description__contains="Correct attendance").exists())
        svc.recalculate(p.pk, self.user)
        self.assertTrue(AuditLog.objects.filter(action="PAYROLL_RECALC").exists())

    def test_unlock_blocked_when_payments_exist(self):
        p, _ = svc.generate_payroll(self.e, 2025, 1, self.user)
        svc.finalize(p.pk, self.user)
        svc.record_payment(p.pk, D("100"), date(2025, 2, 1), "CASH", "", "", self.user)
        with self.assertRaisesMessage(ValidationError, "payments"):
            svc.unlock(p.pk, self.user, "trying anyway")


class PaymentTests(TestCase):
    def setUp(self):
        self.user = make_admin()
        e = make_employee(salary="15000", joined=date(2025, 1, 1), user=self.user)
        mark_month(e, 2025, 1)
        self.p, _ = svc.generate_payroll(e, 2025, 1, self.user)

    def pay(self, amt, **kw):
        return svc.record_payment(self.p.pk, D(amt), kw.pop("on", date(2025, 2, 1)), "BANK", "TXN1", kw.pop("remarks", ""), self.user, **kw)

    def test_draft_cannot_be_paid(self):
        with self.assertRaises(ValidationError):
            self.pay("100")

    def test_partial_then_full(self):
        svc.finalize(self.p.pk, self.user)
        self.p = MonthlyPayroll.objects.get(pk=self.p.pk)
        self.assertEqual(self.p.payment_status, "Pending")
        self.pay("5000")
        self.p = MonthlyPayroll.objects.get(pk=self.p.pk)
        self.assertEqual((self.p.total_paid, self.p.balance, self.p.payment_status), (D("5000"), D("10000"), "Partially Paid"))
        self.pay("10000")
        self.p = MonthlyPayroll.objects.get(pk=self.p.pk)
        self.assertEqual((self.p.balance, self.p.payment_status), (D("0"), "Paid"))
        self.assertEqual(SalaryPayment.objects.filter(payroll=self.p).count(), 2)

    def test_overpayment_blocked_unless_authorized_adjustment(self):
        svc.finalize(self.p.pk, self.user)
        self.pay("14000")
        with self.assertRaisesMessage(ValidationError, "exceeds"):
            self.pay("2000")
        with self.assertRaisesMessage(ValidationError, "Remarks"):
            self.pay("2000", allow_overpay=True)
        adj = self.pay("2000", allow_overpay=True, remarks="Advance correction")
        self.assertTrue(adj.is_adjustment)

    def test_invalid_amounts(self):
        svc.finalize(self.p.pk, self.user)
        for bad in ("0", "-5"):
            with self.assertRaises(ValidationError):
                self.pay(bad)
        with self.assertRaises(ValidationError):
            self.pay("10", on=date(2999, 1, 1))
