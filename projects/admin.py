from django.contrib import admin

from .models import (
    Organization,
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
