"""Explicit public field packets, isolated from all normal project access."""

import hashlib
import json
import uuid
from io import BytesIO
from pathlib import Path

import qrcode
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import F
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_http_methods

from .image_previews import private_image_response
from .models import ActivityEvent, FinishSelection, JobSiteShare, Project
from .services import record_activity


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def guarded(response, *, staff_form=False):
    response["Cache-Control"] = "private, no-store, max-age=0"
    response["X-Robots-Tag"] = "noindex, nofollow, noarchive"
    # Staff POST forms need a same-origin Origin/Referer for Django CSRF.
    # The anonymous packet has GET links only, and must not leak its token.
    response["Referrer-Policy"] = "same-origin" if staff_form else "no-referrer"
    response["X-Content-Type-Options"] = "nosniff"
    return response


def materials(project, *, document_ids=None, selection_ids=None):
    """Allowlisted data only; never use generic detail views on public routes."""
    documents = []
    document_query = project.documents.filter(
        client_visible=True, category__in=("plan", "general", "selection", "other")
    )
    if document_ids is not None:
        document_query = document_query.filter(versions__pk__in=document_ids).distinct()
    for document in document_query.prefetch_related("versions__decisions"):
        versions = list(document.versions.all())
        if not versions:
            continue
        version = versions[0]
        decisions = list(version.decisions.all())
        if any(d.decision == "declined" for d in decisions):
            continue
        if (
            document.requires_client_approval
            and sum(d.decision == "approved" for d in decisions)
            < document.required_approvals
        ):
            continue
        # No active content, spreadsheets, financial record types or original
        # filenames in the public index. Staff must review file contents too.
        if Path(version.original_filename).suffix.lower() not in (
            ".pdf",
            ".png",
            ".jpg",
            ".jpeg",
            ".webp",
        ):
            continue
        documents.append(
            dict(
                id=str(version.pk),
                title=document.title,
                number=version.version_number,
                version=version,
                fingerprint=digest([document.title, version.pk, version.file.name]),
            )
        )
    selections = []
    selection_query = project.finish_selections.filter(
        status=FinishSelection.Status.SELECTED,
        chosen_option__selection_id=F("pk"),
    )
    if selection_ids is not None:
        selection_query = selection_query.filter(pk__in=selection_ids)
    for selection in selection_query.select_related(
        "chosen_option", "package"
    ).prefetch_related("chosen_option__gallery_images"):
        option = selection.chosen_option
        photos = list(option.gallery_images.all())
        data = dict(
            id=str(selection.pk),
            title=selection.title,
            location=selection.location,
            category=selection.package.title if selection.package else "",
            choice=option.name,
            specification=option.specification,
            option=option,
            photos=photos,
        )
        data["fingerprint"] = digest(
            [
                selection.title,
                selection.location,
                data["category"],
                option.pk,
                option.name,
                option.specification,
                option.image.name,
                str(selection.selected_at),
                [(p.pk, p.image.name) for p in photos],
            ]
        )
        selections.append(data)
    return documents, selections


def revision(share, documents, selections):
    return digest(
        [
            share.revision,
            [(r["id"], r["fingerprint"]) for r in documents],
            [(r["id"], r["fingerprint"]) for r in selections],
        ]
    )


