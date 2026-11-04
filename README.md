# Vimal Agencies – Employee Attendance & Salary Management System

Django 5 · PostgreSQL · Bootstrap 5 · Chart.js · openpyxl · ReportLab

Admin-only web application: employees, daily attendance (AJAX), monthly payroll with
historical salary revisions, salary payments, PDF/Excel reports, dashboard and audit log.

## 1. Quick start (development)

```bash
python -m venv .venv && source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                      # then edit (see below)
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```
Open http://127.0.0.1:8000 and sign in. There is **no public registration**.

### PostgreSQL
```sql
CREATE USER vimal WITH PASSWORD 'change-me';
CREATE DATABASE vimal_agencies OWNER vimal;
```
Set `DB_*` in `.env` (defaults to PostgreSQL). `DB_ENGINE=sqlite` is for local trials/tests only.

### Required secrets (`.env`)
| Variable | Purpose |
|---|---|
| `DJANGO_SECRET_KEY` | Django signing key (required when `DJANGO_DEBUG=False`) |
| `AADHAAR_ENCRYPTION_KEY` | Fernet key for Aadhaar encryption. `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |
| `DJANGO_ALLOWED_HOSTS`, `DJANGO_CSRF_TRUSTED_ORIGINS` | Your domain(s), e.g. `https://payroll.example.com` |
| `SESSION_IDLE_TIMEOUT_MINUTES` | Idle session expiry (default 30) |

> **Back up `AADHAAR_ENCRYPTION_KEY`.** Without it stored Aadhaar numbers cannot be recovered.
> It is separate from the database, so a leaked DB dump alone does not expose Aadhaar numbers.

## 2. Running the tests
```bash
python manage.py test            # 75 tests; uses a throw-away key, works with sqlite or PostgreSQL
```
To run against PostgreSQL, set `DB_ENGINE=postgresql` and give the DB user `CREATEDB` permission.

## 3. Deployment (Linux)
```bash
export DJANGO_DEBUG=False
python manage.py migrate
python manage.py collectstatic --noinput
gunicorn config.wsgi:application --bind 127.0.0.1:8000 --workers 3
```
* Put Nginx/Caddy in front with HTTPS (the app redirects to HTTPS and sets secure cookies when `DEBUG=False`;
  the proxy must send `X-Forwarded-Proto`). Static files are served by WhiteNoise.
* Run `python manage.py check --deploy` after configuring.
* Use a shared cache (Redis/Memcached, via Django `CACHES`) if you run several workers, so the
  login-lockout counter (5 failures → 15 min) is shared. The default per-process cache works but is per worker.
* Schedule `pg_dump` backups of the database and keep a copy of `.env`.
* Bootstrap, Bootstrap Icons, Chart.js and Inter load from CDNs; vendor them into `static/` for offline/intranet use.

## 4. Users and permissions
All internal pages require a logged-in **staff** user. Create staff users and grant permissions in
**Django admin → Users** (`/django-admin/`, super-admins only). Suggested roles:

| Role | Permissions |
|---|---|
| Attendance clerk | `attendance.view_attendance`, `add_attendance` |
| Attendance supervisor | + `attendance.change_attendance` (edit existing records) |
| Payroll officer | `payroll.view/add/change_monthlypayroll`, `view_salarypayment`, `add_salarypayment`, `view_salaryrevision` |
| Payroll approver | + `payroll.finalize_payroll` |
| Salary admin | `payroll.add_salaryrevision` |
| Unlocker | `payroll.unlock_payroll` |
| Payment adjuster | `payroll.authorize_overpayment` |
| Auditor | `accounts.view_auditlog` |
Super-admins have everything. Payroll, payments, revisions and attendance are read-only in Django admin
so that every change goes through the validated application logic.

## 5. Business rules implemented
* **Per-day salary** = monthly salary ÷ actual calendar days of the month (28/29/30/31).
* **Unpaid-equivalent days** = absent + 0.5 × half-days + unpaid leave (+ days before joining in the joining month).
* **Deduction** = salary × unpaid-equivalent days ÷ calendar days, rounded **once** (ROUND_HALF_UP) with `Decimal`; **net** = salary − deduction.
* Weekly holidays (Sunday by default; configurable under *Admin settings*) are paid, never counted absent, and cannot be marked.
* Paid leave is never deducted. Unmarked working days are reported as **Not marked / missing** – never assumed present or absent.
  Payroll cannot be finalized while working days are unmarked.
* **Mid-month joiners**: days before the joining date are unpaid (not “absent”).
* **Salary revisions** (`SalaryRevision`): effective from the 1st of a month, strictly after the previous revision (no overlaps or
  back-dating), blocked if finalized payroll exists from that month on. Payroll uses the salary in force for the month. History is never overwritten.
  `Employee.salary` is a convenience copy of the salary currently in force. Salary is edited only via increments, not on the employee form.
* **Payroll**: unique per employee+month. Draft → Finalized. Finalizing requires the month to be complete, no missing attendance, and no
  pending “needs review” flag. If attendance (or salary) changes after generation the draft is flagged *Review* and must be explicitly recalculated.
  Attendance in a finalized month is locked.
* **Unlock** needs `unlock_payroll` and a reason, is audited, returns the payroll to a flagged draft, and is refused once payments exist.
* **Payments** are separate records (cash / bank / UPI, partial allowed). Amount may not exceed the balance unless the user holds
  `authorize_overpayment`; such entries need remarks and are flagged as adjustments.
* **Aadhaar**: validated (12 digits + Verhoeff checksum), encrypted with Fernet, uniqueness via keyed HMAC, shown masked
  (`XXXX XXXX 1234`), never written to logs or reports.
* Employees with attendance/payroll history cannot be deleted (DB `PROTECT` + checks); deactivate instead.

## 6. Project layout
```
config/      settings, urls         accounts/   login, audit log, settings, permissions helpers
employees/   model, Aadhaar crypto  attendance/ model, services, AJAX API, calendar
payroll/     models, services (all calculations), views, salary-slip PDF
reports/     builders (data) + exporters (PDF/Excel)        dashboard/  statistics & charts
templates/   shared base + per-app pages     static/  css, js
```
Business logic lives in `*/services.py`; views only handle HTTP.

## 7. Known limitations / decisions to review
* Attendance cannot be recorded for future dates (so advance-approved leave must be entered on the day).
* Holiday changes are not date-versioned: changing weekly holidays affects the calculation of *draft* payrolls only (finalized ones are snapshots).
* A salary decrease can be recorded as a revision (labelled with a negative increment).
* Salary-slip/report PDFs show “Rs.” because the built-in PDF fonts lack the ₹ glyph; HTML and Excel show ₹.
* No email/SMS, no employee self-service, single company (by design).
