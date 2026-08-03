import uuid
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Sum
from django.db.models.functions import Lower
from django.utils import timezone

from .money import money
from .storage import private_document_storage


def invitation_expiry():
    return timezone.now() + timedelta(days=7)


def project_document_upload_path(instance, filename):
    safe_name = Path(filename).name
    unique_name = f'{uuid.uuid4().hex}_{safe_name}'
    document = instance.document
    return (
        f'project_documents/{document.project.organization_id}/'
        f'{document.project_id}/{document.pk}/{unique_name}'
    )


def selection_option_upload_path(instance, filename):
    safe_name = Path(filename).name
    unique_name = f'{uuid.uuid4().hex}_{safe_name}'
    selection = instance.selection
    return (
        f'selection_options/{selection.project.organization_id}/'
        f'{selection.project_id}/{selection.pk}/{unique_name}'
    )


class Organization(models.Model):
    name = models.CharField(max_length=200)
    slug = models.SlugField(unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ('name',)

    def __str__(self):
        return self.name


class OrganizationMembership(models.Model):
    class Role(models.TextChoices):
        ADMIN = 'admin', 'Admin'
        MANAGER = 'manager', 'Manager'
        PROJECT_MANAGER = 'project_manager', 'Project manager'
        STAFF = 'staff', 'Staff'
        OFFICE_MANAGER = 'office_manager', 'Office manager'
        REAL_ESTATE_AGENT = 'real_estate_agent', 'Real estate agent'
        ACCOUNTANT = 'accountant', 'Accountant'
        CLIENT = 'client', 'Client'
        SUBCONTRACTOR = 'subcontractor', 'Subcontractor'

    INTERNAL_ROLES = (
        Role.ADMIN,
        Role.MANAGER,
        Role.PROJECT_MANAGER,
        Role.STAFF,
        Role.OFFICE_MANAGER,
        Role.REAL_ESTATE_AGENT,
        Role.ACCOUNTANT,
    )
    EXTERNAL_ROLES = (Role.CLIENT, Role.SUBCONTRACTOR)

    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name='memberships',
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='organization_memberships',
    )
    role = models.CharField(max_length=20, choices=Role.choices)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ('organization__name', 'user__email')
        constraints = [
            models.UniqueConstraint(
                fields=('organization', 'user'),
                name='projects_unique_organization_user',
            ),
        ]

    @property
    def is_internal(self):
        return self.role in self.INTERNAL_ROLES

    @property
    def can_view_company_financials(self):
        return self.role in (self.Role.ADMIN, self.Role.ACCOUNTANT)

    @property
    def can_view_project_financials(self):
        return self.role in self.INTERNAL_ROLES

    @property
    def can_invite_clients(self):
        return self.role in (
            self.Role.ADMIN,
            self.Role.MANAGER,
            self.Role.PROJECT_MANAGER,
        )

    def __str__(self):
        return f'{self.user.email} - {self.organization} ({self.get_role_display()})'


class Project(models.Model):
    class Status(models.TextChoices):
        PLANNING = 'planning', 'Planning'
        ACTIVE = 'active', 'Active'
        ON_HOLD = 'on_hold', 'On hold'
        COMPLETED = 'completed', 'Completed'

    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name='projects',
    )
    name = models.CharField(max_length=200)
    code = models.CharField(max_length=50, blank=True)
    description = models.TextField(blank=True)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PLANNING,
    )
    start_date = models.DateField(blank=True, null=True)
    target_completion_date = models.DateField(blank=True, null=True)
    contract_amount = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        blank=True,
        null=True,
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
        related_name='created_projects',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ('organization__name', 'name')

    def __str__(self):
        return f'{self.organization}: {self.name}'


class ProjectInternalAccess(models.Model):
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name='internal_access',
    )
    membership = models.ForeignKey(
        OrganizationMembership,
        on_delete=models.CASCADE,
        related_name='project_access',
    )
    can_manage = models.BooleanField(default=False)
    can_invite_clients = models.BooleanField(default=False)
    receives_notifications = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True)
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
        related_name='project_internal_access_assignments',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ('membership__user__email',)
        constraints = [
            models.UniqueConstraint(
                fields=('project', 'membership'),
                name='projects_unique_internal_access',
            ),
        ]

    @property
    def user(self):
        return self.membership.user

    def clean(self):
        super().clean()
        errors = {}
        if self.membership_id and self.project_id:
            if self.membership.organization_id != self.project.organization_id:
                errors['membership'] = 'The team member must belong to this company.'
            if not self.membership.is_internal:
                errors['membership'] = 'Project internal access requires an internal role.'
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f'{self.membership.user.email} - {self.project}'


class ProjectMembership(models.Model):
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name='project_memberships',
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='project_memberships',
    )
    role = models.CharField(
        max_length=20,
        choices=(
            (
                OrganizationMembership.Role.CLIENT,
                OrganizationMembership.Role.CLIENT.label,
            ),
            (
                OrganizationMembership.Role.SUBCONTRACTOR,
                OrganizationMembership.Role.SUBCONTRACTOR.label,
            ),
        ),
    )
    is_active = models.BooleanField(default=True)
    invited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
        related_name='project_memberships_created',
    )
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ('project__name', 'user__email')
        constraints = [
            models.UniqueConstraint(
                fields=('project', 'user'),
                name='projects_unique_project_user',
            ),
        ]

    def clean(self):
        super().clean()
        if self.role not in OrganizationMembership.EXTERNAL_ROLES:
            raise ValidationError({'role': 'Project access is for external roles.'})

    def __str__(self):
        return f'{self.user.email} - {self.project} ({self.get_role_display()})'


class ProjectInvitation(models.Model):
    token = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name='invitations',
    )
    email = models.EmailField()
    role = models.CharField(
        max_length=20,
        choices=(
            (
                OrganizationMembership.Role.CLIENT,
                OrganizationMembership.Role.CLIENT.label,
            ),
            (
                OrganizationMembership.Role.SUBCONTRACTOR,
                OrganizationMembership.Role.SUBCONTRACTOR.label,
            ),
        ),
        default=OrganizationMembership.Role.CLIENT,
    )
    invited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='project_invitations_sent',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(default=invitation_expiry)
    accepted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
        related_name='project_invitations_accepted',
    )
    accepted_at = models.DateTimeField(blank=True, null=True)
    revoked_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ('-created_at',)
        constraints = [
            models.UniqueConstraint(
                Lower('email'),
                'project',
                condition=models.Q(accepted_at__isnull=True, revoked_at__isnull=True),
                name='projects_unique_pending_invite_email',
            ),
        ]

    def clean(self):
        super().clean()
        self.email = self.email.strip().lower()
        if self.role not in OrganizationMembership.EXTERNAL_ROLES:
            raise ValidationError({'role': 'Invitations are for external roles.'})

    @property
    def is_valid(self):
        return (
            self.accepted_at is None
            and self.revoked_at is None
            and self.expires_at > timezone.now()
        )

    def __str__(self):
        return f'{self.email} invited to {self.project}'


