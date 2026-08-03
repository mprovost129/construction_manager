from html import escape
from io import BytesIO

from django.utils.formats import date_format
from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    KeepTogether,
    LongTable,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from projects.models import OrganizationMembership

from .models import Invoice

PRIMARY = colors.HexColor('#174f3a')
PRIMARY_DARK = colors.HexColor('#103c2b')
ACCENT = colors.HexColor('#dfb95f')
INK = colors.HexColor('#17231d')
MUTED = colors.HexColor('#68756e')
BORDER = colors.HexColor('#dfe4e0')
CANVAS = colors.HexColor('#f3f5f2')
WHITE = colors.white
WARNING = colors.HexColor('#9a6700')
DANGER = colors.HexColor('#9f2d2d')


def _safe(value):
    return escape(str(value or ''))


def _money(value):
    return f'${value:,.2f}'


def _date(value):
    return date_format(value, 'F j, Y') if value else '-'


def _client_lines(invoice):
    memberships = invoice.project.project_memberships.filter(
        role=OrganizationMembership.Role.CLIENT,
        is_active=True,
    ).select_related('user')
    lines = []
    for membership in memberships:
        name = membership.user.get_full_name()
        lines.append(_safe(name or membership.user.email))
        if name:
            lines.append(_safe(membership.user.email))
    return lines or ['Project client']


def _styles():
    base = getSampleStyleSheet()
    return {
        'body': ParagraphStyle(
            'InvoiceBody',
            parent=base['BodyText'],
            fontName='Helvetica',
            fontSize=9,
            leading=13,
            textColor=INK,
            spaceAfter=0,
        ),
        'small': ParagraphStyle(
            'InvoiceSmall',
            parent=base['BodyText'],
            fontName='Helvetica',
            fontSize=8,
            leading=11,
            textColor=MUTED,
            spaceAfter=0,
        ),
        'label': ParagraphStyle(
            'InvoiceLabel',
            parent=base['BodyText'],
            fontName='Helvetica-Bold',
            fontSize=7,
            leading=9,
            textColor=MUTED,
            uppercase=True,
            spaceAfter=4,
        ),
        'heading': ParagraphStyle(
            'InvoiceHeading',
            parent=base['Heading2'],
            fontName='Helvetica-Bold',
            fontSize=13,
            leading=16,
            textColor=INK,
            spaceBefore=0,
            spaceAfter=8,
        ),
        'title': ParagraphStyle(
            'InvoiceTitle',
            parent=base['Heading1'],
            fontName='Helvetica-Bold',
            fontSize=25,
            leading=28,
            alignment=TA_RIGHT,
            textColor=PRIMARY_DARK,
            spaceAfter=2,
        ),
        'right': ParagraphStyle(
            'InvoiceRight',
            parent=base['BodyText'],
            fontName='Helvetica',
            fontSize=9,
            leading=12,
            alignment=TA_RIGHT,
            textColor=INK,
        ),
        'right_bold': ParagraphStyle(
            'InvoiceRightBold',
            parent=base['BodyText'],
            fontName='Helvetica-Bold',
            fontSize=9,
            leading=12,
            alignment=TA_RIGHT,
            textColor=INK,
        ),
        'table_header': ParagraphStyle(
            'InvoiceTableHeader',
            parent=base['BodyText'],
            fontName='Helvetica-Bold',
            fontSize=8,
            leading=10,
            textColor=WHITE,
        ),
        'table_header_right': ParagraphStyle(
            'InvoiceTableHeaderRight',
            parent=base['BodyText'],
            fontName='Helvetica-Bold',
            fontSize=8,
            leading=10,
            alignment=TA_RIGHT,
            textColor=WHITE,
        ),
    }


def _status_table(invoice, styles, width):
    palette = {
        Invoice.Status.DRAFT: (CANVAS, MUTED),
        Invoice.Status.ISSUED: (colors.HexColor('#fff4d6'), WARNING),
        Invoice.Status.PARTIALLY_PAID: (colors.HexColor('#fff4d6'), WARNING),
        Invoice.Status.PAID: (colors.HexColor('#e6f4ec'), PRIMARY),
        Invoice.Status.VOIDED: (colors.HexColor('#fce8e8'), DANGER),
    }
    background, foreground = palette[invoice.status]
    copy = invoice.get_status_display().upper()
    if invoice.status == Invoice.Status.VOIDED:
        copy += f' - {_safe(invoice.void_reason)}'
    status_style = ParagraphStyle(
        'InvoiceStatus',
        parent=styles['body'],
        fontName='Helvetica-Bold',
        textColor=foreground,
    )
    table = Table(
        [[Paragraph(copy, status_style)]],
        colWidths=[width],
    )
    table.setStyle(
        TableStyle(
            [
                ('BACKGROUND', (0, 0), (-1, -1), background),
                ('TEXTCOLOR', (0, 0), (-1, -1), foreground),
                ('BOX', (0, 0), (-1, -1), 0.75, foreground),
                ('LEFTPADDING', (0, 0), (-1, -1), 10),
                ('RIGHTPADDING', (0, 0), (-1, -1), 10),
                ('TOPPADDING', (0, 0), (-1, -1), 7),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 7),
            ]
        )
    )
    return table


