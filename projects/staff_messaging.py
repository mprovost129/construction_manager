"""Private staff inbox with durable in-app alerts and queued email delivery."""
import uuid
from datetime import timedelta

from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import Exists, OuterRef, Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from core.models import OperationalEvent
from core.operations import record_operational_event

from .access import internal_organizations_for_user
from .models import (
    ActivityEvent,
    InAppNotification,
    NotificationPreference,
    StaffMessage,
    StaffMessageDelivery,
    StaffParticipant,
    StaffThread,
)
from .services import record_activity, send_notification_email
from .staff_access import staff_projects, staff_recipients, staff_threads


def require_staff(user):
    if not user.is_active or not internal_organizations_for_user(user).exists():
        raise PermissionDenied('Staff messaging is available only to active internal team members.')


def scoped_object(queryset, value):
    try:
        pk = int(value)
    except (TypeError, ValueError):
        raise Http404 from None
    if not 0 < pk <= 9223372036854775807:
        raise Http404
    return get_object_or_404(queryset, pk=pk)


class ContextForm(forms.Form):
    organization = forms.ModelChoiceField(queryset=None, label='Company')
    project = forms.ModelChoiceField(queryset=None, required=False,
        empty_label='General conversation — no project')

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['organization'].queryset = internal_organizations_for_user(user)
        self.fields['project'].queryset = staff_projects(user)
        if self.fields['organization'].queryset.count() == 1:
            self.fields['organization'].initial = self.fields['organization'].queryset.first()

    def clean(self):
        cleaned = super().clean()
        if cleaned.get('project') and cleaned.get('organization') != cleaned['project'].organization:
            self.add_error('project', 'Choose a project in this company.')
        return cleaned


class RecipientField(forms.ModelMultipleChoiceField):
    def label_from_instance(self, user):
        return f'{user.get_full_name() or user.email} ({user.email})' if user.get_full_name() else user.email


