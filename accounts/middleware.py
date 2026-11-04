from django.conf import settings
from django.contrib.auth.views import redirect_to_login
from django.shortcuts import resolve_url

PUBLIC_PREFIXES = ("/login/", "/static/", "/django-admin/")


class LoginRequiredMiddleware:
    """Every internal page requires an authenticated staff user."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path
        if not path.startswith(PUBLIC_PREFIXES) and not request.user.is_authenticated:
            if request.headers.get("x-requested-with") == "XMLHttpRequest" or \
                    "application/json" in request.headers.get("accept", ""):
                from django.http import JsonResponse
                return JsonResponse({"ok": False, "error": "Session expired. Please log in again."}, status=401)
            return redirect_to_login(request.get_full_path(), resolve_url(settings.LOGIN_URL))
        return self.get_response(request)
