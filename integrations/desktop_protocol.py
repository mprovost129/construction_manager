"""Restricted QBWC SOAP 1.1 protocol. Only CompanyQuery is implemented.

No input can select an arbitrary query or an accounting write operation.
SOAP bodies, credentials, file paths, and QuickBooks responses are never logged.
"""
import hashlib
import ntpath
import secrets
import uuid
from datetime import timedelta
from xml.etree import ElementTree as ET

from django.contrib.auth.hashers import check_password, make_password
from django.db import transaction
from django.utils import timezone

from .desktop_models import DesktopAuthThrottle, DesktopConnection, DesktopSession

SOAP = 'http://schemas.xmlsoap.org/soap/envelope/'
INTUIT = 'http://developer.intuit.com/'
MAX_XML_BYTES = 262144
METHODS = {
    'serverVersion': ('ticket',),
    'clientVersion': ('strVersion',),
    'authenticate': ('strUserName', 'strPassword'),
    'sendRequestXML': ('ticket', 'strHCPResponse', 'strCompanyFileName', 'qbXMLCountry', 'qbXMLMajorVers', 'qbXMLMinorVers'),
    'receiveResponseXML': ('ticket', 'response', 'hresult', 'message'),
    'connectionError': ('ticket', 'hresult', 'message'),
    'getLastError': ('ticket',),
    'closeConnection': ('ticket',),
}
DUMMY_PASSWORD = make_password(secrets.token_urlsafe(32))


class ProtocolError(Exception):
    pass