def _page_decor(invoice):
    watermark = None
    if invoice.status == Invoice.Status.DRAFT:
        watermark = 'DRAFT'
    elif invoice.status == Invoice.Status.VOIDED:
        watermark = 'VOID'

    def draw(canvas, doc):
        canvas.saveState()
        canvas.setTitle(f'{invoice.display_number} - {invoice.title}')
        canvas.setAuthor(invoice.organization.name)
        canvas.setSubject(f'Invoice for {invoice.project.name}')
        if watermark:
            canvas.setFillColor(colors.HexColor('#edf0ed'))
            canvas.setFont('Helvetica-Bold', 72)
            canvas.translate(letter[0] / 2, letter[1] / 2)
            canvas.rotate(35)
            canvas.drawCentredString(0, 0, watermark)
            canvas.rotate(-35)
            canvas.translate(-letter[0] / 2, -letter[1] / 2)
        canvas.setStrokeColor(BORDER)
        canvas.line(doc.leftMargin, 0.48 * inch, letter[0] - doc.rightMargin, 0.48 * inch)
        canvas.setFillColor(MUTED)
        canvas.setFont('Helvetica', 7.5)
        canvas.drawString(doc.leftMargin, 0.29 * inch, 'Construction Manager')
        canvas.drawRightString(
            letter[0] - doc.rightMargin,
            0.29 * inch,
            f'Page {doc.page}',
        )
        canvas.restoreState()

    return draw


