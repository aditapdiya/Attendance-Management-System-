import calendar
from datetime import date
from decimal import Decimal

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Sum
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from accounts.decorators import perm_required
from employees.models import Employee
from . import services as svc
from .forms import PaymentForm, RevisionForm
from .models import MonthlyPayroll, SalaryPayment, SalaryRevision
from .slip import slip_pdf_bytes


def parse_period(request, src=None):
    src = src or request.GET
    today = date.today()
    try:
        y, m = int(src.get("year", today.year)), int(src.get("month", today.month))
        date(y, m, 1)
        return y, m
    except (TypeError, ValueError):
        return today.year, today.month


def _back(y, m):
    return redirect(f"{reverse('payroll:list')}?year={y}&month={m}")


def _err(request, ex):
    for m in ex.messages:
        messages.error(request, m)


@perm_required("payroll.view_monthlypayroll")
def payroll_list(request):
    y, m = parse_period(request)
    last = date(y, m, calendar.monthrange(y, m)[1])
    saved = {p.employee_id: p for p in MonthlyPayroll.objects.filter(year=y, month=m).select_related("employee")}
    emps = list(Employee.objects.filter(is_active=True, joining_date__lte=last).order_by("name"))
    for p in saved.values():
        if p.employee not in emps:
            emps.append(p.employee)
    rows, tot = [], {"gross": Decimal(0), "ded": Decimal(0), "net": Decimal(0)}
    for e in emps:
        p = saved.get(e.id)
        row = {"emp": e, "p": p, "calc": None, "error": None}
        if p is None:
            try:
                row["calc"] = svc.calculate(e, y, m)
            except ValidationError as ex:
                row["error"] = " ".join(ex.messages)
        src = p or row["calc"]
        if src:
            tot["gross"] += src.gross_salary
            tot["ded"] += src.deductions
            tot["net"] += src.net_salary
        rows.append(row)
    n_draft = sum(1 for r in rows if r["p"] and not r["p"].is_final)
    return render(request, "payroll/list.html", {
        "rows": rows, "y": y, "m": m, "month_name": calendar.month_name[m], "tot": tot,
        "n_unsaved": sum(1 for r in rows if not r["p"] and r["calc"]), "n_draft": n_draft,
        "years": range(date.today().year - 3, date.today().year + 2),
        "months": [(i, calendar.month_name[i]) for i in range(1, 13)],
        "month_over": svc.month_ended(y, m),
        "crumbs": [("Payroll", None)],
    })


@require_POST
@perm_required("payroll.add_monthlypayroll")
def generate(request):
    y, m = parse_period(request, request.POST)
    emp = Employee.objects.filter(pk=request.POST.get("employee") or 0).first()
    res = svc.generate_bulk(y, m, request.user, emp)
    if res["created"]:
        messages.success(request, f"{res['created']} draft payroll(s) generated for {calendar.month_name[m]} {y}.")
    if res["existing"]:
        messages.info(request, f"{res['existing']} already existed and were not changed.")
    for e in res["errors"]:
        messages.warning(request, e)
    return _back(y, m)


@require_POST
@perm_required("payroll.change_monthlypayroll")
def recalculate(request, pk):
    p = get_object_or_404(MonthlyPayroll, pk=pk)
    try:
        svc.recalculate(pk, request.user)
        messages.success(request, "Payroll recalculated from current attendance and salary.")
    except ValidationError as ex:
        _err(request, ex)
    return redirect("payroll:detail", pk=pk)


@require_POST
@perm_required("payroll.finalize_payroll")
def finalize(request, pk):
    get_object_or_404(MonthlyPayroll, pk=pk)
    try:
        svc.finalize(pk, request.user)
        messages.success(request, "Payroll finalized.")
    except ValidationError as ex:
        _err(request, ex)
    return redirect("payroll:detail", pk=pk)


@require_POST
@perm_required("payroll.finalize_payroll")
def finalize_all(request):
    y, m = parse_period(request, request.POST)
    res = svc.finalize_bulk(y, m, request.user)
    if res["done"]:
        messages.success(request, f"{res['done']} payroll(s) finalized.")
    for e in res["errors"]:
        messages.warning(request, e)
    return _back(y, m)


@require_POST
@perm_required("payroll.unlock_payroll")
def unlock(request, pk):
    get_object_or_404(MonthlyPayroll, pk=pk)
    try:
        svc.unlock(pk, request.user, request.POST.get("reason"))
        messages.warning(request, "Payroll unlocked and set back to draft. Review and recalculate it.")
    except ValidationError as ex:
        _err(request, ex)
    return redirect("payroll:detail", pk=pk)


@perm_required("payroll.view_monthlypayroll")
def detail(request, pk):
    p = get_object_or_404(MonthlyPayroll.objects.select_related("employee", "finalized_by", "generated_by"), pk=pk)
    return render(request, "payroll/detail.html", {
        "p": p, "payments": p.payments.select_related("recorded_by"), "form": PaymentForm(),
        "can_overpay": request.user.has_perm("payroll.authorize_overpayment"),
        "crumbs": [("Payroll", "payroll:list"), (f"{p.employee.name} – {p.period}", None)],
    })


