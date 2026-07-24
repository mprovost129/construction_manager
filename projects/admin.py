from django.contrib import admin

from .models import (
    ActivityEvent,
    ConversationMessage,
    ConversationThread,
    Organization,
    OrganizationInvitation,
    OrganizationMembership,
    Project,
    ProjectInvitation,
    ProjectMembership,
)


class OrganizationMembershipInline(admin.TabularInline):
    model = OrganizationMembership
    extra = 0


@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin):
    list_display = ('name', 'slug', 'created_at')
    search_fields = ('name', 'slug')
    prepopulated_fields = {'slug': ('name',)}
    inlines = (OrganizationMembershipInline,)


class ProjectMembershipInline(admin.TabularInline):
    model = ProjectMembership
    extra = 0


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ('name', 'organization', 'status', 'updated_at')
    list_filter = ('organization', 'status')
    search_fields = ('name', 'code', 'organization__name')
    inlines = (ProjectMembershipInline,)


@admin.register(OrganizationMembership)
class OrganizationMembershipAdmin(admin.ModelAdmin):
    list_display = ('user', 'organization', 'role', 'is_active')
    list_filter = ('organization', 'role', 'is_active')
    search_fields = ('user__email', 'organization__name')


@admin.register(ProjectMembership)
class ProjectMembershipAdmin(admin.ModelAdmin):
    list_display = ('user', 'project', 'role', 'is_active', 'joined_at')
    list_filter = ('role', 'is_active', 'project__organization')
    search_fields = ('user__email', 'project__name')


@admin.register(ProjectInvitation)
class ProjectInvitationAdmin(admin.ModelAdmin):
    list_display = ('email', 'project', 'role', 'created_at', 'accepted_at')
    list_filter = ('role', 'project__organization', 'accepted_at', 'revoked_at')
    search_fields = ('email', 'project__name')
    readonly_fields = ('token', 'created_at', 'accepted_at')


@admin.register(OrganizationInvitation)
class OrganizationInvitationAdmin(admin.ModelAdmin):
    list_display = ('email', 'organization', 'role', 'created_at', 'accepted_at')
    list_filter = ('role', 'organization', 'accepted_at', 'revoked_at')
    search_fields = ('email', 'organization__name')
    readonly_fields = ('token', 'created_at', 'accepted_at')


@admin.register(ActivityEvent)
class ActivityEventAdmin(admin.ModelAdmin):
    list_display = ('created_at', 'organization', 'project', 'event_type', 'actor')
    list_filter = ('event_type', 'organization')
    search_fields = ('summary', 'actor__email', 'project__name')
    readonly_fields = (
        'organization',
        'project',
        'actor',
        'event_type',
        'summary',
        'metadata',
        'created_at',
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class ConversationMessageInline(admin.TabularInline):
    model = ConversationMessage
    extra = 0
    readonly_fields = ('author', 'body', 'created_at')
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(ConversationThread)
class ConversationThreadAdmin(admin.ModelAdmin):
    list_display = ('subject', 'project', 'status', 'created_by', 'updated_at')
    list_filter = ('status', 'project__organization')
    search_fields = ('subject', 'project__name', 'created_by__email')
    readonly_fields = ('project', 'subject', 'created_by', 'created_at', 'updated_at')
    inlines = (ConversationMessageInline,)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
