from django.utils import timezone

from .access import can_use_action_center, is_project_client
from .models import ChangeOrder, DocumentDecision, FinishSelection, ScheduleMilestone


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
    project_summaries = []

    for project in projects:
        if not can_use_action_center(user, project):
            continue
        action_center = build_project_action_center(user, project)
        project_summaries.append(
            {
                'project': project,
                'viewer_is_client': action_center['viewer_is_client'],
                'decision_count': action_center['decision_count'],
                'draft_count': action_center['draft_count'],
                'schedule_count': action_center['schedule_count'],
                'conversation_count': action_center['conversation_count'],
                'action_count': action_center['action_count'],
            }
        )

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
