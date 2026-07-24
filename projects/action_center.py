from django.utils import timezone

from .access import is_project_client
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
