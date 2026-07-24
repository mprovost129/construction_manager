import mimetypes

from django.contrib import messages
from django.contrib.auth import get_user_model, login
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Count, Max
from django.http import FileResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views import View
from django.views.generic import DetailView, FormView, TemplateView

from .access import (
    can_invite_clients,
    can_manage_organization,
    can_manage_project,
    can_use_change_orders,
    can_use_project_documents,
    can_use_project_messaging,
    internal_organizations_for_user,
    is_project_client,
    organization_membership_for,
    projects_for_user,
)
from .forms import (
    ChangeOrderDecisionForm,
    ChangeOrderForm,
    ClientInvitationForm,
    ConversationReplyForm,
    ConversationThreadForm,
    DocumentDecisionForm,
    InvitationSignupForm,
    ProjectDocumentCreateForm,
    ProjectDocumentVersionForm,
    ProjectForm,
    TeamInvitationForm,
    TeamMembershipForm,
)
from .models import (
    ActivityEvent,
    ChangeOrder,
    ConversationMessage,
    ConversationThread,
    DocumentDecision,
    Organization,
    OrganizationInvitation,
    OrganizationMembership,
    Project,
    ProjectDocument,
    ProjectDocumentVersion,
    ProjectInvitation,
    ProjectMembership,
)
from .services import (
    accept_organization_invitation,
    accept_project_invitation,
    document_client_recipients,
    record_activity,
    send_change_order_decision_notification,
    send_change_order_review_notification,
    send_change_order_voided_notification,
    send_document_available_notification,
    send_document_decision_notification,
    send_message_notifications,
    send_project_invitation,
    send_team_invitation,
)


def managed_project_or_404(user, pk):
    project = get_object_or_404(
        projects_for_user(user).select_related('organization'),
        pk=pk,
    )
    if not can_manage_project(user, project):
        raise PermissionDenied('You cannot manage this project.')
    return project


def messaging_project_or_404(user, pk):
    project = get_object_or_404(
        projects_for_user(user).select_related('organization'),
        pk=pk,
    )
    if not can_use_project_messaging(user, project):
        raise PermissionDenied('You cannot access project messaging.')
    return project


def documents_project_or_404(user, pk):
    project = get_object_or_404(
        projects_for_user(user).select_related('organization'),
        pk=pk,
    )
    if not can_use_project_documents(user, project):
        raise PermissionDenied('You cannot access project documents.')
    return project


def visible_document_or_404(user, project, document_pk):
    queryset = ProjectDocument.objects.filter(project=project)
    if is_project_client(user, project):
        queryset = queryset.filter(client_visible=True)
    return get_object_or_404(queryset, pk=document_pk)


def change_orders_project_or_404(user, pk):
    project = get_object_or_404(
        projects_for_user(user).select_related('organization'),
        pk=pk,
    )
    if not can_use_change_orders(user, project):
        raise PermissionDenied('You cannot access project change orders.')
    return project


def visible_change_order_or_404(user, project, change_order_pk):
    queryset = ChangeOrder.objects.filter(project=project)
    if is_project_client(user, project):
        queryset = queryset.exclude(status=ChangeOrder.Status.DRAFT)
    return get_object_or_404(queryset, pk=change_order_pk)


def internal_organization_or_404(user, slug):
    return get_object_or_404(internal_organizations_for_user(user), slug=slug)


def managed_organization_or_404(user, slug):
    organization = internal_organization_or_404(user, slug)
    if not can_manage_organization(user, organization):
        raise PermissionDenied('Only company administrators can manage the team.')
    return organization


class ProjectDetailView(LoginRequiredMixin, DetailView):
    model = Project
    template_name = 'projects/project_detail.html'
    context_object_name = 'project'

    def get_queryset(self):
        return projects_for_user(self.request.user).select_related('organization')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        organization_membership = organization_membership_for(
            self.request.user, self.object.organization
        )
        project_membership = self.object.project_memberships.filter(
            user=self.request.user,
            is_active=True,
        ).first()
        is_internal = bool(
            self.request.user.is_superuser
            or (organization_membership and organization_membership.is_internal)
        )
        context.update(
            {
                'organization_membership': organization_membership,
                'project_membership': project_membership,
                'can_manage_project': can_manage_project(
                    self.request.user, self.object
                ),
                'can_invite_clients': can_invite_clients(
                    self.request.user, self.object
                ),
                'can_use_project_messaging': can_use_project_messaging(
                    self.request.user, self.object
                ),
                'can_use_project_documents': can_use_project_documents(
                    self.request.user, self.object
                ),
                'can_use_change_orders': can_use_change_orders(
                    self.request.user, self.object
                ),
                'can_view_project_financials': bool(
                    self.request.user.is_superuser
                    or (
                        organization_membership
                        and organization_membership.can_view_project_financials
                    )
                ),
                'activity_events': (
                    self.object.activity_events.select_related('actor')[:10]
                    if is_internal
                    else ()
                ),
            }
        )
        return context


class ProjectMessageListView(LoginRequiredMixin, TemplateView):
    template_name = 'projects/message_list.html'

    def dispatch(self, request, *args, **kwargs):
        self.project = messaging_project_or_404(request.user, kwargs['pk'])
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(
            {
                'project': self.project,
                'threads': self.project.conversation_threads.select_related(
                    'created_by'
                ).annotate(message_count=Count('messages')),
                'can_manage_project': can_manage_project(
                    self.request.user, self.project
                ),
            }
        )
        return context


