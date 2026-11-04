from functools import wraps
from django.core.exceptions import PermissionDenied


def staff_required(view):
    """Admin-only: active staff users. Permissions are layered on top per view."""
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not (request.user.is_authenticated and request.user.is_active and request.user.is_staff):
            raise PermissionDenied
        return view(request, *args, **kwargs)
    return wrapper


def perm_required(*perms):
    """Staff + all listed permissions (superusers pass)."""
    def deco(view):
        @wraps(view)
        @staff_required
        def wrapper(request, *args, **kwargs):
            if not request.user.has_perms(perms):
                raise PermissionDenied
            return view(request, *args, **kwargs)
        return wrapper
    return deco
