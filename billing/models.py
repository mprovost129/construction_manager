from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

from projects.models import (
    ChangeOrder,
    CostCode,
    FinishSelection,
    Organization,
    Project,
)
from projects.money import money


class Invoice(models.Model):
    class Status(models.TextChoices):
        DRAFT = 'draft', 'Draft'
        ISSUED = 'issued', 'Issued'
        PARTIALLY_PAID = 'partially_paid', 'Partially paid'
        PAID = 'paid', 'Paid'
        VOIDED = 'voided', 'Voided'

    organization = models.ForeignKey(
        Organization,
        on_delete=models.PROTECT,
        related_name='invoices',
    )
    project = models.ForeignKey(
        Project,
        on_delete=models.PROTECT,
        related_name='invoices',
    )
    number = models.PositiveBigIntegerField(null=True, blank=True)
    title = models.CharField(max_length=200)
    notes = models.TextField(blank=True)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT,
    )
    issue_date = models.DateField(null=True, blank=True)
    due_date = models.DateField(null=True, blank=True)
    subtotal_amount = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=0,
    )
    tax_rate = models.DecimalField(
        max_digits=6,
        decimal_places=3,
        default=Decimal('0'),
        help_text='Tax percentage, e.g. 7.250 for 7.25%. Tax is computed automatically from this rate.',
    )
    tax_amount = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=0,
    )
    total_amount = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=0,
    )
    amount_paid = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=0,
    )
    source_change_order = models.OneToOneField(
        ChangeOrder,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='invoice',
    )
    source_selection = models.OneToOneField(
        FinishSelection,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='invoice',
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='invoices_created',
    )
    issued_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='invoices_issued',
    )
    issued_at = models.DateTimeField(null=True, blank=True)
    voided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='invoices_voided',
    )
    voided_at = models.DateTimeField(null=True, blank=True)
    void_reason = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ('-number', '-created_at', '-pk')
        constraints = [
            models.UniqueConstraint(
                fields=('organization', 'number'),
                condition=Q(number__isnull=False),
                name='billing_unique_invoice_number_per_org',
            ),
            models.CheckConstraint(
                condition=Q(subtotal_amount__gte=0),
                name='billing_invoice_subtotal_nonnegative',
            ),
            models.CheckConstraint(
                condition=Q(tax_amount__gte=0),
                name='billing_invoice_tax_nonnegative',
            ),
            models.CheckConstraint(
                condition=Q(tax_rate__gte=0) & Q(tax_rate__lte=100),
                name='billing_invoice_tax_rate_within_range',
            ),
            models.CheckConstraint(
                condition=Q(total_amount__gte=0),
                name='billing_invoice_total_nonnegative',
            ),
            models.CheckConstraint(
                condition=Q(amount_paid__gte=0) & Q(amount_paid__lte=models.F('total_amount')),
                name='billing_invoice_paid_within_total',
            ),
            models.CheckConstraint(
                condition=~(
                    Q(source_change_order__isnull=False)
                    & Q(source_selection__isnull=False)
                ),
                name='billing_invoice_one_source_type',
            ),
            models.CheckConstraint(
                condition=(
                    Q(
                        status='draft',
                        number__isnull=True,
                        issue_date__isnull=True,
                        issued_by__isnull=True,
                        issued_at__isnull=True,
                    )
                    | Q(
                        status__in=('issued', 'partially_paid', 'paid', 'voided'),
                        number__isnull=False,
                        issue_date__isnull=False,
                        issued_by__isnull=False,
                        issued_at__isnull=False,
                    )
                ),
                name='billing_invoice_issue_fields_match_status',
            ),
            models.CheckConstraint(
                condition=(
                    Q(
                        status='voided',
                        voided_by__isnull=False,
                        voided_at__isnull=False,
                    )
                    | (
                        ~Q(status='voided')
                        & Q(
                            voided_by__isnull=True,
                            voided_at__isnull=True,
                            void_reason='',
                        )
                    )
                ),
                name='billing_invoice_void_fields_match_status',
            ),
            models.CheckConstraint(
                condition=(
                    Q(status__in=('draft', 'issued', 'voided'), amount_paid=0)
                    | (
                        Q(status='partially_paid')
                        & Q(amount_paid__gt=0)
                        & Q(amount_paid__lt=models.F('total_amount'))
                    )
                    | Q(status='paid', amount_paid=models.F('total_amount'))
                ),
                name='billing_invoice_payment_matches_status',
            ),
            models.CheckConstraint(
                condition=Q(status='draft') | Q(total_amount__gt=0),
                name='billing_issued_invoice_total_positive',
            ),
        ]

    @property
    def display_number(self):
        return f'INV-{self.number:06d}' if self.number is not None else 'Draft invoice'

    @property
    def balance_due(self):
        return money(max(self.total_amount - self.amount_paid, Decimal('0')))

    @property
    def client_visible(self):
        return self.status != self.Status.DRAFT

    @property
    def is_editable(self):
        return self.status == self.Status.DRAFT

    def recalculate_totals(self, *, save=True):
        subtotal = sum(
            (line.total_amount for line in self.line_items.all()),
            Decimal('0'),
        )
        self.subtotal_amount = money(subtotal)
        self.tax_amount = money(self.subtotal_amount * self.tax_rate / Decimal('100'))
        self.total_amount = money(self.subtotal_amount + self.tax_amount)
        if save:
            self.save(
                update_fields=(
                    'subtotal_amount',
                    'tax_rate',
                    'tax_amount',
                    'total_amount',
                    'updated_at',
                )
            )

    def clean(self):
        super().clean()
        self.title = self.title.strip()
        self.notes = self.notes.strip()
        self.void_reason = self.void_reason.strip()
        errors = {}
        if not self.title:
            errors['title'] = 'Invoice title cannot be blank.'
        if self.project_id and self.organization_id:
            if self.project.organization_id != self.organization_id:
                errors['project'] = 'The invoice project must belong to its company.'
        if self.source_change_order_id:
            if self.source_change_order.project_id != self.project_id:
                errors['source_change_order'] = (
                    'The change order must belong to the invoice project.'
                )
            if self.source_change_order.status != ChangeOrder.Status.APPROVED:
                errors['source_change_order'] = (
                    'Only an approved change order can originate an invoice.'
                )
        if self.source_selection_id:
            if self.source_selection.project_id != self.project_id:
                errors['source_selection'] = (
                    'The selection must belong to the invoice project.'
                )
            if self.source_selection.status != FinishSelection.Status.SELECTED:
                errors['source_selection'] = (
                    'Only a completed selection can originate an invoice.'
                )
        if self.source_change_order_id and self.source_selection_id:
            errors['source_selection'] = 'An invoice can have only one originating record.'
        if self.tax_amount < 0:
            errors['tax_amount'] = 'Tax cannot be negative.'
        if self.tax_rate < 0 or self.tax_rate > 100:
            errors['tax_rate'] = 'Tax rate must be between 0 and 100 percent.'
        if self.amount_paid < 0 or self.amount_paid > self.total_amount:
            errors['amount_paid'] = 'Paid amount must be between zero and the total.'
        if self.due_date and self.issue_date and self.due_date < self.issue_date:
            errors['due_date'] = 'Due date cannot be before the issue date.'

        issued_statuses = {
            self.Status.ISSUED,
            self.Status.PARTIALLY_PAID,
            self.Status.PAID,
            self.Status.VOIDED,
        }
        if self.status == self.Status.DRAFT:
            if self.number is not None or self.issue_date or self.issued_by_id or self.issued_at:
                errors['status'] = 'Draft invoices cannot have issue details.'
            if self.amount_paid:
                errors['amount_paid'] = 'Draft invoices cannot have payments.'
        elif self.status in issued_statuses:
            if self.number is None or not self.issue_date or not self.issued_by_id or not self.issued_at:
                errors['status'] = 'Issued invoices require a number and issue details.'
            if self.total_amount <= 0:
                errors['total_amount'] = 'An issued invoice must have a positive total.'

        if self.status == self.Status.ISSUED and self.amount_paid:
            errors['amount_paid'] = 'An issued invoice cannot have a paid amount.'
        elif self.status == self.Status.PARTIALLY_PAID and not (
            0 < self.amount_paid < self.total_amount
        ):
            errors['amount_paid'] = 'A partially paid invoice requires a partial payment.'
        elif self.status == self.Status.PAID and self.amount_paid != self.total_amount:
            errors['amount_paid'] = 'A paid invoice must have a zero balance.'

        if self.status == self.Status.VOIDED:
            if not self.voided_by_id or not self.voided_at or not self.void_reason:
                errors['status'] = 'Voided invoices require an actor, time, and reason.'
            if self.amount_paid:
                errors['amount_paid'] = 'A paid invoice cannot be voided locally.'
        elif self.voided_by_id or self.voided_at or self.void_reason:
            errors['status'] = 'Only voided invoices can have void details.'
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.pk:
            original = type(self).objects.filter(pk=self.pk).values(
                'status',
                'number',
                'organization_id',
                'project_id',
                'title',
                'notes',
                'issue_date',
                'due_date',
                'subtotal_amount',
                'tax_rate',
                'tax_amount',
                'total_amount',
                'source_change_order_id',
                'source_selection_id',
            ).first()
            if original and original['number'] is not None:
                if original['number'] != self.number:
                    raise ValidationError(
                        {'number': 'Issued invoice numbers are immutable.'}
                    )
            if original and original['status'] != self.Status.DRAFT:
                immutable_fields = (
                    'organization_id',
                    'project_id',
                    'title',
                    'notes',
                    'issue_date',
                    'due_date',
                    'subtotal_amount',
                    'tax_rate',
                    'tax_amount',
                    'total_amount',
                    'source_change_order_id',
                    'source_selection_id',
                )
                if any(original[field] != getattr(self, field) for field in immutable_fields):
                    raise ValidationError(
                        'Issued invoice details and totals are immutable.'
                    )
        return super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.project}: {self.display_number} {self.title}'