@perm_required("payroll.view_monthlypayroll")
def slip(request, pk):
    p = get_object_or_404(MonthlyPayroll.objects.select_related("employee"), pk=pk)
    return render(request, "payroll/slip.html", {"p": p, "crumbs": [("Payroll", "payroll:list"), ("Salary slip", None)]})


@perm_required("payroll.view_monthlypayroll")
def slip_pdf(request, pk):
    p = get_object_or_404(MonthlyPayroll.objects.select_related("employee"), pk=pk)
    resp = HttpResponse(slip_pdf_bytes(p), content_type="application/pdf")
    resp["Content-Disposition"] = f'attachment; filename="salary_slip_{p.employee.employee_code}_{p.year}_{p.month:02d}.pdf"'
    return resp


# ---------------------------------------------------------------- increments
@perm_required("payroll.view_salaryrevision")
def increments(request):
    form = RevisionForm(request.POST or None) if request.user.has_perm("payroll.add_salaryrevision") else None
    if request.method == "POST":
        if form is None:
            raise Http404
        if form.is_valid():
            e = form.cleaned_data["employee"]
            try:
                svc.add_revision(e, form.cleaned_data["revised_salary"], form.cleaned_data["effective_month"],
                                 form.cleaned_data["reason"], request.user)
                messages.success(request, f"Salary revision recorded for {e.name}.")
                return redirect("payroll:increments")
            except ValidationError as ex:
                form.add_error(None, ex)
    qs = SalaryRevision.objects.select_related("employee", "created_by").order_by("-effective_from", "employee__name")
    emp_id = request.GET.get("employee", "")
    if emp_id.isdigit():
        qs = qs.filter(employee_id=emp_id)
    page = Paginator(qs, 25).get_page(request.GET.get("page"))
    return render(request, "payroll/increments.html", {
        "page": page, "form": form, "emp_id": emp_id, "employees": Employee.objects.order_by("name"),
        "current": {e.id: str(e.salary) for e in Employee.objects.filter(is_active=True)},
        "crumbs": [("Salary increments", None)],
    })


@perm_required("payroll.view_salaryrevision")
def salary_history(request):
    emp = Employee.objects.filter(pk=request.GET.get("employee") or 0).first()
    revisions = payrolls = None
    if emp:
        revisions = emp.revisions.select_related("created_by").order_by("effective_from")
        payrolls = emp.payrolls.order_by("-year", "-month")
    return render(request, "payroll/salary_history.html", {
        "emp": emp, "revisions": revisions, "payrolls": payrolls, "employees": Employee.objects.order_by("name"),
        "crumbs": [("Salary history", None)],
    })


# ---------------------------------------------------------------- payments
@perm_required("payroll.view_salarypayment")
def payments(request):
    y, m = parse_period(request)
    status = request.GET.get("status", "")
    all_months = request.GET.get("all") == "1"
    qs = MonthlyPayroll.objects.filter(status=MonthlyPayroll.FINALIZED).select_related("employee").annotate(paid=Sum("payments__amount_paid"))
    if not all_months:
        qs = qs.filter(year=y, month=m)
    rows = [p for p in qs.order_by("-year", "-month", "employee__name") if not status or p.payment_status == status]
    totals = {"net": sum((p.net_salary for p in rows), Decimal(0)),
              "paid": sum((p.total_paid for p in rows), Decimal(0))}
    totals["balance"] = totals["net"] - totals["paid"]
    recent = SalaryPayment.objects.select_related("payroll__employee", "recorded_by")[:15]
    return render(request, "payroll/payments.html", {
        "rows": rows, "totals": totals, "y": y, "m": m, "status": status, "all_months": all_months,
        "years": range(date.today().year - 3, date.today().year + 2),
        "months": [(i, calendar.month_name[i]) for i in range(1, 13)],
        "form": PaymentForm(), "recent": recent,
        "can_overpay": request.user.has_perm("payroll.authorize_overpayment"),
        "crumbs": [("Salary payments", None)],
    })


@require_POST
@perm_required("payroll.add_salarypayment")
def pay(request, pk):
    p = get_object_or_404(MonthlyPayroll, pk=pk)
    form = PaymentForm(request.POST)
    if form.is_valid():
        try:
            svc.record_payment(pk, form.cleaned_data["amount_paid"], form.cleaned_data["payment_date"],
                               form.cleaned_data["payment_mode"], form.cleaned_data["transaction_reference"],
                               form.cleaned_data["remarks"], request.user,
                               allow_overpay=request.user.has_perm("payroll.authorize_overpayment"))
            messages.success(request, f"Payment recorded for {p.employee.name}.")
        except ValidationError as ex:
            _err(request, ex)
    else:
        for field, errs in form.errors.items():
            for e in errs:
                messages.error(request, f"{field}: {e}")
    nxt = request.POST.get("next", "")
    return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else reverse("payroll:detail", args=[pk]))
