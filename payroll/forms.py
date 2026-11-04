from datetime import date, datetime
from decimal import Decimal

from django import forms

from employees.models import Employee
from .models import SalaryPayment


class RevisionForm(forms.Form):
    employee = forms.ModelChoiceField(Employee.objects.filter(is_active=True).order_by("name"))
    revised_salary = forms.DecimalField(max_digits=12, decimal_places=2, min_value=Decimal("0.01"), label="Revised monthly salary (₹)")
    effective_month = forms.CharField(label="Effective from (month)", widget=forms.TextInput(attrs={"type": "month"}))
    reason = forms.CharField(max_length=255, required=False, widget=forms.TextInput(attrs={"placeholder": "e.g. Annual increment"}))

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.fields["employee"].queryset = Employee.objects.filter(is_active=True).order_by("name")
        for f in self.fields.values():
            f.widget.attrs.setdefault("class", "form-select" if isinstance(f, forms.ModelChoiceField) else "form-control")

    def clean_effective_month(self):
        try:
            return datetime.strptime(self.cleaned_data["effective_month"], "%Y-%m").date()
        except ValueError:
            raise forms.ValidationError("Choose a valid month.")


class PaymentForm(forms.ModelForm):
    class Meta:
        model = SalaryPayment
        fields = ["amount_paid", "payment_date", "payment_mode", "transaction_reference", "remarks"]
        widgets = {"payment_date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")}

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.fields["payment_date"].initial = date.today()
        for f in self.fields.values():
            f.widget.attrs.setdefault("class", "form-select" if isinstance(f.widget, forms.Select) else "form-control")
