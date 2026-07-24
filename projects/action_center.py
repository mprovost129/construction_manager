from collections import defaultdict

from django.db.models import Count, Prefetch
from django.utils import timezone

from .access import is_project_client
from .models import (
    ChangeOrder,
    ConversationThread,
    DocumentDecision,
    FinishSelection,
    OrganizationMembership,
    ProjectDocument,
    ProjectDocumentVersion,
    ScheduleMilestone,
)


def build_project_action_center(user, project):
    viewer_is_client = is_project_client(user, project)

    pending_change_orders = list(
        project.change_orders.filter(status=ChangeOrder.Status.PENDING).order_by(
            'number'
        )
    )
    draft_change_orders = []
    if not viewer_is_client:
        draft_change_orders = list(
            project.change_orders.filter(status=ChangeOrder.Status.DRAFT).order_by(
                'number'
            )
        )

    open_selections = list(
        project.finish_selections.filter(
            status=FinishSelection.Status.OPEN
        ).select_related('chosen_option')
    )
    today = timezone.localdate()
    for selection in open_selections:
        selection.is_overdue = bool(
            selection.due_date and selection.due_date < today
        )
    draft_selections = []
    if not viewer_is_client:
        draft_selections = list(
            project.finish_selections.filter(
                status=FinishSelection.Status.DRAFT
            )
        )

    documents = project.documents.filter(
        requires_client_approval=True,
    ).prefetch_related('versions__decisions')
    if viewer_is_client:
        documents = documents.filter(client_visible=True)
    document_actions = []
    for document in documents:
        version = document.latest_version
        if not version:
            continue
        decisions = list(version.decisions.all())
        if viewer_is_client:
            if any(decision.decided_by_id == user.pk for decision in decisions):
                continue
            state = 'Awaiting your decision'
            state_class = 'status-pill--on_hold'
        else:
            decision_values = {decision.decision for decision in decisions}
            if DocumentDecision.Decision.DECLINED in decision_values:
                state = 'Declined'
                state_class = 'status-pill--declined'
            elif not decisions:
                state = 'Awaiting client'
                state_class = 'status-pill--on_hold'
            else:
                continue
        document_actions.append(
            {
                'document': document,
                'version': version,
                'state': state,
                'state_class': state_class,
            }
        )

    delayed_milestones = project.schedule_milestones.filter(
        status=ScheduleMilestone.Status.DELAYED
    )
    if viewer_is_client:
        delayed_milestones = delayed_milestones.filter(client_visible=True)
    delayed_milestones = list(delayed_milestones)

    open_conversations = list(
        project.conversation_threads.filter(status='open').select_related(
            'created_by'
        )
    )

    decision_count = (
        len(pending_change_orders)
        + len(open_selections)
        + len(document_actions)
    )
    draft_count = len(draft_change_orders) + len(draft_selections)
    schedule_count = len(delayed_milestones)

    return {
        'viewer_is_client': viewer_is_client,
        'pending_change_orders': pending_change_orders,
        'draft_change_orders': draft_change_orders,
        'open_selections': open_selections,
        'draft_selections': draft_selections,
        'document_actions': document_actions,
        'delayed_milestones': delayed_milestones,
        'open_conversations': open_conversations,
        'decision_count': decision_count,
        'draft_count': draft_count,
        'schedule_count': schedule_count,
        'conversation_count': len(open_conversations),
        'action_count': decision_count + draft_count + schedule_count,
    }


