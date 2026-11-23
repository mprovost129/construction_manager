"""A small, shared HTML allowlist for messages, browser rendering and email."""

import uuid
from html.parser import HTMLParser
from urllib.parse import urlsplit

import nh3
from django import forms
from django.core.exceptions import ValidationError
from django.utils.html import escape, format_html
from django.utils.safestring import mark_safe


def safe_link(tag, attribute, value):
    if tag == "a" and attribute == "href":
        try:
            url = urlsplit(value.strip())
        except ValueError:
            return None
        if url.scheme.lower() in ("https", "http", "mailto") and not any(
            ord(c) < 32 for c in value
        ):
            return value
    return None


def sanitize_message(value):
    return nh3.clean(
        value or "",
        tags={"p", "br", "strong", "b", "em", "i", "ul", "ol", "li", "a"},
        attributes={"a": {"href"}},
        attribute_filter=safe_link,
        clean_content_tags={"script", "style", "iframe", "object", "svg", "math"},
        url_schemes={"https", "http", "mailto"},
        url_relative="deny",
    )


class PlainMessage(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.lists = []
        self.links = []

    def handle_starttag(self, tag, attrs):
        if tag in ("ol", "ul"):
            self.lists.append([tag, 0])
        if tag in ("p", "br", "li"):
            self.parts.append("\n")
        if tag == "li":
            if self.lists and self.lists[-1][0] == "ol":
                self.lists[-1][1] += 1
                self.parts.append(f"{self.lists[-1][1]}. ")
            else:
                self.parts.append("- ")
        if tag == "a":
            self.links.append(dict(attrs).get("href", ""))

    def handle_endtag(self, tag):
        if tag in ("p", "li"):
            self.parts.append("\n")
        if tag in ("ol", "ul") and self.lists:
            self.lists.pop()
        if tag == "a" and self.links:
            href = self.links.pop()
            if href:
                self.parts.append(f" ({href})")

    def handle_data(self, data):
        self.parts.append(data)


def normalize_message(body, body_html=""):
    if len(body_html or "") > 60000:
        raise ValidationError(
            "Message formatting is too large. Paste as plain text or shorten the message."
        )
    cleaned = sanitize_message(body_html)
    if body_html:
        parser = PlainMessage()
        parser.feed(cleaned)
        body = "\n".join(
            line.rstrip() for line in "".join(parser.parts).splitlines()
        ).strip()
    else:
        body = (body or "").strip()
    if not body or not body.replace("\u200b", "").replace("\xa0", "").strip():
        raise ValidationError("Message cannot be blank.")
    if len(body) > 10000:
        raise ValidationError("Keep the message within 10,000 characters.")
    return body, cleaned


def rendered_message(message):
    if message.body_html:
        return mark_safe(sanitize_message(message.body_html))
    return mark_safe(str(escape(message.body)).replace("\n", "<br>"))


def message_email(message, *, heading, url, audience):
    return format_html(
        '<div><p>{}</p><div>{}</div><p><a href="{}">Open the conversation to respond</a></p><p>{}</p></div>',
        heading,
        rendered_message(message),
        url,
        audience,
    )


class MessageFormMixin:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["body"].required = False
        self.fields["body"].max_length = 10000
        self.fields["body_html"] = forms.CharField(
            required=False, max_length=60000, widget=forms.HiddenInput
        )
        if "submission_id" not in self.fields:
            self.fields["submission_id"] = forms.UUIDField(
                required=False, widget=forms.HiddenInput
            )
        self.fields["submission_id"].initial = uuid.uuid4()

    def clean(self):
        data = super().clean()
        try:
            data["body"], data["body_html"] = normalize_message(
                data.get("body", ""), data.get("body_html", "")
            )
        except ValidationError as error:
            self.add_error("body", error)
        data["submission_id"] = data.get("submission_id") or uuid.uuid4()
        return data


def mark_message_sent(request, submission_id):
    # The redirected page clears only a matching draft, never an unrelated reply.
    request.session["sent_message_tokens"] = [str(submission_id)]