def build_invoice_pdf(invoice):
    buffer = BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        rightMargin=0.65 * inch,
        leftMargin=0.65 * inch,
        topMargin=0.55 * inch,
        bottomMargin=0.68 * inch,
        title=f'{invoice.display_number} - {invoice.title}',
        author=invoice.organization.name,
        subject=f'Invoice for {invoice.project.name}',
    )
    styles = _styles()
    width = document.width
    story = []

    brand = Paragraph(
        f'<font size="17"><b>{_safe(invoice.organization.name)}</b></font><br/>'
        '<font color="#68756e" size="8">CONSTRUCTION MANAGEMENT</font>',
        styles['body'],
    )
    invoice_heading = Paragraph(
        f'INVOICE<br/><font size="10" color="#68756e">'
        f'{_safe(invoice.display_number)}</font>',
        styles['title'],
    )
    header = Table([[brand, invoice_heading]], colWidths=[width * 0.58, width * 0.42])
    header.setStyle(
        TableStyle(
            [
                ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                ('LEFTPADDING', (0, 0), (-1, -1), 0),
                ('RIGHTPADDING', (0, 0), (-1, -1), 0),
                ('TOPPADDING', (0, 0), (-1, -1), 0),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
            ]
        )
    )
    story.extend([header, Spacer(1, 0.2 * inch), _status_table(invoice, styles, width)])
    story.append(Spacer(1, 0.25 * inch))

    project_code = f' ({_safe(invoice.project.code)})' if invoice.project.code else ''
    bill_to = '<br/>'.join(_client_lines(invoice))
    project_info = (
        f'<b>{_safe(invoice.project.name)}</b>{project_code}<br/>'
        f'{_safe(invoice.title)}'
    )
    details = Table(
        [
            [
                Paragraph('BILL TO', styles['label']),
                Paragraph('PROJECT', styles['label']),
                Paragraph('DATES', styles['label']),
            ],
            [
                Paragraph(bill_to, styles['body']),
                Paragraph(project_info, styles['body']),
                Paragraph(
                    f'<b>Issued:</b> {_date(invoice.issue_date)}<br/>'
                    f'<b>Due:</b> {_date(invoice.due_date)}',
                    styles['body'],
                ),
            ],
        ],
        colWidths=[width * 0.33, width * 0.36, width * 0.31],
    )
    details.setStyle(
        TableStyle(
            [
                ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                ('LINEBELOW', (0, 0), (-1, 0), 0.75, BORDER),
                ('LEFTPADDING', (0, 0), (-1, -1), 0),
                ('RIGHTPADDING', (0, 0), (-1, -1), 12),
                ('TOPPADDING', (0, 0), (-1, 0), 0),
                ('BOTTOMPADDING', (0, 0), (-1, 0), 5),
                ('TOPPADDING', (0, 1), (-1, 1), 7),
                ('BOTTOMPADDING', (0, 1), (-1, 1), 3),
            ]
        )
    )
    story.extend([details, Spacer(1, 0.28 * inch)])

    line_rows = [
        [
            Paragraph('Category', styles['table_header']),
            Paragraph('Description', styles['table_header']),
            Paragraph('Qty', styles['table_header']),
            Paragraph('Unit price', styles['table_header_right']),
            Paragraph('Amount', styles['table_header_right']),
        ]
    ]
    for line in invoice.line_items.all():
        line_rows.append(
            [
                Paragraph(_safe(line.get_category_display()), styles['small']),
                Paragraph(_safe(line.description), styles['body']),
                Paragraph(f'{line.quantity:g}', styles['small']),
                Paragraph(_money(line.unit_price), styles['right']),
                Paragraph(_money(line.total_amount), styles['right_bold']),
            ]
        )
    line_table = LongTable(
        line_rows,
        colWidths=[width * 0.18, width * 0.38, width * 0.09, width * 0.17, width * 0.18],
        repeatRows=1,
    )
    line_style = [
        ('BACKGROUND', (0, 0), (-1, 0), PRIMARY_DARK),
        ('TEXTCOLOR', (0, 0), (-1, 0), WHITE),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 7),
        ('RIGHTPADDING', (0, 0), (-1, -1), 7),
        ('TOPPADDING', (0, 0), (-1, 0), 7),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 7),
        ('TOPPADDING', (0, 1), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 1), (-1, -1), 8),
        ('LINEBELOW', (0, 1), (-1, -1), 0.5, BORDER),
    ]
    for row_number in range(2, len(line_rows), 2):
        line_style.append(('BACKGROUND', (0, row_number), (-1, row_number), CANVAS))
    line_table.setStyle(TableStyle(line_style))
    story.extend([line_table, Spacer(1, 0.22 * inch)])

    totals = Table(
        [
            [Paragraph('Subtotal', styles['body']), Paragraph(_money(invoice.subtotal_amount), styles['right'])],
            [Paragraph(f'Tax ({invoice.tax_rate}%)', styles['body']), Paragraph(_money(invoice.tax_amount), styles['right'])],
            [Paragraph('<b>Total</b>', styles['body']), Paragraph(f'<b>{_money(invoice.total_amount)}</b>', styles['right'])],
            [Paragraph('Paid', styles['body']), Paragraph(_money(invoice.amount_paid), styles['right'])],
            [Paragraph('<b>Balance due</b>', styles['body']), Paragraph(f'<b>{_money(invoice.balance_due)}</b>', styles['right'])],
        ],
        colWidths=[1.45 * inch, 1.2 * inch],
        hAlign='RIGHT',
    )
    totals.setStyle(
        TableStyle(
            [
                ('LEFTPADDING', (0, 0), (-1, -1), 8),
                ('RIGHTPADDING', (0, 0), (-1, -1), 8),
                ('TOPPADDING', (0, 0), (-1, -1), 6),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
                ('LINEABOVE', (0, 2), (-1, 2), 0.75, BORDER),
                ('BACKGROUND', (0, 4), (-1, 4), ACCENT),
                ('TEXTCOLOR', (0, 4), (-1, 4), PRIMARY_DARK),
                ('BOX', (0, 4), (-1, 4), 0.75, PRIMARY_DARK),
            ]
        )
    )
    story.append(totals)

    if invoice.notes:
        notes = Paragraph(_safe(invoice.notes).replace('\n', '<br/>'), styles['body'])
        story.extend(
            [
                Spacer(1, 0.3 * inch),
                KeepTogether(
                    [
                        Paragraph('NOTES', styles['label']),
                        Table(
                            [[notes]],
                            colWidths=[width],
                            style=TableStyle(
                                [
                                    ('BACKGROUND', (0, 0), (-1, -1), CANVAS),
                                    ('BOX', (0, 0), (-1, -1), 0.5, BORDER),
                                    ('LEFTPADDING', (0, 0), (-1, -1), 10),
                                    ('RIGHTPADDING', (0, 0), (-1, -1), 10),
                                    ('TOPPADDING', (0, 0), (-1, -1), 9),
                                    ('BOTTOMPADDING', (0, 0), (-1, -1), 9),
                                ]
                            ),
                        ),
                    ]
                ),
            ]
        )

    document.build(
        story,
        onFirstPage=_page_decor(invoice),
        onLaterPages=_page_decor(invoice),
    )
    return buffer.getvalue()
