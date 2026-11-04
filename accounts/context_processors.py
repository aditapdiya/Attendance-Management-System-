from django.conf import settings

def branding(request):
    return {"COMPANY_NAME": settings.COMPANY_NAME}
