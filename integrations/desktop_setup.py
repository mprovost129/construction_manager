"""Provision and revoke the shared, verification-only Desktop connection."""
from urllib.parse import urlsplit
from xml.etree import ElementTree as ET

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured, PermissionDenied, ValidationError
from django.db import transaction
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.debug import sensitive_variables

from projects.access import organization_membership_for
from projects.models import Organization
from projects.services import record_activity

from .desktop_models import DesktopConnection, DesktopSession
from .desktop_protocol import company_file_digest


def allowed(user, organization, action='view'):
    if not user.is_authenticated or not user.is_active:
        return False
    if user.is_superuser:
        return True
    membership = organization_membership_for(user, organization)
    return bool(membership and {
        'view': membership.can_view_accounting,
        'manage': membership.can_manage_desktop,
        'check': membership.can_check_desktop,
    }[action])


def require_access(user, organization, action):
    if not allowed(user, organization, action):
        raise PermissionDenied('Your administrator has not granted this QuickBooks permission.')


def current_connection(organization, *, lock=False):
    query = DesktopConnection.objects.filter(organization=organization).order_by('-active', '-pk')
    if lock:
        query = query.select_for_update()
    return query.first()


def audit(organization, actor, summary, connection):
    record_activity(organization=organization, actor=actor,
        event_type='desktop_connection_changed', summary=summary,
        metadata={'connection_id': connection.pk, 'revision': connection.configuration_revision})


@sensitive_variables()
@transaction.atomic
def configure(*, organization, actor, company_name, file_path, password, revision):
    Organization.objects.select_for_update().get(pk=organization.pk)
    require_access(actor, organization, 'manage')
    connection = current_connection(organization, lock=True)
    if revision != (connection.configuration_revision if connection else 0):
        raise ValidationError('The connection changed. Reload this page before continuing.')
    if connection and connection.active:
        raise ValidationError('Disconnect the existing connection before changing its configuration.')
    connection = connection or DesktopConnection(organization=organization)
    # FileID stays stable when resetting credentials for the same file. A different
    # file must be configured separately in a future multi-company workflow.
    path_digest = company_file_digest(file_path)
    if connection.pk and connection.company_file_digest != path_digest:
        raise ValidationError('This setup is bound to a different file location. Contact the site owner to review a file move before reconnecting.')
    connection.expected_company_name = company_name.strip()
    connection.company_file_digest = path_digest
    connection.set_password(password)
    connection.configuration_revision = revision + 1
    connection.active = True
    connection.verified_at = None
    connection.last_error_code = ''
    connection.check_requested_at = timezone.now()
    connection.save()
    DesktopSession.objects.filter(connection=connection).delete()
    audit(organization, actor, f'{actor.email} prepared the QuickBooks company-file check.', connection)
    return connection


@transaction.atomic
def change_connection(*, organization, actor, action, revision):
    Organization.objects.select_for_update().get(pk=organization.pk)
    require_access(actor, organization, 'manage' if action == 'disconnect' else 'check')
    connection = current_connection(organization, lock=True)
    if not connection or not connection.active:
        raise ValidationError('There is no active connection to update.')
    if connection.configuration_revision != revision:
        raise ValidationError('The connection changed. Reload this page before continuing.')
    if action == 'disconnect':
        connection.active = False
        connection.password_hash = '!'
        connection.check_requested_at = None
        connection.verified_at = None
        connection.configuration_revision += 1
        connection.last_error_code = ''
        DesktopSession.objects.filter(connection=connection).delete()
        summary = f'{actor.email} disconnected QuickBooks and revoked its connector credentials.'
    elif action == 'check':
        if connection.check_requested_at:
            return connection
        connection.check_requested_at = timezone.now()
        connection.last_error_code = ''
        summary = f'{actor.email} requested a QuickBooks company-file check.'
    else:
        raise ValidationError('Unsupported connection action.')
    connection.save()
    audit(organization, actor, summary, connection)
    return connection


def public_base_url():
    base = settings.SITE_BASE_URL.rstrip('/')
    parsed = urlsplit(base)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path:
        raise ImproperlyConfigured('Configure SITE_BASE_URL as the canonical HTTPS origin before distributing connection files.')
    return base


def qwc_document(connection):
    base = public_base_url()
    root = ET.Element('QBWCXML')
    values = {
        'AppName': f'Heritage 360 - {connection.organization.slug}',
        'AppID': '',
        'AppURL': base + reverse('integrations:desktop_connector'),
        'AppDescription': 'Verify the selected QuickBooks company. No financial transfers.',
        'AppSupport': base + reverse('integrations:desktop_support'),
        'UserName': str(connection.username),
        'OwnerID': '{' + str(connection.owner_id).upper() + '}',
        'FileID': '{' + str(connection.file_id).upper() + '}',
        'QBType': 'QBFS',
        'IsReadOnly': 'true',
        'PersonalDataPref': 'pdpNotNeeded',
        'UnattendedModePref': 'umpOptional',
    }
    for name, value in values.items():
        ET.SubElement(root, name).text = value
    # Deliberately omit Scheduler and CompanyFile: the operator opens the intended
    # file and runs Update Selected. Neither credentials nor local paths are exported.
    return ET.tostring(root, encoding='utf-8', xml_declaration=True)
