from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.management import call_command
from django.db import DatabaseError
from django.http import HttpResponse
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from projects.models import ActivityEvent, Organization, OrganizationMembership
from projects.services import send_notification_email

from .models import OperationalEvent
from .operations import (
    OperationalFailureMiddleware,
    can_view_operations,
    record_operational_event,
)


class OperationsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        users = get_user_model()
        cls.developer = users.objects.create_superuser('developer@example.com', 'test-password')
        cls.staff = users.objects.create_user('staff@example.com', 'test-password', is_staff=True)
        cls.member = users.objects.create_user('member@example.com', 'test-password')
        cls.company = Organization.objects.create(name='Private company', slug='private')
        OrganizationMembership.objects.create(
            organization=cls.company, user=cls.staff,
            role=OrganizationMembership.Role.ADMIN,
        )

    def test_access_requires_explicit_developer_permission(self):
        url = reverse('core:operations')
        self.assertRedirects(self.client.get(url), f'/accounts/login/?next={url}')
        for user in (self.staff, self.member):
            self.client.force_login(user)
            response = self.client.get(url)
            self.assertEqual(response.status_code, 403)
            self.assertIn('no-store', response['Cache-Control'])
            self.assertNotContains(self.client.get(reverse('core:home')), '> Operations</a>')
        self.member.user_permissions.add(Permission.objects.get(codename='view_operations'))
        self.client.force_login(self.member)
        self.assertContains(self.client.get(url), 'Developer workspace')
        self.member.is_active = False
        self.assertFalse(can_view_operations(self.member))

    def test_switch_keeps_identity_and_operations_is_not_cached(self):
        self.client.force_login(self.developer)
        self.assertContains(self.client.get(reverse('core:home')), '> Operations</a>')
        response = self.client.get(reverse('core:operations'))
        self.assertContains(response, 'href="/">Open app</a>')
        self.assertContains(response, 'aria-current="page">Operations</a>')
        self.assertIn('no-store', response['Cache-Control'])
        self.assertEqual(int(self.client.session['_auth_user_id']), self.developer.pk)

    def test_database_failure_renders_unknown_without_workspace_queries(self):
        self.client.force_login(self.developer)
        with patch('core.operations.database_probe', return_value=(False, None)), patch(
            'projects.context_processors.workspace_navigation',
            side_effect=AssertionError('Must not render workspace context'),
        ):
            response = self.client.get(reverse('core:operations'))
        self.assertContains(response, 'Unavailable')
        self.assertContains(response, 'Counts are unknown')

    def test_missing_operational_table_does_not_report_zero(self):
        self.client.force_login(self.developer)
        with patch('core.operations.OperationalEvent.objects.filter',
                   side_effect=DatabaseError('private database error')):
            response = self.client.get(reverse('core:operations'))
        self.assertContains(response, 'Counts are unknown')
        self.assertNotContains(response, 'private database error')

    @override_settings(
        OPERATIONS_DASHBOARD_URLS={'Sentry': 'https://secret@example.com/?token=private'},
        TURNSTILE_SECRET_KEY='private-turnstile-value',
        EMAIL_HOST_PASSWORD='private-mail-value',
    )
    def test_snapshot_counts_and_history_do_not_expose_payloads(self):
        self.client.force_login(self.developer)
        OperationalEvent.objects.create(kind=OperationalEvent.Kind.EMAIL_FAILED)
        old = OperationalEvent.objects.create(kind=OperationalEvent.Kind.EMAIL_FAILED)
        OperationalEvent.objects.filter(pk=old.pk).update(
            created_at=timezone.now() - timedelta(days=2)
        )
        ActivityEvent.objects.create(
            organization=self.company, event_type=ActivityEvent.Type.CLIENT_VIEWED,
            summary='private-client-text', metadata={'secret': 'private-metadata'},
        )
        response = self.client.get(reverse('core:operations'))
        self.assertContains(response, '1</strong><span>Notification send failures')
        self.assertContains(response, 'Client viewed a page')
        self.assertContains(response, 'https://sentry.io/')
        for value in ('private-client-text', 'private-metadata', 'private-turnstile-value',
                      'private-mail-value', 'token=private', 'Private company'):
            self.assertNotContains(response, value)

    def test_health_probe_returns_only_minimal_status(self):
        for healthy, code, status in ((True, 200, 'ok'), (False, 503, 'unavailable')):
            with patch('core.operations_views.database_probe', return_value=(healthy, 1)):
                response = self.client.get(reverse('core:health'))
            self.assertEqual(response.status_code, code)
            self.assertEqual(response.json(), {'status': status})
            self.assertIn('no-store', response['Cache-Control'])
        self.assertEqual(OperationalEvent.objects.count(), 0)

    def test_server_failure_records_no_request_content(self):
        middleware = OperationalFailureMiddleware(lambda request: HttpResponse(status=500))
        request = RequestFactory().get('/private-client/?token=secret')
        self.assertEqual(middleware(request).status_code, 500)
        event = OperationalEvent.objects.get()
        self.assertEqual(event.kind, OperationalEvent.Kind.REQUEST_FAILED)
        self.assertEqual(set(event.__dict__) - {'_state'}, {'id', 'kind', 'created_at'})

    def test_recording_failure_and_closed_stderr_do_not_escape(self):
        with patch('core.operations.OperationalEvent.objects.create',
                   side_effect=DatabaseError('private detail')), patch(
            'sys.stderr.write', side_effect=OSError('closed'),
        ):
            record_operational_event(OperationalEvent.Kind.REQUEST_FAILED)
        self.assertEqual(OperationalEvent.objects.count(), 0)

    def test_notification_failures_and_test_sends_are_distinguished(self):
        args = dict(subject='Private subject', message='Private body',
                    recipient_list=['recipient@example.com'])
        with patch('projects.services.send_mail', side_effect=RuntimeError('mail failed')):
            self.assertIsNone(send_notification_email(**args))
        self.assertEqual(send_notification_email(**args), 1)
        self.assertEqual(set(OperationalEvent.objects.values_list('kind', flat=True)),
                         {'email_failed', 'email_test'})

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.smtp.EmailBackend')
    def test_external_acceptance_is_not_labeled_delivered(self):
        with patch('projects.services.send_mail', return_value=1):
            send_notification_email(subject='Test', message='Test',
                                    recipient_list=['recipient@example.com'])
        self.assertEqual(OperationalEvent.objects.get().kind, 'email_accepted')

    def test_retention_removes_only_old_operational_events(self):
        old = OperationalEvent.objects.create(kind='email_failed')
        current = OperationalEvent.objects.create(kind='email_failed')
        OperationalEvent.objects.filter(pk=old.pk).update(
            created_at=timezone.now() - timedelta(days=40)
        )
        call_command('prune_operational_events', dry_run=True, stdout=StringIO())
        self.assertEqual(OperationalEvent.objects.count(), 2)
        call_command('prune_operational_events', stdout=StringIO())
        self.assertEqual(list(OperationalEvent.objects.values_list('pk', flat=True)), [current.pk])