class OrganizationInvitation(models.Model):
    token = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name='team_invitations',
    )
    email = models.EmailField()
    role = models.CharField(
        max_length=20,
        choices=(
            (
                OrganizationMembership.Role.ADMIN,
                OrganizationMembership.Role.ADMIN.label,
            ),
            (
                OrganizationMembership.Role.MANAGER,
                OrganizationMembership.Role.MANAGER.label,
            ),
            (
                OrganizationMembership.Role.PROJECT_MANAGER,
                OrganizationMembership.Role.PROJECT_MANAGER.label,
            ),
            (
                OrganizationMembership.Role.STAFF,
                OrganizationMembership.Role.STAFF.label,
            ),
            (
                OrganizationMembership.Role.OFFICE_MANAGER,
                OrganizationMembership.Role.OFFICE_MANAGER.label,
            ),
            (
                OrganizationMembership.Role.REAL_ESTATE_AGENT,
                OrganizationMembership.Role.REAL_ESTATE_AGENT.label,
            ),
            (
                OrganizationMembership.Role.ACCOUNTANT,
                OrganizationMembership.Role.ACCOUNTANT.label,
            ),
        ),
    )
    invited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='organization_invitations_sent',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(default=invitation_expiry)
    accepted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
        related_name='organization_invitations_accepted',
    )
    accepted_at = models.DateTimeField(blank=True, null=True)
    revoked_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ('-created_at',)
        constraints = [
            models.UniqueConstraint(
                Lower('email'),
                'organization',
                condition=models.Q(accepted_at__isnull=True, revoked_at__isnull=True),
                name='projects_unique_pending_team_invite_email',
            ),
        ]

    def clean(self):
        super().clean()
        self.email = self.email.strip().lower()
        if self.role not in OrganizationMembership.INTERNAL_ROLES:
            raise ValidationError({'role': 'Team invitations require an internal role.'})

    @property
    def is_valid(self):
        return (
            self.accepted_at is None
            and self.revoked_at is None
            and self.expires_at > timezone.now()
        )

    def __str__(self):
        return f'{self.email} invited to {self.organization}'


