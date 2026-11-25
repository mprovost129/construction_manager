"""Recoverable project deletion and deliberate copying of reusable setup."""

from django import forms
import uuid
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from .access import (
    can_manage_project,
    internal_organizations_for_user,
    organization_membership_for,
    projects_for_user,
)
from .models import (
    ActivityEvent,
    FinishSelection,
    JobSiteShare,
    Organization,
    OrganizationMembership,
    Project,
    ProjectInternalAccess,
    SelectionPackage,
    StaffMessageDelivery,
)
from .allowance_groups import copied_budget
from .selection_templates import (
    ROW_FIELDS,
    copied_files,
    copy_options,
    reusable_description,
)
from .services import record_activity


def admin_organizations(user):
    if not user.is_authenticated or getattr(user, "_client_preview_project_id", None):
        return Organization.objects.none()
    if user.is_superuser:
        return Organization.objects.all()
    return (
        internal_organizations_for_user(user)
        .filter(
            memberships__user=user,
            memberships__is_active=True,
            memberships__role=OrganizationMembership.Role.ADMIN,
        )
        .distinct()
    )


def can_copy_project(user, project):
    return (
        can_manage_project(user, project)
        and (internal_organizations_for_user(user, management_only=True)
             .filter(pk=project.organization_id).exists()
             or project.internal_access.filter(membership__user=user,
                 membership__is_active=True, is_active=True, is_project_manager=True).exists())
    )


class ConfirmProjectForm(forms.Form):
    name = forms.CharField(label="Type the project name to confirm", max_length=200)

    def __init__(self, *args, project, **kwargs):
        super().__init__(*args, **kwargs)
        self.project = project

    def clean_name(self):
        if self.cleaned_data["name"] != self.project.name:
            raise forms.ValidationError("The name must match this project exactly.")
        return self.cleaned_data["name"]


class SelectionRows(forms.ModelMultipleChoiceField):
    def label_from_instance(self, selection):
        return f"{selection.package.title + ' — ' if selection.package else ''}{selection.title}"