class ComposeForm(forms.Form):
    recipients = RecipientField(queryset=None, widget=forms.CheckboxSelectMultiple,
        help_text='Only the selected people and you can read this conversation. Choose up to 20 recipients.')
    subject = forms.CharField(max_length=200)
    body = forms.CharField(max_length=10000, label='Message', widget=forms.Textarea(attrs={'rows': 7}))
    submission_id = forms.UUIDField(widget=forms.HiddenInput)

    def __init__(self, *args, user, organization, project, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['recipients'].queryset = staff_recipients(organization, project).exclude(pk=user.pk)
        self.fields['submission_id'].initial = uuid.uuid4()

    def clean_subject(self):
        subject = self.cleaned_data['subject']
        if '\n' in subject or '\r' in subject:
            raise forms.ValidationError('Keep the subject on one line.')
        return subject

    def clean_recipients(self):
        recipients = self.cleaned_data['recipients']
        if len(recipients) > 20:
            raise forms.ValidationError('Choose no more than 20 recipients.')
        return recipients


class ReplyForm(forms.Form):
    body = forms.CharField(max_length=10000, label='Reply', widget=forms.Textarea(attrs={'rows': 5}))
    submission_id = forms.UUIDField(widget=forms.HiddenInput)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['submission_id'].initial = uuid.uuid4()


def queue_notifications(message):
    """Called in the message transaction: every saved reply has durable alerts."""
    thread = message.thread
    title = f'Staff message: {thread.subject}'[:200]
    for participant in thread.participants.select_related('user').exclude(user=message.author):
        if not staff_threads(participant.user).filter(pk=thread.pk).exists():
            continue
        StaffMessageDelivery.objects.create(message=message, recipient=participant.user,
            email_status='pending' if participant.email_enabled else 'disabled')
        InAppNotification.objects.create(user=participant.user, project=thread.project, staff_thread=thread,
            event_type=NotificationPreference.Event.MESSAGES, title=title,
            message=f'{message.author.get_full_name() or message.author.email}: {message.body}',
            url_path=reverse('projects:staff_message_thread', args=[thread.pk]))


def process_email_queue(*, limit=50):
    """An atomic claim permits one attempt per email, even with overlapping jobs.

    Do not automatically retry sending/failed states: SMTP acceptance can be
    ambiguous. A crash before a claim leaves a pending email for the next run.
    """
    accepted = skipped = failed = 0
    failed = StaffMessageDelivery.objects.filter(email_status='sending',
        attempted_at__lt=timezone.now() - timedelta(minutes=15)).update(email_status='failed')
    if failed:
        record_operational_event(OperationalEvent.Kind.EMAIL_FAILED)
    pending_ids = list(StaffMessageDelivery.objects.filter(email_status='pending')
                       .order_by('pk').values_list('pk', flat=True)[:limit])
    for delivery_id in pending_ids:
        delivery = StaffMessageDelivery.objects.select_related('recipient', 'message__author',
            'message__thread__project', 'message__thread__organization').get(pk=delivery_id)
        user, message = delivery.recipient, delivery.message
        thread = message.thread
        participant = thread.participants.filter(user=user, email_enabled=True).first()
        if not participant or not staff_threads(user).filter(pk=thread.pk).exists():
            skipped += StaffMessageDelivery.objects.filter(pk=delivery_id, email_status='pending').update(email_status='skipped')
            continue
        if not StaffMessageDelivery.objects.filter(pk=delivery_id, email_status='pending').update(
                email_status='sending', attempted_at=timezone.now()):
            continue
        context = thread.project.name if thread.project_id else f'{thread.organization.name} — General'
        subject = ' '.join(f'Heritage staff message — {thread.subject}'.splitlines())
        url = settings.SITE_BASE_URL.rstrip('/') + reverse('projects:staff_message_thread', args=[thread.pk])
        result = send_notification_email(subject=subject,
            message=(f'{message.author.get_full_name() or message.author.email} wrote in {context}:\n'
                     f'{thread.subject}\n\n{message.body}\n\nOpen the conversation to respond:\n{url}\n\n'
                     'This is an internal staff conversation. Reply in Heritage 360; replying to this email does not post to the conversation.'),
            recipient_list=[user.email])
        StaffMessageDelivery.objects.filter(pk=delivery_id).update(email_status='accepted' if result else 'failed')
        if not result:
            failed += 1
            break  # A provider outage should not consume web workers or the whole queue.
        accepted += 1
    return accepted, skipped, failed


def audit_message(message):
    record_activity(organization=message.thread.organization, project=message.thread.project,
        actor=message.author, event_type=ActivityEvent.Type.STAFF_MESSAGE_SENT,
        summary='Internal staff message sent.', metadata={'staff_thread_id': message.thread_id})


@login_required
@require_http_methods(['GET', 'HEAD'])
def inbox(request):
    require_staff(request.user)
    threads = staff_threads(request.user).select_related('organization', 'project')
    project = None
    scope = request.GET.get('scope', '')
    if request.GET.get('project'):
        project = scoped_object(staff_projects(request.user), request.GET['project'])
        threads = threads.filter(project=project)
    elif scope == 'general':
        threads = threads.filter(project__isnull=True)
    search = request.GET.get('q', '').strip()[:100]
    if search:
        threads = threads.filter(Q(subject__icontains=search) | Q(messages__body__icontains=search)).distinct()
    unread = InAppNotification.objects.filter(staff_thread=OuterRef('pk'), user=request.user, read_at__isnull=True)
    threads = threads.annotate(is_unread=Exists(unread))
    if request.GET.get('unread') == '1':
        threads = threads.filter(is_unread=True)
    page = Paginator(threads, 25).get_page(request.GET.get('page'))
    params = request.GET.copy()
    params.pop('page', None)
    return render(request, 'projects/staff_inbox.html', {'page_obj': page, 'project': project,
        'scope': scope, 'search': search, 'unread_only': request.GET.get('unread') == '1',
        'querystring': params.urlencode()})


@login_required
@require_http_methods(['GET', 'HEAD'])
def start(request):
    require_staff(request.user)
    return render(request, 'projects/staff_start.html', {'form': ContextForm(user=request.user)})


@login_required
@require_http_methods(['GET', 'HEAD', 'POST'])
def compose(request):
    require_staff(request.user)
    project = None
    if request.GET.get('project'):
        project = scoped_object(staff_projects(request.user), request.GET['project'])
        organization = project.organization
        if request.GET.get('organization') and str(organization.pk) != request.GET['organization']:
            raise PermissionDenied('The project does not belong to this company.')
    else:
        organization = scoped_object(internal_organizations_for_user(request.user), request.GET.get('organization'))
    form = ComposeForm(request.POST if request.method == 'POST' else None,
                       user=request.user, organization=organization, project=project)
    if request.method == 'POST' and form.is_valid():
        existing = StaffMessage.objects.filter(author=request.user, submission_id=form.cleaned_data['submission_id']).first()
        if existing:
            get_object_or_404(staff_threads(request.user), pk=existing.thread_id)
            return redirect('projects:staff_message_thread', thread_pk=existing.thread_id)
        try:
            with transaction.atomic():
                thread = StaffThread.objects.create(organization=organization, project=project,
                    subject=form.cleaned_data['subject'], created_by=request.user)
                StaffParticipant.objects.bulk_create([StaffParticipant(thread=thread, user=user)
                    for user in [request.user, *form.cleaned_data['recipients']]])
                message = StaffMessage.objects.create(thread=thread, author=request.user,
                    body=form.cleaned_data['body'], submission_id=form.cleaned_data['submission_id'])
                queue_notifications(message)
                audit_message(message)
        except IntegrityError:
            existing = StaffMessage.objects.filter(author=request.user, submission_id=form.cleaned_data['submission_id']).first()
            if not existing:
                raise
            thread = get_object_or_404(staff_threads(request.user), pk=existing.thread_id)
        messages.success(request, 'Message sent. Email alerts are queued and usually arrive within five minutes.')
        return redirect('projects:staff_message_thread', thread_pk=thread.pk)
    return render(request, 'projects/staff_compose.html', {'form': form, 'organization': organization, 'project': project})


@login_required
@require_http_methods(['GET', 'HEAD', 'POST'])
def conversation(request, thread_pk):
    require_staff(request.user)
    thread = get_object_or_404(staff_threads(request.user).select_related('organization', 'project'), pk=thread_pk)
    form = ReplyForm(request.POST if request.method == 'POST' else None)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            thread = get_object_or_404(staff_threads(request.user).select_for_update(), pk=thread_pk)
            message, created = StaffMessage.objects.get_or_create(
                author=request.user, submission_id=form.cleaned_data['submission_id'],
                defaults={'thread': thread, 'body': form.cleaned_data['body']})
            if message.thread_id != thread.pk:
                form.add_error(None, 'This reply was already submitted in another conversation. Reload and try again.')
            elif created:
                StaffThread.objects.filter(pk=thread.pk).update(updated_at=message.created_at)
                queue_notifications(message)
                audit_message(message)
        if not form.errors:
            return redirect('projects:staff_message_thread', thread_pk=thread.pk)
    read_before = timezone.now()
    page = Paginator(thread.messages.select_related('author').order_by('-created_at', '-pk'), 50).get_page(request.GET.get('page'))
    if request.method == 'GET' and page.number == 1:
        # Do not mark a reply that arrives while this page is being read.
        thread.notifications.filter(user=request.user, read_at__isnull=True, created_at__lte=read_before).update(read_at=read_before)
    participant = thread.participants.get(user=request.user)
    return render(request, 'projects/staff_thread.html', {'thread': thread, 'project': thread.project,
        'form': form, 'thread_messages': reversed(list(page.object_list)), 'page_obj': page,
        'participants': thread.participants.select_related('user'), 'email_enabled': participant.email_enabled})


@login_required
@require_POST
def preferences(request, thread_pk):
    require_staff(request.user)
    thread = get_object_or_404(staff_threads(request.user), pk=thread_pk)
    thread.participants.filter(user=request.user).update(email_enabled=request.POST.get('email_enabled') == 'on')
    messages.success(request, 'Your email preference for this conversation was saved.')
    return redirect('projects:staff_message_thread', thread_pk=thread.pk)
