"""Single-worker execution without holding business-row transactions across HTTP calls.

PostgreSQL requires a direct or session-pooled connection (not transaction pooling).
The process mutex also prevents reentrant acquisition on the same database session.
SQLite is supported for single-process unit tests only.
"""

from contextlib import contextmanager
from functools import wraps
from threading import Lock

from django.conf import settings
from django.db import connection
from django.utils import timezone

from .customer_sync import QuickBooksSyncBusy, QuickBooksSyncError
from .models import QuickBooksSyncAttempt

_mutex = Lock()
_active = set()


@contextmanager
def attempt_execution_lock(attempt_id, *, namespace=0x5142):
    key = (connection.alias, namespace, attempt_id)
    with _mutex:
        if key in _active:
            raise QuickBooksSyncBusy("This synchronization is already executing.")
        _active.add(key)
    acquired = False
    raw_connection = None
    # Reserved negative advisory-lock range for Construction Manager sync attempts.
    lock_id = -((namespace << 48) + int(attempt_id))
    try:
        if connection.vendor == "postgresql":
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_try_advisory_lock(%s)", [lock_id])
                acquired = cursor.fetchone()[0]
            raw_connection = connection.connection
            if not acquired:
                raise QuickBooksSyncBusy("This synchronization is already executing.")
        yield
    finally:
        try:
            if acquired and raw_connection is not None and not raw_connection.closed:
                # Release on the exact session that acquired it, never a replacement.
                try:
                    with raw_connection.cursor() as cursor:
                        cursor.execute("SELECT pg_advisory_unlock(%s)", [lock_id])
                except Exception:
                    raw_connection.close()  # A broken session must not retain a lock.
        finally:
            with _mutex:
                _active.discard(key)


def exclusive_execution(function):
    @wraps(function)
    def execute(attempt_id, *, api_client=None, recovery_before=None):
        with attempt_execution_lock(attempt_id):
            attempt = QuickBooksSyncAttempt.objects.get(pk=attempt_id)
            if attempt.status != QuickBooksSyncAttempt.Status.RUNNING:
                raise QuickBooksSyncError(
                    "Only running synchronization attempts can execute."
                )
            if recovery_before is not None:
                if (attempt.last_execution_at or attempt.started_at) > recovery_before:
                    raise QuickBooksSyncBusy(
                        "This synchronization is too recent to recover."
                    )
            if attempt.execution_count >= settings.QUICKBOOKS_SYNC_MAX_ATTEMPTS:
                QuickBooksSyncAttempt.objects.filter(pk=attempt_id).update(
                    status="failed",
                    retryable=False,
                    next_retry_at=None,
                    error_code="recovery_limit",
                    error_message="Interrupted synchronization reached its recovery limit. Review the remote record.",
                    completed_at=timezone.now(),
                )
                raise QuickBooksSyncError(
                    "This synchronization requires review after repeated interruptions."
                )
            QuickBooksSyncAttempt.objects.filter(pk=attempt_id).update(
                last_execution_at=timezone.now(),
                execution_count=attempt.execution_count + 1,
            )
            try:
                return function(attempt_id, api_client=api_client)
            except Exception as exc:
                # Keep request identity/checkpoint for review; never expose raw errors.
                QuickBooksSyncAttempt.objects.filter(
                    pk=attempt_id, status="running"
                ).update(
                    status="failed",
                    retryable=False,
                    next_retry_at=None,
                    error_code="execution_error",
                    error_message="Synchronization stopped unexpectedly. Review the saved request before retrying.",
                    completed_at=timezone.now(),
                )
                raise QuickBooksSyncError(
                    "Synchronization stopped unexpectedly and requires review."
                ) from exc

    return execute