class ProjectMessageCreateView(LoginRequiredMixin, FormView):
    form_class = ConversationThreadForm
    template_name = 'projects/message_form.html'

    def dispatch(self, request, *args, **kwargs):
        self.project = messaging_project_or_404(request.user, kwargs['pk'])
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['project'] = self.project
        return context

    def form_valid(self, form):
        with transaction.atomic():
            thread = ConversationThread.objects.create(
                project=self.project,
                subject=form.cleaned_data['subject'].strip(),
                created_by=self.request.user,
            )
            message = ConversationMessage(
                thread=thread,
                author=self.request.user,
                body=form.cleaned_data['body'],
            )
            message.full_clean()
            message.save()
            record_activity(
                organization=self.project.organization,
                project=self.project,
                actor=self.request.user,
                event_type=ActivityEvent.Type.MESSAGE_THREAD_CREATED,
                summary=(
                    f'{self.request.user.email} started the conversation '
                    f'"{thread.subject}".'
                ),
                metadata={'thread_id': thread.pk, 'subject': thread.subject},
            )
        send_message_notifications(self.request, message, new_thread=True)
        messages.success(self.request, 'Conversation started.')
        return redirect(
            'projects:message_thread', pk=self.project.pk, thread_pk=thread.pk
        )


class ProjectMessageThreadView(LoginRequiredMixin, View):
    template_name = 'projects/message_thread.html'

    def get_objects(self, request, pk, thread_pk):
        project = messaging_project_or_404(request.user, pk)
        thread = get_object_or_404(
            ConversationThread.objects.select_related('created_by'),
            pk=thread_pk,
            project=project,
        )
        return project, thread

    def render_thread(self, request, project, thread, form=None):
        return render(
            request,
            self.template_name,
            {
                'project': project,
                'thread': thread,
                'thread_messages': thread.messages.select_related('author'),
                'form': form or ConversationReplyForm(),
                'can_manage_project': can_manage_project(request.user, project),
                'viewer_is_client': is_project_client(request.user, project),
            },
        )

    def get(self, request, pk, thread_pk):
        project, thread = self.get_objects(request, pk, thread_pk)
        return self.render_thread(request, project, thread)

    def post(self, request, pk, thread_pk):
        project, thread = self.get_objects(request, pk, thread_pk)
        if thread.status == ConversationThread.Status.CLOSED:
            messages.error(request, 'This conversation is closed. It must be reopened before replying.')
            return redirect(
                'projects:message_thread', pk=project.pk, thread_pk=thread.pk
            )
        form = ConversationReplyForm(request.POST)
        if not form.is_valid():
            return self.render_thread(request, project, thread, form=form)
        with transaction.atomic():
            message = form.save(commit=False)
            message.thread = thread
            message.author = request.user
            message.full_clean()
            message.save()
            ConversationThread.objects.filter(pk=thread.pk).update(
                updated_at=timezone.now()
            )
            record_activity(
                organization=project.organization,
                project=project,
                actor=request.user,
                event_type=ActivityEvent.Type.MESSAGE_SENT,
                summary=f'{request.user.email} replied to "{thread.subject}".',
                metadata={'thread_id': thread.pk, 'subject': thread.subject},
            )
        send_message_notifications(request, message)
        messages.success(request, 'Reply sent.')
        return redirect(
            'projects:message_thread', pk=project.pk, thread_pk=thread.pk
        )


class ProjectDocumentListView(LoginRequiredMixin, TemplateView):
    template_name = 'projects/document_list.html'

    def dispatch(self, request, *args, **kwargs):
        self.project = documents_project_or_404(request.user, kwargs['pk'])
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        documents = self.project.documents.prefetch_related(
            'versions__decisions'
        )
        if is_project_client(self.request.user, self.project):
            documents = documents.filter(client_visible=True)
        for document in documents:
            version = document.latest_version
            document.current_version = version
            document.current_decisions = list(version.decisions.all()) if version else []
            decisions = {decision.decision for decision in document.current_decisions}
            if not document.requires_client_approval:
                document.approval_status = 'Not required'
                document.approval_status_class = ''
            elif DocumentDecision.Decision.DECLINED in decisions:
                document.approval_status = 'Declined'
                document.approval_status_class = 'status-pill--declined'
            elif DocumentDecision.Decision.APPROVED in decisions:
                document.approval_status = 'Approved'
                document.approval_status_class = 'status-pill--active'
            else:
                document.approval_status = 'Awaiting decision'
                document.approval_status_class = 'status-pill--on_hold'
        context.update(
            {
                'project': self.project,
                'documents': documents,
                'can_manage_project': can_manage_project(
                    self.request.user, self.project
                ),
            }
        )
        return context


class ProjectDocumentCreateView(LoginRequiredMixin, FormView):
    form_class = ProjectDocumentCreateForm
    template_name = 'projects/document_form.html'

    def dispatch(self, request, *args, **kwargs):
        self.project = managed_project_or_404(request.user, kwargs['pk'])
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['project'] = self.project
        return context

    def form_valid(self, form):
        upload = form.cleaned_data['file']
        with transaction.atomic():
            document = form.save(commit=False)
            document.project = self.project
            document.created_by = self.request.user
            document.full_clean()
            document.save()
            version = ProjectDocumentVersion.objects.create(
                document=document,
                version_number=1,
                file=upload,
                original_filename=upload.name,
                notes=form.cleaned_data['version_notes'],
                uploaded_by=self.request.user,
            )
            record_activity(
                organization=self.project.organization,
                project=self.project,
                actor=self.request.user,
                event_type=ActivityEvent.Type.DOCUMENT_CREATED,
                summary=f'{self.request.user.email} added "{document.title}".',
                metadata={
                    'document_id': document.pk,
                    'version_id': version.pk,
                    'version_number': version.version_number,
                },
            )
        send_document_available_notification(self.request, version)
        messages.success(self.request, f'{document.title} was uploaded.')
        return redirect(
            'projects:document_detail',
            pk=self.project.pk,
            document_pk=document.pk,
        )