class CopyProjectForm(forms.Form):
    name = forms.CharField(label="New project name", max_length=200)
    code = forms.CharField(label="New project code", max_length=50, required=False)
    notes = forms.BooleanField(required=False, label="Copy internal project notes")
    dates = forms.BooleanField(required=False, label="Copy project dates")
    selections = SelectionRows(
        queryset=FinishSelection.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        label="Selection rows to copy",
    )
    choices = forms.BooleanField(
        required=False,
        initial=True,
        label="Include choices and customer prices for the checked selections",
    )
    photos = forms.BooleanField(
        required=False, initial=True, label="Include choice photos and attachments"
    )

    def __init__(self, *args, source, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["name"].initial = f"{source.name[:193]} (copy)"
        self.fields["selections"].queryset = source.finish_selections.select_related(
            "package"
        ).order_by("number", "pk")

    def clean(self):
        data = super().clean()
        if data.get("selections") and data.get("photos") and not data.get("choices"):
            self.add_error(
                "photos", "Include choices to copy their photos and attachments."
            )
        return data


@login_required
@require_http_methods(["GET", "HEAD"])
def deleted_projects(request):
    organizations = admin_organizations(request.user)
    if not organizations.exists():
        raise PermissionDenied
    projects = (
        Project.all_objects.filter(
            organization__in=organizations, deleted_at__isnull=False
        )
        .select_related("organization", "deleted_by")
        .order_by("-deleted_at")
    )
    return render(
        request,
        "projects/deleted_projects.html",
        {"page_obj": Paginator(projects, 30).get_page(request.GET.get("page"))},
    )


@login_required
@require_http_methods(["GET", "HEAD", "POST"])
def delete_project(request, pk):
    project = get_object_or_404(
        Project.objects.filter(organization__in=admin_organizations(request.user)),
        pk=pk,
    )
    form = ConfirmProjectForm(
        request.POST if request.method == "POST" else None, project=project
    )
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            project = get_object_or_404(
                Project.objects.select_for_update(), pk=project.pk
            )
            project.deleted_at = timezone.now()
            project.deleted_by = request.user
            project.save(update_fields=["deleted_at", "deleted_by", "updated_at"])
            JobSiteShare.objects.filter(project=project).update(enabled=False, token=uuid.uuid4())
            # Old invitation links must not become valid again after restoration.
            project.invitations.filter(
                accepted_at__isnull=True, revoked_at__isnull=True
            ).update(revoked_at=timezone.now())
            StaffMessageDelivery.objects.filter(
                message__thread__project=project, email_status="pending"
            ).update(email_status="skipped")
            record_activity(
                organization=project.organization,
                project=project,
                actor=request.user,
                event_type=ActivityEvent.Type.PROJECT_UPDATED,
                summary="Project moved to Deleted projects.",
                metadata={"action": "project_deleted"},
            )
        messages.success(
            request,
            "Project deleted. An administrator can restore it from Deleted projects.",
        )
        return redirect("projects:deleted_projects")
    return render(
        request, "projects/project_delete.html", {"project": project, "form": form}
    )


@login_required
@require_http_methods(["GET", "HEAD", "POST"])
def restore_project(request, pk):
    project = get_object_or_404(
        Project.all_objects.filter(
            organization__in=admin_organizations(request.user), deleted_at__isnull=False
        ),
        pk=pk,
    )
    form = ConfirmProjectForm(
        request.POST if request.method == "POST" else None, project=project
    )
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            project = get_object_or_404(
                Project.all_objects.select_for_update(), pk=pk, deleted_at__isnull=False
            )
            project.deleted_at = None
            project.deleted_by = None
            project.save(update_fields=["deleted_at", "deleted_by", "updated_at"])
            record_activity(
                organization=project.organization,
                project=project,
                actor=request.user,
                event_type=ActivityEvent.Type.PROJECT_UPDATED,
                summary="Project restored from Deleted projects.",
                metadata={"action": "project_restored"},
            )
        messages.success(
            request,
            "Project restored with its previous access and records. Send new invitations if needed.",
        )
        return redirect("projects:detail", pk=pk)
    return render(
        request,
        "projects/project_delete.html",
        {"project": project, "form": form, "restoring": True},
    )


def copy_setup(source, user, data):
    with copied_files() as saved:
        source = Project.objects.select_for_update().get(pk=source.pk)
        target = Project.objects.create(
            organization=source.organization,
            name=data["name"],
            code=data.get("code", ""),
            description=source.description if data.get("notes") else "",
            start_date=source.start_date if data.get("dates") else None,
            target_completion_date=source.target_completion_date
            if data.get("dates")
            else None,
            created_by=user,
        )
        membership = organization_membership_for(user, source.organization)
        if membership:
            ProjectInternalAccess.objects.create(
                project=target,
                membership=membership,
                can_manage=True,
                can_invite_clients=True,
                is_project_manager=(membership.role == OrganizationMembership.Role.PROJECT_MANAGER
                    or source.internal_access.filter(membership=membership, is_active=True,
                                                     is_project_manager=True).exists()),
                assigned_by=user,
            )
        packages = {}
        selected_ids = [row.pk for row in data.get("selections", [])]
        rows = (
            source.finish_selections.filter(pk__in=selected_ids)
            .select_related("package")
            .prefetch_related("options__gallery_images")
            .order_by("number", "pk")
        )
        for number, row in enumerate(rows, 1):
            if row.package_id and row.package_id not in packages:
                packages[row.package_id] = SelectionPackage.objects.create(
                    project=target, title=row.package.title, created_by=user, **copied_budget(row.package, [r.allowance_group for r in rows if r.package_id == row.package_id and r.allowance_mode == 'section'])
                )
            new_row = FinishSelection.objects.create(
                project=target,
                number=number,
                package=packages.get(row.package_id),
                created_by=user,
                **{
                    field: reusable_description(row.description, row.title)
                    if field == "description"
                    else getattr(row, field)
                    for field in ROW_FIELDS
                },
            )
            if data.get("choices"):
                copy_options(
                    row.options.all(),
                    new_row,
                    saved,
                    include_files=bool(data.get("photos")),
                )
        record_activity(
            organization=target.organization,
            project=target,
            actor=user,
            event_type=ActivityEvent.Type.PROJECT_CREATED,
            summary="Created a project from selected reusable setup.",
            metadata={
                "source_project_id": source.pk,
                "selection_count": len(selected_ids),
            },
        )
        return target


@login_required
@require_http_methods(["GET", "HEAD", "POST"])
def copy_project(request, pk):
    source = get_object_or_404(projects_for_user(request.user), pk=pk)
    if not can_copy_project(request.user, source):
        raise PermissionDenied
    form = CopyProjectForm(
        request.POST if request.method == "POST" else None, source=source
    )
    if request.method == "POST" and form.is_valid():
        project = copy_setup(source, request.user, form.cleaned_data)
        messages.success(
            request,
            "Project copied. Review its setup and assign its project team before publishing selections.",
        )
        return redirect("projects:detail", pk=project.pk)
    return render(
        request, "projects/project_copy.html", {"project": source, "form": form}
    )
