from django.db import models


class OperationalEvent(models.Model):
    """Small, content-free signals. Never store requests or exception text here."""

    class Kind(models.TextChoices):
        REQUEST_FAILED = 'request_failed', 'Server request failed'
        EMAIL_FAILED = 'email_failed', 'Notification email failed'
        EMAIL_ACCEPTED = 'email_accepted', 'Notification accepted by email backend'
        EMAIL_TEST = 'email_test', 'Notification handled by test email backend'

    kind = models.CharField(max_length=24, choices=Kind.choices)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ('-created_at', '-pk')
        default_permissions = ()
        permissions = [('view_operations', 'Can view application operations')]
