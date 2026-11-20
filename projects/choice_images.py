"""Private supplementary images shared by project and template choice editors."""
from contextlib import contextmanager
from hashlib import sha256
import json

from django import forms
from django.core.exceptions import PermissionDenied
from django.db.models import Max
from django.contrib.auth.decorators import login_required
from django.http import FileResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.views.decorators.http import require_GET

from .models import ChoiceImage, SelectionTemplateOption

MAX_GALLERY_IMAGES = 20


def gallery_revision(photos):
    return sha256(json.dumps([(p.pk, p.image.name, p.caption, p.sort_order) for p in photos]).encode()).hexdigest()


class MultipleImageInput(forms.FileInput):
    allow_multiple_selected = True


class MultipleImageField(forms.ImageField):
    widget = MultipleImageInput(attrs={'accept': 'image/png,image/jpeg,image/webp'})

    def clean(self, data, initial=None):
        files = data if isinstance(data, (list, tuple)) else [data] if data else []
        if len(files) > MAX_GALLERY_IMAGES:
            raise forms.ValidationError('Upload up to 20 additional images at a time.')
        if sum(f.size for f in files) > 50 * 1024 * 1024:
            raise forms.ValidationError('Keep each upload batch under 50 MB.')
        result = []
        for upload in files:
            if upload.size > 10 * 1024 * 1024:
                raise forms.ValidationError('Each image must be 10 MB or smaller.')
            image = super().clean(upload)
            if image.image.format not in ('PNG', 'JPEG', 'WEBP'):
                raise forms.ValidationError('Use PNG, JPEG, or WebP images.')
            result.append(image)
        return result