class ProjectDocumentDetailView(LoginRequiredMixin, TemplateView):
    template_name = 'projects/document_detail.html'

    def dispatch(self, request, *args, **kwargs):
        self.project = documents_project_or_404(request.user, kwargs['pk'])
        self.document = visible_document_or_404(
            request.user, self.project, kwargs['document_pk']
        )
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        versions = self.document.versions.prefetch_related(
            'decisions__decided_by'
        ).select_related('uploaded_by')
        current_version = versions.first()
        viewer_is_client = is_project_client(self.request.user, self.project)
        viewer_decision = None
        if current_version:
            viewer_decision = current_version.decisions.filter(
                decided_by=self.request.user
            ).first()
        context.update(
            {
                'project': self.project,
                'document': self.document,
                'versions': versions,
                'current_version': current_version,
                'viewer_decision': viewer_decision,
                'can_manage_project': can_manage_project(
                    self.request.user, self.project
                ),
                'can_decide': bool(
                    viewer_is_client
                    and current_version
                    and self.document.requires_client_approval
                    and not viewer_decision
                ),
                'decision_form': DocumentDecisionForm(),
            }
        )
        return context


class ProjectDocumentVersionCreateView(LoginRequiredMixin, FormView):
    form_class = ProjectDocumentVersionForm
    template_name = 'projects/document_version_form.html'

    def dispatch(self, request, *args, **kwargs):
        self.project = managed_project_or_404(request.user, kwargs['pk'])
        self.document = get_object_or_404(
            ProjectDocument, project=self.project, pk=kwargs['document_pk']
        )
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update({'project': self.project, 'document': self.document})
        return context

    def form_valid(self, form):
        upload = form.cleaned_data['file']
        with transaction.atomic():
            document = ProjectDocument.objects.select_for_update().get(
                pk=self.document.pk
            )
            latest = document.versions.first()
            version = ProjectDocumentVersion.objects.create(
                document=document,
                version_number=(latest.version_number + 1 if latest else 1),
                file=upload,
                original_filename=upload.name,
                notes=form.cleaned_data['notes'],
                uploaded_by=self.request.user,
            )
            record_activity(
                organization=self.project.organization,
                project=self.project,
                actor=self.request.user,
                event_type=ActivityEvent.Type.DOCUMENT_VERSION_ADDED,
                summary=(
                    f'{self.request.user.email} added version '
                    f'{version.version_number} of "{document.title}".'
                ),
                metadata={
                    'document_id': document.pk,
                    'version_id': version.pk,
                    'version_number': version.version_number,
                },
            )
        send_document_available_notification(self.request, version)
        messages.success(self.request, f'Version {version.version_number} uploaded.')
        return redirect(
            'projects:document_detail',
            pk=self.project.pk,
            document_pk=self.document.pk,
        )


class ProjectDocumentDownloadView(LoginRequiredMixin, View):
    def get(self, request, pk, document_pk, version_pk):
        project = documents_project_or_404(request.user, pk)
        document = visible_document_or_404(request.user, project, document_pk)
        version = get_object_or_404(
            ProjectDocumentVersion, pk=version_pk, document=document
        )
        content_type, _ = mimetypes.guess_type(version.original_filename)
        return FileResponse(
            version.file.open('rb'),
            as_attachment=True,
            filename=version.original_filename,
            content_type=content_type or 'application/octet-stream',
        )


class ProjectDocumentDecisionView(LoginRequiredMixin, View):
    http_method_names = ('post',)

    def post(self, request, pk, document_pk):
        project = documents_project_or_404(request.user, pk)
        if not is_project_client(request.user, project):
            raise PermissionDenied('Only project clients can record a decision.')
        document = get_object_or_404(
            ProjectDocument,
            pk=document_pk,
            project=project,
            client_visible=True,
            requires_client_approval=True,
        )
        version = document.latest_version
        if not version:
            raise PermissionDenied('This document does not have a version to review.')
        if version.decisions.filter(decided_by=request.user).exists():
            messages.info(request, 'Your decision for this version is already recorded.')
            return redirect(
                'projects:document_detail', pk=project.pk, document_pk=document.pk
            )
        form = DocumentDecisionForm(request.POST)
        if not form.is_valid():
            messages.error(request, 'Choose approve or decline before submitting.')
            return redirect(
                'projects:document_detail', pk=project.pk, document_pk=document.pk
            )
        with transaction.atomic():
            decision = form.save(commit=False)
            decision.version = version
            decision.decided_by = request.user
            decision.full_clean()
            decision.save()
            record_activity(
                organization=project.organization,
                project=project,
                actor=request.user,
                event_type=ActivityEvent.Type.DOCUMENT_DECISION_RECORDED,
                summary=(
                    f'{request.user.email} {decision.get_decision_display().lower()} '
                    f'"{document.title}" version {version.version_number}.'
                ),
                metadata={
                    'document_id': document.pk,
                    'version_id': version.pk,
                    'version_number': version.version_number,
                    'decision': decision.decision,
                },
            )
        send_document_decision_notification(request, decision)
        messages.success(request, 'Your document decision was recorded.')
        return redirect(
            'projects:document_detail', pk=project.pk, document_pk=document.pk
        )


class ChangeOrderListView(LoginRequiredMixin, TemplateView):
    template_name = 'projects/change_order_list.html'

    def dispatch(self, request, *args, **kwargs):
        self.project = change_orders_project_or_404(request.user, kwargs['pk'])
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        change_orders = self.project.change_orders.select_related(
            'created_by', 'decided_by'
        )
        if is_project_client(self.request.user, self.project):
            change_orders = change_orders.exclude(status=ChangeOrder.Status.DRAFT)
        context.update(
            {
                'project': self.project,
                'change_orders': change_orders,
                'can_manage_project': can_manage_project(
                    self.request.user, self.project
                ),
            }
        )
        return context


