from io import BytesIO

from django.conf import settings
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

NAVY = colors.HexColor("#172554")
BLUE = colors.HexColor("#2563EB")


def rs(x):  # Helvetica has no ₹ glyph, so PDFs use "Rs."
    return f"Rs. {x:,.2f}"


def slip_pdf_bytes(p):
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=18*mm, rightMargin=18*mm, topMargin=16*mm, bottomMargin=16*mm,
                            title=f"Salary slip {p.employee.employee_code} {p.period}")
    ss = getSampleStyleSheet()
    h = ParagraphStyle("h", parent=ss["Title"], textColor=NAVY, fontSize=20, spaceAfter=2)
    sub = ParagraphStyle("s", parent=ss["Normal"], textColor=colors.grey, alignment=1)
    e = p.employee
    story = [Paragraph(settings.COMPANY_NAME.upper(), h), Paragraph(f"Salary Slip – {p.period}", sub)]
    if not p.is_final:
        story.append(Paragraph("<font color='#DC2626'><b>DRAFT – not finalized</b></font>", ParagraphStyle("d", alignment=1)))
    story.append(Spacer(1, 8*mm))
    info = [["Employee ID", e.employee_code, "Designation", e.designation],
            ["Name", e.name, "Department", e.department or "-"],
            ["Joining date", f"{e.joining_date:%d %b %Y}", "Payment status", p.payment_status if p.is_final else "-"]]
    t = Table(info, colWidths=[30*mm, 55*mm, 30*mm, 55*mm])
    t.setStyle(TableStyle([("FONTSIZE", (0, 0), (-1, -1), 9), ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                           ("FONTNAME", (2, 0), (2, -1), "Helvetica-Bold"), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                           ("BOX", (0, 0), (-1, -1), 0.5, colors.lightgrey)]))
    story += [t, Spacer(1, 6*mm)]
    att = [["Attendance summary", "Days"],
           ["Calendar days in month", p.total_calendar_days], ["Present", p.present_days], ["Absent", p.absent_days],
           ["Half days", p.half_days], ["Paid leave", p.paid_leave_days], ["Unpaid leave", p.unpaid_leave_days],
           ["Weekly holidays (paid)", p.holiday_days], ["Days before joining (unpaid)", p.pre_joining_days],
           ["Payable days", p.payable_days]]
    a = Table(att, colWidths=[110*mm, 60*mm])
    a.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), NAVY), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                           ("FONTSIZE", (0, 0), (-1, -1), 9), ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                           ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F8FAFC")]),
                           ("GRID", (0, 0), (-1, -1), 0.25, colors.lightgrey), ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold")]))
    story += [a, Spacer(1, 6*mm)]
    pay = [["Salary calculation", "Amount"],
           ["Monthly salary (applicable this month)", rs(p.gross_salary)],
           ["Per-day salary (salary / calendar days)", rs(p.per_day_salary)],
           [f"Deduction ({p.unpaid_equivalent_days} unpaid-equivalent day(s))", "- " + rs(p.deductions)],
           ["NET SALARY", rs(p.net_salary)]]
    s = Table(pay, colWidths=[110*mm, 60*mm])
    s.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), BLUE), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                           ("FONTSIZE", (0, 0), (-1, -1), 10), ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                           ("GRID", (0, 0), (-1, -1), 0.25, colors.lightgrey),
                           ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"), ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#EFF6FF"))]))
    story += [s, Spacer(1, 12*mm),
              Paragraph("This is a computer-generated salary slip.", ParagraphStyle("f", parent=ss["Normal"], fontSize=8, textColor=colors.grey))]
    doc.build(story)
    return buf.getvalue()