class ActivityEvent(models.Model):
    class Type(models.TextChoices):
        PROJECT_CREATED = 'project_created', 'Project created'
        PROJECT_UPDATED = 'project_updated', 'Project updated'
        CLIENT_INVITED = 'client_invited', 'Client invited'
        CLIENT_INVITE_RESENT = 'client_invite_resent', 'Client invitation resent'
        CLIENT_INVITE_REVOKED = 'client_invite_revoked', 'Client invitation revoked'
        CLIENT_JOINED = 'client_joined', 'Client joined'
        CLIENT_ACCESS_REVOKED = 'client_access_revoked', 'Client access revoked'
        CLIENT_ACCESS_RESTORED = 'client_access_restored', 'Client access restored'
        TEAM_INVITED = 'team_invited', 'Team member invited'
        TEAM_INVITE_RESENT = 'team_invite_resent', 'Team invitation resent'
        TEAM_INVITE_REVOKED = 'team_invite_revoked', 'Team invitation revoked'
        TEAM_JOINED = 'team_joined', 'Team member joined'
        TEAM_ROLE_CHANGED = 'team_role_changed', 'Team role changed'
        TEAM_ACCESS_CHANGED = 'team_access_changed', 'Team access changed'
        INTERNAL_ACCESS_ASSIGNED = (
            'internal_access_assigned',
            'Internal project access assigned',
        )
        INTERNAL_ACCESS_UPDATED = (
            'internal_access_updated',
            'Internal project access updated',
        )
        INTERNAL_ACCESS_REVOKED = (
            'internal_access_revoked',
            'Internal project access revoked',
        )
        MESSAGE_THREAD_CREATED = 'message_thread_created', 'Message thread created'
        MESSAGE_SENT = 'message_sent', 'Message sent'
        MESSAGE_THREAD_STATUS_CHANGED = (
            'message_thread_status_changed',
            'Message thread status changed',
        )
        DOCUMENT_CREATED = 'document_created', 'Document created'
        DOCUMENT_VERSION_ADDED = 'document_version_added', 'Document version added'
        CLIENT_UPLOAD_CREATED = 'client_upload_created', 'Client upload created'
        DOCUMENT_DECISION_RECORDED = (
            'document_decision_recorded',
            'Document decision recorded',
        )
        CHANGE_ORDER_CREATED = 'change_order_created', 'Change order created'
        CHANGE_ORDER_UPDATED = 'change_order_updated', 'Change order updated'
        CHANGE_ORDER_SUBMITTED = 'change_order_submitted', 'Change order submitted'
        CHANGE_ORDER_DECIDED = 'change_order_decided', 'Change order decided'
        CHANGE_ORDER_VOIDED = 'change_order_voided', 'Change order voided'
        CHANGE_ORDER_REVISED = 'change_order_revised', 'Change order revised'
        CHANGE_ORDER_REPLACEMENT_CREATED = (
            'change_order_replacement_created',
            'Change order replacement created',
        )
        CHANGE_ORDER_LINE_ITEM_ADDED = (
            'change_order_line_item_added',
            'Change order line item added',
        )
        CHANGE_ORDER_LINE_ITEM_UPDATED = (
            'change_order_line_item_updated',
            'Change order line item updated',
        )
        CHANGE_ORDER_LINE_ITEM_REMOVED = (
            'change_order_line_item_removed',
            'Change order line item removed',
        )
        SELECTION_CREATED = 'selection_created', 'Selection created'
        SELECTION_UPDATED = 'selection_updated', 'Selection updated'
        SELECTION_OPTION_ADDED = 'selection_option_added', 'Selection option added'
        SELECTION_OPTION_UPDATED = (
            'selection_option_updated',
            'Selection option updated',
        )
        SELECTION_OPTION_REMOVED = (
            'selection_option_removed',
            'Selection option removed',
        )
        SELECTION_PUBLISHED = 'selection_published', 'Selection published'
        SELECTION_CHOSEN = 'selection_chosen', 'Selection chosen'
        SELECTION_REOPENED = 'selection_reopened', 'Selection reopened'
        SELECTION_VOIDED = 'selection_voided', 'Selection voided'
        SELECTION_PACKAGE_CREATED = (
            'selection_package_created',
            'Selection package created',
        )
        SELECTION_CUSTOM_REQUEST_SUBMITTED = (
            'selection_custom_request_submitted',
            'Selection custom request submitted',
        )
        SELECTION_CUSTOM_REQUEST_REVIEWED = (
            'selection_custom_request_reviewed',
            'Selection custom request reviewed',
        )
        SELECTION_CREDIT_DISPOSITION_SET = (
            'selection_credit_disposition_set',
            'Selection credit disposition set',
        )
        SELECTION_REMINDER_SENT = (
            'selection_reminder_sent',
            'Selection reminder sent',
        )
        SCHEDULE_MILESTONE_CREATED = (
            'schedule_milestone_created',
            'Schedule milestone created',
        )
        SCHEDULE_MILESTONE_UPDATED = (
            'schedule_milestone_updated',
            'Schedule milestone updated',
        )
        QUICKBOOKS_CONNECTED = 'quickbooks_connected', 'QuickBooks connected'
        QUICKBOOKS_RECONNECTED = 'quickbooks_reconnected', 'QuickBooks reconnected'
        QUICKBOOKS_DISCONNECTED = 'quickbooks_disconnected', 'QuickBooks disconnected'
        QUICKBOOKS_CAPABILITIES_REFRESHED = (
            'quickbooks_capabilities_refreshed',
            'QuickBooks capabilities refreshed',
        )
        QUICKBOOKS_PROJECT_MAPPED = (
            'quickbooks_project_mapped',
            'Project mapped to QuickBooks customer',
        )
        QUICKBOOKS_PROJECT_UNLINKED = (
            'quickbooks_project_unlinked',
            'Project unlinked from QuickBooks customer',
        )
        QUICKBOOKS_PROJECT_MAPPING_TOMBSTONED = (
            'quickbooks_project_mapping_tombstoned',
            'QuickBooks customer mapping tombstoned',
        )
        QUICKBOOKS_CUSTOMER_SYNC_SUCCEEDED = (
            'quickbooks_customer_sync_succeeded',
            'QuickBooks customer sync succeeded',
        )
        QUICKBOOKS_CUSTOMER_SYNC_FAILED = (
            'quickbooks_customer_sync_failed',
            'QuickBooks customer sync failed',
        )
        QUICKBOOKS_SYNC_RESOLVED = (
            'quickbooks_sync_resolved',
            'QuickBooks sync issue resolved',
        )
        QUICKBOOKS_INVOICE_MAPPED = (
            'quickbooks_invoice_mapped',
            'QuickBooks invoice mapped',
        )
        INVOICE_DRAFT_CREATED = (
            'invoice_draft_created',
            'Invoice draft created',
        )
        INVOICE_DRAFT_DISCARDED = (
            'invoice_draft_discarded',
            'Invoice draft discarded',
        )
        INVOICE_ISSUED = 'invoice_issued', 'Invoice issued'
        INVOICE_VOIDED = 'invoice_voided', 'Invoice voided'
        PAYMENT_RECORDED = 'payment_recorded', 'Payment recorded'
        PAYMENT_DELETED = 'payment_deleted', 'Payment deleted'
        ESTIMATE_CREATED = 'estimate_created', 'Estimate created'
        ESTIMATE_UPDATED = 'estimate_updated', 'Estimate updated'
        ESTIMATE_SUBMITTED = 'estimate_submitted', 'Estimate submitted'
        ESTIMATE_DECIDED = 'estimate_decided', 'Estimate decided'
        ESTIMATE_VOIDED = 'estimate_voided', 'Estimate voided'
        ESTIMATE_REVISED = 'estimate_revised', 'Estimate revised'
        ESTIMATE_REPLACEMENT_CREATED = (
            'estimate_replacement_created',
            'Estimate replacement created',
        )
        ESTIMATE_LINE_ITEM_ADDED = (
            'estimate_line_item_added',
            'Estimate line item added',
        )
        ESTIMATE_LINE_ITEM_UPDATED = (
            'estimate_line_item_updated',
            'Estimate line item updated',
        )
        ESTIMATE_LINE_ITEM_REMOVED = (
            'estimate_line_item_removed',
            'Estimate line item removed',
        )
        PROJECT_COST_ENTRY_RECORDED = (
            'project_cost_entry_recorded',
            'Actual cost recorded',
        )
        PROJECT_COST_ENTRY_DELETED = (
            'project_cost_entry_deleted',
            'Actual cost deleted',
        )

    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name='activity_events',
    )
    project = models.ForeignKey(
        Project,
        blank=True,
        null=True,
        on_delete=models.CASCADE,
        related_name='activity_events',
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
        related_name='activity_events',
    )
    event_type = models.CharField(max_length=40, choices=Type.choices)
    summary = models.CharField(max_length=300)
    metadata = models.JSONField(blank=True, default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ('-created_at', '-pk')
        indexes = (
            models.Index(
                fields=('project', '-created_at'),
                name='activity_proj_created_idx',
            ),
            models.Index(
                fields=('project', 'event_type', '-created_at'),
                name='activity_proj_type_idx',
            ),
        )

    def clean(self):
        super().clean()
        if self.project_id and self.project.organization_id != self.organization_id:
            raise ValidationError({'project': 'Project must belong to the organization.'})

    def __str__(self):
        return self.summary


class ConversationThread(models.Model):
    class Status(models.TextChoices):
        OPEN = 'open', 'Open'
        CLOSED = 'closed', 'Closed'

    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name='conversation_threads',
    )
    subject = models.CharField(max_length=200)
    status = models.CharField(
        max_length=10,
        choices=Status.choices,
        default=Status.OPEN,
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='conversation_threads_created',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ('-updated_at', '-pk')

    def __str__(self):
        return f'{self.project}: {self.subject}'


class ConversationMessage(models.Model):
    thread = models.ForeignKey(
        ConversationThread,
        on_delete=models.CASCADE,
        related_name='messages',
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='conversation_messages',
    )
    body = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ('created_at', 'pk')

    def clean(self):
        super().clean()
        self.body = self.body.strip()
        if not self.body:
            raise ValidationError({'body': 'Message cannot be blank.'})

    def __str__(self):
        return f'{self.author.email} on {self.thread.subject}'


class ProjectDocument(models.Model):
    class Category(models.TextChoices):
        GENERAL = 'general', 'General'
        PLAN = 'plan', 'Plan or drawing'
        CONTRACT = 'contract', 'Contract'
        SELECTION = 'selection', 'Selection'
        CHANGE_ORDER = 'change_order', 'Change order'
        CLIENT_UPLOAD = 'client_upload', 'Client upload'
        OTHER = 'other', 'Other'

    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name='documents',
    )
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    category = models.CharField(
        max_length=20,
        choices=Category.choices,
        default=Category.GENERAL,
    )
    client_visible = models.BooleanField(default=True)
    requires_client_approval = models.BooleanField(default=False)
    required_approvals = models.PositiveSmallIntegerField(default=1)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='project_documents_created',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ('title', 'pk')

    @property
    def latest_version(self):
        return self.versions.first()

    def clean(self):
        super().clean()
        if self.requires_client_approval and not self.client_visible:
            raise ValidationError(
                {
                    'requires_client_approval': (
                        'A document requiring client approval must be client-visible.'
                    )
                }
            )
        if self.required_approvals < 1:
            raise ValidationError(
                {'required_approvals': 'At least one approval must be required.'}
            )

    def __str__(self):
        return f'{self.project}: {self.title}'