class ChangeOrderCreateView(LoginRequiredMixin, FormView):
    form_class = ChangeOrderForm
    template_name = 'projects/change_order_form.html'

    def dispatch(self, request, *args, **kwargs):
        self.project = managed_project_or_404(request.user, kwargs['pk'])
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update({'project': self.project, 'change_order': None})
        return context

    def form_valid(self, form):
        with transaction.atomic():
            Project.objects.select_for_update().get(pk=self.project.pk)
            current_number = self.project.change_orders.aggregate(
                maximum=Max('number')
            )['maximum']
            change_order = form.save(commit=False)
            change_order.project = self.project
            change_order.number = (current_number or 0) + 1
            change_order.created_by = self.request.user
            change_order.full_clean()
            change_order.save()
            record_activity(
                organization=self.project.organization,
                project=self.project,
                actor=self.request.user,
                event_type=ActivityEvent.Type.CHANGE_ORDER_CREATED,
                summary=(
                    f'{self.request.user.email} created '
                    f'{change_order.display_number} - "{change_order.title}".'
                ),
                metadata={
                    'change_order_id': change_order.pk,
                    'number': change_order.number,
                    'price_delta': str(change_order.price_delta),
                    'cost_delta': str(change_order.cost_delta),
                },
            )
        messages.success(self.request, f'{change_order.display_number} was created.')
        return redirect(
            'projects:change_order_detail',
            pk=self.project.pk,
            change_order_pk=change_order.pk,
        )


class ChangeOrderUpdateView(LoginRequiredMixin, FormView):
    form_class = ChangeOrderForm
    template_name = 'projects/change_order_form.html'

    def dispatch(self, request, *args, **kwargs):
        self.project = managed_project_or_404(request.user, kwargs['pk'])
        self.change_order = get_object_or_404(
            ChangeOrder,
            project=self.project,
            pk=kwargs['change_order_pk'],
        )
        if self.change_order.status != ChangeOrder.Status.DRAFT:
            raise PermissionDenied('Only draft change orders can be edited.')
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['instance'] = self.change_order
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(
            {'project': self.project, 'change_order': self.change_order}
        )
        return context

    def form_valid(self, form):
        editable_fields = tuple(ChangeOrderForm.Meta.fields)
        with transaction.atomic():
            change_order = get_object_or_404(
                ChangeOrder.objects.select_for_update(),
                project=self.project,
                pk=self.change_order.pk,
            )
            if change_order.status != ChangeOrder.Status.DRAFT:
                raise PermissionDenied('Only draft change orders can be edited.')
            for field_name in editable_fields:
                setattr(change_order, field_name, form.cleaned_data[field_name])
            change_order.full_clean()
            change_order.save(update_fields=editable_fields + ('updated_at',))
            record_activity(
                organization=self.project.organization,
                project=self.project,
                actor=self.request.user,
                event_type=ActivityEvent.Type.CHANGE_ORDER_UPDATED,
                summary=(
                    f'{self.request.user.email} updated '
                    f'{change_order.display_number} - "{change_order.title}".'
                ),
                metadata={
                    'change_order_id': change_order.pk,
                    'number': change_order.number,
                    'price_delta': str(change_order.price_delta),
                    'cost_delta': str(change_order.cost_delta),
                },
            )
        messages.success(self.request, f'{change_order.display_number} was updated.')
        return redirect(
            'projects:change_order_detail',
            pk=self.project.pk,
            change_order_pk=change_order.pk,
        )


class ChangeOrderDetailView(LoginRequiredMixin, TemplateView):
    template_name = 'projects/change_order_detail.html'

    def dispatch(self, request, *args, **kwargs):
        self.project = change_orders_project_or_404(request.user, kwargs['pk'])
        self.change_order = visible_change_order_or_404(
            request.user, self.project, kwargs['change_order_pk']
        )
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        viewer_is_client = is_project_client(self.request.user, self.project)
        context.update(
            {
                'project': self.project,
                'change_order': self.change_order,
                'can_manage_project': can_manage_project(
                    self.request.user, self.project
                ),
                'can_decide': bool(
                    viewer_is_client
                    and self.change_order.status == ChangeOrder.Status.PENDING
                ),
                'decision_form': ChangeOrderDecisionForm(),
            }
        )
        return context


class ChangeOrderSubmitView(LoginRequiredMixin, View):
    http_method_names = ('post',)

    def post(self, request, pk, change_order_pk):
        project = managed_project_or_404(request.user, pk)
        if not document_client_recipients(project):
            messages.error(
                request,
                'Assign an active client to the project before requesting approval.',
            )
            return redirect(
                'projects:change_order_detail',
                pk=project.pk,
                change_order_pk=change_order_pk,
            )
        with transaction.atomic():
            change_order = get_object_or_404(
                ChangeOrder.objects.select_for_update(),
                project=project,
                pk=change_order_pk,
            )
            if change_order.status != ChangeOrder.Status.DRAFT:
                raise PermissionDenied('Only draft change orders can be submitted.')
            change_order.status = ChangeOrder.Status.PENDING
            change_order.submitted_by = request.user
            change_order.submitted_at = timezone.now()
            change_order.full_clean()
            change_order.save(
                update_fields=(
                    'status',
                    'submitted_by',
                    'submitted_at',
                    'updated_at',
                )
            )
            record_activity(
                organization=project.organization,
                project=project,
                actor=request.user,
                event_type=ActivityEvent.Type.CHANGE_ORDER_SUBMITTED,
                summary=(
                    f'{request.user.email} sent {change_order.display_number} '
                    'to the client for a decision.'
                ),
                metadata={
                    'change_order_id': change_order.pk,
                    'number': change_order.number,
                },
            )
        send_change_order_review_notification(request, change_order)
        messages.success(request, 'The change order was sent to the client.')
        return redirect(
            'projects:change_order_detail',
            pk=project.pk,
            change_order_pk=change_order.pk,
        )


