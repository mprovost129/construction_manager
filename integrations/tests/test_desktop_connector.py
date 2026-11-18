import uuid
from datetime import timedelta
from xml.etree import ElementTree as ET

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from billing.models import CreditMemo, Invoice, InvoiceLineItem
from billing.services import (
    create_credit_memo_from_change_order,
    issue_credit_memo,
    issue_invoice,
)
from integrations.desktop_exports import (
    mark_delivery_started,
    record_receipt,
    reserve_export,
)
from integrations.desktop_models import (
    DesktopAuthThrottle,
    DesktopConnection,
    DesktopExport,
    DesktopSession,
)
from integrations.desktop_protocol import (
    INTUIT,
    MAX_XML_BYTES,
    SOAP,
    company_file_digest,
    digest,
)
from projects.models import (
    ChangeOrder,
    Organization,
    OrganizationMembership,
    Project,
    ProjectMembership,
)


@override_settings(QUICKBOOKS_DESKTOP_CONNECTOR_ENABLED=True, APP_ENVIRONMENT='test')
class DesktopProtocolTests(TestCase):
    def setUp(self):
        self.organization = Organization.objects.create(name='Example Construction', slug='example')
        self.path = r'C:\Accounting\Example.qbw'
        self.connection = DesktopConnection.objects.create(
            organization=self.organization, active=True,
            expected_company_name='Example Construction', company_file_digest=company_file_digest(self.path),
        )
        self.connection.set_password('synthetic-test-password')
        self.connection.save()
        self.url = reverse('integrations:desktop_connector')

    def call(self, name, params, **kwargs):
        envelope = ET.Element(f'{{{SOAP}}}Envelope')
        method = ET.SubElement(ET.SubElement(envelope, f'{{{SOAP}}}Body'), f'{{{INTUIT}}}{name}')
        for key, value in params.items():
            ET.SubElement(method, f'{{{INTUIT}}}{key}').text = str(value)
        return self.client.post(self.url, ET.tostring(envelope), content_type='text/xml',
                                HTTP_SOAPACTION=f'"{INTUIT}{name}"', **kwargs)

    def value(self, response, name):
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response['Cache-Control'], 'no-store')
        return ET.fromstring(response.content).find(f'.//{{{INTUIT}}}{name}Result')

    def login(self, **overrides):
        params = {'strUserName': str(self.connection.username), 'strPassword': 'synthetic-test-password'}
        params.update(overrides)
        return [item.text or '' for item in self.value(self.call('authenticate', params), 'authenticate')]

    def send(self, ticket, **overrides):
        params = dict(ticket=ticket, strHCPResponse='', strCompanyFileName=self.path,
                      qbXMLCountry='US', qbXMLMajorVers=16, qbXMLMinorVers=0)
        params.update(overrides)
        return self.call('sendRequestXML', params)

    def company_response(self, **overrides):
        session = DesktopSession.objects.get(connection=self.connection)
        values = {'request_id': str(session.request_id), 'status': '0', 'name': 'Example Construction'}
        values.update(overrides)
        root = ET.Element('QBXML')
        result = ET.SubElement(ET.SubElement(root, 'QBXMLMsgsRs'), 'CompanyQueryRs',
                               requestID=values['request_id'], statusCode=values['status'])
        ET.SubElement(ET.SubElement(result, 'CompanyRet'), 'CompanyName').text = values['name']
        return ET.tostring(root, encoding='unicode')

    def receive(self, ticket, response, **overrides):
        params = dict(ticket=ticket, response=response, hresult='', message='')
        params.update(overrides)
        return self.call('receiveResponseXML', params)

    def test_read_only_handshake_and_duplicate_callback(self):
        ticket, filename = self.login()
        self.assertEqual(filename, '')
        session = DesktopSession.objects.get(connection=self.connection)
        self.assertEqual(session.ticket_digest, digest(ticket))
        self.assertNotEqual(self.connection.password_hash, 'synthetic-test-password')
        query = self.value(self.send(ticket), 'sendRequestXML').text
        root = ET.fromstring(query)
        self.assertEqual([node.tag for node in root.iter()], ['QBXML', 'QBXMLMsgsRq', 'CompanyQueryRq'])
        self.assertEqual(self.value(self.send(ticket), 'sendRequestXML').text, query)
        response = self.company_response()
        for _ in range(2):
            self.assertEqual(self.value(self.receive(ticket, response), 'receiveResponseXML').text, '100')
        self.connection.refresh_from_db()
        self.assertIsNotNone(self.connection.verified_at)
        self.assertIsNone(self.value(self.send(ticket), 'sendRequestXML').text)
        self.call('closeConnection', {'ticket': ticket})
        self.assertEqual(self.send(ticket).status_code, 500)

    def test_bad_password_unknown_and_disabled_accounts_have_same_result(self):
        self.assertEqual(self.login(strPassword='wrong'), ['', 'nvu'])
        self.assertEqual(self.login(strUserName=str(uuid.uuid4())), ['', 'nvu'])
        self.assertEqual(self.login(strUserName='not-a-uuid'), ['', 'nvu'])
        self.connection.active = False
        self.connection.save()
        self.assertEqual(self.login(), ['', 'nvu'])
        self.assertFalse(DesktopSession.objects.exists())

    def test_database_throttle_and_expiration(self):
        for _ in range(10):
            self.login(strPassword='wrong')
        self.assertEqual(self.login(), ['', 'nvu'])
        DesktopAuthThrottle.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertTrue(self.login()[0])

    def test_new_login_revokes_old_ticket_and_previous_verification(self):
        first, _ = self.login()
        DesktopConnection.objects.filter(pk=self.connection.pk).update(verified_at=timezone.now())
        second, _ = self.login()
        self.assertNotEqual(first, second)
        self.assertEqual(self.send(first).status_code, 500)
        self.connection.refresh_from_db()
        self.assertIsNone(self.connection.verified_at)
        self.assertEqual(self.send(second).status_code, 200)

    def test_expired_ticket_and_revoked_connection(self):
        ticket, _ = self.login()
        DesktopSession.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self.send(ticket).status_code, 500)
        ticket, _ = self.login()
        DesktopConnection.objects.update(active=False)
        self.assertEqual(self.send(ticket).status_code, 500)

    def test_wrong_file_returns_no_query_and_revokes_verification(self):
        ticket, _ = self.login()
        DesktopConnection.objects.update(verified_at=timezone.now())
        self.assertIsNone(self.value(self.send(ticket, strCompanyFileName=r'C:\Other.qbw'), 'sendRequestXML').text)
        self.connection.refresh_from_db()
        self.assertIsNone(self.connection.verified_at)
        self.assertEqual(DesktopSession.objects.get().error_code, 'COMPANY_FILE_MISMATCH')

    def test_windows_path_normalization(self):
        ticket, _ = self.login()
        self.assertTrue(self.value(self.send(ticket, strCompanyFileName='c:/ACCOUNTING/Example.qbw'), 'sendRequestXML').text)

    def test_mismatched_company_request_status_and_conflicting_replay(self):
        for override in ({'name': 'Other Company'}, {'request_id': str(uuid.uuid4())}, {'status': '500'}):
            with self.subTest(override=override):
                ticket, _ = self.login()
                self.send(ticket)
                self.assertEqual(self.value(self.receive(ticket, self.company_response(**override)), 'receiveResponseXML').text, '-1')
                self.connection.refresh_from_db()
                self.assertIsNone(self.connection.verified_at)
        ticket, _ = self.login()
        self.send(ticket)
        self.receive(ticket, self.company_response())
        self.assertEqual(self.value(self.receive(ticket, self.company_response(name='Changed')), 'receiveResponseXML').text, '-1')
        self.connection.refresh_from_db()
        self.assertIsNone(self.connection.verified_at)

    def test_unsolicited_response_and_quickbooks_error(self):
        ticket, _ = self.login()
        self.assertEqual(self.value(self.receive(ticket, self.company_response()), 'receiveResponseXML').text, '-1')
        ticket, _ = self.login()
        self.send(ticket)
        result = self.receive(ticket, self.company_response(), hresult='0x123', message='Private file details')
        self.assertEqual(self.value(result, 'receiveResponseXML').text, '-1')
        error = self.call('getLastError', {'ticket': ticket})
        self.assertNotIn(b'Private file details', error.content)
        self.assertEqual(self.value(error, 'getLastError').text, 'RESPONSE_OUT_OF_SEQUENCE')

    def test_connection_error_and_unknown_version_never_return_writes(self):
        ticket, _ = self.login()
        self.assertIsNone(self.value(self.send(ticket, qbXMLMajorVers='unknown'), 'sendRequestXML').text)
        ticket, _ = self.login()
        result = self.call('connectionError', dict(ticket=ticket, hresult='failure', message='private path'))
        self.assertEqual(self.value(result, 'connectionError').text, 'done')
        self.assertIsNone(self.value(self.send(ticket), 'sendRequestXML').text)

    def test_untrusted_xml_and_extra_operations_are_rejected(self):
        for raw in (b'<!DOCTYPE x [<!ENTITY p SYSTEM "file:///secret">]><x>&p;</x>',
                    b'<unclosed', b'x' * (MAX_XML_BYTES + 1)):
            with self.subTest(raw=raw[:20]):
                result = self.client.post(self.url, raw, content_type='text/xml', HTTP_SOAPACTION=INTUIT + 'authenticate')
                self.assertEqual(result.status_code, 500)
                self.assertIn(b'Invalid connector request.', result.content)
        self.assertEqual(self.call('InvoiceAdd', {}).status_code, 500)
        self.assertEqual(self.call('authenticate', {'strUserName': 'test', 'strPassword': 'secret', 'extra': 'bad'}).status_code, 500)

    def test_inner_xml_entity_is_rejected(self):
        ticket, _ = self.login()
        self.send(ticket)
        result = self.receive(ticket, '<!DOCTYPE x [<!ENTITY a "boom">]><x>&a;</x>')
        self.assertEqual(self.value(result, 'receiveResponseXML').text, '-1')

    def test_browser_session_cannot_substitute_for_connector_ticket(self):
        admin = get_user_model().objects.create_superuser(email='admin@example.test', password='test')
        self.client.force_login(admin)
        self.assertEqual(self.send('invented-ticket' * 3).status_code, 500)

    @override_settings(QUICKBOOKS_DESKTOP_CONNECTOR_ENABLED=False)
    def test_disabled_endpoint(self):
        self.assertEqual(self.call('serverVersion', {'ticket': ''}).status_code, 404)

    @override_settings(APP_ENVIRONMENT='production')
    def test_production_requires_https(self):
        self.assertEqual(self.call('serverVersion', {'ticket': ''}).status_code, 403)
        self.assertEqual(self.call('serverVersion', {'ticket': ''}, secure=True).status_code, 200)


