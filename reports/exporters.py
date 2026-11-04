from datetime import date, datetime
from decimal import Decimal
from io import BytesIO

from django.conf import settings
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

NAVY, BLUE = "172554", "2563EB"


def fmt(v):
    if v is None or v == "":
        return ""
    if isinstance(v, Decimal):
        return f"{v:,.2f}"
    if isinstance(v, datetime):
        return v.strftime("%d %b %Y %H:%M")
    if isinstance(v, date):
        return v.strftime("%d %b %Y")
    return str(v)


def to_xlsx(rep) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = rep.title[:31]
    ws["A1"] = settings.COMPANY_NAME.upper()
    ws["A1"].font = Font(bold=True, size=16, color=NAVY)
    ws["A2"] = rep.title
    ws["A2"].font = Font(bold=True, size=13)
    ws["A3"] = rep.subtitle
    ws["A4"] = f"Generated on {datetime.now():%d %b %Y %H:%M}   |   Amounts in INR"
    ws["A4"].font = Font(italic=True, color="6B7280")
    r = 6
    for c, h in enumerate(rep.headers, 1):
        cell = ws.cell(r, c, h)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor=NAVY)
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

    def put(row, bold=False):
        nonlocal r
        r += 1
        for c, v in enumerate(row, 1):
            cell = ws.cell(r, c, float(v) if isinstance(v, Decimal) and False else v)
            if isinstance(v, Decimal):
                cell.value = v
                cell.number_format = "#,##0.00"
            elif isinstance(v, datetime):
                cell.value = v.replace(tzinfo=None)
                cell.number_format = "dd-mmm-yyyy hh:mm"
            elif isinstance(v, date):
                cell.number_format = "dd-mmm-yyyy"
            if bold:
                cell.font = Font(bold=True)
                cell.fill = PatternFill("solid", fgColor="DBEAFE")
    for row in rep.rows:
        put(row)
    if rep.totals:
        put(rep.totals, bold=True)
    if rep.summary:
        r += 1
        for k, v in rep.summary:
            r += 1
            ws.cell(r, 1, k).font = Font(bold=True)
            ws.cell(r, 2, v)
    for c in range(1, len(rep.headers) + 1):
        width = max([len(fmt(x[c - 1])) if c - 1 < len(x) else 0 for x in rep.rows + ([rep.totals] if rep.totals else [])] + [len(str(rep.headers[c - 1]))])
        ws.column_dimensions[get_column_letter(c)].width = min(max(width + 3, 10), 40)
    ws.freeze_panes = ws.cell(7, 1)
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def to_pdf(rep) -> bytes:
    buf = BytesIO()
    page = landscape(A4) if len(rep.headers) > 6 else A4
    doc = SimpleDocTemplate(buf, pagesize=page, leftMargin=12*mm, rightMargin=12*mm, topMargin=14*mm, bottomMargin=14*mm, title=rep.title)
    ss = getSampleStyleSheet()
    brand = ParagraphStyle("b", parent=ss["Title"], textColor=colors.HexColor("#" + NAVY), fontSize=18, alignment=0, spaceAfter=0)
    small = ParagraphStyle("sm", parent=ss["Normal"], fontSize=8.5, textColor=colors.grey)
    cell = ParagraphStyle("c", parent=ss["Normal"], fontSize=8, leading=10)
    story = [Paragraph(settings.COMPANY_NAME.upper(), brand), Paragraph(f"<b>{rep.title}</b>", ss["Heading3"]),
             Paragraph(rep.subtitle, ss["Normal"]),
             Paragraph(f"Generated on {datetime.now():%d %b %Y %H:%M} | Amounts in INR", small), Spacer(1, 4*mm)]
    data = [[Paragraph(f"<b>{h}</b>", ParagraphStyle("h", parent=cell, textColor=colors.white)) for h in rep.headers]]
    for row in rep.rows:
        data.append([Paragraph(fmt(v), cell) for v in row])
    if rep.totals:
        data.append([Paragraph(f"<b>{fmt(v)}</b>", cell) for v in rep.totals])
    if len(data) == 1:
        data.append([Paragraph("No records for the selected period.", cell)] + [""] * (len(rep.headers) - 1))
    t = Table(data, repeatRows=1)
    style = [("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#" + NAVY)), ("GRID", (0, 0), (-1, -1), 0.25, colors.lightgrey),
             ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F8FAFC")]), ("VALIGN", (0, 0), (-1, -1), "MIDDLE")]
    if rep.totals:
        style.append(("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#DBEAFE")))
    t.setStyle(TableStyle(style))
    story.append(t)
    if rep.summary:
        story += [Spacer(1, 5*mm), Paragraph("<b>Summary</b>", ss["Normal"])]
        for k, v in rep.summary:
            story.append(Paragraph(f"{k}: <b>{v}</b>", cell))

    def footer(canvas, d):
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(colors.grey)
        canvas.drawString(12*mm, 8*mm, f"{settings.COMPANY_NAME} – {rep.title}")
        canvas.drawRightString(page[0] - 12*mm, 8*mm, f"Page {d.page}")
        canvas.restoreState()
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buf.getvalue()
