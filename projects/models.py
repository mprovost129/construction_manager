import uuid
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models.functions import Lower
from django.utils import timezone


def invitation_expiry():
    return timezone.now() + timedelta(days=7)


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
        STAFF = 'staff', 'Staff'
        ACCOUNTANT = 'accountant', 'Accountant'
        CLIENT = 'client', 'Client'
        SUBCONTRACTOR = 'subcontractor', 'Subcontractor'

    INTERNAL_ROLES = (Role.ADMIN, Role.STAFF, Role.ACCOUNTANT)
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
        return self.role in (self.Role.ADMIN, self.Role.STAFF)

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
                OrganizationMembership.Role.STAFF,
                OrganizationMembership.Role.STAFF.label,
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
        MESSAGE_THREAD_CREATED = 'message_thread_created', 'Message thread created'
        MESSAGE_SENT = 'message_sent', 'Message sent'
        MESSAGE_THREAD_STATUS_CHANGED = (
            'message_thread_status_changed',
            'Message thread status changed',
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
