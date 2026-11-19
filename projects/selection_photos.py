"""Private, repeatable photo imports; never change a financial decision."""
import hashlib
import io
import json
import re
import uuid
import warnings
import zipfile

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.db import transaction
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods
from PIL import Image, UnidentifiedImageError

from .access import can_manage_project
from .models import ActivityEvent, FinishSelection, Project, SelectionReferencePhoto
from .services import record_activity
from .views import (
    managed_project_or_404,
    selections_project_or_404,
    visible_selection_or_404,
)

MAX_ARCHIVE = 50 * 1024 * 1024
MAX_IMAGE = 15 * 1024 * 1024


class PhotoImportForm(forms.Form):
    archive = forms.FileField(label='Photo transfer package (.zip)')

    def clean_archive(self):
        value = self.cleaned_data['archive']
        if value.size > MAX_ARCHIVE:
            raise ValidationError('Keep each photo package under 50 MB.')
        return value


def read_package(upload, project):
    """Validate the whole bounded package before storing any objects. Never extract paths."""
    try:
        with zipfile.ZipFile(upload) as archive:
            infos = archive.infolist()
            names = [f.filename for f in infos]
            if len(infos) > 201 or len(names) != len(set(names)) or sum(f.file_size for f in infos) > MAX_ARCHIVE:
                raise ValueError
            if archive.getinfo('manifest.json').file_size > 256 * 1024:
                raise ValueError
            manifest = json.loads(archive.read('manifest.json'))
            if manifest['version'] != 1 or manifest['project_id'] != project.pk:
                raise ValueError
            entries = manifest['photos']
            if not isinstance(entries, list) or not 1 <= len(entries) <= 200:
                raise ValueError
            selections = {s.pk: s for s in project.finish_selections.filter(status=FinishSelection.Status.IMPORTED)}
            prepared, keys = [], set()
            for entry in entries:
                selection = selections.get(entry['selection_id'])
                if selection is None or selection.title != entry['selection_title']:
                    raise ValueError
                name, key, caption = entry['file'], entry['source_key'], entry['caption']
                if not re.fullmatch(r'[A-Za-z0-9_-]+\.(jpg|jpeg|png|webp)', name, re.I):
                    raise ValueError
                if not isinstance(key, str) or not re.fullmatch(r'buildertrend:[0-9]+:[0-9]+:[0-9]+', key):
                    raise ValueError
                if not isinstance(caption, str) or not 1 <= len(caption) <= 250:
                    raise ValueError
                if (selection.pk, key) in keys or archive.getinfo(name).file_size > MAX_IMAGE:
                    raise ValueError
                keys.add((selection.pk, key))
                data = archive.read(name)
                digest = hashlib.sha256(data).hexdigest()
                if digest != entry['sha256']:
                    raise ValueError
                with warnings.catch_warnings():
                    warnings.simplefilter('error', Image.DecompressionBombWarning)
                    with Image.open(io.BytesIO(data)) as image:
                        if image.format not in ('JPEG', 'PNG', 'WEBP') or image.width * image.height > 40000000:
                            raise ValueError
                        extension = {'JPEG': 'jpg', 'PNG': 'png', 'WEBP': 'webp'}[image.format]
                        image.verify()
                prepared.append((selection, key, caption, digest, extension, data))
            return prepared
    except (KeyError, TypeError, ValueError, OSError, zipfile.BadZipFile, RuntimeError,
            UnidentifiedImageError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise ValidationError('Invalid photo package, image, or selection match. No photos were imported.') from None


def import_photos(project, actor, prepared):
    saved, added, skipped = [], 0, 0
    try:
        with transaction.atomic():
            Project.objects.select_for_update().get(pk=project.pk)
            for selection, key, caption, digest, extension, data in prepared:
                # Recheck after obtaining the project lock, including old previews.
                current = project.finish_selections.get(pk=selection.pk)
                if current.status != FinishSelection.Status.IMPORTED or current.title != selection.title:
                    raise ValidationError('A selection changed. Prepare a fresh photo package.')
                existing = current.reference_photos.filter(source_key=key).first()
                if existing and existing.sha256 != digest:
                    raise ValidationError('A source photo changed. Review it separately; existing photos were not overwritten.')
                if existing or current.reference_photos.filter(sha256=digest).exists():
                    skipped += 1
                    continue
                photo = SelectionReferencePhoto(selection=current, caption=caption, source_key=key,
                                                sha256=digest, uploaded_by=actor)
                photo.image.save(f'{uuid.uuid4().hex}.{extension}', ContentFile(data), save=False)
                saved.append((photo.image.storage, photo.image.name))
                photo.save()
                added += 1
            record_activity(organization=project.organization, project=project, actor=actor,
                            event_type=ActivityEvent.Type.SELECTION_UPDATED,
                            summary=f'Imported {added} selection reference photos; skipped {skipped} duplicates.',
                            metadata={'photos_added': added, 'photos_skipped': skipped, 'client_visible': False})
    except Exception:
        for storage, name in saved:
            storage.delete(name)
        raise
    return added, skipped


@login_required
@require_http_methods(['GET', 'POST'])
def photo_import(request, pk):
    project = managed_project_or_404(request.user, pk)
    form = PhotoImportForm(request.POST or None, request.FILES or None)
    if request.method == 'POST' and form.is_valid():
        try:
            prepared = read_package(form.cleaned_data['archive'], project)
            added, skipped = import_photos(project, request.user, prepared)
        except ValidationError as error:
            form.add_error(None, error)
        else:
            messages.success(request, f'{added} photos imported as internal references. {skipped} duplicates skipped.')
            return redirect('projects:selection_photo_import', pk=project.pk)
    return render(request, 'projects/selection_photo_import.html', {'project': project, 'form': form,
                  'photos': SelectionReferencePhoto.objects.filter(selection__project=project).select_related('selection')})


@login_required
@require_http_methods(['GET'])
def photo_image(request, pk, selection_pk, photo_pk):
    project = selections_project_or_404(request.user, pk)
    selection = visible_selection_or_404(request.user, project, selection_pk)
    photo = get_object_or_404(SelectionReferencePhoto, selection=selection, pk=photo_pk)
    if not photo.client_visible and not can_manage_project(request.user, project):
        raise Http404
    response = FileResponse(photo.image.open('rb'), content_type={'jpg': 'image/jpeg', 'png': 'image/png',
                            'webp': 'image/webp'}[photo.image.name.rsplit('.', 1)[-1]])
    response['Cache-Control'] = 'private, no-store'
    response['X-Content-Type-Options'] = 'nosniff'
    return response


@login_required
@require_http_methods(['POST'])
def photo_visibility(request, pk, photo_pk):
    project = managed_project_or_404(request.user, pk)
    with transaction.atomic():
        photo = get_object_or_404(SelectionReferencePhoto.objects.select_for_update(),
                                 selection__project=project, pk=photo_pk)
        photo.client_visible = request.POST.get('client_visible') == 'on'
        photo.save(update_fields=['client_visible'])
        record_activity(organization=project.organization, project=project, actor=request.user,
                        event_type=ActivityEvent.Type.SELECTION_UPDATED,
                        summary='Updated selection reference photo visibility.',
                        metadata={'photo_id': photo.pk, 'client_visible': photo.client_visible})
    return redirect('projects:selection_photo_import', pk=project.pk)