class InvoiceLineItem(models.Model):
    class Category(models.TextChoices):
        PRODUCT = 'product', 'Product'
        MATERIAL = 'material', 'Material'
        LABOR = 'labor', 'Labor'
        SUBCONTRACTOR = 'subcontractor', 'Subcontractor'
        COMMISSION = 'commission', 'Commission/markup'
        TAX = 'tax', 'Tax'
        ALLOWANCE = 'allowance', 'Allowance'
        OTHER = 'other', 'Other'

    invoice = models.ForeignKey(
        Invoice,
        on_delete=models.CASCADE,
        related_name='line_items',
    )
    category = models.CharField(
        max_length=20,
        choices=Category.choices,
        default=Category.OTHER,
    )
    cost_code = models.ForeignKey(
        CostCode,
        blank=True,
        null=True,
        on_delete=models.PROTECT,
        related_name='invoice_line_items',
    )
    description = models.CharField(max_length=200)
    quantity = models.DecimalField(max_digits=10, decimal_places=2, default=1)
    unit_price = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    sort_order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ('sort_order', 'pk')
        constraints = [
            models.CheckConstraint(
                condition=Q(quantity__gt=0),
                name='billing_invoice_line_quantity_positive',
            ),
            models.CheckConstraint(
                condition=Q(unit_price__gte=0),
                name='billing_invoice_line_price_nonnegative',
            ),
        ]

    @property
    def total_amount(self):
        return money(self.quantity * self.unit_price)

    def clean(self):
        super().clean()
        self.description = self.description.strip()
        errors = {}
        if not self.description:
            errors['description'] = 'Describe this invoice line.'
        if self.quantity <= 0:
            errors['quantity'] = 'Quantity must be greater than zero.'
        if self.unit_price < 0:
            errors['unit_price'] = 'Unit price cannot be negative.'
        if self.invoice_id and self.invoice.status != Invoice.Status.DRAFT:
            errors['invoice'] = 'Issued invoice lines are immutable.'
        if (
            self.cost_code_id
            and self.invoice_id
            and self.cost_code.organization_id != self.invoice.organization_id
        ):
            errors['cost_code'] = 'Choose a cost code from this company.'
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.invoice_id and self.invoice.status != Invoice.Status.DRAFT:
            raise ValidationError('Issued invoice lines are immutable.')
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.invoice.status != Invoice.Status.DRAFT:
            raise ValidationError('Issued invoice lines are immutable.')
        return super().delete(*args, **kwargs)

    def __str__(self):
        return f'{self.invoice.display_number}: {self.description}'


