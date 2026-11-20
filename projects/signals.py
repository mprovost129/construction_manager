import logging

from django.contrib.auth.signals import user_logged_in, user_logged_out
from django.db import DatabaseError
from django.dispatch import receiver

from .models import ActivityEvent, OrganizationMembership
from .services import record_activity

logger = logging.getLogger(__name__)


def record_client_session_event(user, event_type, verb):
    memberships = user.project_memberships.filter(
        role=OrganizationMembership.Role.CLIENT,
        is_active=True,
    ).select_related('project__organization')
    for membership in memberships:
        try:
            record_activity(
                organization=membership.project.organization,
                project=membership.project,
                actor=user,
                event_type=event_type,
                summary=f'{user.email} {verb}.',
                metadata={'user_id': user.pk},
            )
        except DatabaseError:
            logger.warning('Client session audit could not be recorded.')


@receiver(user_logged_in)
def record_client_login(sender, request, user, **kwargs):
    record_client_session_event(user, ActivityEvent.Type.CLIENT_LOGGED_IN, 'logged in')


@receiver(user_logged_out)
def record_client_logout(sender, request, user, **kwargs):
    if user is not None:
        record_client_session_event(
            user,
            ActivityEvent.Type.CLIENT_LOGGED_OUT,
            'logged out',
        )