class ChangeOrderDecisionView(LoginRequiredMixin, View):
    http_method_names = ('post',)

    def post(self, request, pk, change_order_pk):
        project = change_orders_project_or_404(request.user, pk)
        if not is_project_client(request.user, project):
            raise PermissionDenied('Only assigned clients can record a decision.')
        form = ChangeOrderDecisionForm(request.POST)
        if not form.is_valid():
            messages.error(request, 'Choose approve or decline before submitting.')
            return redirect(
                'projects:change_order_detail',
                pk=project.pk,
                change_order_pk=change_order_pk,
            )
        with transaction.atomic():
            change_order = get_object_or_404(
                ChangeOrder.objects.select_for_update(),
                project=project,
                pk=change_order_pk,
            )
            if change_order.status != ChangeOrder.Status.PENDING:
                messages.info(request, 'This change order is no longer awaiting a decision.')
                return redirect(
                    'projects:change_order_detail',
                    pk=project.pk,
                    change_order_pk=change_order.pk,
                )
            change_order.status = form.cleaned_data['decision']
            change_order.client_comment = form.cleaned_data['comment']
            change_order.decided_by = request.user
            change_order.decided_at = timezone.now()
            change_order.full_clean()
            change_order.save(
                update_fields=(
                    'status',
                    'client_comment',
                    'decided_by',
                    'decided_at',
                    'updated_at',
                )
            )
            record_activity(
                organization=project.organization,
                project=project,
                actor=request.user,
                event_type=ActivityEvent.Type.CHANGE_ORDER_DECIDED,
                summary=(
                    f'{request.user.email} {change_order.get_status_display().lower()} '
                    f'{change_order.display_number} - "{change_order.title}".'
                ),
                metadata={
                    'change_order_id': change_order.pk,
                    'number': change_order.number,
                    'decision': change_order.status,
                },
            )
        send_change_order_decision_notification(request, change_order)
        messages.success(request, 'Your change order decision was recorded.')
        return redirect(
            'projects:change_order_detail',
            pk=project.pk,
            change_order_pk=change_order.pk,
        )


class ChangeOrderVoidView(LoginRequiredMixin, View):
    http_method_names = ('post',)

    def post(self, request, pk, change_order_pk):
        project = managed_project_or_404(request.user, pk)
        with transaction.atomic():
            change_order = get_object_or_404(
                ChangeOrder.objects.select_for_update(),
                project=project,
                pk=change_order_pk,
            )
            if change_order.status != ChangeOrder.Status.PENDING:
                raise PermissionDenied(
                    'Only change orders awaiting a client decision can be voided.'
                )
            change_order.status = ChangeOrder.Status.VOIDED
            change_order.voided_by = request.user
            change_order.voided_at = timezone.now()
            change_order.full_clean()
            change_order.save(
                update_fields=(
                    'status',
                    'voided_by',
                    'voided_at',
                    'updated_at',
                )
            )
            record_activity(
                organization=project.organization,
                project=project,
                actor=request.user,
                event_type=ActivityEvent.Type.CHANGE_ORDER_VOIDED,
                summary=(
                    f'{request.user.email} voided {change_order.display_number} '
                    f'- "{change_order.title}".'
                ),
                metadata={
                    'change_order_id': change_order.pk,
                    'number': change_order.number,
                },
            )
        send_change_order_voided_notification(request, change_order)
        messages.success(request, 'The change order was voided.')
        return redirect(
            'projects:change_order_detail',
            pk=project.pk,
            change_order_pk=change_order.pk,
        )


class ProjectMessageStatusView(LoginRequiredMixin, View):
    http_method_names = ('post',)

    def post(self, request, pk, thread_pk, action):
        project = managed_project_or_404(request.user, pk)
        thread = get_object_or_404(
            ConversationThread, pk=thread_pk, project=project
        )
        if action not in ('close', 'reopen'):
            raise PermissionDenied('Unknown conversation action.')
        thread.status = (
            ConversationThread.Status.CLOSED
            if action == 'close'
            else ConversationThread.Status.OPEN
        )
        thread.save(update_fields=('status', 'updated_at'))
        record_activity(
            organization=project.organization,
            project=project,
            actor=request.user,
            event_type=ActivityEvent.Type.MESSAGE_THREAD_STATUS_CHANGED,
            summary=f'{request.user.email} {action}d "{thread.subject}".',
            metadata={
                'thread_id': thread.pk,
                'subject': thread.subject,
                'status': thread.status,
            },
        )
        messages.success(request, f'Conversation {action}d.')
        return redirect(
            'projects:message_thread', pk=project.pk, thread_pk=thread.pk
        )


class ProjectCreateView(LoginRequiredMixin, FormView):
    form_class = ProjectForm
    template_name = 'projects/project_form.html'

    def get_organizations(self):
        return internal_organizations_for_user(
            self.request.user, management_only=True
        )

    def dispatch(self, request, *args, **kwargs):
        if not self.get_organizations().exists():
            raise PermissionDenied('You cannot create projects.')
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['organizations'] = self.get_organizations()
        return kwargs

    def get_initial(self):
        initial = super().get_initial()
        organization_id = self.request.GET.get('organization')
        if organization_id and self.get_organizations().filter(pk=organization_id).exists():
            initial['organization'] = organization_id
        elif self.get_organizations().count() == 1:
            initial['organization'] = self.get_organizations().first()
        return initial

    def form_valid(self, form):
        project = form.save(commit=False)
        project.created_by = self.request.user
        project.save()
        record_activity(
            organization=project.organization,
            project=project,
            actor=self.request.user,
            event_type=ActivityEvent.Type.PROJECT_CREATED,
            summary=f'{self.request.user.email} created the project.',
        )
        messages.success(self.request, f'{project.name} was created.')
        return redirect('projects:detail', pk=project.pk)


class ProjectUpdateView(LoginRequiredMixin, FormView):
    form_class = ProjectForm
    template_name = 'projects/project_form.html'

    def dispatch(self, request, *args, **kwargs):
        self.project = managed_project_or_404(request.user, kwargs['pk'])
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs.update(
            {
                'instance': self.project,
                'organizations': Organization.objects.filter(
                    pk=self.project.organization_id
                ),
                'lock_organization': True,
            }
        )
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['project'] = self.project
        return context

    def form_valid(self, form):
        changed_fields = list(form.changed_data)
        project = form.save()
        if changed_fields:
            record_activity(
                organization=project.organization,
                project=project,
                actor=self.request.user,
                event_type=ActivityEvent.Type.PROJECT_UPDATED,
                summary=f'{self.request.user.email} updated the project.',
                metadata={'fields': changed_fields},
            )
        messages.success(self.request, f'{project.name} was updated.')
        return redirect('projects:detail', pk=project.pk)


