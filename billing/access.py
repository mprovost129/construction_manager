from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404

from projects.access import (
    can_manage_project,
    is_project_client,
    organization_membership_for,
    projects_for_user,
)

from .models import Invoice


def can_use_invoices(user, project):
    if not user.is_authenticated:
        return False
    if user.is_superuser or is_project_client(user, project):
        return True
    membership = organization_membership_for(user, project.organization)
    return bool(
        membership
        and membership.is_internal
        and membership.can_view_project_financials
        and projects_for_user(user).filter(pk=project.pk).exists()
    )


def invoice_project_or_404(user, project_id):
    project = get_object_or_404(
        projects_for_user(user).select_related('organization'),
        pk=project_id,
    )
    if not can_use_invoices(user, project):
        raise PermissionDenied('You cannot access project invoices.')
    return project


def visible_invoices(user, project):
    invoices = project.invoices.select_related('created_by', 'issued_by')
    if is_project_client(user, project):
        invoices = invoices.exclude(status=Invoice.Status.DRAFT)
    return invoices


def visible_invoice_or_404(user, project, invoice_id):
    return get_object_or_404(
        visible_invoices(user, project).prefetch_related('line_items'),
        pk=invoice_id,
    )


def require_invoice_manager(user, project):
    if not can_manage_project(user, project):
        raise PermissionDenied('You cannot manage invoices for this project.')

