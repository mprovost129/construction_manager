from django.conf import settings
from django.http import Http404, HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.debug import sensitive_post_parameters, sensitive_variables
from django.views.decorators.http import require_POST

from .desktop_protocol import (
    MAX_XML_BYTES,
    ProtocolError,
    dispatch,
    parse_call,
    soap_fault,
    soap_response,
)


@csrf_exempt  # QBWC uses a separate password and expiring ticket, never browser cookies.
@sensitive_post_parameters()
@sensitive_variables()
@require_POST
def connector(request):
    if not settings.QUICKBOOKS_DESKTOP_CONNECTOR_ENABLED:
        raise Http404
    if settings.APP_ENVIRONMENT == 'production' and not request.is_secure():
        return HttpResponse(status=403)
    try:
        length = int(request.META.get('CONTENT_LENGTH') or 0)
        if length > MAX_XML_BYTES or request.content_type != 'text/xml':
            raise ProtocolError
        raw = request.read(MAX_XML_BYTES + 1)
        if len(raw) > MAX_XML_BYTES:
            raise ProtocolError
        name, params = parse_call(raw, request.headers.get('SOAPAction', ''))
        result = dispatch(name, params, request.META.get('REMOTE_ADDR', ''))
        response = HttpResponse(soap_response(name, result), content_type='text/xml; charset=utf-8')
    except (ProtocolError, ValueError):
        response = HttpResponse(soap_fault(), status=500, content_type='text/xml; charset=utf-8')
    response['Cache-Control'] = 'no-store'
    return response