class ProjectPeopleView(LoginRequiredMixin, TemplateView):
    template_name = 'projects/project_people.html'

    def dispatch(self, request, *args, **kwargs):
        self.project = managed_project_or_404(request.user, kwargs['pk'])
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(
            {
                'project': self.project,
                'project_memberships': self.project.project_memberships.select_related(
                    'user'
                ),
                'invitations': self.project.invitations.select_related(
                    'invited_by', 'accepted_by'
                )[:50],
            }
        )
        return context


class ClientInviteView(LoginRequiredMixin, FormView):
    form_class = ClientInvitationForm
    template_name = 'projects/invite_client.html'

    def dispatch(self, request, *args, **kwargs):
        self.project = managed_project_or_404(request.user, kwargs['pk'])
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['project'] = self.project
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['project'] = self.project
        return context

    def form_valid(self, form):
        with transaction.atomic():
            if form.expired_invitation:
                form.expired_invitation.revoked_at = timezone.now()
                form.expired_invitation.save(update_fields=('revoked_at',))
            invitation = form.save(commit=False)
            invitation.project = self.project
            invitation.role = OrganizationMembership.Role.CLIENT
            invitation.invited_by = self.request.user
            invitation.full_clean()
            invitation.save()
            record_activity(
                organization=self.project.organization,
                project=self.project,
                actor=self.request.user,
                event_type=ActivityEvent.Type.CLIENT_INVITED,
                summary=f'{self.request.user.email} invited {invitation.email}.',
                metadata={'email': invitation.email},
            )
        send_project_invitation(self.request, invitation)
        messages.success(self.request, f'Invitation sent to {invitation.email}.')
        return redirect('projects:people', pk=self.project.pk)


class ProjectInvitationResendView(LoginRequiredMixin, View):
    http_method_names = ('post',)

    def post(self, request, pk, invitation_pk):
        project = managed_project_or_404(request.user, pk)
        old_invitation = get_object_or_404(
            ProjectInvitation, pk=invitation_pk, project=project
        )
        if old_invitation.accepted_at:
            messages.error(request, 'Accepted invitations cannot be resent.')
            return redirect('projects:people', pk=project.pk)
        with transaction.atomic():
            old_invitation.revoked_at = timezone.now()
            old_invitation.save(update_fields=('revoked_at',))
            invitation = ProjectInvitation.objects.create(
                project=project,
                email=old_invitation.email,
                role=old_invitation.role,
                invited_by=request.user,
            )
            record_activity(
                organization=project.organization,
                project=project,
                actor=request.user,
                event_type=ActivityEvent.Type.CLIENT_INVITE_RESENT,
                summary=f'{request.user.email} resent the invitation to {invitation.email}.',
                metadata={'email': invitation.email},
            )
        send_project_invitation(request, invitation)
        messages.success(request, f'Invitation resent to {invitation.email}.')
        return redirect('projects:people', pk=project.pk)


class ProjectInvitationRevokeView(LoginRequiredMixin, View):
    http_method_names = ('post',)

    def post(self, request, pk, invitation_pk):
        project = managed_project_or_404(request.user, pk)
        invitation = get_object_or_404(
            ProjectInvitation, pk=invitation_pk, project=project
        )
        if invitation.accepted_at:
            messages.error(request, 'Accepted invitations cannot be revoked.')
        elif invitation.revoked_at:
            messages.info(request, 'That invitation is already revoked.')
        else:
            invitation.revoked_at = timezone.now()
            invitation.save(update_fields=('revoked_at',))
            record_activity(
                organization=project.organization,
                project=project,
                actor=request.user,
                event_type=ActivityEvent.Type.CLIENT_INVITE_REVOKED,
                summary=f'{request.user.email} revoked the invitation to {invitation.email}.',
                metadata={'email': invitation.email},
            )
            messages.success(request, f'Invitation to {invitation.email} was revoked.')
        return redirect('projects:people', pk=project.pk)


class ProjectMemberAccessView(LoginRequiredMixin, View):
    http_method_names = ('post',)

    def post(self, request, pk, membership_pk, action):
        project = managed_project_or_404(request.user, pk)
        membership = get_object_or_404(
            ProjectMembership.objects.select_related('user'),
            pk=membership_pk,
            project=project,
        )
        if action not in ('revoke', 'restore'):
            raise PermissionDenied('Unknown access action.')
        active = action == 'restore'
        membership.is_active = active
        membership.save(update_fields=('is_active',))
        event_type = (
            ActivityEvent.Type.CLIENT_ACCESS_RESTORED
            if active
            else ActivityEvent.Type.CLIENT_ACCESS_REVOKED
        )
        verb = 'restored' if active else 'revoked'
        record_activity(
            organization=project.organization,
            project=project,
            actor=request.user,
            event_type=event_type,
            summary=f'{request.user.email} {verb} access for {membership.user.email}.',
            metadata={'email': membership.user.email},
        )
        messages.success(request, f'Project access was {verb}.')
        return redirect('projects:people', pk=project.pk)


class CompanyListView(LoginRequiredMixin, TemplateView):
    template_name = 'projects/company_list.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['organizations'] = internal_organizations_for_user(self.request.user)
        return context


