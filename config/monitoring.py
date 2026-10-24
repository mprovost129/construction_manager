import logging
import re

import sentry_sdk

logger = logging.getLogger(__name__)

EMAIL_PATTERN = re.compile(r'(?<![\w.+-])[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}(?![\w.-])')
UUID_PATTERN = re.compile(
    r'(?i)\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-'
    r'[89ab][0-9a-f]{3}-[0-9a-f]{12}\b'
)
SENSITIVE_QUERY_VALUE_PATTERN = re.compile(
    r'(?i)([?&](?:(?:[a-z0-9]+[_-])*)'
    r'(?:code|key|secret|signature|state|token)=)[^&#\s]+'
)


def _scrub_string(value):
    value = UUID_PATTERN.sub('[Filtered UUID]', value)
    value = EMAIL_PATTERN.sub('[Filtered email]', value)
    return SENSITIVE_QUERY_VALUE_PATTERN.sub(r'\1[Filtered]', value)


def scrub_sensitive_event_values(value, hint=None):
    """Remove common client identifiers wherever Sentry places event data."""
    if isinstance(value, dict):
        return {
            scrub_sensitive_event_values(key): scrub_sensitive_event_values(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [scrub_sensitive_event_values(item) for item in value]
    if isinstance(value, tuple):
        return tuple(scrub_sensitive_event_values(item) for item in value)
    if isinstance(value, str):
        return _scrub_string(value)
    return value


def get_traces_sample_rate(dsn, raw_value):
    """Avoid parsing optional Sentry settings while monitoring is disabled."""
    if not dsn:
        return 0.0
    return float(raw_value)


def configure_sentry(*, dsn, environment, release, traces_sample_rate):
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
