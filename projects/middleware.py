import logging

from django.db import DatabaseError

from .models import ActivityEvent, OrganizationMembership
from .services import record_activity

logger = logging.getLogger(__name__)


class ClientViewAuditMiddleware:
    """Record successful authenticated client page views without query data."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if (
            request.method != 'GET'
            or response.status_code >= 400
            or not request.user.is_authenticated
            or request.resolver_match is None
        ):
            return response
        kwargs = request.resolver_match.kwargs
        project_id = kwargs.get('pk') or kwargs.get('project_id')
        if not project_id:
            return response
        membership = request.user.project_memberships.filter(
            project_id=project_id,
            role=OrganizationMembership.Role.CLIENT,
            is_active=True,
        ).select_related('project__organization').first()
        if membership is None:
            return response
        project = membership.project
        try:
            record_activity(
                organization=project.organization,
                project=project,
                actor=request.user,
                event_type=ActivityEvent.Type.CLIENT_VIEWED,
                summary=f'{request.user.email} viewed {request.path}.',
                metadata={
                    'path': request.path[:500],
                    'view_name': request.resolver_match.view_name or '',
                },
            )
        except DatabaseError:
            logger.warning('Client page-view audit could not be recorded.')
        return response
