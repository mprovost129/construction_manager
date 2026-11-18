"""Installation, delegated access and credential lifecycle regression tests."""
from xml.etree import ElementTree as ET

from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import check_password
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from integrations.desktop_forms import ConnectionForm
from integrations.desktop_models import DesktopConnection, DesktopSession
from integrations.desktop_protocol import authenticate, dispatch, ProtocolError
from integrations.desktop_setup import configure, change_connection
from projects.forms import TeamMembershipForm
from projects.models import ActivityEvent, Organization, OrganizationMembership


@override_settings(QUICKBOOKS_DESKTOP_CONNECTOR_ENABLED=True,
                   SITE_BASE_URL='https://heritage.example', APP_ENVIRONMENT='test')
class DesktopSetupTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='Example Construction', slug='example')
        self.other = Organization.objects.create(name='Other', slug='other')
        self.admin = get_user_model().objects.create_user('admin@example.test', password='Heritage-test-login-54!')
        self.operator = get_user_model().objects.create_user('operator@example.test', password='Operator-test-login-54!')
        self.admin_member = OrganizationMembership.objects.create(organization=self.org, user=self.admin, role='admin')
        self.member = OrganizationMembership.objects.create(organization=self.org, user=self.operator,
            role='accountant', accounting_view=True, desktop_manage=False, desktop_check=False)
        self.url = reverse('integrations:desktop_connection', args=[self.org.slug])
        self.download = reverse('integrations:desktop_qwc', args=[self.org.slug])
        self.path = r'C:\Accounting\Example.qbw'
        self.password = 'Connector-test-password-948!'
        self.client.force_login(self.admin)

    def provision(self):
        return configure(organization=self.org, actor=self.admin, company_name=self.org.name,
                         file_path=self.path, password=self.password, revision=0)

    def data(self, **overrides):
        data = dict(action='configure', company_name=self.org.name, file_path=self.path,
            connector_password=self.password, confirm_connector_password=self.password,
            password='Heritage-test-login-54!', prepared='on', revision=0)
        data.update(overrides)
        return data

    def grant(self, **values):
        OrganizationMembership.objects.filter(pk=self.member.pk).update(**values)

    def test_prepare_download_contains_no_secrets_or_file_path(self):
        response = self.client.post(self.url, self.data())
        self.assertRedirects(response, self.url)
        connection = DesktopConnection.objects.get()
        self.assertTrue(check_password(self.password, connection.password_hash))
        self.assertIsNone(connection.verified_at)
        self.assertIsNotNone(connection.check_requested_at)
        download = self.client.get(self.download)
        self.assertEqual(download.status_code, 200)
        self.assertIn('no-store', download['Cache-Control'])
        document = ET.fromstring(download.content)
        self.assertEqual(document.findtext('IsReadOnly'), 'true')
        self.assertEqual(document.findtext('PersonalDataPref'), 'pdpNotNeeded')
        self.assertEqual(document.findtext('UserName'), str(connection.username))
        self.assertEqual(document.findtext('FileID').strip('{}').lower(), str(connection.file_id))
        self.assertEqual(document.findtext('AppURL'), 'https://heritage.example' + reverse('integrations:desktop_connector'))
        for field in ('Password', 'CompanyFile', 'Scheduler'):
            self.assertIsNone(document.find(field))
        self.assertNotIn(self.password.encode(), download.content)
        self.assertNotIn(self.path.encode(), download.content)
        audit = str(list(ActivityEvent.objects.values('summary', 'metadata')))
        self.assertNotIn(self.path, audit)
        self.assertNotIn(self.password, audit)

    def test_reauthentication_and_confirmation_required(self):
        for override in ({'password': 'wrong'}, {'prepared': ''}, {'confirm_connector_password': 'different'},
                         {'connector_password': 'short'}, {'file_path': 'relative.qbw'}):
            with self.subTest(override=override):
                self.assertEqual(self.client.post(self.url, self.data(**override)).status_code, 200)
                self.assertFalse(DesktopConnection.objects.exists())

    def test_windows_paths_and_password_reuse(self):
        for path in (self.path, r'\\office\accounts\Example.qbw'):
            self.assertTrue(ConnectionForm(self.data(file_path=path), user=self.admin).is_valid())
        for path in ('Example.qbw', r'C:Example.qbw', r'C:\folder\*.qbw', 'https://example/file.qbw', '/tmp/a.qbw'):
            self.assertFalse(ConnectionForm(self.data(file_path=path), user=self.admin).is_valid())
        login_password = 'Heritage-test-login-54!'
        form = ConnectionForm(self.data(connector_password=login_password,
            confirm_connector_password=login_password), user=self.admin)
        self.assertFalse(form.is_valid())

    def test_view_check_and_manage_are_separate_and_revocable(self):
        connection = self.provision()
        self.client.force_login(self.operator)
        self.assertContains(self.client.get(self.url), 'Check queued')
        self.assertNotContains(self.client.get(self.url), 'href="' + self.download + '"')
        self.assertEqual(self.client.get(self.download).status_code, 403)
        self.assertEqual(self.client.post(self.url, {'action': 'check', 'revision': 1}).status_code, 403)
        self.grant(desktop_check=True)
        self.assertEqual(self.client.post(self.url, {'action': 'check', 'revision': 1}).status_code, 302)
        self.assertEqual(self.client.get(self.download).status_code, 403)
        self.grant(desktop_manage=True, desktop_check=False)
        self.assertEqual(self.client.get(self.download).status_code, 200)
        self.assertEqual(self.client.post(self.url, {'action': 'check', 'revision': 1}).status_code, 403)
        self.grant(accounting_view=False)
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.assertEqual(self.client.get(self.download).status_code, 404)
        self.assertEqual(self.client.post(self.url, {'action': 'check', 'revision': 1}).status_code, 404)

    def test_client_inactive_and_other_company_cannot_use_grants(self):
        self.provision()
        self.client.force_login(self.operator)
        for values in ({'role': 'client', 'desktop_manage': True, 'desktop_check': True},
                       {'role': 'accountant', 'is_active': False},
                       {'is_active': True, 'organization': self.other}):
            self.grant(**values)
            for method, url in ((self.client.get, self.url), (self.client.get, self.download), (self.client.post, self.url)):
                self.assertEqual(method(url).status_code, 404)

    def test_admin_can_be_denied_and_flags_require_view(self):
        self.provision()
        self.admin_member.desktop_manage = False
        self.admin_member.desktop_check = False
        self.admin_member.save()
        self.assertEqual(self.client.get(self.download).status_code, 403)
        self.assertEqual(self.client.post(self.url, {'action': 'check', 'revision': 1}).status_code, 403)
        for field in ('desktop_manage', 'desktop_check'):
            form = TeamMembershipForm({'role': 'accountant', 'is_active': 'on', field: 'on'}, instance=self.member)
            self.assertFalse(form.is_valid())
            self.assertIn(field, form.errors)

    def test_disconnect_and_reset_revoke_password_and_live_session(self):
        connection = self.provision()
        ticket, _ = authenticate(str(connection.username), self.password, 'test')
        response = self.client.post(self.url, {'action': 'disconnect', 'revision': 1,
            'password': 'Heritage-test-login-54!', 'confirm': 'on'})
        self.assertRedirects(response, self.url)
        self.assertFalse(DesktopSession.objects.exists())
        self.assertEqual(authenticate(str(connection.username), self.password, 'test'), ['', 'nvu'])
        with self.assertRaises(ProtocolError):
            dispatch('closeConnection', {'ticket': ticket})
        new = configure(organization=self.org, actor=self.admin, company_name=self.org.name,
            file_path=self.path, password='A-new-test-connector-password-52!', revision=2)
        self.assertEqual(new.pk, connection.pk)
        self.assertEqual(new.owner_id, connection.owner_id)
        self.assertEqual(new.file_id, connection.file_id)
        self.assertEqual(new.configuration_revision, 3)
        self.assertEqual(authenticate(str(connection.username), self.password, 'test'), ['', 'nvu'])
        self.assertTrue(authenticate(str(connection.username), 'A-new-test-connector-password-52!', 'test')[0])

    def test_stale_forms_duplicate_prepare_and_file_move_fail_closed(self):
        connection = self.provision()
        self.assertContains(self.client.post(self.url, self.data()), 'Connect QuickBooks Desktop')
        self.assertEqual(DesktopConnection.objects.count(), 1)
        with self.assertRaises(ValidationError):
            change_connection(organization=self.org, actor=self.admin, action='disconnect', revision=0)
        change_connection(organization=self.org, actor=self.admin, action='disconnect', revision=1)
        with self.assertRaises(ValidationError):
            configure(organization=self.org, actor=self.admin, company_name=self.org.name,
                      file_path=r'C:\Different.qbw', password=self.password, revision=2)
        connection.refresh_from_db()
        self.assertFalse(connection.active)

    def test_pending_requests_are_idempotent_and_idle_login_keeps_verification(self):
        connection = self.provision()
        DesktopConnection.objects.filter(pk=connection.pk).update(check_requested_at=None, verified_at=timezone.now())
        self.assertEqual(authenticate(str(connection.username), self.password, 'test'), ['', 'none'])
        connection.refresh_from_db()
        self.assertIsNotNone(connection.verified_at)
        for _ in range(2):
            change_connection(organization=self.org, actor=self.admin, action='check', revision=1)
        self.assertEqual(ActivityEvent.objects.filter(summary__contains='requested a QuickBooks').count(), 1)

    def test_only_one_active_connection_per_company(self):
        self.provision()
        with self.assertRaises(IntegrityError), transaction.atomic():
            DesktopConnection.objects.create(organization=self.org, active=True)

    @override_settings(MFA_REQUIRED_FOR_ADMINS=True)
    def test_accounting_access_requires_mfa(self):
        self.provision()
        self.client.force_login(self.operator)
        self.assertTrue(self.client.get(self.url).url.startswith(reverse('users:mfa_setup')))
        self.assertTrue(self.client.get(self.download).url.startswith(reverse('users:mfa_setup')))
        self.assertEqual(self.client.post(self.url, {'action': 'check', 'revision': 1}).status_code, 403)

    def test_csrf_and_turnstile_protect_browser_mutations(self):
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.admin)
        self.assertEqual(csrf_client.post(self.url, self.data()).status_code, 403)
        with override_settings(TURNSTILE_FORMS_ENABLED=True):
            self.assertEqual(self.client.post(self.url, self.data()).status_code, 403)
        self.assertFalse(DesktopConnection.objects.exists())

    @override_settings(QUICKBOOKS_DESKTOP_CONNECTOR_ENABLED=False)
    def test_disabled_flag_blocks_configuration_and_download(self):
        self.provision()
        self.assertEqual(self.client.post(self.url, self.data()).status_code, 403)
        self.assertEqual(self.client.get(self.download).status_code, 404)

    def test_public_support_and_soap_contract_do_not_disclose_company(self):
        self.provision()
        self.client.logout()
        support = self.client.get(reverse('integrations:desktop_support'))
        self.assertEqual(support.status_code, 200)
        self.assertNotContains(support, self.org.name)
        response = self.client.get(reverse('integrations:desktop_connector') + '?wsdl')
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(self.org.name.encode(), response.content)
        root = ET.fromstring(response.content)
        self.assertEqual(root.tag, '{http://schemas.xmlsoap.org/wsdl/}definitions')
        operations = root.findall('{http://schemas.xmlsoap.org/wsdl/}portType/{http://schemas.xmlsoap.org/wsdl/}operation')
        self.assertEqual(len(operations), 8)