class CompanyTeamView(LoginRequiredMixin, TemplateView):
    template_name = 'projects/company_team.html'

    def dispatch(self, request, *args, **kwargs):
        self.organization = internal_organization_or_404(
            request.user, kwargs['slug']
        )
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(
            {
                'organization': self.organization,
                'can_manage_team': can_manage_organization(
                    self.request.user, self.organization
                ),
                'team_memberships': self.organization.memberships.filter(
                    role__in=OrganizationMembership.INTERNAL_ROLES
                ).select_related('user'),
                'team_invitations': self.organization.team_invitations.select_related(
                    'invited_by', 'accepted_by'
                )[:50],
                'activity_events': self.organization.activity_events.filter(
                    project__isnull=True
                ).select_related('actor')[:20],
            }
        )
        return context


class TeamInviteView(LoginRequiredMixin, FormView):
    form_class = TeamInvitationForm
    template_name = 'projects/invite_team_member.html'

    def dispatch(self, request, *args, **kwargs):
        self.organization = managed_organization_or_404(
            request.user, kwargs['slug']
        )
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['organization'] = self.organization
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['organization'] = self.organization
        return context

    def form_valid(self, form):
        with transaction.atomic():
            if form.expired_invitation:
                form.expired_invitation.revoked_at = timezone.now()
                form.expired_invitation.save(update_fields=('revoked_at',))
            invitation = form.save(commit=False)
            invitation.organization = self.organization
            invitation.invited_by = self.request.user
            invitation.full_clean()
            invitation.save()
            record_activity(
                organization=self.organization,
                actor=self.request.user,
                event_type=ActivityEvent.Type.TEAM_INVITED,
                summary=(
                    f'{self.request.user.email} invited {invitation.email} as '
                    f'{invitation.get_role_display()}.'
                ),
                metadata={'email': invitation.email, 'role': invitation.role},
            )
        send_team_invitation(self.request, invitation)
        messages.success(self.request, f'Invitation sent to {invitation.email}.')
        return redirect('projects:company_team', slug=self.organization.slug)


class TeamInvitationResendView(LoginRequiredMixin, View):
    http_method_names = ('post',)

    def post(self, request, slug, invitation_pk):
        organization = managed_organization_or_404(request.user, slug)
        old_invitation = get_object_or_404(
            OrganizationInvitation,
            pk=invitation_pk,
            organization=organization,
        )
        if old_invitation.accepted_at:
            messages.error(request, 'Accepted invitations cannot be resent.')
            return redirect('projects:company_team', slug=organization.slug)
        with transaction.atomic():
            old_invitation.revoked_at = timezone.now()
            old_invitation.save(update_fields=('revoked_at',))
            invitation = OrganizationInvitation.objects.create(
                organization=organization,
                email=old_invitation.email,
                role=old_invitation.role,
                invited_by=request.user,
            )
            record_activity(
                organization=organization,
                actor=request.user,
                event_type=ActivityEvent.Type.TEAM_INVITE_RESENT,
                summary=f'{request.user.email} resent the invitation to {invitation.email}.',
                metadata={'email': invitation.email, 'role': invitation.role},
            )
        send_team_invitation(request, invitation)
        messages.success(request, f'Invitation resent to {invitation.email}.')
        return redirect('projects:company_team', slug=organization.slug)


class TeamInvitationRevokeView(LoginRequiredMixin, View):
    http_method_names = ('post',)

    def post(self, request, slug, invitation_pk):
        organization = managed_organization_or_404(request.user, slug)
        invitation = get_object_or_404(
            OrganizationInvitation,
            pk=invitation_pk,
            organization=organization,
        )
        if invitation.accepted_at:
            messages.error(request, 'Accepted invitations cannot be revoked.')
        elif invitation.revoked_at:
            messages.info(request, 'That invitation is already revoked.')
        else:
            invitation.revoked_at = timezone.now()
            invitation.save(update_fields=('revoked_at',))
            record_activity(
                organization=organization,
                actor=request.user,
                event_type=ActivityEvent.Type.TEAM_INVITE_REVOKED,
                summary=f'{request.user.email} revoked the invitation to {invitation.email}.',
                metadata={'email': invitation.email},
            )
            messages.success(request, f'Invitation to {invitation.email} was revoked.')
        return redirect('projects:company_team', slug=organization.slug)


class TeamMembershipUpdateView(LoginRequiredMixin, View):
    http_method_names = ('post',)

    def post(self, request, slug, membership_pk):
        organization = managed_organization_or_404(request.user, slug)
        membership = get_object_or_404(
            OrganizationMembership.objects.select_related('user'),
            pk=membership_pk,
            organization=organization,
            role__in=OrganizationMembership.INTERNAL_ROLES,
        )
        if membership.user_id == request.user.id:
            messages.error(request, 'You cannot change your own role or access.')
            return redirect('projects:company_team', slug=organization.slug)

        old_role = membership.role
        old_active = membership.is_active
        form = TeamMembershipForm(request.POST, instance=membership)
        if not form.is_valid():
            messages.error(request, 'Choose a valid team role and access status.')
            return redirect('projects:company_team', slug=organization.slug)

        new_role = form.cleaned_data['role']
        new_active = form.cleaned_data['is_active']
        removing_admin = old_role == OrganizationMembership.Role.ADMIN and (
            new_role != OrganizationMembership.Role.ADMIN or not new_active
        )
        if removing_admin:
            other_admins = organization.memberships.filter(
                role=OrganizationMembership.Role.ADMIN,
                is_active=True,
            ).exclude(pk=membership.pk)
            if not other_admins.exists():
                messages.error(request, 'A company must retain at least one active admin.')
                return redirect('projects:company_team', slug=organization.slug)

        membership = form.save()
        if old_role != membership.role:
            record_activity(
                organization=organization,
                actor=request.user,
                event_type=ActivityEvent.Type.TEAM_ROLE_CHANGED,
                summary=(
                    f'{request.user.email} changed {membership.user.email} from '
                    f'{OrganizationMembership.Role(old_role).label} to '
                    f'{membership.get_role_display()}.'
                ),
                metadata={
                    'email': membership.user.email,
                    'old_role': old_role,
                    'new_role': membership.role,
                },
            )
        if old_active != membership.is_active:
            status = 'restored' if membership.is_active else 'deactivated'
            record_activity(
                organization=organization,
                actor=request.user,
                event_type=ActivityEvent.Type.TEAM_ACCESS_CHANGED,
                summary=f'{request.user.email} {status} access for {membership.user.email}.',
                metadata={'email': membership.user.email, 'active': membership.is_active},
            )
        messages.success(request, f'{membership.user.email} was updated.')
        return redirect('projects:company_team', slug=organization.slug)