class ProjectDocumentVersion(models.Model):
    document = models.ForeignKey(
        ProjectDocument,
        on_delete=models.CASCADE,
        related_name='versions',
    )
    version_number = models.PositiveIntegerField()
    file = models.FileField(
        storage=private_document_storage,
        upload_to=project_document_upload_path,
    )
    original_filename = models.CharField(max_length=255)
    notes = models.TextField(blank=True)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='project_document_versions_uploaded',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ('-version_number', '-pk')
        constraints = [
            models.UniqueConstraint(
                fields=('document', 'version_number'),
                name='projects_unique_document_version_number',
            ),
        ]

    def __str__(self):
        return f'{self.document.title} v{self.version_number}'


class DocumentDecision(models.Model):
    class Decision(models.TextChoices):
        APPROVED = 'approved', 'Approved'
        DECLINED = 'declined', 'Declined'

    version = models.ForeignKey(
        ProjectDocumentVersion,
        on_delete=models.CASCADE,
        related_name='decisions',
    )
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='document_decisions',
    )
    decision = models.CharField(max_length=10, choices=Decision.choices)
    comment = models.TextField(blank=True)
    decided_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ('decided_at', 'pk')
        constraints = [
            models.UniqueConstraint(
                fields=('version', 'decided_by'),
                name='projects_unique_document_decision_per_user_version',
            ),
        ]

    def __str__(self):
        return (
            f'{self.decided_by.email} {self.get_decision_display().lower()} '
            f'{self.version}'
        )


class CostCode(models.Model):
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name='cost_codes',
    )
    code = models.CharField(max_length=20)
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ('code',)
        constraints = [
            models.UniqueConstraint(
                fields=('organization', 'code'),
                name='projects_unique_cost_code_per_organization',
            ),
        ]

    def clean(self):
        super().clean()
        self.code = self.code.strip()
        self.name = self.name.strip()
        self.description = self.description.strip()
        errors = {}
        if not self.code:
            errors['code'] = 'Enter a cost code.'
        if not self.name:
            errors['name'] = 'Enter a name for this cost code.'
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f'{self.code} - {self.name}'


