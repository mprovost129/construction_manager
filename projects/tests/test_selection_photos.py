import hashlib
import io
import json
import tempfile
import zipfile
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.storage import FileSystemStorage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from PIL import Image

from projects.models import (
    FinishSelection,
    Organization,
    OrganizationMembership,
    Project,
    ProjectMembership,
    SelectionReferencePhoto,
)
from projects.selection_photos import import_photos, read_package


class SelectionPhotoTests(TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.storage_patch = patch.object(SelectionReferencePhoto._meta.get_field('image'), 'storage',
                                          FileSystemStorage(location=self.temp.name))
        self.storage_patch.start()
        self.addCleanup(self.storage_patch.stop)
        User = get_user_model()
        self.admin = User.objects.create_user('admin@example.test', 'password')
        self.customer = User.objects.create_user('client@example.test', 'password')
        self.outsider = User.objects.create_user('outside@example.test', 'password')
        self.org = Organization.objects.create(name='Builder', slug='builder')
        other = Organization.objects.create(name='Other', slug='other')
        for user, org, role in [(self.admin,self.org,'admin'), (self.customer,self.org,'client'),
                                (self.outsider,other,'admin')]:
            OrganizationMembership.objects.create(organization=org,user=user,role=role)
        self.project = Project.objects.create(organization=self.org,name='Source')
        self.foreign = Project.objects.create(organization=other,name='Foreign')
        ProjectMembership.objects.create(project=self.project,user=self.customer,role='client')
        self.selection = FinishSelection.objects.create(project=self.project, number=1, title='Cabinet finish',
            status='imported', imported_total='123.45', import_source={'source_key':'test'},
            description='STAFF SOURCE TEXT', created_by=self.admin)
        image = io.BytesIO()
        Image.new('RGB',(4,4),'green').save(image,format='PNG')
        self.image = image.getvalue()
        self.client.force_login(self.admin)

    def package(self, **overrides):
        entry = {'selection_id':self.selection.pk, 'selection_title':self.selection.title,
                 'source_key':'buildertrend:1:2:3', 'caption':'Oak finish', 'file':'image.png',
                 'sha256':hashlib.sha256(self.image).hexdigest(), **overrides}
        output = io.BytesIO()
        with zipfile.ZipFile(output,'w') as archive:
            archive.writestr('manifest.json',json.dumps({'version':1,'project_id':self.project.pk,'photos':[entry]}))
            archive.writestr(entry['file'],self.image)
        output.seek(0)
        return output

    def import_one(self):
        return import_photos(self.project,self.admin,read_package(self.package(),self.project))

    def test_import_repeat_and_history_preserved(self):
        self.assertEqual(self.import_one(),(1,0))
        self.assertEqual(self.import_one(),(0,1))
        self.assertEqual(SelectionReferencePhoto.objects.count(),1)
        self.assertFalse(SelectionReferencePhoto.objects.get().client_visible)
        self.selection.refresh_from_db()
        self.assertEqual(str(self.selection.imported_total),'123.45')
        self.assertEqual(self.selection.status,'imported')
        self.assertIsNone(self.selection.selected_at)
        self.assertEqual(self.selection.options.count(),0)

    def test_invalid_matches_images_paths_and_checksums_rejected(self):
        for changes in [{'selection_id':999}, {'selection_title':'Wrong'}, {'file':'../image.png'},
                        {'sha256':'0'*64}, {'source_key':'unknown'}]:
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                read_package(self.package(**changes),self.project)
        self.image = b'not an image'
        with self.assertRaises(ValidationError):
            read_package(self.package(),self.project)
        self.assertFalse(SelectionReferencePhoto.objects.exists())

    def test_wrong_project_rejected(self):
        with self.assertRaises(ValidationError):
            read_package(self.package(),self.foreign)

    def test_changed_source_never_overwrites(self):
        self.import_one()
        image = io.BytesIO()
        Image.new('RGB',(4,4),'red').save(image,format='PNG')
        self.image = image.getvalue()
        with self.assertRaises(ValidationError):
            self.import_one()
        self.assertEqual(SelectionReferencePhoto.objects.count(),1)

    def test_client_visibility_and_cross_project_protection(self):
        self.import_one()
        photo=SelectionReferencePhoto.objects.get()
        url=reverse('projects:selection_reference_image',args=[self.project.pk,self.selection.pk,photo.pk])
        response=self.client.get(url)
        self.assertEqual(response.status_code,200)
        self.assertEqual(response['Cache-Control'],'private, no-store')
        response.close()
        self.client.force_login(self.customer)
        self.assertEqual(self.client.get(url).status_code,404)
        board=reverse('projects:selection_list',args=[self.project.pk])
        self.assertNotContains(self.client.get(board),'STAFF SOURCE TEXT')
        self.assertNotContains(self.client.get(board),'Oak finish')
        visibility=reverse('projects:selection_photo_visibility',args=[self.project.pk,photo.pk])
        self.assertIn(self.client.post(visibility,{'client_visible':'on'}).status_code,(403,404))
        self.client.force_login(self.admin)
        self.assertEqual(self.client.post(visibility,{'client_visible':'on'}).status_code,302)
        self.client.force_login(self.customer)
        response=self.client.get(url)
        self.assertEqual(response.status_code,200)
        response.close()
        self.assertContains(self.client.get(board),'Oak finish')
        self.assertNotContains(self.client.get(board),'STAFF SOURCE TEXT')
        self.client.force_login(self.outsider)
        self.assertIn(self.client.get(url).status_code,(403,404))
        wrong=reverse('projects:selection_reference_image',args=[self.foreign.pk,self.selection.pk,photo.pk])
        self.assertEqual(self.client.get(wrong).status_code,404)

    def test_upload_permission_and_confirmation(self):
        url=reverse('projects:selection_photo_import',args=[self.project.pk])
        response=self.client.post(url,{'archive':SimpleUploadedFile('photos.zip',self.package().read())},follow=True)
        self.assertContains(response,'1 photos imported as internal references. 0 duplicates skipped.')
        self.client.force_login(self.customer)
        self.assertIn(self.client.get(url).status_code,(403,404))
        self.assertIn(self.client.post(url,{'archive':SimpleUploadedFile('photos.zip',self.package().read())}).status_code,(403,404))

    def test_storage_cleanup_on_transaction_failure(self):
        with patch('projects.selection_photos.record_activity',side_effect=RuntimeError('audit failed')):
            with self.assertRaises(RuntimeError):
                self.import_one()
        self.assertFalse(SelectionReferencePhoto.objects.exists())
        from pathlib import Path
        self.assertFalse([p for p in Path(self.temp.name).rglob('*') if p.is_file()])
