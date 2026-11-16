from django.conf import settings
from django.core.mail.backends.smtp import EmailBackend as SMTPBackend


class EmailBackend(SMTPBackend):
    """Apply the deployment reply address to app mail, including password resets."""

    def send_messages(self, email_messages):
        messages = list(email_messages or ())
        for message in messages:
            if not message.reply_to and settings.DEFAULT_REPLY_TO_EMAIL:
                message.reply_to = [settings.DEFAULT_REPLY_TO_EMAIL]
        return super().send_messages(messages)
