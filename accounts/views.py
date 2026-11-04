from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.views import LoginView, LogoutView
from django.core.cache import cache
from django.core.paginator import Paginator
from django.shortcuts import redirect, render
from django.urls import reverse_lazy
from django.views.decorators.http import require_POST

from attendance.models import WeeklyHoliday
from .audit import audit
from .decorators import perm_required, staff_required
from .forms import LoginForm, StyledPasswordChangeForm
from .models import AuditLog

MAX_FAILS, LOCK_SECONDS = 5, 15 * 60


def _key(request, username):
    ip = request.META.get("REMOTE_ADDR", "")
    return f"loginfail:{ip}:{(username or '').lower()}"


class AdminLoginView(LoginView):
    template_name = "accounts/login.html"
    authentication_form = LoginForm
    redirect_authenticated_user = True

    def post(self, request, *a, **kw):
        if cache.get(_key(request, request.POST.get("username")), 0) >= MAX_FAILS:
            messages.error(request, "Too many failed attempts. Please try again in 15 minutes.")
            return self.get(request, *a, **kw)
        return super().post(request, *a, **kw)

    def form_invalid(self, form):
        k = _key(self.request, self.request.POST.get("username"))
        cache.set(k, cache.get(k, 0) + 1, LOCK_SECONDS)
        audit(None, "LOGIN_FAILED", f"Failed login for username '{self.request.POST.get('username', '')[:50]}'")
        return super().form_invalid(form)

    def form_valid(self, form):
        cache.delete(_key(self.request, self.request.POST.get("username")))
        resp = super().form_valid(form)
        audit(self.request.user, "LOGIN", "User logged in")
        return resp


class AdminLogoutView(LogoutView):
    http_method_names = ["post", "options"]

    def post(self, request, *a, **kw):
        audit(request.user, "LOGOUT", "User logged out")
        return super().post(request, *a, **kw)


@staff_required
def password_change(request):
    form = StyledPasswordChangeForm(request.user, request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        update_session_auth_hash(request, user)
        audit(request.user, "PASSWORD_CHANGE", "Password changed")
        messages.success(request, "Your password was changed.")
        return redirect("dashboard:home")
    return render(request, "accounts/password_change.html", {"form": form, "crumbs": [("Change password", None)]})


@perm_required("accounts.view_settings_page")
def settings_page(request):
    if request.method == "POST":
        days = sorted({int(d) for d in request.POST.getlist("weekdays") if d.isdigit() and 0 <= int(d) <= 6})
        old = list(WeeklyHoliday.objects.values_list("weekday", flat=True))
        WeeklyHoliday.objects.exclude(weekday__in=days).delete()
        for d in days:
            WeeklyHoliday.objects.get_or_create(weekday=d)
        audit(request.user, "SETTINGS", f"Weekly holidays changed from {sorted(old)} to {days}")
        messages.success(request, "Weekly holidays updated. Finalized payrolls are unaffected; review draft payrolls.")
        return redirect("accounts:settings")
    selected = set(WeeklyHoliday.objects.values_list("weekday", flat=True))
    names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    return render(request, "accounts/settings.html", {
        "weekdays": [(i, n, i in selected) for i, n in enumerate(names)],
        "crumbs": [("Admin settings", None)],
    })


@perm_required("accounts.view_auditlog")
def audit_log(request):
    qs = AuditLog.objects.select_related("user")
    q = request.GET.get("q", "").strip()
    action = request.GET.get("action", "")
    if q:
        qs = qs.filter(description__icontains=q) | qs.filter(user__username__icontains=q)
    if action:
        qs = qs.filter(action=action)
    page = Paginator(qs, 30).get_page(request.GET.get("page"))
    actions = AuditLog.objects.order_by().values_list("action", flat=True).distinct()
    return render(request, "accounts/audit_log.html", {
        "page": page, "q": q, "action": action, "actions": sorted(actions),
        "crumbs": [("Audit log", None)],
    })


def error_404(request, exception):
    return render(request, "404.html", status=404)


def error_500(request):
    return render(request, "500.html", status=500)