class ChangeOrder(models.Model):
    class Status(models.TextChoices):
        DRAFT = 'draft', 'Draft'
        PENDING = 'pending', 'Awaiting client'
        APPROVED = 'approved', 'Approved'
        DECLINED = 'declined', 'Declined'
        VOIDED = 'voided', 'Voided'

    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name='change_orders',
    )
    number = models.PositiveIntegerField()
    title = models.CharField(max_length=200)
    description = models.TextField()
    reason = models.TextField(blank=True)
    price_delta = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    cost_delta = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    schedule_delta_days = models.IntegerField(default=0)
    status = models.CharField(
        max_length=10,
        choices=Status.choices,
        default=Status.DRAFT,
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='change_orders_created',
    )
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        blank=True,
        null=True,
        on_delete=models.PROTECT,
        related_name='change_orders_submitted',
    )
    submitted_at = models.DateTimeField(blank=True, null=True)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        blank=True,
        null=True,
        on_delete=models.PROTECT,
        related_name='change_orders_decided',
    )
    decided_at = models.DateTimeField(blank=True, null=True)
    client_comment = models.TextField(blank=True)
    voided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        blank=True,
        null=True,
        on_delete=models.PROTECT,
        related_name='change_orders_voided',
    )
    voided_at = models.DateTimeField(blank=True, null=True)
    void_reason = models.TextField(blank=True)
    requires_financial_reversal = models.BooleanField(default=False)
    required_approvals = models.PositiveSmallIntegerField(default=1)
    replaces = models.OneToOneField(
        'self',
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
        related_name='replaced_by',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ('-number', '-pk')
        constraints = [
            models.UniqueConstraint(
                fields=('project', 'number'),
                name='projects_unique_change_order_number_per_project',
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        status='draft',
                        submitted_by__isnull=True,
                        submitted_at__isnull=True,
                    )
                    | models.Q(
                        status__in=('pending', 'approved', 'declined', 'voided'),
                        submitted_by__isnull=False,
                        submitted_at__isnull=False,
                    )
                ),
                name='projects_change_order_submission_matches_status',
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        status__in=('approved', 'declined'),
                        decided_by__isnull=False,
                        decided_at__isnull=False,
                    )
                    | models.Q(
                        status__in=('draft', 'pending', 'voided'),
                        decided_by__isnull=True,
                        decided_at__isnull=True,
                    )
                ),
                name='projects_change_order_decision_matches_status',
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        status='voided',
                        voided_by__isnull=False,
                        voided_at__isnull=False,
                    )
                    | models.Q(
                        status__in=('draft', 'pending', 'approved', 'declined'),
                        voided_by__isnull=True,
                        voided_at__isnull=True,
                    )
                ),
                name='projects_change_order_void_matches_status',
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(status='voided')
                    | models.Q(requires_financial_reversal=False)
                ),
                name='projects_change_order_reversal_requires_voided',
            ),
        ]

    @property
    def display_number(self):
        return f'CO-{self.number:03d}'

    @property
    def margin_delta(self):
        return self.price_delta - self.cost_delta

    @property
    def quickbooks_handoff_label(self):
        if self.status == self.Status.APPROVED:
            return 'Send to QuickBooks when invoiced'
        return 'Not ready for QuickBooks'

    def recalculate_from_line_items(self):
        totals = self.line_items.aggregate(
            price=Sum(
                F('quantity') * F('unit_price'),
                output_field=models.DecimalField(max_digits=14, decimal_places=2),
            ),
            cost=Sum(
                F('quantity') * F('unit_cost'),
                output_field=models.DecimalField(max_digits=14, decimal_places=2),
            ),
        )
        self.price_delta = money(totals['price'] or Decimal('0'))
        self.cost_delta = money(totals['cost'] or Decimal('0'))
        self.save(update_fields=('price_delta', 'cost_delta', 'updated_at'))

    def clean(self):
        super().clean()
        self.title = self.title.strip()
        self.description = self.description.strip()
        self.reason = self.reason.strip()
        self.client_comment = self.client_comment.strip()
        self.void_reason = self.void_reason.strip()
        errors = {}
        if not self.title:
            errors['title'] = 'Title cannot be blank.'
        if not self.description:
            errors['description'] = 'Describe the work covered by this change order.'
        if self.required_approvals < 1:
            errors['required_approvals'] = 'At least one approval must be required.'
        if self.status in (self.Status.APPROVED, self.Status.DECLINED):
            if not self.decided_by_id or not self.decided_at:
                errors['status'] = 'A completed decision requires an authenticated client.'
        elif self.decided_by_id or self.decided_at:
            errors['status'] = 'Only approved or declined orders can have a decision.'
        if self.status == self.Status.DRAFT:
            if self.submitted_by_id or self.submitted_at:
                errors['status'] = 'Draft change orders cannot have submission details.'
        elif not self.submitted_by_id or not self.submitted_at:
            errors['status'] = 'Submitted change orders require an authenticated sender.'
        if self.status == self.Status.VOIDED:
            if not self.voided_by_id or not self.voided_at:
                errors['status'] = 'Voided change orders require an authenticated actor.'
        else:
            if self.voided_by_id or self.voided_at:
                errors['status'] = 'Only voided change orders can have void details.'
            if self.requires_financial_reversal:
                errors['status'] = 'Only voided change orders require a financial reversal.'
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f'{self.project}: {self.display_number} {self.title}'


class ChangeOrderLineItem(models.Model):
    class Category(models.TextChoices):
        PRODUCT = 'product', 'Product'
        MATERIAL = 'material', 'Material'
        LABOR = 'labor', 'Labor'
        SUBCONTRACTOR = 'subcontractor', 'Subcontractor'
        COMMISSION = 'commission', 'Commission/markup'
        TAX = 'tax', 'Tax'
        ALLOWANCE = 'allowance', 'Allowance'
        OTHER = 'other', 'Other'

    change_order = models.ForeignKey(
        ChangeOrder,
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
        related_name='change_order_line_items',
    )
    description = models.CharField(max_length=200)
    quantity = models.DecimalField(max_digits=10, decimal_places=2, default=1)
    unit_price = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    unit_cost = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    sort_order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ('sort_order', 'pk')

    @property
    def total_price(self):
        return self.quantity * self.unit_price

    @property
    def total_cost(self):
        return self.quantity * self.unit_cost

    @property
    def total_margin(self):
        return self.total_price - self.total_cost

    def clean(self):
        super().clean()
        self.description = self.description.strip()
        errors = {}
        if not self.description:
            errors['description'] = 'Describe this line item.'
        if self.quantity <= 0:
            errors['quantity'] = 'Quantity must be greater than zero.'
        if self.unit_price < 0:
            errors['unit_price'] = 'Unit price cannot be negative.'
        if self.unit_cost < 0:
            errors['unit_cost'] = 'Unit cost cannot be negative.'
        if (
            self.cost_code_id
            and self.change_order_id
            and self.cost_code.organization_id != self.change_order.project.organization_id
        ):
            errors['cost_code'] = 'Choose a cost code from this company.'
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f'{self.change_order.display_number}: {self.description}'


class ChangeOrderDecision(models.Model):
    class Decision(models.TextChoices):
        APPROVED = 'approved', 'Approved'
        DECLINED = 'declined', 'Declined'

    change_order = models.ForeignKey(
        ChangeOrder,
        on_delete=models.CASCADE,
        related_name='decisions',
    )
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='change_order_decisions',
    )
    decision = models.CharField(max_length=10, choices=Decision.choices)
    comment = models.TextField(blank=True)
    decided_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ('decided_at', 'pk')
        constraints = [
            models.UniqueConstraint(
                fields=('change_order', 'decided_by'),
                name='projects_unique_change_order_decision_per_user',
            ),
        ]

    def __str__(self):
        return (
            f'{self.decided_by.email} {self.get_decision_display().lower()} '
            f'{self.change_order.display_number}'
        )