class InvitationAcceptView(View):
    template_name = 'projects/accept_invitation.html'

    def get_invitation(self, token):
        return get_object_or_404(
            ProjectInvitation.objects.select_related('project__organization'),
            token=token,
        )

    def invalid_response(self, request, invitation):
        return render(
            request,
            'projects/invitation_invalid.html',
            {'invitation': invitation},
            status=410,
        )

    def accept_authenticated_user(self, request, invitation):
        if request.user.email.casefold() != invitation.email.casefold():
            return render(
                request,
                'projects/invitation_email_mismatch.html',
                {'invitation': invitation},
                status=403,
            )
        try:
            project = accept_project_invitation(invitation, request.user)
        except ValidationError as error:
            return render(
                request,
                'projects/invitation_invalid.html',
                {'invitation': invitation, 'error': error},
                status=409,
            )
        messages.success(request, f'You now have access to {project.name}.')
        return redirect('projects:detail', pk=project.pk)

    def get(self, request, token):
        invitation = self.get_invitation(token)
        if not invitation.is_valid:
            return self.invalid_response(request, invitation)
        if request.user.is_authenticated:
            if request.user.email.casefold() != invitation.email.casefold():
                return render(
                    request,
                    'projects/invitation_email_mismatch.html',
                    {'invitation': invitation},
                    status=403,
                )
            return render(
                request,
                self.template_name,
                {
                    'invitation': invitation,
                    'existing_user': True,
                    'authenticated_match': True,
                },
            )

        existing_user = get_user_model().objects.filter(
            email__iexact=invitation.email
        ).exists()
        return render(
            request,
            self.template_name,
            {
                'invitation': invitation,
                'existing_user': existing_user,
                'form': None if existing_user else InvitationSignupForm(),
            },
        )

    def post(self, request, token):
        invitation = self.get_invitation(token)
        if not invitation.is_valid:
            return self.invalid_response(request, invitation)
        if request.user.is_authenticated:
            return self.accept_authenticated_user(request, invitation)
        if get_user_model().objects.filter(email__iexact=invitation.email).exists():
            return redirect(
                f"{reverse('login')}?next="
                f"{reverse('projects:accept_invitation', args=(invitation.token,))}"
            )

        form = InvitationSignupForm(request.POST)
        if not form.is_valid():
            return render(
                request,
                self.template_name,
                {
                    'invitation': invitation,
                    'existing_user': False,
                    'form': form,
                },
            )

        user = form.save(commit=False)
        user.email = invitation.email
        user.full_clean()
        user.save()
        project = accept_project_invitation(invitation, user)
        login(request, user, backend='django.contrib.auth.backends.ModelBackend')
        messages.success(request, f'Welcome. You now have access to {project.name}.')
        return redirect('projects:detail', pk=project.pk)


class TeamInvitationAcceptView(View):
    template_name = 'projects/accept_team_invitation.html'

    def get_invitation(self, token):
        return get_object_or_404(
            OrganizationInvitation.objects.select_related('organization'),
            token=token,
        )

    def invalid_response(self, request, invitation, *, error=None, status=410):
        return render(
            request,
            'projects/team_invitation_invalid.html',
            {'invitation': invitation, 'error': error},
            status=status,
        )

    def accept_authenticated_user(self, request, invitation):
        if request.user.email.casefold() != invitation.email.casefold():
            return render(
                request,
                'projects/team_invitation_email_mismatch.html',
                {'invitation': invitation},
                status=403,
            )
        try:
            organization = accept_organization_invitation(invitation, request.user)
        except ValidationError as error:
            return self.invalid_response(
                request, invitation, error=error, status=409
            )
        messages.success(request, f'You joined {organization.name}.')
        return redirect('projects:company_team', slug=organization.slug)

    def get(self, request, token):
        invitation = self.get_invitation(token)
        if not invitation.is_valid:
            return self.invalid_response(request, invitation)
        if request.user.is_authenticated:
            if request.user.email.casefold() != invitation.email.casefold():
                return render(
                    request,
                    'projects/team_invitation_email_mismatch.html',
                    {'invitation': invitation},
                    status=403,
                )
            return render(
                request,
                self.template_name,
                {'invitation': invitation, 'authenticated_match': True},
            )
        existing_user = get_user_model().objects.filter(
            email__iexact=invitation.email
        ).exists()
        return render(
            request,
            self.template_name,
            {
                'invitation': invitation,
                'existing_user': existing_user,
                'form': None if existing_user else InvitationSignupForm(),
            },
        )

    def post(self, request, token):
        invitation = self.get_invitation(token)
        if not invitation.is_valid:
            return self.invalid_response(request, invitation)
        if request.user.is_authenticated:
            return self.accept_authenticated_user(request, invitation)
        if get_user_model().objects.filter(email__iexact=invitation.email).exists():
            return redirect(
                f"{reverse('login')}?next="
                f"{reverse('projects:accept_team_invitation', args=(invitation.token,))}"
            )
        form = InvitationSignupForm(request.POST)
        if not form.is_valid():
            return render(
                request,
                self.template_name,
                {
                    'invitation': invitation,
                    'existing_user': False,
                    'form': form,
                },
            )
        user = form.save(commit=False)
        user.email = invitation.email
        user.full_clean()
        user.save()
        organization = accept_organization_invitation(invitation, user)
        login(request, user, backend='django.contrib.auth.backends.ModelBackend')
        messages.success(request, f'Welcome to {organization.name}.')
        return redirect('projects:company_team', slug=organization.slug)
