import logging
import re
from urllib.parse import unquote_plus

import sentry_sdk

logger = logging.getLogger(__name__)

EMAIL_PATTERN = re.compile(r'(?<![\w.+-])[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}(?![\w-])')
UUID_PATTERN = re.compile(
    r'(?i)\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-'
    r'[89ab][0-9a-f]{3}-[0-9a-f]{12}\b'
)
QUERY_PARAMETER_PATTERN = re.compile(r'(?i)([?&])([^=&#\s]+)=([^&#\s]+)')
SENSITIVE_QUERY_KEY_SUFFIXES = (
    'auth',
    'authorization',
    'code',
    'credential',
    'key',
    'password',
    'secret',
    'signature',
    'state',
    'token',
)
DATABASE_EXCEPTION_MODULE_PREFIXES = ('django.db', 'psycopg', 'psycopg2')
DATABASE_EXCEPTION_TYPES = {
    'DataError',
    'DatabaseError',
    'IntegrityError',
    'InternalError',
    'OperationalError',
    'ProgrammingError',
}


def _scrub_string(value):
    value = UUID_PATTERN.sub('[Filtered UUID]', value)
    value = EMAIL_PATTERN.sub('[Filtered email]', value)
    return QUERY_PARAMETER_PATTERN.sub(_scrub_query_parameter, value)


def _scrub_query_parameter(match):
    delimiter, key, parameter_value = match.groups()
    normalized_key = re.sub(r'[^a-z0-9]', '', unquote_plus(key).lower())
    if normalized_key.endswith(SENSITIVE_QUERY_KEY_SUFFIXES):
        parameter_value = '[Filtered]'
    return f'{delimiter}{key}={parameter_value}'


def _scrub_database_exception_details(event):
    if not isinstance(event, dict):
        return event

    exception = event.get('exception')
    if not isinstance(exception, dict):
        return event

    values = exception.get('values')
    if not isinstance(values, list):
        return event

    database_exception_found = any(
        isinstance(exception_value, dict)
        and (
            str(exception_value.get('module', '')).startswith(
                DATABASE_EXCEPTION_MODULE_PREFIXES
            )
            or str(exception_value.get('type', '')) in DATABASE_EXCEPTION_TYPES
        )
        for exception_value in values
    )
    if database_exception_found:
        for exception_value in values:
            if isinstance(exception_value, dict) and 'value' in exception_value:
                exception_value['value'] = '[Filtered database exception details]'
        for field in ('breadcrumbs', 'extra', 'logentry', 'message'):
            event.pop(field, None)
    return event


def _scrub_nested_value(value):
    if isinstance(value, dict):
        return {
            _scrub_nested_value(key): _scrub_nested_value(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_scrub_nested_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_scrub_nested_value(item) for item in value)
    if isinstance(value, str):
        return _scrub_string(value)
    return value


def scrub_sensitive_event_values(value, hint=None):
    """Remove common client identifiers wherever Sentry places event data."""
    return _scrub_nested_value(_scrub_database_exception_details(value))


def get_traces_sample_rate(dsn, raw_value):
    """Avoid parsing optional Sentry settings while monitoring is disabled."""
    if not dsn:
        return 0.0
    try:
        sample_rate = float(raw_value)
    except (TypeError, ValueError):
        logger.warning(
            'SENTRY_TRACES_SAMPLE_RATE is invalid; performance tracing is disabled.'
        )
        return 0.0
    if not 0 <= sample_rate <= 1:
        logger.warning(
            'SENTRY_TRACES_SAMPLE_RATE is outside 0–1; performance tracing is disabled.'
        )
        return 0.0
    return sample_rate


def configure_sentry(
    *,
    dsn,
    environment,
    release,
    traces_sample_rate,
    disabled_integrations=(),
):
    """Configure production error monitoring without collecting client PII."""
    if not dsn:
        return False
    if not 0 <= traces_sample_rate <= 1:
        raise ValueError('SENTRY_TRACES_SAMPLE_RATE must be between 0 and 1.')
    try:
        sentry_sdk.init(
            dsn=dsn,
            environment=environment,
            release=release or None,
            sample_rate=1.0,
            traces_sample_rate=traces_sample_rate,
            disabled_integrations=list(disabled_integrations),
            before_send=scrub_sensitive_event_values,
            before_send_transaction=scrub_sensitive_event_values,
            data_collection={
                'user_info': False,
                'cookies': {'mode': 'off'},
                'http_headers': {'request': {'mode': 'off'}},
                'http_bodies': [],
                'url_query_params': {'mode': 'off'},
                'graphql': {'document': False, 'variables': False},
                'gen_ai': {'inputs': False, 'outputs': False},
                'database_query_data': False,
                'queues': False,
                'stack_frame_variables': False,
                'frame_context_lines': 5,
            },
        )
    except ValueError:
        logger.warning(
            'Sentry monitoring is disabled because SENTRY_DSN is invalid.',
            exc_info=True,
        )
        return False
    return True