class CreditMemo(models.Model):
    class Status(models.TextChoices):
        DRAFT = 'draft', 'Draft'
        ISSUED = 'issued', 'Issued'
        VOIDED = 'voided', 'Voided'

    organization = models.ForeignKey(
        Organization,
        on_delete=models.PROTECT,
        related_name='credit_memos',
    )
    project = models.ForeignKey(
        Project,
        on_delete=models.PROTECT,
        related_name='credit_memos',
    )
    source_change_order = models.OneToOneField(
        ChangeOrder,
        on_delete=models.PROTECT,
        related_name='credit_memo',
    )
    number = models.PositiveBigIntegerField(null=True, blank=True)
    amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    status = models.CharField(
        max_length=10,
        choices=Status.choices,
        default=Status.DRAFT,
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='credit_memos_created',
    )
    issued_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='credit_memos_issued',
    )
    issued_at = models.DateTimeField(null=True, blank=True)
    voided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='credit_memos_voided',
    )
    voided_at = models.DateTimeField(null=True, blank=True)
    void_reason = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ('-number', '-created_at', '-pk')
        constraints = [
            models.UniqueConstraint(
                fields=('organization', 'number'),
                condition=Q(number__isnull=False),
                name='billing_unique_credit_memo_number_per_org',
            ),
            models.CheckConstraint(
                condition=Q(amount__gt=0),
                name='billing_credit_memo_amount_positive',
            ),
            models.CheckConstraint(
                condition=(
                    Q(
                        status='draft',
                        number__isnull=True,
                        issued_by__isnull=True,
                        issued_at__isnull=True,
                    )
                    | Q(
                        status__in=('issued', 'voided'),
                        number__isnull=False,
                        issued_by__isnull=False,
                        issued_at__isnull=False,
                    )
                ),
                name='billing_credit_memo_issue_fields_match_status',
            ),
            models.CheckConstraint(
                condition=(
                    Q(
                        status='voided',
                        voided_by__isnull=False,
                        voided_at__isnull=False,
                    )
                    | (
                        ~Q(status='voided')
                        & Q(
                            voided_by__isnull=True,
                            voided_at__isnull=True,
                            void_reason='',
                        )
                    )
                ),
                name='billing_credit_memo_void_fields_match_status',
            ),
        ]

    @property
    def display_number(self):
        return f'CM-{self.number:06d}' if self.number is not None else 'Draft credit memo'

    @property
    def client_visible(self):
        return self.status != self.Status.DRAFT

    @property
    def applied_amount(self):
        applied = self.payments.aggregate(total=models.Sum('amount'))['total'] or Decimal('0')
        return money(min(applied, self.amount))

    @property
    def remaining_balance(self):
        return money(max(self.amount - self.applied_amount, Decimal('0')))

    def clean(self):
        super().clean()
        self.void_reason = self.void_reason.strip()
        errors = {}
        if self.project_id and self.organization_id:
            if self.project.organization_id != self.organization_id:
                errors['project'] = 'The credit memo project must belong to its company.'
        if self.source_change_order_id:
            if self.source_change_order.project_id != self.project_id:
                errors['source_change_order'] = (
                    'The change order must belong to the credit memo project.'
                )
            if self.source_change_order.status != ChangeOrder.Status.APPROVED:
                errors['source_change_order'] = (
                    'Only an approved change order can originate a credit memo.'
                )
            if self.source_change_order.price_delta >= 0:
                errors['source_change_order'] = (
                    'Only a change order with a client credit can originate a credit memo.'
                )
        if self.status == self.Status.DRAFT:
            if self.number is not None or self.issued_by_id or self.issued_at:
                errors['status'] = 'Draft credit memos cannot have issue details.'
        elif self.number is None or not self.issued_by_id or not self.issued_at:
            errors['status'] = 'Issued credit memos require a number and issue details.'
        if self.status == self.Status.VOIDED:
            if not self.voided_by_id or not self.voided_at or not self.void_reason:
                errors['status'] = 'Voided credit memos require an actor, time, and reason.'
        elif self.voided_by_id or self.voided_at or self.void_reason:
            errors['status'] = 'Only voided credit memos can have void details.'
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.pk:
            original = type(self).objects.filter(pk=self.pk).values(
                'status',
                'number',
                'organization_id',
                'project_id',
                'amount',
                'source_change_order_id',
            ).first()
            if original and original['number'] is not None:
                if original['number'] != self.number:
                    raise ValidationError(
                        {'number': 'Issued credit memo numbers are immutable.'}
                    )
            if original and original['status'] != self.Status.DRAFT:
                immutable_fields = (
                    'organization_id',
                    'project_id',
                    'amount',
                    'source_change_order_id',
                )
                if any(original[field] != getattr(self, field) for field in immutable_fields):
                    raise ValidationError(
                        'Issued credit memo details and totals are immutable.'
                    )
        return super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.project}: {self.display_number}'