class Estimate(models.Model):
    class Status(models.TextChoices):
        DRAFT = 'draft', 'Draft'
        PENDING = 'pending', 'Awaiting client'
        APPROVED = 'approved', 'Approved'
        DECLINED = 'declined', 'Declined'
        VOIDED = 'voided', 'Voided'

    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name='estimates',
    )
    number = models.PositiveIntegerField()
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    price_total = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    cost_total = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    status = models.CharField(
        max_length=10,
        choices=Status.choices,
        default=Status.DRAFT,
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='estimates_created',
    )
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        blank=True,
        null=True,
        on_delete=models.PROTECT,
        related_name='estimates_submitted',
    )
    submitted_at = models.DateTimeField(blank=True, null=True)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        blank=True,
        null=True,
        on_delete=models.PROTECT,
        related_name='estimates_decided',
    )
    decided_at = models.DateTimeField(blank=True, null=True)
    client_comment = models.TextField(blank=True)
    voided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        blank=True,
        null=True,
        on_delete=models.PROTECT,
        related_name='estimates_voided',
    )
    voided_at = models.DateTimeField(blank=True, null=True)
    void_reason = models.TextField(blank=True)
    required_approvals = models.PositiveSmallIntegerField(default=1)
    replaces = models.OneToOneField(
        'self',
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
        related_name='replaced_by',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ('-number', '-pk')
        constraints = [
            models.UniqueConstraint(
                fields=('project', 'number'),
                name='projects_unique_estimate_number_per_project',
            ),
            models.UniqueConstraint(
                fields=('project',),
                condition=models.Q(status='approved'),
                name='projects_unique_approved_estimate_per_project',
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        status='draft',
                        submitted_by__isnull=True,
                        submitted_at__isnull=True,
                    )
                    | models.Q(
                        status__in=('pending', 'approved', 'declined', 'voided'),
                        submitted_by__isnull=False,
                        submitted_at__isnull=False,
                    )
                ),
                name='projects_estimate_submission_matches_status',
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        status__in=('approved', 'declined'),
                        decided_by__isnull=False,
                        decided_at__isnull=False,
                    )
                    | models.Q(
                        status__in=('draft', 'pending', 'voided'),
                        decided_by__isnull=True,
                        decided_at__isnull=True,
                    )
                ),
                name='projects_estimate_decision_matches_status',
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        status='voided',
                        voided_by__isnull=False,
                        voided_at__isnull=False,
                    )
                    | models.Q(
                        status__in=('draft', 'pending', 'approved', 'declined'),
                        voided_by__isnull=True,
                        voided_at__isnull=True,
                    )
                ),
                name='projects_estimate_void_matches_status',
            ),
        ]

    @property
    def display_number(self):
        return f'EST-{self.number:03d}'

    @property
    def margin_total(self):
        return self.price_total - self.cost_total

    def recalculate_from_line_items(self):
        totals = self.line_items.aggregate(
            price=Sum(
                F('quantity') * F('unit_price'),
                output_field=models.DecimalField(max_digits=14, decimal_places=2),
            ),
            cost=Sum(
                F('quantity') * F('unit_cost'),
                output_field=models.DecimalField(max_digits=14, decimal_places=2),
            ),
        )
        self.price_total = money(totals['price'] or Decimal('0'))
        self.cost_total = money(totals['cost'] or Decimal('0'))
        self.save(update_fields=('price_total', 'cost_total', 'updated_at'))

    def clean(self):
        super().clean()
        self.title = self.title.strip()
        self.description = self.description.strip()
        self.client_comment = self.client_comment.strip()
        self.void_reason = self.void_reason.strip()
        errors = {}
        if not self.title:
            errors['title'] = 'Title cannot be blank.'
        if self.required_approvals < 1:
            errors['required_approvals'] = 'At least one approval must be required.'
        if self.status in (self.Status.APPROVED, self.Status.DECLINED):
            if not self.decided_by_id or not self.decided_at:
                errors['status'] = 'A completed decision requires an authenticated client.'
        elif self.decided_by_id or self.decided_at:
            errors['status'] = 'Only approved or declined estimates can have a decision.'
        if self.status == self.Status.DRAFT:
            if self.submitted_by_id or self.submitted_at:
                errors['status'] = 'Draft estimates cannot have submission details.'
        elif not self.submitted_by_id or not self.submitted_at:
            errors['status'] = 'Submitted estimates require an authenticated sender.'
        if self.status == self.Status.VOIDED:
            if not self.voided_by_id or not self.voided_at:
                errors['status'] = 'Voided estimates require an authenticated actor.'
        elif self.voided_by_id or self.voided_at:
            errors['status'] = 'Only voided estimates can have void details.'
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f'{self.project}: {self.display_number} {self.title}'


class EstimateLineItem(models.Model):
    class Category(models.TextChoices):
        PRODUCT = 'product', 'Product'
        MATERIAL = 'material', 'Material'
        LABOR = 'labor', 'Labor'
        SUBCONTRACTOR = 'subcontractor', 'Subcontractor'
        COMMISSION = 'commission', 'Commission/markup'
        TAX = 'tax', 'Tax'
        ALLOWANCE = 'allowance', 'Allowance'
        OTHER = 'other', 'Other'

    estimate = models.ForeignKey(
        Estimate,
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
        related_name='estimate_line_items',
    )
    description = models.CharField(max_length=200)
    quantity = models.DecimalField(max_digits=10, decimal_places=2, default=1)
    unit_price = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    unit_cost = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    sort_order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ('sort_order', 'pk')

    @property
    def total_price(self):
        return self.quantity * self.unit_price

    @property
    def total_cost(self):
        return self.quantity * self.unit_cost

    @property
    def total_margin(self):
        return self.total_price - self.total_cost

    def clean(self):
        super().clean()
        self.description = self.description.strip()
        errors = {}
        if not self.description:
            errors['description'] = 'Describe this line item.'
        if self.quantity <= 0:
            errors['quantity'] = 'Quantity must be greater than zero.'
        if self.unit_price < 0:
            errors['unit_price'] = 'Unit price cannot be negative.'
        if self.unit_cost < 0:
            errors['unit_cost'] = 'Unit cost cannot be negative.'
        if (
            self.cost_code_id
            and self.estimate_id
            and self.cost_code.organization_id != self.estimate.project.organization_id
        ):
            errors['cost_code'] = 'Choose a cost code from this company.'
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f'{self.estimate.display_number}: {self.description}'


class EstimateDecision(models.Model):
    class Decision(models.TextChoices):
        APPROVED = 'approved', 'Approved'
        DECLINED = 'declined', 'Declined'

    estimate = models.ForeignKey(
        Estimate,
        on_delete=models.CASCADE,
        related_name='decisions',
    )
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='estimate_decisions',
    )
    decision = models.CharField(max_length=10, choices=Decision.choices)
    comment = models.TextField(blank=True)
    decided_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ('decided_at', 'pk')
        constraints = [
            models.UniqueConstraint(
                fields=('estimate', 'decided_by'),
                name='projects_unique_estimate_decision_per_user',
            ),
        ]

    def __str__(self):
        return (
            f'{self.decided_by.email} {self.get_decision_display().lower()} '
            f'{self.estimate.display_number}'
        )


