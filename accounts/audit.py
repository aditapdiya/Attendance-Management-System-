from .models import AuditLog


def audit(user, action, description):
    """Record an audit entry. Never pass Aadhaar numbers in description."""
    if user is not None and not getattr(user, "is_authenticated", False):
        user = None
    return AuditLog.objects.create(user=user, action=action, description=description[:2000])