class ShareForm(forms.Form):
    revision = forms.CharField(widget=forms.HiddenInput)
    enabled = forms.BooleanField(
        required=False, label="Enable job-site access without a login"
    )
    documents = forms.MultipleChoiceField(
        required=False, widget=forms.CheckboxSelectMultiple
    )
    selections = forms.MultipleChoiceField(
        required=False, widget=forms.CheckboxSelectMultiple
    )
    reviewed = forms.BooleanField(
        required=False,
        label=(
            "I reviewed the selected files, specifications and photos for job-site sharing. "
            "They contain no private client, company or financial information."
        ),
    )

    def __init__(self, *args, documents, selections, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["documents"].choices = [
            (r["id"], f"{r['title']} — version {r['number']}") for r in documents
        ]
        self.fields["selections"].choices = [
            (r["id"], f"{r['category']} / {r['title']} — {r['choice']}")
            for r in selections
        ]

    def clean(self):
        data = super().clean()
        if data.get("enabled"):
            if not data.get("reviewed"):
                self.add_error(
                    "reviewed", "Review the materials and confirm before publishing."
                )
            if not data.get("documents") and not data.get("selections"):
                raise forms.ValidationError(
                    "Choose at least one document or approved selection."
                )
        return data


@login_required
@require_http_methods(["GET", "POST"])
def manage(request, pk):
    from .views import managed_project_or_404

    project = managed_project_or_404(request.user, pk)
    with transaction.atomic():
        Project.objects.select_for_update().get(pk=project.pk)
        share, _ = JobSiteShare.objects.get_or_create(project=project)
        documents, selections = materials(project)
        version = revision(share, documents, selections)
        initial = dict(
            revision=version,
            enabled=share.enabled,
            documents=list(share.manifest.get("documents", {})),
            selections=list(share.manifest.get("selections", {})),
        )
        form = ShareForm(
            request.POST if request.method == "POST" else None,
            initial=initial,
            documents=documents,
            selections=selections,
        )
        if request.method == "POST":
            action = request.POST.get("action")
            if request.POST.get("revision") != version:
                form.is_valid()
                form.add_error(
                    None,
                    "Materials or sharing settings changed. Refresh and review them before saving.",
                )
            elif action in ("disable", "rotate"):
                share.token = uuid.uuid4()
                if action == "disable":
                    share.enabled = False
                save_share(share, request.user, action)
                messages.success(
                    request,
                    "Old QR codes no longer work. Print a new sign before using the new link."
                    if action == "rotate"
                    else "Job-site access disabled. Old QR codes no longer work.",
                )
                return redirect("projects:job_site_manage", pk=pk)
            elif action == "save" and form.is_valid():
                if share.enabled and not form.cleaned_data["enabled"]:
                    share.token = uuid.uuid4()
                share.enabled = form.cleaned_data["enabled"]
                share.manifest = {
                    "documents": {
                        r["id"]: r["fingerprint"]
                        for r in documents
                        if r["id"] in form.cleaned_data["documents"]
                    },
                    "selections": {
                        r["id"]: r["fingerprint"]
                        for r in selections
                        if r["id"] in form.cleaned_data["selections"]
                    },
                }
                save_share(share, request.user, "save")
                messages.success(
                    request,
                    "Job-site materials saved. Only the checked materials are shared."
                    if share.enabled
                    else "Job-site materials saved. Access is off.",
                )
                return redirect("projects:job_site_manage", pk=pk)
    public_url = request.build_absolute_uri(
        reverse("projects:job_site_public", args=[share.token])
    )
    withheld = sum(
        saved != {r["id"]: r["fingerprint"] for r in current}.get(key)
        for kind, current in [("documents", documents), ("selections", selections)]
        for key, saved in share.manifest.get(kind, {}).items()
    )
    return guarded(
        render(
            request,
            "projects/job_site_manage.html",
            dict(
                project=project,
                share=share,
                form=form,
                public_url=public_url,
                documents=documents,
                selections=selections,
                withheld=withheld,
            ),
        ),
        staff_form=True,
    )


def save_share(share, actor, action):
    share.revision += 1
    share.updated_by = actor
    share.save()
    record_activity(
        organization=share.project.organization,
        project=share.project,
        actor=actor,
        event_type=ActivityEvent.Type.PROJECT_UPDATED,
        summary=f"Job-site sharing settings updated ({action}).",
        metadata=dict(
            action=action,
            enabled=share.enabled,
            documents=list(share.manifest.get("documents", {})),
            selections=list(share.manifest.get("selections", {})),
        ),
    )


def published(token, *, version_pk=None, selection_pk=None):
    share = get_object_or_404(
        JobSiteShare.objects.select_related("project"),
        token=token,
        enabled=True,
        project__deleted_at__isnull=True,
    )
    document_ids = share.manifest.get("documents", {})
    selection_ids = share.manifest.get("selections", {})
    if version_pk is not None:
        if str(version_pk) not in document_ids:
            raise Http404
        document_ids, selection_ids = [version_pk], []
    if selection_pk is not None:
        if str(selection_pk) not in selection_ids:
            raise Http404
        document_ids, selection_ids = [], [selection_pk]
    documents, selections = materials(
        share.project, document_ids=document_ids, selection_ids=selection_ids
    )
    return (
        share,
        [
            r
            for r in documents
            if share.manifest.get("documents", {}).get(r["id"]) == r["fingerprint"]
        ],
        [
            r
            for r in selections
            if share.manifest.get("selections", {}).get(r["id"]) == r["fingerprint"]
        ],
    )


@require_GET
def public(request, token):
    share, documents, selections = published(token)
    return guarded(
        render(
            request,
            "projects/job_site_public.html",
            dict(
                project_name=share.project.name,
                token=share.token,
                documents=documents,
                selections=selections,
                updated_at=share.updated_at,
            ),
        )
    )


@require_GET
def document(request, token, version_pk):
    _, documents, _ = published(token, version_pk=version_pk)
    item = next((r for r in documents if r["id"] == str(version_pk)), None)
    if not item:
        raise Http404
    version = item["version"]
    suffix = Path(version.original_filename).suffix.lower()
    try:
        return guarded(
            FileResponse(
                version.file.open("rb"),
                as_attachment=True,
                filename=f"job-site-document-v{version.version_number}{suffix}",
            )
        )
    except FileNotFoundError:
        raise Http404 from None


@require_GET
def photo(request, token, selection_pk, photo_pk=0):
    _, _, selections = published(token, selection_pk=selection_pk)
    item = next((r for r in selections if r["id"] == str(selection_pk)), None)
    if not item:
        raise Http404
    file = (
        item["option"].image
        if not photo_pk
        else next((p.image for p in item["photos"] if p.pk == photo_pk), None)
    )
    if not file:
        raise Http404
    try:
        return guarded(private_image_response(request, file))
    except FileNotFoundError:
        raise Http404 from None


@login_required
@require_GET
def sign(request, pk):
    from .views import managed_project_or_404

    project = managed_project_or_404(request.user, pk)
    share = get_object_or_404(JobSiteShare, project=project, enabled=True)
    return guarded(
        render(
            request, "projects/job_site_sign.html", dict(project=project, share=share)
        )
    )


@login_required
@require_GET
def qr(request, pk):
    from .views import managed_project_or_404

    project = managed_project_or_404(request.user, pk)
    share = get_object_or_404(JobSiteShare, project=project, enabled=True)
    url = request.build_absolute_uri(
        reverse("projects:job_site_public", args=[share.token])
    )
    output = BytesIO()
    qrcode.make(url, box_size=10, border=4).save(output, format="PNG")
    response = HttpResponse(output.getvalue(), content_type="image/png")
    if request.GET.get("download") == "1":
        response["Content-Disposition"] = (
            'attachment; filename="heritage-job-site-qr.png"'
        )
    return guarded(response)
