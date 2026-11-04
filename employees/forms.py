from datetime import date
from decimal import Decimal

from django import forms
from django.core.exceptions import ValidationError

from . import crypto
from .models import Employee


class EmployeeForm(forms.ModelForm):
    aadhaar_number = forms.CharField(
        required=False, label="Aadhaar number", max_length=14,
        widget=forms.TextInput(attrs={"inputmode": "numeric", "autocomplete": "off", "placeholder": "12-digit Aadhaar"}),
        help_text="Stored encrypted. On edit, leave blank to keep the existing number.")
    initial_salary = forms.DecimalField(label="Monthly salary (₹)", max_digits=10, decimal_places=2,
                                        min_value=Decimal("0.01"), required=False)

    class Meta:
        model = Employee
        fields = ["name", "phone_number", "designation", "department", "joining_date",
                  "address", "notes"]
        widgets = {"joining_date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
                   "address": forms.Textarea(attrs={"rows": 2}), "notes": forms.Textarea(attrs={"rows": 2})}

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.creating = not self.instance.pk
        if self.creating:
            self.fields["initial_salary"].required = True
        else:
            del self.fields["initial_salary"]
            if self.instance.has_history():
                self.fields["joining_date"].disabled = True
                self.fields["joining_date"].help_text = "Locked: attendance or payroll exists."
            if self.instance.aadhaar_last4:
                self.fields["aadhaar_number"].widget.attrs["placeholder"] = self.instance.masked_aadhaar
        for n, f in self.fields.items():
            f.widget.attrs.setdefault("class", "form-control")

    def clean_name(self):
        return " ".join(self.cleaned_data["name"].split())

    def clean_joining_date(self):
        d = self.cleaned_data["joining_date"]
        if d > date.today():
            raise ValidationError("Joining date cannot be in the future.")
        return d

    def clean_aadhaar_number(self):
        raw = self.cleaned_data.get("aadhaar_number", "")
        if not raw.strip():
            return ""
        n = crypto.validate_aadhaar(raw)
        h = crypto.blind_index(n)
        if Employee.objects.filter(aadhaar_hash=h).exclude(pk=self.instance.pk).exists():
            raise ValidationError("An employee with this Aadhaar number already exists.")
        return n

    def save(self, commit=True):
        emp = super().save(commit=False)
        if self.cleaned_data.get("aadhaar_number"):
            emp.set_aadhaar(self.cleaned_data["aadhaar_number"])
        if self.creating:
            emp.salary = self.cleaned_data["initial_salary"]
        if commit:
            emp.save()
        return emp