class ProjectCostEntry(models.Model):
    """A staff-recorded actual cost incurred on a project (job costing).

    Distinct from estimate/change-order cost_delta fields, which are planned/committed
    costs: this is what has actually been spent, used to compute an estimated final cost
    and profitability alongside the committed-cost figures.
    """

    class Category(models.TextChoices):
        PRODUCT = 'product', 'Product'
        MATERIAL = 'material', 'Material'
        LABOR = 'labor', 'Labor'
        SUBCONTRACTOR = 'subcontractor', 'Subcontractor'
        COMMISSION = 'commission', 'Commission/markup'
        TAX = 'tax', 'Tax'
        ALLOWANCE = 'allowance', 'Allowance'
        OTHER = 'other', 'Other'

    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name='cost_entries',
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
        related_name='project_cost_entries',
    )
    description = models.CharField(max_length=200)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    incurred_date = models.DateField()
    note = models.TextField(blank=True)
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='project_cost_entries_recorded',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ('-incurred_date', '-pk')
        constraints = [
            models.CheckConstraint(
                condition=models.Q(amount__gt=0),
                name='projects_cost_entry_amount_positive',
            ),
        ]

    def clean(self):
        super().clean()
        self.description = self.description.strip()
        self.note = self.note.strip()
        errors = {}
        if not self.description:
            errors['description'] = 'Describe this cost.'
        if self.amount is not None and self.amount <= 0:
            errors['amount'] = 'Amount must be greater than zero.'
        if (
            self.cost_code_id
            and self.project_id
            and self.cost_code.organization_id != self.project.organization_id
        ):
            errors['cost_code'] = 'Choose a cost code from this company.'
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f'{self.project}: {self.description} (${self.amount})'


class SelectionPackage(models.Model):
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name='selection_packages',
    )
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='selection_packages_created',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ('title', 'pk')
        constraints = [
            models.UniqueConstraint(
                Lower('title'),
                'project',
                name='projects_unique_selection_package_title',
            ),
        ]

    def clean(self):
        super().clean()
        self.title = self.title.strip()
        self.description = self.description.strip()
        if not self.title:
            raise ValidationError({'title': 'Title cannot be blank.'})

    def __str__(self):
        return f'{self.project}: {self.title}'


class FinishSelection(models.Model):
    class Status(models.TextChoices):
        DRAFT = 'draft', 'Draft'
        OPEN = 'open', 'Awaiting client'
        SELECTED = 'selected', 'Selected'
        VOIDED = 'voided', 'Voided'

    class CreditDisposition(models.TextChoices):
        UNDETERMINED = 'undetermined', 'Undetermined'
        APPLY_ELSEWHERE = 'apply_elsewhere', 'Apply to another selection'
        RETURN_AT_CLOSING = 'return_at_closing', 'Return at closing'
        RETAIN_AS_MARGIN = 'retain_as_margin', 'Retain as project margin'

    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name='finish_selections',
    )
    package = models.ForeignKey(
        SelectionPackage,
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
        related_name='choices',
    )
    number = models.PositiveIntegerField()
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    location = models.CharField(max_length=200, blank=True)
    allowance_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
    )
    due_date = models.DateField(blank=True, null=True)
    status = models.CharField(
        max_length=10,
        choices=Status.choices,
        default=Status.DRAFT,
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='finish_selections_created',
    )
    opened_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        blank=True,
        null=True,
        on_delete=models.PROTECT,
        related_name='finish_selections_opened',
    )
    opened_at = models.DateTimeField(blank=True, null=True)
    chosen_option = models.ForeignKey(
        'SelectionOption',
        blank=True,
        null=True,
        on_delete=models.PROTECT,
        related_name='chosen_for_selections',
    )
    selected_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        blank=True,
        null=True,
        on_delete=models.PROTECT,
        related_name='finish_selections_made',
    )
    selected_at = models.DateTimeField(blank=True, null=True)
    client_comment = models.TextField(blank=True)
    voided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        blank=True,
        null=True,
        on_delete=models.PROTECT,
        related_name='finish_selections_voided',
    )
    voided_at = models.DateTimeField(blank=True, null=True)
    credit_disposition = models.CharField(
        max_length=20,
        choices=CreditDisposition.choices,
        default=CreditDisposition.UNDETERMINED,
    )
    credit_disposition_set_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        blank=True,
        null=True,
        on_delete=models.PROTECT,
        related_name='finish_selection_credit_dispositions_set',
    )
    credit_disposition_set_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ('-number', '-pk')
        constraints = [
            models.UniqueConstraint(
                fields=('project', 'number'),
                name='projects_unique_selection_number_per_project',
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        credit_disposition='undetermined',
                        credit_disposition_set_by__isnull=True,
                        credit_disposition_set_at__isnull=True,
                    )
                    | models.Q(
                        credit_disposition__in=(
                            'apply_elsewhere',
                            'return_at_closing',
                            'retain_as_margin',
                        ),
                        credit_disposition_set_by__isnull=False,
                        credit_disposition_set_at__isnull=False,
                    )
                ),
                name='projects_selection_credit_disposition_matches_actor',
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        status='draft',
                        opened_by__isnull=True,
                        opened_at__isnull=True,
                    )
                    | models.Q(
                        status__in=('open', 'selected', 'voided'),
                        opened_by__isnull=False,
                        opened_at__isnull=False,
                    )
                ),
                name='projects_selection_open_matches_status',
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        status='selected',
                        chosen_option__isnull=False,
                        selected_by__isnull=False,
                        selected_at__isnull=False,
                    )
                    | models.Q(
                        status__in=('draft', 'open', 'voided'),
                        chosen_option__isnull=True,
                        selected_by__isnull=True,
                        selected_at__isnull=True,
                    )
                ),
                name='projects_selection_choice_matches_status',
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        status='voided',
                        voided_by__isnull=False,
                        voided_at__isnull=False,
                    )
                    | models.Q(
                        status__in=('draft', 'open', 'selected'),
                        voided_by__isnull=True,
                        voided_at__isnull=True,
                    )
                ),
                name='projects_selection_void_matches_status',
            ),
        ]

    @property
    def display_number(self):
        return f'SEL-{self.number:03d}'

    @property
    def selected_variance(self):
        if not self.chosen_option_id:
            return None
        return self.chosen_option.price - self.allowance_amount

    @property
    def requires_change_order(self):
        return bool(self.selected_variance is not None and self.selected_variance > 0)

    @property
    def has_credit(self):
        return bool(self.selected_variance is not None and self.selected_variance < 0)

    def clean(self):
        super().clean()
        self.title = self.title.strip()
        self.description = self.description.strip()
        self.location = self.location.strip()
        self.client_comment = self.client_comment.strip()
        errors = {}
        if not self.title:
            errors['title'] = 'Title cannot be blank.'
        if self.allowance_amount < 0:
            errors['allowance_amount'] = 'Allowance cannot be negative.'
        if self.status == self.Status.DRAFT:
            if self.opened_by_id or self.opened_at:
                errors['status'] = 'Draft selections cannot have publication details.'
        elif not self.opened_by_id or not self.opened_at:
            errors['status'] = 'Published selections require an authenticated sender.'
        if self.status == self.Status.SELECTED:
            if not self.chosen_option_id or not self.selected_by_id or not self.selected_at:
                errors['status'] = 'A completed selection requires an option and client.'
            elif self.pk and self.chosen_option.selection_id != self.pk:
                errors['chosen_option'] = 'The chosen option must belong to this selection.'
        elif self.chosen_option_id or self.selected_by_id or self.selected_at:
            errors['status'] = 'Only completed selections can have a client choice.'
        if self.status == self.Status.VOIDED:
            if not self.voided_by_id or not self.voided_at:
                errors['status'] = 'Voided selections require an authenticated actor.'
        elif self.voided_by_id or self.voided_at:
            errors['status'] = 'Only voided selections can have void details.'
        if self.credit_disposition != self.CreditDisposition.UNDETERMINED and not self.has_credit:
            errors['credit_disposition'] = (
                'A disposition can only be set when the selection has a credit.'
            )
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f'{self.project}: {self.display_number} {self.title}'