def digest(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def company_file_digest(path):
    # Match Windows paths consistently even when the server is Linux.
    return digest(ntpath.normcase(ntpath.normpath(path.strip())))


def parse_xml(raw):
    try:
        text = raw.decode('utf-8-sig') if isinstance(raw, bytes) else raw
        if len(text.encode('utf-8')) > MAX_XML_BYTES or '<!DOCTYPE' in text.upper() or '<!ENTITY' in text.upper():
            raise ProtocolError
        root = ET.fromstring(text)
        if sum(1 for _ in root.iter()) > 2000:
            raise ProtocolError
        return root
    except (UnicodeError, ET.ParseError, ValueError) as exc:
        raise ProtocolError from exc


def parse_call(raw, action):
    root = parse_xml(raw)
    if root.tag != f'{{{SOAP}}}Envelope':
        raise ProtocolError
    bodies = root.findall(f'{{{SOAP}}}Body')
    if len(bodies) != 1 or len(bodies[0]) != 1:
        raise ProtocolError
    call = bodies[0][0]
    name = call.tag.removeprefix(f'{{{INTUIT}}}')
    if name not in METHODS or call.tag != f'{{{INTUIT}}}{name}' or action.strip('"') != INTUIT + name:
        raise ProtocolError
    params = {}
    for child in call:
        key = child.tag.removeprefix(f'{{{INTUIT}}}')
        if child.tag != f'{{{INTUIT}}}{key}' or key not in METHODS[name] or key in params or len(child):
            raise ProtocolError
        params[key] = child.text or ''
    if set(params) != set(METHODS[name]):
        raise ProtocolError
    return name, params


def soap_response(name, value):
    envelope = ET.Element(f'{{{SOAP}}}Envelope')
    body = ET.SubElement(envelope, f'{{{SOAP}}}Body')
    response = ET.SubElement(body, f'{{{INTUIT}}}{name}Response')
    result = ET.SubElement(response, f'{{{INTUIT}}}{name}Result')
    if isinstance(value, list):
        for item in value:
            ET.SubElement(result, f'{{{INTUIT}}}string').text = item
    else:
        result.text = str(value)
    return ET.tostring(envelope, encoding='utf-8', xml_declaration=True)


def soap_fault():
    envelope = ET.Element(f'{{{SOAP}}}Envelope')
    fault = ET.SubElement(ET.SubElement(envelope, f'{{{SOAP}}}Body'), f'{{{SOAP}}}Fault')
    ET.SubElement(fault, 'faultcode').text = 'Client'
    ET.SubElement(fault, 'faultstring').text = 'Invalid connector request.'
    return ET.tostring(envelope, encoding='utf-8', xml_declaration=True)


@transaction.atomic
def allow_authentication(username, remote_address):
    now = timezone.now()
    DesktopAuthThrottle.objects.filter(expires_at__lte=now).delete()
    allowed = True
    for value, limit in (('account:' + username.lower(), 10), ('ip:' + remote_address, 100)):
        throttle, _ = DesktopAuthThrottle.objects.select_for_update().get_or_create(
            key=digest(value), defaults={'expires_at': now + timedelta(minutes=15)},
        )
        throttle.attempts += 1
        throttle.save(update_fields=['attempts'])
        allowed = allowed and throttle.attempts <= limit
    return allowed


def authenticate(username, password, remote_address):
    # Database-backed limits apply across workers, including successful logins.
    if not allow_authentication(username, remote_address):
        return ['', 'nvu']
    try:
        user_id = uuid.UUID(username)
    except ValueError:
        check_password(password, DUMMY_PASSWORD)
        return ['', 'nvu']
    with transaction.atomic():
        connection = DesktopConnection.objects.select_for_update().filter(username=user_id, active=True).first()
        valid = check_password(password, connection.password_hash if connection else DUMMY_PASSWORD)
        if not connection or not valid or not connection.expected_company_name.strip() or len(connection.company_file_digest) != 64:
            return ['', 'nvu']
        connection.verified_at = None
        connection.save(update_fields=['verified_at'])
        ticket = secrets.token_urlsafe(32)
        DesktopSession.objects.update_or_create(connection=connection, defaults={
            'ticket_digest': digest(ticket), 'expires_at': timezone.now() + timedelta(minutes=15),
            'request_id': uuid.uuid4(), 'state': DesktopSession.State.AUTHENTICATED,
            'error_code': '', 'response_digest': '',
        })
        # The already-open file is checked before any query is returned.
        return [ticket, '']


def fail(session, code):
    session.state = DesktopSession.State.ERROR
    session.error_code = code
    session.save(update_fields=['state', 'error_code'])
    # A failed recheck must revoke any previous verification.
    DesktopConnection.objects.filter(pk=session.connection_id).update(verified_at=None)


def dispatch(name, params, remote_address=''):
    if name == 'serverVersion':
        return 'Heritage Desktop preflight 1.0'
    if name == 'clientVersion':
        return ''
    if name == 'authenticate':
        return authenticate(params['strUserName'], params['strPassword'], remote_address)
    ticket = params['ticket']
    if not 32 <= len(ticket) <= 100:
        raise ProtocolError
    with transaction.atomic():
        session = DesktopSession.objects.select_for_update().select_related('connection').filter(
            ticket_digest=digest(ticket), expires_at__gt=timezone.now(), connection__active=True,
        ).exclude(state=DesktopSession.State.CLOSED).first()
        if not session:
            raise ProtocolError
        if name == 'getLastError':
            return session.error_code or 'No connector error.'
        if name == 'closeConnection':
            session.state = DesktopSession.State.CLOSED
            session.save(update_fields=['state'])
            return 'Read-only connection check finished.'
        if name == 'connectionError':
            fail(session, 'QUICKBOOKS_UNAVAILABLE')
            return 'done'
        if session.state == DesktopSession.State.ERROR:
            return -1 if name == 'receiveResponseXML' else ''
        if name == 'sendRequestXML':
            if not params['strCompanyFileName'].strip() or company_file_digest(params['strCompanyFileName']) != session.connection.company_file_digest:
                fail(session, 'COMPANY_FILE_MISMATCH')
                return ''
            try:
                compatible = int(params['qbXMLMajorVers']) >= 1 and int(params['qbXMLMinorVers']) >= 0
            except ValueError:
                compatible = False
            if not compatible:
                fail(session, 'UNSUPPORTED_XML_VERSION')
                return ''
            if session.state == DesktopSession.State.VERIFIED:
                return ''
            session.state = DesktopSession.State.REQUESTED
            session.save(update_fields=['state'])
            return ('<?xml version="1.0"?><?qbxml version="1.0"?>'
                    '<QBXML><QBXMLMsgsRq onError="stopOnError">'
                    f'<CompanyQueryRq requestID="{session.request_id}"/>'
                    '</QBXMLMsgsRq></QBXML>')
        if name == 'receiveResponseXML':
            response_digest = digest(params['response'])
            if session.state == DesktopSession.State.VERIFIED:
                if response_digest == session.response_digest and not params['hresult']:
                    return 100
                fail(session, 'CONFLICTING_RESPONSE')
                return -1
            if session.state != DesktopSession.State.REQUESTED or params['hresult']:
                fail(session, 'RESPONSE_OUT_OF_SEQUENCE')
                return -1
            try:
                root = parse_xml(params['response'])
                if root.tag != 'QBXML' or len(root) != 1 or root[0].tag != 'QBXMLMsgsRs' or len(root[0]) != 1:
                    raise ProtocolError
                results = root.findall('./QBXMLMsgsRs/CompanyQueryRs')
                if len(results) != 1:
                    raise ProtocolError
                result = results[0]
                if result.get('requestID') != str(session.request_id) or result.get('statusCode') != '0':
                    raise ProtocolError
                names = result.findall('./CompanyRet/CompanyName')
                if len(names) != 1 or (names[0].text or '').strip().casefold() != session.connection.expected_company_name.strip().casefold():
                    raise ProtocolError
            except ProtocolError:
                fail(session, 'COMPANY_RESPONSE_REJECTED')
                return -1
            session.state = DesktopSession.State.VERIFIED
            session.response_digest = response_digest
            session.save(update_fields=['state', 'response_digest'])
            DesktopConnection.objects.filter(pk=session.connection_id).update(verified_at=timezone.now())
            return 100
        raise ProtocolError