class ChoiceGalleryMixin:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.photos = list(self.instance.gallery_images.all()) if self.instance.pk else []
        self.saved_gallery_files = []
        self.pending_choice_files = []
        self.original_gallery_revision = gallery_revision(self.photos)
        self.fields['gallery_revision'] = forms.CharField(required=False, widget=forms.HiddenInput, initial=self.original_gallery_revision)
        self.fields['image'].label = 'Cover image'
        self.fields['image_caption'].label = 'Cover caption'
        self.fields['additional_images'] = MultipleImageField(required=False, label='Additional images',
            help_text='Select multiple views or accessories. Up to 20 additional images per choice; 10 MB each, 50 MB per batch. Save, then add captions or change the cover below.')
        self.fields['remove_cover'] = forms.BooleanField(required=False, label='Remove current cover image')
        if self.photos:
            self.fields['cover_photo'] = forms.ChoiceField(required=False, label='Cover photo',
                choices=[('', 'Keep the cover above')] + [(str(p.pk), p.caption or f'Photo {i+1}') for i,p in enumerate(self.photos)])
        for photo in self.photos:
            self.fields[f'photo_caption_{photo.pk}'] = forms.CharField(required=False, max_length=250, initial=photo.caption, label='Caption')
            self.fields[f'photo_order_{photo.pk}'] = forms.IntegerField(required=False, min_value=0, max_value=10000, initial=photo.sort_order, label='Display order')
            self.fields[f'photo_remove_{photo.pk}'] = forms.BooleanField(required=False, label='Remove this image')

    @property
    def cover_url(self):
        option = self.instance
        if not option.pk or not option.image:
            return ''
        if isinstance(option, SelectionTemplateOption):
            row = option.row
            return reverse('projects:selection_template_media', args=[row.template.organization.slug, row.template_id, row.pk, option.pk, 'image'])
        return reverse('projects:selection_option_image', args=[option.selection.project_id, option.selection_id, option.pk])

    @property
    def main_fields(self):
        return [field for field in self if not field.name.startswith('photo_')]

    @property
    def gallery_fields(self):
        return [{'photo': p, 'caption': self[f'photo_caption_{p.pk}'], 'order': self[f'photo_order_{p.pk}'],
                 'remove': self[f'photo_remove_{p.pk}']} for p in self.photos]

    def clean(self):
        data = super().clean()
        if (self.photos or data.get('gallery_revision')) and data.get('gallery_revision') != self.original_gallery_revision:
            raise forms.ValidationError('These photos changed in another window. Reload the choice before saving.')
        kept = sum(not data.get(f'photo_remove_{p.pk}') for p in self.photos)
        if kept + len(data.get('additional_images', [])) > MAX_GALLERY_IMAGES:
            self.add_error('additional_images', 'A choice can have up to 20 additional images. Remove images before adding more.')
        cover = data.get('cover_photo')
        if cover and data.get(f'photo_remove_{cover}'):
            self.add_error('cover_photo', 'The cover image cannot also be removed.')
        if cover and self.files.get('image'):
            self.add_error('cover_photo', 'Choose an existing cover or upload a new cover, not both.')
        if data.get('remove_cover') and self.files.get('image'):
            self.add_error('remove_cover', 'Do not remove the cover while uploading its replacement.')
        return data

    def track_choice_files(self, option):
        self.pending_choice_files.extend(getattr(option, name) for name in ('image', 'attachment')
            if getattr(option, name) and not getattr(option, name)._committed)

    def save_gallery(self, option):
        """Called after saving the choice, while its project/template is locked."""
        data = self.cleaned_data
        photos = option.gallery_images
        if gallery_revision(photos.all()) != self.original_gallery_revision:
            raise PermissionDenied('These photos changed in another window. Reload the choice before saving.')
        for original in self.photos:
            if data.get(f'photo_remove_{original.pk}'):
                photos.filter(pk=original.pk).delete()
            else:
                photos.filter(pk=original.pk).update(caption=data.get(f'photo_caption_{original.pk}', ''),
                    sort_order=data.get(f'photo_order_{original.pk}') or 0)
        uploads = data.get('additional_images', [])
        if photos.count() + len(uploads) > MAX_GALLERY_IMAGES:
            raise PermissionDenied('The photo gallery changed. Reload before adding more images.')
        parent = {'template_option': option} if isinstance(option, SelectionTemplateOption) else {'option': option}
        order = (photos.aggregate(value=Max('sort_order'))['value'] or 0) + 1
        for index, upload in enumerate(uploads):
            photo = ChoiceImage(**parent, sort_order=order + index)
            photo.image.save(upload.name, upload, save=False)
            self.saved_gallery_files.append((photo.image.storage, photo.image.name))
            photo.save()
        if data.get('remove_cover'):
            option.image = ''
            option.image_caption = ''
        cover_pk = data.get('cover_photo')
        cover = photos.filter(pk=cover_pk).first() if cover_pk else photos.first() if not option.image else None
        if cover_pk and not cover:
            raise PermissionDenied('The cover photo changed. Reload this choice before saving.')
        if cover:
            old_image, old_caption = option.image.name, option.image_caption
            option.image = cover.image.name
            option.image_caption = cover.caption
            if old_image:
                cover.image = old_image
                cover.caption = old_caption
                cover.save(update_fields=['image', 'caption'])
            else:
                cover.delete()
        option.save(update_fields=['image', 'image_caption'])


@contextmanager
def saved_choice_files(form):
    """Remove new private blobs if the surrounding database transaction fails."""
    form.track_choice_files(form.instance)
    try:
        yield
    except Exception:
        files = form.saved_gallery_files + [(f.storage, f.name) for f in form.pending_choice_files if f._committed]
        for storage, name in files:
            storage.delete(name)
        raise


def image_response(photo):
    response = FileResponse(photo.image.open('rb'), as_attachment=False)
    response['Cache-Control'] = 'private, no-store'
    response['X-Content-Type-Options'] = 'nosniff'
    return response


@login_required
@require_GET
def project_image(request, pk, selection_pk, option_pk, image_pk):
    from .views import selections_project_or_404, visible_selection_or_404
    project = selections_project_or_404(request.user, pk)
    selection = visible_selection_or_404(request.user, project, selection_pk)
    photo = get_object_or_404(ChoiceImage, option__selection=selection, option_id=option_pk, pk=image_pk)
    return image_response(photo)


@login_required
@require_GET
def template_image(request, slug, template_pk, row_pk, option_pk, image_pk):
    from .selection_template_views import template_for
    template = template_for(request.user, slug, template_pk)
    photo = get_object_or_404(ChoiceImage, template_option__row__template=template,
        template_option__row_id=row_pk, template_option_id=option_pk, pk=image_pk)
    return image_response(photo)
