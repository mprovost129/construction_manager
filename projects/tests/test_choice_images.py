from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone

from projects.choice_images import gallery_revision, MAX_GALLERY_IMAGES
from projects.models import ChoiceImage, SelectionOption, ProjectMembership
from projects.selection_templates import copy_template, load_template
from . import test_selection_templates as fixtures


def photo(name='view.png', color='green'):
    data = BytesIO()
    Image.new('RGB', (30, 20), color).save(data, format='PNG')
    return SimpleUploadedFile(name, data.getvalue(), content_type='image/png')


class ChoiceImageTests(TestCase):
    setUp = fixtures.SelectionTemplateTests.setUp
    url = fixtures.SelectionTemplateTests.url
    make_template = fixtures.SelectionTemplateTests.make_template

    def payload(self, option, **changes):
        result = dict(name=option.name, price=option.price, cost=getattr(option, 'cost', 0),
            sort_order=option.sort_order, image_caption=option.image_caption,
            gallery_revision=gallery_revision(option.gallery_images.all()))
        for item in option.gallery_images.all():
            result[f'photo_caption_{item.pk}'] = item.caption
            result[f'photo_order_{item.pk}'] = item.sort_order
        result.update(changes)
        return result

    def edit_url(self):
        return reverse('projects:selection_option_edit', args=[self.project.pk, self.selection.pk, self.option.pk])

    def test_project_upload_caption_cover_and_remove(self):
        original_cover = self.option.image.name
        response = self.client.post(self.edit_url(), self.payload(self.option,
            additional_images=[photo('side.png'), photo('accessory.png', 'blue')], image_caption='Front view'))
        self.assertEqual(response.status_code, 302)
        self.option.refresh_from_db()
        side, accessory = list(self.option.gallery_images.all())
        side_path = side.image.name
        payload = self.payload(self.option, cover_photo=str(side.pk))
        payload[f'photo_caption_{side.pk}'] = 'Side view'
        payload[f'photo_caption_{accessory.pk}'] = 'Matching handle'
        payload[f'photo_order_{accessory.pk}'] = 0
        self.assertEqual(self.client.post(self.edit_url(), payload).status_code, 302)
        self.option.refresh_from_db(); side.refresh_from_db(); accessory.refresh_from_db()
        self.assertEqual((self.option.image.name, self.option.image_caption), (side_path, 'Side view'))
        self.assertEqual((side.image.name, side.caption), (original_cover, 'Front view'))
        self.assertEqual(self.option.gallery_images.first(), accessory)
        payload = self.payload(self.option)
        payload[f'photo_remove_{accessory.pk}'] = 'on'
        self.assertEqual(self.client.post(self.edit_url(), payload).status_code, 302)
        self.assertFalse(ChoiceImage.objects.filter(pk=accessory.pk).exists())
        self.assertContains(self.client.get(reverse('projects:selection_detail', args=[self.project.pk, self.selection.pk])), 'Front view')

    def test_template_upload_and_copy_load_preserve_independent_gallery(self):
        template = self.make_template(); row = template.rows.get(); option = row.options.get()
        url = self.url('option_edit', 'builder', template.pk, row.pk, option.pk)
        self.assertEqual(self.client.post(url, self.payload(option, additional_images=[photo(), photo('detail.png')])).status_code, 302)
        image = option.gallery_images.first(); image.caption = 'Accessory'; image.sort_order = 7; image.save()
        clone = copy_template(template, self.admin, 'Generic gallery')
        self.assertEqual(load_template(clone, self.target, self.admin, clone.rows.all()), (1, 0))
        copied = clone.rows.get().options.get().gallery_images.get(caption='Accessory')
        loaded = self.target.finish_selections.get().options.get().gallery_images.get(caption='Accessory')
        self.assertEqual(len({image.image.name, copied.image.name, loaded.image.name}), 3)
        self.assertEqual((copied.sort_order, loaded.sort_order), (7, 7))
        image.image.delete(save=False)
        with loaded.image.open('rb') as file:
            self.assertTrue(file.read().startswith(b'\x89PNG'))

    def test_save_project_as_template_keeps_gallery(self):
        image = ChoiceImage.objects.create(option=self.option, image=photo(), caption='Back view', sort_order=3)
        template = self.make_template()
        copied = template.rows.get().options.get().gallery_images.get()
        self.assertEqual(copied.caption, 'Back view')
        self.assertNotEqual(copied.image.name, image.image.name)

    def test_new_choice_with_only_additional_images_gets_cover(self):
        url = reverse('projects:selection_option_create', args=[self.project.pk, self.selection.pk])
        result = self.client.post(url, dict(name='Product gallery', price=100, cost=50, sort_order=0,
            additional_images=[photo(), photo('back.png')]))
        self.assertEqual(result.status_code, 302)
        option = self.selection.options.get(name='Product gallery')
        self.assertTrue(option.image)
        self.assertEqual(option.gallery_images.count(), 1)

    def test_all_uploads_validated_before_saving(self):
        before = {p for p in Path(self.media_dir.name).rglob('*') if p.is_file()}
        for invalid in (SimpleUploadedFile('fake.png', b'not an image', content_type='image/png'),
                        SimpleUploadedFile('script.svg', b'<svg/>', content_type='image/svg+xml')):
            response = self.client.post(self.edit_url(), self.payload(self.option, additional_images=[photo(), invalid]))
            self.assertEqual(response.status_code, 200)
            self.assertFalse(self.option.gallery_images.exists())
        self.assertEqual(before, {p for p in Path(self.media_dir.name).rglob('*') if p.is_file()})
        response = self.client.post(self.edit_url(), self.payload(self.option, additional_images=[photo() for _ in range(MAX_GALLERY_IMAGES+1)]))
        self.assertContains(response, 'up to 20')

    def test_private_media_and_draft_rules(self):
        ProjectMembership.objects.create(project=self.project, user=self.customer, role='client')
        image = ChoiceImage.objects.create(option=self.option, image=photo(), caption='Product side')
        url = image.get_absolute_url()
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Cache-Control'], 'private, no-store'); response.close()
        template = self.make_template(); template_image = template.rows.get().options.get().gallery_images.get()
        for user in (self.customer, self.outsider):
            self.client.force_login(user)
            self.assertEqual(self.client.get(url).status_code, 404)
            self.assertEqual(self.client.get(template_image.get_absolute_url()).status_code, 404)
            self.assertIn(self.client.post(self.edit_url(), self.payload(self.option)).status_code, (403, 404))
        self.selection.status = 'open'; self.selection.opened_at = timezone.now(); self.selection.opened_by = self.admin; self.selection.save()
        self.client.force_login(self.customer)
        response = self.client.get(url); self.assertEqual(response.status_code, 200); response.close()
        detail = self.client.get(reverse('projects:selection_detail', args=[self.project.pk, self.selection.pk]))
        self.assertContains(detail, 'Product side'); self.assertNotContains(detail, 'Estimated cost')
        self.client.force_login(self.admin)
        self.assertEqual(self.client.post(self.edit_url(), self.payload(self.option)).status_code, 403)
        self.client.logout(); self.assertEqual(self.client.get(url).status_code, 302)

    def test_foreign_cover_photo_and_mismatched_url_are_rejected(self):
        other_option = SelectionOption.objects.create(selection=self.selection, name='Other', price=1, cost=0)
        own = ChoiceImage.objects.create(option=self.option, image=photo(), caption='Own')
        foreign = ChoiceImage.objects.create(option=other_option, image=photo(), caption='Other')
        response = self.client.post(self.edit_url(), self.payload(self.option, cover_photo=str(foreign.pk)))
        self.assertContains(response, 'Select a valid choice')
        url = reverse('projects:selection_choice_image', args=[self.project.pk, self.selection.pk, self.option.pk, foreign.pk])
        self.assertEqual(self.client.get(url).status_code, 404)
        secure = Client(enforce_csrf_checks=True); secure.force_login(self.admin)
        self.assertEqual(secure.post(self.edit_url(), self.payload(self.option)).status_code, 403)
        self.assertTrue(ChoiceImage.objects.filter(pk=own.pk).exists())

    def test_stale_gallery_edit_cannot_overwrite_new_captions(self):
        image = ChoiceImage.objects.create(option=self.option, image=photo(), caption='First')
        payload = self.payload(self.option)
        image.caption = 'Updated by another PM'; image.save()
        self.assertContains(self.client.post(self.edit_url(), payload), 'changed in another window')
        image.refresh_from_db(); self.assertEqual(image.caption, 'Updated by another PM')

    def test_failed_upload_rolls_back_files_and_choice(self):
        before = {p for p in Path(self.media_dir.name).rglob('*') if p.is_file()}
        with patch('projects.choice_images.ChoiceImage.save', side_effect=RuntimeError('storage test')):
            with self.assertRaises(RuntimeError):
                self.client.post(self.edit_url(), self.payload(self.option, name='Should roll back', image=photo('cover.png'), additional_images=[photo()]))
        self.option.refresh_from_db(); self.assertEqual(self.option.name, 'Oak')
        self.assertFalse(self.option.gallery_images.exists())
        self.assertEqual(before, {p for p in Path(self.media_dir.name).rglob('*') if p.is_file()})

    def test_failed_copy_rolls_back_new_gallery_files(self):
        ChoiceImage.objects.create(option=self.option, image=photo(), caption='Side')
        template = self.make_template()
        before = {p for p in Path(self.media_dir.name).rglob('*') if p.is_file()}
        with patch('projects.selection_templates.ChoiceImage.save', side_effect=RuntimeError('copy failure')):
            with self.assertRaises(RuntimeError):
                load_template(template, self.target, self.admin, template.rows.all())
        self.assertFalse(self.target.finish_selections.exists())
        self.assertEqual(before, {p for p in Path(self.media_dir.name).rglob('*') if p.is_file()})
