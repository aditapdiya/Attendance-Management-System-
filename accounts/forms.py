from django import forms
from django.contrib.auth.forms import AuthenticationForm, PasswordChangeForm


class LoginForm(AuthenticationForm):
    username = forms.CharField(widget=forms.TextInput(attrs={"class": "form-control form-control-lg", "autofocus": True, "placeholder": "Username"}))
    password = forms.CharField(strip=False, widget=forms.PasswordInput(attrs={"class": "form-control form-control-lg", "placeholder": "Password"}))

    error_messages = {
        "invalid_login": "Incorrect username or password. Please try again.",
        "inactive": "This account is inactive.",
    }

    def confirm_login_allowed(self, user):
        super().confirm_login_allowed(user)
        if not user.is_staff:
            raise forms.ValidationError("This account is not authorised for the admin panel.", code="inactive")


class StyledPasswordChangeForm(PasswordChangeForm):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        for f in self.fields.values():
            f.widget.attrs["class"] = "form-control"