class SelectionOption(models.Model):
    selection = models.ForeignKey(
        FinishSelection,
        on_delete=models.CASCADE,
        related_name='options',
    )
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    price = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    cost = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    is_recommended = models.BooleanField(default=False)
    sort_order = models.PositiveIntegerField(default=0)
    vendor_name = models.CharField(max_length=200, blank=True)
    product_url = models.URLField(blank=True)
    specification = models.TextField(blank=True)
    lead_time = models.CharField(max_length=100, blank=True)
    image = models.FileField(
        storage=private_document_storage,
        upload_to=selection_option_upload_path,
        blank=True,
        null=True,
    )
    attachment = models.FileField(
        storage=private_document_storage,
        upload_to=selection_option_upload_path,
        blank=True,
        null=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ('sort_order', 'pk')
        constraints = [
            models.UniqueConstraint(
                Lower('name'),
                'selection',
                name='projects_unique_selection_option_name',
            ),
        ]

    @property
    def allowance_variance(self):
        return self.price - self.selection.allowance_amount

    @property
    def allowance_variance_magnitude(self):
        return abs(self.allowance_variance)

    @property
    def margin(self):
        return self.price - self.cost

    def clean(self):
        super().clean()
        self.name = self.name.strip()
        self.description = self.description.strip()
        self.vendor_name = self.vendor_name.strip()
        self.product_url = self.product_url.strip()
        self.specification = self.specification.strip()
        self.lead_time = self.lead_time.strip()
        if not self.name:
            raise ValidationError({'name': 'Option name cannot be blank.'})
        if self.price < 0:
            raise ValidationError({'price': 'Option price cannot be negative.'})
        if self.cost < 0:
            raise ValidationError({'cost': 'Option cost cannot be negative.'})

    def __str__(self):
        return f'{self.selection.display_number}: {self.name}'


class SelectionCustomRequest(models.Model):
    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending review'
        REVIEWED = 'reviewed', 'Reviewed'

    selection = models.ForeignKey(
        FinishSelection,
        on_delete=models.CASCADE,
        related_name='custom_requests',
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='selection_custom_requests_made',
    )
    description = models.TextField()
    target_price = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        blank=True,
        null=True,
    )
    status = models.CharField(
        max_length=10,
        choices=Status.choices,
        default=Status.PENDING,
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        blank=True,
        null=True,
        on_delete=models.PROTECT,
        related_name='selection_custom_requests_reviewed',
    )
    reviewed_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ('-created_at', '-pk')
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(
                        status='pending',
                        reviewed_by__isnull=True,
                        reviewed_at__isnull=True,
                    )
                    | models.Q(
                        status='reviewed',
                        reviewed_by__isnull=False,
                        reviewed_at__isnull=False,
                    )
                ),
                name='projects_selection_custom_request_review_matches_status',
            ),
        ]

    def clean(self):
        super().clean()
        self.description = self.description.strip()
        if not self.description:
            raise ValidationError({'description': 'Describe the requested option.'})
        if self.target_price is not None and self.target_price < 0:
            raise ValidationError({'target_price': 'Target price cannot be negative.'})

    def __str__(self):
        return f'{self.selection.display_number}: custom request by {self.requested_by.email}'


class ScheduleMilestone(models.Model):
    class Status(models.TextChoices):
        PLANNED = 'planned', 'Planned'
        IN_PROGRESS = 'in_progress', 'In progress'
        DELAYED = 'delayed', 'Delayed'
        COMPLETED = 'completed', 'Completed'
        CANCELLED = 'cancelled', 'Cancelled'

    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name='schedule_milestones',
    )
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    start_date = models.DateField()
    end_date = models.DateField(blank=True, null=True)
    status = models.CharField(
        max_length=15,
        choices=Status.choices,
        default=Status.PLANNED,
    )
    client_visible = models.BooleanField(default=False)
    internal_notes = models.TextField(blank=True)
    sort_order = models.PositiveIntegerField(default=0)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='schedule_milestones_created',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ('start_date', 'sort_order', 'pk')

    @property
    def effective_end_date(self):
        return self.end_date or self.start_date

    def clean(self):
        super().clean()
        self.title = self.title.strip()
        self.description = self.description.strip()
        self.internal_notes = self.internal_notes.strip()
        errors = {}
        if not self.title:
            errors['title'] = 'Title cannot be blank.'
        if self.end_date and self.start_date and self.end_date < self.start_date:
            errors['end_date'] = 'End date cannot be before the start date.'
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f'{self.project}: {self.title}'