class Payment(models.Model):
    class Method(models.TextChoices):
        CHECK = 'check', 'Check'
        ACH = 'ach', 'ACH/bank transfer'
        CARD = 'card', 'Card'
        CASH = 'cash', 'Cash'
        CREDIT_MEMO = 'credit_memo', 'Credit memo'
        OTHER = 'other', 'Other'

    invoice = models.ForeignKey(
        Invoice,
        on_delete=models.PROTECT,
        related_name='payments',
    )
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    method = models.CharField(
        max_length=12,
        choices=Method.choices,
        default=Method.OTHER,
    )
    credit_memo = models.ForeignKey(
        CreditMemo,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='payments',
    )
    reference = models.CharField(max_length=100, blank=True)
    paid_date = models.DateField()
    note = models.TextField(blank=True)
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='payments_recorded',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ('-paid_date', '-pk')
        constraints = [
            models.CheckConstraint(
                condition=Q(amount__gt=0),
                name='billing_payment_amount_positive',
            ),
            models.CheckConstraint(
                condition=(
                    Q(method='credit_memo', credit_memo__isnull=False)
                    | (~Q(method='credit_memo') & Q(credit_memo__isnull=True))
                ),
                name='billing_payment_credit_memo_matches_method',
            ),
        ]

    def clean(self):
        super().clean()
        if self.credit_memo_id and self.invoice_id:
            if self.credit_memo.project_id != self.invoice.project_id:
                raise ValidationError(
                    {'credit_memo': 'The credit memo must belong to the invoice project.'}
                )

    def __str__(self):
        return f'{self.invoice.display_number}: ${self.amount:,.2f} on {self.paid_date}'