class DesktopExportTests(TestCase):
    def setUp(self):
        users = get_user_model()
        self.admin = users.objects.create_user(email='admin@example.test')
        self.client_user = users.objects.create_user(email='client@example.test')
        self.organization = Organization.objects.create(name='Example', slug='example')
        OrganizationMembership.objects.create(organization=self.organization, user=self.admin, role='admin')
        self.project = Project.objects.create(organization=self.organization, name='Example house', created_by=self.admin)
        ProjectMembership.objects.create(project=self.project, user=self.client_user, role='client')
        self.connection = DesktopConnection.objects.create(
            organization=self.organization, active=True, expected_company_name='Example',
            company_file_digest=company_file_digest(r'C:\Example.qbw'), verified_at=timezone.now(),
        )
        self.invoice = Invoice.objects.create(organization=self.organization, project=self.project,
            title='Construction', created_by=self.admin, due_date=timezone.localdate() + timedelta(days=30))
        InvoiceLineItem.objects.create(invoice=self.invoice, description='Work', quantity=1, unit_price=100)
        self.invoice.recalculate_totals()
        self.invoice = issue_invoice(invoice_id=self.invoice.pk, actor=self.admin)

    def reserve(self, **kwargs):
        params = dict(connection_id=self.connection.pk, kind='invoice', source_id=self.invoice.pk, actor=self.admin)
        params.update(kwargs)
        return reserve_export(**params)

    def receipt(self, export, **kwargs):
        params = dict(export_id=export.pk, connection_id=self.connection.pk,
                      request_id=export.request_id, txn_id='TXN-1', edit_sequence='EDIT-1')
        params.update(kwargs)
        return record_receipt(**params)

    def test_reserved_once_and_uncertain_result_cannot_be_resent(self):
        export = self.reserve()
        self.assertEqual(self.reserve().pk, export.pk)
        mark_delivery_started(export_id=export.pk, actor=self.admin)
        self.assertEqual(self.reserve().state, DesktopExport.State.UNCERTAIN)
        with self.assertRaises(ValidationError):
            mark_delivery_started(export_id=export.pk, actor=self.admin)
        self.assertEqual(DesktopExport.objects.count(), 1)

    def test_matching_receipt_is_idempotent_conflicting_receipt_rejected(self):
        export = self.reserve()
        with self.assertRaises(ValidationError):
            self.receipt(export)
        mark_delivery_started(export_id=export.pk, actor=self.admin)
        for _ in range(2):
            self.assertEqual(self.receipt(export).state, DesktopExport.State.CONFIRMED)
        with self.assertRaises(ValidationError):
            self.receipt(export, txn_id='different')
        with self.assertRaises(ValidationError):
            mark_delivery_started(export_id=export.pk, actor=self.admin)

    def test_receipt_must_match_request_and_company(self):
        export = self.reserve()
        mark_delivery_started(export_id=export.pk, actor=self.admin)
        for kwargs in ({'request_id': uuid.uuid4()}, {'txn_id': ''}, {'edit_sequence': ' '}, {'txn_id': 'x' * 51}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValidationError):
                self.receipt(export, **kwargs)
        other = DesktopConnection.objects.create(organization=self.organization, active=True, company_file_digest='a' * 64)
        with self.assertRaises(ValidationError):
            self.receipt(export, connection_id=other.pk)
        DesktopConnection.objects.filter(pk=self.connection.pk).update(company_file_digest='b' * 64)
        with self.assertRaises(ValidationError):
            self.receipt(export)

    def test_clients_and_other_company_admin_cannot_reserve(self):
        with self.assertRaises(PermissionDenied):
            self.reserve(actor=self.client_user)
        other = Organization.objects.create(name='Other', slug='other')
        OrganizationMembership.objects.create(organization=other, user=self.client_user, role='admin')
        with self.assertRaises(PermissionDenied):
            self.reserve(actor=self.client_user)
        self.connection.organization = other
        self.connection.save()
        with self.assertRaises(PermissionDenied):
            self.reserve(actor=self.client_user)

    def test_stale_or_revoked_verification_blocks_preparation(self):
        for values in ({'verified_at': None}, {'verified_at': timezone.now() - timedelta(minutes=16)}, {'active': False}):
            DesktopConnection.objects.filter(pk=self.connection.pk).update(**values)
            with self.subTest(values=values), self.assertRaises(ValidationError):
                self.reserve()

    def test_changed_record_or_file_cannot_be_sent(self):
        export = self.reserve()
        self.invoice.line_items.update(unit_price=200)
        with self.assertRaises(ValidationError):
            mark_delivery_started(export_id=export.pk, actor=self.admin)
        with self.assertRaises(ValidationError):
            self.reserve()
        self.invoice.line_items.update(unit_price=100)
        DesktopConnection.objects.filter(pk=self.connection.pk).update(company_file_digest='c' * 64)
        with self.assertRaises(ValidationError):
            mark_delivery_started(export_id=export.pk, actor=self.admin)

    def test_second_connection_cannot_export_same_invoice(self):
        self.reserve()
        other = DesktopConnection.objects.create(organization=self.organization, active=True,
            expected_company_name='Example', company_file_digest='d' * 64, verified_at=timezone.now())
        with self.assertRaises(ValidationError):
            self.reserve(connection_id=other.pk)

    def test_draft_and_voided_invoices_rejected(self):
        draft = Invoice.objects.create(organization=self.organization, project=self.project, created_by=self.admin)
        with self.assertRaises(ValidationError):
            self.reserve(source_id=draft.pk)
        Invoice.objects.filter(pk=self.invoice.pk).update(status=Invoice.Status.VOIDED,
            voided_by=self.admin, voided_at=timezone.now(), void_reason='test')
        with self.assertRaises(ValidationError):
            self.reserve()

    def test_issued_credit_supported_but_draft_and_unapproved_source_rejected(self):
        now = timezone.now()
        change = ChangeOrder.objects.create(project=self.project, number=1, title='Credit', price_delta=-50,
            status=ChangeOrder.Status.APPROVED, created_by=self.admin, submitted_by=self.admin,
            submitted_at=now, decided_by=self.client_user, decided_at=now)
        credit = create_credit_memo_from_change_order(change_order_id=change.pk, actor=self.admin)
        with self.assertRaises(ValidationError):
            self.reserve(kind='credit_memo', source_id=credit.pk)
        credit = issue_credit_memo(credit_memo_id=credit.pk, actor=self.admin)
        export = self.reserve(kind='credit_memo', source_id=credit.pk)
        self.assertEqual(export.credit_memo_id, credit.pk)
        self.assertEqual(credit.status, CreditMemo.Status.ISSUED)
        ChangeOrder.objects.filter(pk=change.pk).update(status=ChangeOrder.Status.PENDING, decided_by=None, decided_at=None)
        with self.assertRaises(ValidationError):
            mark_delivery_started(export_id=export.pk, actor=self.admin)

    def test_database_rejects_invalid_export_state_and_duplicate_source(self):
        export = self.reserve()
        with self.assertRaises(IntegrityError), transaction.atomic():
            DesktopExport.objects.filter(pk=export.pk).update(state='confirmed')
        with self.assertRaises(IntegrityError), transaction.atomic():
            DesktopExport.objects.create(connection=self.connection, invoice=self.invoice,
                prepared_by=self.admin, content_digest='x' * 64, company_file_digest='y' * 64)