def build_portfolio_action_center(user, projects):
    projects = list(projects)
    project_ids = [project.pk for project in projects]
    if not project_ids:
        return _portfolio_result([])

    if user.is_superuser:
        internal_project_ids = set(project_ids)
        client_project_ids = set()
    else:
        management_organization_ids = set(
            user.organization_memberships.filter(
                is_active=True,
                role__in=(
                    OrganizationMembership.Role.ADMIN,
                    OrganizationMembership.Role.STAFF,
                ),
            ).values_list('organization_id', flat=True)
        )
        internal_project_ids = {
            project.pk
            for project in projects
            if project.organization_id in management_organization_ids
        }
        client_project_ids = set(
            user.project_memberships.filter(
                project_id__in=project_ids,
                is_active=True,
                role=OrganizationMembership.Role.CLIENT,
            ).values_list('project_id', flat=True)
        )

    allowed_project_ids = internal_project_ids | client_project_ids
    if not allowed_project_ids:
        return _portfolio_result([])

    counts = defaultdict(
        lambda: {
            'decision_count': 0,
            'draft_count': 0,
            'schedule_count': 0,
            'conversation_count': 0,
        }
    )

    change_order_counts = (
        ChangeOrder.objects.filter(
            project_id__in=allowed_project_ids,
            status__in=(ChangeOrder.Status.DRAFT, ChangeOrder.Status.PENDING),
        )
        .values('project_id', 'status')
        .annotate(total=Count('pk'))
    )
    for row in change_order_counts:
        if row['status'] == ChangeOrder.Status.PENDING:
            counts[row['project_id']]['decision_count'] += row['total']
        elif row['project_id'] in internal_project_ids:
            counts[row['project_id']]['draft_count'] += row['total']

    selection_counts = (
        FinishSelection.objects.filter(
            project_id__in=allowed_project_ids,
            status__in=(FinishSelection.Status.DRAFT, FinishSelection.Status.OPEN),
        )
        .values('project_id', 'status')
        .annotate(total=Count('pk'))
    )
    for row in selection_counts:
        if row['status'] == FinishSelection.Status.OPEN:
            counts[row['project_id']]['decision_count'] += row['total']
        elif row['project_id'] in internal_project_ids:
            counts[row['project_id']]['draft_count'] += row['total']

    documents = ProjectDocument.objects.filter(
        project_id__in=allowed_project_ids,
        requires_client_approval=True,
    ).prefetch_related(
        Prefetch(
            'versions',
            queryset=ProjectDocumentVersion.objects.prefetch_related('decisions'),
        )
    )
    for document in documents:
        viewer_is_client = document.project_id in client_project_ids
        if viewer_is_client and not document.client_visible:
            continue
        versions = list(document.versions.all())
        if not versions:
            continue
        decisions = list(versions[0].decisions.all())
        if viewer_is_client:
            requires_action = not any(
                decision.decided_by_id == user.pk for decision in decisions
            )
        else:
            decision_values = {decision.decision for decision in decisions}
            requires_action = (
                DocumentDecision.Decision.DECLINED in decision_values
                or not decisions
            )
        if requires_action:
            counts[document.project_id]['decision_count'] += 1

    milestone_counts = (
        ScheduleMilestone.objects.filter(
            project_id__in=allowed_project_ids,
            status=ScheduleMilestone.Status.DELAYED,
        )
        .values('project_id', 'client_visible')
        .annotate(total=Count('pk'))
    )
    for row in milestone_counts:
        if row['project_id'] in client_project_ids and not row['client_visible']:
            continue
        counts[row['project_id']]['schedule_count'] += row['total']

    conversation_counts = (
        ConversationThread.objects.filter(
            project_id__in=allowed_project_ids,
            status=ConversationThread.Status.OPEN,
        )
        .values('project_id')
        .annotate(total=Count('pk'))
    )
    for row in conversation_counts:
        counts[row['project_id']]['conversation_count'] = row['total']

    project_summaries = []
    for project in projects:
        if project.pk not in allowed_project_ids:
            continue
        project_counts = counts[project.pk]
        action_count = (
            project_counts['decision_count']
            + project_counts['draft_count']
            + project_counts['schedule_count']
        )
        project_summaries.append(
            {
                'project': project,
                'viewer_is_client': project.pk in client_project_ids,
                **project_counts,
                'action_count': action_count,
            }
        )

    return _portfolio_result(project_summaries)


def _portfolio_result(project_summaries):
    priority_projects = sorted(
        (
            summary
            for summary in project_summaries
            if summary['action_count'] or summary['conversation_count']
        ),
        key=lambda summary: (
            -summary['action_count'],
            -summary['conversation_count'],
            summary['project'].organization.name.lower(),
            summary['project'].name.lower(),
        ),
    )

    return {
        'project_summaries': project_summaries,
        'priority_projects': priority_projects,
        'project_count': len(project_summaries),
        'projects_with_attention_count': len(priority_projects),
        'decision_count': sum(
            summary['decision_count'] for summary in project_summaries
        ),
        'draft_count': sum(
            summary['draft_count'] for summary in project_summaries
        ),
        'schedule_count': sum(
            summary['schedule_count'] for summary in project_summaries
        ),
        'conversation_count': sum(
            summary['conversation_count'] for summary in project_summaries
        ),
        'action_count': sum(
            summary['action_count'] for summary in project_summaries
        ),
        'viewer_has_internal_scope': any(
            not summary['viewer_is_client'] for summary in project_summaries
        ),
    }
