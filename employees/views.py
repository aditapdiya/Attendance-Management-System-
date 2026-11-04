from datetime import date

from django.contrib import messages
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from accounts.audit import audit
from accounts.decorators import perm_required
from attendance.models import Attendance
from payroll import services as pay
from payroll.models import MonthlyPayroll, SalaryPayment, SalaryRevision
from .forms import EmployeeForm
from .models import Employee


@perm_required("employees.view_employee")
def employee_list(request):
    qs = Employee.objects.all()
    q = request.GET.get("q", "").strip()
    status = request.GET.get("status", "")
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(employee_code__icontains=q) | Q(phone_number__icontains=q))
    if status == "active":
        qs = qs.filter(is_active=True)
    elif status == "inactive":
        qs = qs.filter(is_active=False)
    page = Paginator(qs, 15).get_page(request.GET.get("page"))
    return render(request, "employees/list.html", {"page": page, "q": q, "status": status,
                                                    "crumbs": [("Employees", None)]})


@perm_required("employees.add_employee")
def employee_add(request):
    form = EmployeeForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            emp = form.save()
            pay.create_initial_revision(emp, request.user)
            audit(request.user, "EMPLOYEE_CREATE", f"Created employee {emp.employee_code} - {emp.name}")
        messages.success(request, f"Employee {emp.name} ({emp.employee_code}) added.")
        return redirect("employees:profile", pk=emp.pk)
    return render(request, "employees/form.html", {"form": form, "title": "Add employee",
                  "crumbs": [("Employees", "employees:list"), ("Add employee", None)]})


@perm_required("employees.change_employee")
def employee_edit(request, pk):
    emp = get_object_or_404(Employee, pk=pk)
    old_join = emp.joining_date
    form = EmployeeForm(request.POST or None, instance=emp)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            emp = form.save()
            if emp.joining_date != old_join:
                pay.shift_initial_revision(emp)
            audit(request.user, "EMPLOYEE_UPDATE", f"Updated employee {emp.employee_code}"
                  + (" (Aadhaar replaced)" if form.cleaned_data.get("aadhaar_number") else ""))
        messages.success(request, "Employee updated.")
        return redirect("employees:profile", pk=emp.pk)
    return render(request, "employees/form.html", {"form": form, "title": f"Edit {emp.name}", "employee": emp,
                  "crumbs": [("Employees", "employees:list"), (emp.name, ("employees:profile", emp.pk)), ("Edit", None)]})


@perm_required("employees.view_employee")
def employee_profile(request, pk):
    emp = get_object_or_404(Employee, pk=pk)
    today = date.today()
    month_start = today.replace(day=1)
    stats = {r["status"]: r["n"] for r in emp.attendance_records.filter(
        attendance_date__gte=month_start, attendance_date__lte=today).values("status").annotate(n=Count("id"))}
    att_page = Paginator(emp.attendance_records.order_by("-attendance_date"), 10).get_page(request.GET.get("apage"))
    payrolls = emp.payrolls.annotate(paid=Sum("payments__amount_paid")).order_by("-year", "-month")
    payments = SalaryPayment.objects.filter(payroll__employee=emp).select_related("payroll").order_by("-payment_date", "-id")[:25]
    revisions = list(emp.revisions.select_related("created_by").order_by("-effective_from"))
    cur_found = False
    for r in revisions:
        if r.effective_from > today:
            r.state = "future"
        elif not cur_found:
            r.state, cur_found = "current", True
        else:
            r.state = ""
    return render(request, "employees/profile.html", {
        "emp": emp, "stats": stats, "att_page": att_page, "payrolls": payrolls, "payments": payments,
        "revisions": revisions,
        "current_salary": pay.salary_for_month(emp, today.year, today.month),
        "crumbs": [("Employees", "employees:list"), (emp.name, None)],
    })


@require_POST
@perm_required("employees.change_employee")
def employee_toggle(request, pk):
    emp = get_object_or_404(Employee, pk=pk)
    emp.is_active = not emp.is_active
    emp.save(update_fields=["is_active", "updated_at"])
    verb = "reactivated" if emp.is_active else "deactivated"
    audit(request.user, "EMPLOYEE_STATUS", f"Employee {emp.employee_code} {verb}")
    messages.success(request, f"{emp.name} {verb}.")
    nxt = request.POST.get("next", "")
    if nxt.startswith("/") and not nxt.startswith("//"):
        return redirect(nxt)
    return redirect("employees:profile", pk=emp.pk)


@require_POST
@perm_required("employees.delete_employee")
def employee_delete(request, pk):
    emp = get_object_or_404(Employee, pk=pk)
    if emp.has_history():
        messages.error(request, "This employee has attendance or payroll history and cannot be deleted. Deactivate instead.")
        return redirect("employees:profile", pk=pk)
    code, name = emp.employee_code, emp.name
    emp.delete()
    audit(request.user, "EMPLOYEE_DELETE", f"Deleted employee {code} - {name} (no history)")
    messages.success(request, f"{name} deleted.")
    return redirect("employees:list")
