import tempfile
from datetime import date
from pathlib import Path
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, Client, override_settings
from django.urls import reverse
from django.utils import timezone
from projects.models import (Organization, OrganizationMembership, Project, ProjectMembership,
    FinishSelection, SelectionOption, SelectionPackage, SelectionTemplate, SelectionTemplateRow,
    SelectionTemplateOption, ActivityEvent)
from projects.selection_templates import save_project_template, copy_template, load_template
from projects.tests import grant_internal_access


class SelectionTemplateTests(TestCase):
    def setUp(self):
        self.media_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.media_dir.cleanup)
        self.media_settings = override_settings(PRIVATE_MEDIA_ROOT=Path(self.media_dir.name))
        self.media_settings.enable()
        self.addCleanup(self.media_settings.disable)
        self.org = Organization.objects.create(name='Builder', slug='builder')
        self.other_org = Organization.objects.create(name='Other', slug='other')
        self.admin = get_user_model().objects.create_user('admin@example.test', 'password')
        self.customer = get_user_model().objects.create_user('customer@example.test', 'password')
        self.manager = get_user_model().objects.create_user('pm@example.test', 'password')
        self.outsider = get_user_model().objects.create_user('outside@example.test', 'password')
        for user, org, role in [(self.admin,self.org,'admin'),(self.customer,self.org,'client'),
                               (self.manager,self.org,'project_manager'),(self.outsider,self.other_org,'admin')]:
            OrganizationMembership.objects.create(organization=org,user=user,role=role)
        self.project = Project.objects.create(organization=self.org,name='Source')
        self.target = Project.objects.create(organization=self.org,name='Target')
        self.foreign = Project.objects.create(organization=self.other_org,name='Foreign')
        ProjectMembership.objects.create(project=self.target,user=self.customer,role='client')
        grant_internal_access(self.manager,self.project)
        self.category = SelectionPackage.objects.create(project=self.project,title='Cabinetry',created_by=self.admin)
        self.selection = FinishSelection.objects.create(project=self.project,package=self.category,number=1,
            title='Cabinet finish',location='Kitchen',description='Choose a finish',allowance_amount=500,
            created_by=self.admin,due_date=date(2020,1,1))
        self.option = SelectionOption.objects.create(selection=self.selection,name='Oak',price=600,cost=123,
            image=SimpleUploadedFile('oak.png',b'original-image'),
            attachment=SimpleUploadedFile('spec.pdf',b'%PDF-spec'),vendor_name='Vendor')
        self.client.force_login(self.admin)

    def url(self,name,*args):
        return reverse('projects:selection_template_'+name,args=args)

    def make_template(self):
        return save_project_template(self.project,self.admin,'Standard home','Reusable',self.project.finish_selections.all())

    def test_templates_navigation_routes_authorized_staff_to_library(self):
        self.make_template()
        for user in (self.admin, self.manager):
            self.client.force_login(user)
            response = self.client.get(self.url('home'), follow=True)
            self.assertRedirects(response, self.url('library', 'builder'))
            self.assertContains(response, 'href="/templates/" aria-current="page">Templates</a>')
            for label in ('Create template', 'Edit template', 'Name &amp; description', 'Copy / duplicate', 'Deleted templates'):
                self.assertContains(response, label)

    def test_templates_home_blocks_clients_and_inactive_staff(self):
        for user in (self.customer, self.manager):
            if user == self.manager:
                user.organization_memberships.update(is_active=False)
            self.client.force_login(user)
            self.assertEqual(self.client.get(self.url('home')).status_code, 403)
            self.assertNotContains(self.client.get(reverse('core:home')), 'href="/templates/"')
        self.client.logout()
        response = self.client.get(self.url('home'))
        self.assertEqual(response.status_code, 302)
        self.assertIn('next=/templates/', response.url)

    def test_templates_company_picker_only_lists_allowed_companies(self):
        Organization.objects.create(name='Hidden company', slug='hidden')
        OrganizationMembership.objects.create(organization=self.other_org, user=self.admin, role='admin')
        response = self.client.get(self.url('home'))
        self.assertContains(response, self.url('library', 'builder'))
        self.assertContains(response, self.url('library', 'other'))
        self.assertNotContains(response, 'Hidden company')

    def test_create_grid_add_update_delete_and_stale_edit(self):
        response=self.client.post(self.url('create','builder'),{'name':'Kitchen','description':'Finishes'})
        self.assertEqual(response.status_code,302)
        template=SelectionTemplate.objects.get(name='Kitchen')
        detail=self.url('detail','builder',template.pk)
        self.assertContains(self.client.get(detail),'Save template rows')
        data={'version':template.updated_at.isoformat(),'rows-TOTAL_FORMS':'1','rows-INITIAL_FORMS':'0',
              'rows-0-title':'Counter','rows-0-category':'Kitchen','rows-0-location':'Kitchen',
              'rows-0-allowance_amount':'1200','rows-0-sort_order':'2'}
        self.assertEqual(self.client.post(detail,data).status_code,302)
        row=template.rows.get()
        data.update({'rows-INITIAL_FORMS':'1','rows-0-id':row.pk,'rows-0-title':'Changed'})
        self.assertContains(self.client.post(detail,data),'changed in another window')
        row.refresh_from_db()
        self.assertEqual(row.title,'Counter')
        template.refresh_from_db()
        data['version']=template.updated_at.isoformat()
        self.assertEqual(self.client.post(detail,data).status_code,302)
        row.refresh_from_db()
        self.assertEqual(row.title,'Changed')
        template.refresh_from_db()
        data.update(version=template.updated_at.isoformat())
        data['rows-0-DELETE']='on'
        self.assertEqual(self.client.post(detail,data).status_code,302)
        self.assertFalse(template.rows.exists())

    def test_save_copy_load_independent_files_and_no_approval_or_internal_cost(self):
        self.selection.status='selected'
        self.selection.opened_at=self.selection.selected_at=timezone.now()
        self.selection.opened_by=self.admin
        self.selection.selected_by=self.customer
        self.selection.chosen_option=self.option
        self.selection.client_comment='Private client decision'
        self.selection.save()
        template=self.make_template()
        copied=copy_template(template,self.admin,'Another home')
        self.assertEqual(load_template(copied,self.target,self.admin,copied.rows.all(),date(2030,1,1)),(1,0))
        selection=self.target.finish_selections.get()
        option=selection.options.get()
        self.assertEqual(selection.status,'draft')
        self.assertIsNone(selection.chosen_option_id)
        self.assertEqual(selection.client_comment,'')
        self.assertEqual(selection.due_date,date(2030,1,1))
        self.assertEqual(option.cost,0)
        self.assertEqual(option.price,600)
        names=[self.option.image.name,template.rows.get().options.get().image.name,copied.rows.get().options.get().image.name,option.image.name]
        self.assertEqual(len(set(names)),4)
        self.option.image.delete(save=False)
        with option.image.open('rb') as image:
            self.assertEqual(image.read(),b'original-image')
        self.assertGreaterEqual(ActivityEvent.objects.count(),3)

    def test_duplicate_load_and_renamed_loaded_row_never_overwrites(self):
        template=self.make_template()
        self.assertEqual(load_template(template,self.target,self.admin,template.rows.all()),(1,0))
        loaded=self.target.finish_selections.get()
        loaded.title='Job specific title'
        loaded.save()
        self.assertEqual(load_template(template,self.target,self.admin,template.rows.all()),(0,1))
        loaded.refresh_from_db()
        self.assertEqual(loaded.title,'Job specific title')
        self.assertEqual(load_template(copy_template(template,self.admin,'Copy'),self.project,self.admin,SelectionTemplate.objects.get(name='Copy').rows.all()),(0,1))

    def test_failed_file_copy_rolls_back_rows_and_private_files(self):
        template=self.make_template()
        before=set(Path(self.media_dir.name).rglob('*'))
        with patch('projects.selection_templates.SelectionOption.full_clean',side_effect=ValueError('fail')):
            with self.assertRaises(ValueError):
                load_template(template,self.target,self.admin,template.rows.all())
        self.assertFalse(self.target.finish_selections.exists())
        self.assertFalse(self.target.selection_packages.exists())
        self.assertFalse([p for p in set(Path(self.media_dir.name).rglob('*'))-before if p.is_file()])

    def test_client_and_other_company_cannot_read_templates_or_files(self):
        template=self.make_template()
        row=template.rows.get()
        option=row.options.get()
        urls=[self.url('library','builder'),self.url('detail','builder',template.pk),
              self.url('media','builder',template.pk,row.pk,option.pk,'image'),
              self.url('media','builder',template.pk,row.pk,option.pk,'attachment'),
              self.url('copy','builder',template.pk),self.url('delete','builder',template.pk)]
        for user in (self.customer,self.outsider):
            self.client.force_login(user)
            for url in urls:
                self.assertEqual(self.client.get(url).status_code,404,url)
                self.assertIn(self.client.post(url,{'confirm':'on'}).status_code,(404,405))

    def test_assigned_manager_can_use_library_but_not_unassigned_job(self):
        template=self.make_template()
        self.client.force_login(self.manager)
        self.assertEqual(self.client.get(self.url('detail','builder',template.pk)).status_code,200)
        self.assertEqual(self.client.get(self.url('load',self.target.pk,template.pk)).status_code,404)
        OrganizationMembership.objects.filter(user=self.manager).update(is_active=False)
        self.assertEqual(self.client.get(self.url('detail','builder',template.pk)).status_code,404)

    def test_cross_company_load_and_forged_row_ids_rejected(self):
        template=self.make_template()
        self.assertEqual(self.client.post(self.url('load',self.foreign.pk,template.pk),{'rows':[template.rows.get().pk]}).status_code,404)
        foreign_template=SelectionTemplate.objects.create(organization=self.other_org,name='Private',created_by=self.outsider)
        foreign_row=SelectionTemplateRow.objects.create(template=foreign_template,title='Hidden')
        response=self.client.post(self.url('load',self.target.pk,template.pk),{'rows':[foreign_row.pk]})
        self.assertContains(response,'Select a valid choice')
        self.assertFalse(self.target.finish_selections.exists())
        row=template.rows.get()
        data={'version':template.updated_at.isoformat(),'rows-TOTAL_FORMS':1,'rows-INITIAL_FORMS':1,
              'rows-0-id':foreign_row.pk,'rows-0-title':'Stolen','rows-0-allowance_amount':0,'rows-0-sort_order':0}
        self.assertIn(self.client.post(self.url('detail','builder',template.pk),data).status_code,(200,403))
        foreign_row.refresh_from_db()
        self.assertEqual(foreign_row.title,'Hidden')
        row.refresh_from_db()
        self.assertEqual(row.title,'Cabinet finish')

    def test_delete_restore_keeps_loaded_project_unchanged(self):
        template=self.make_template()
        load_template(template,self.target,self.admin,template.rows.all())
        delete=self.url('delete','builder',template.pk)
        self.assertEqual(self.client.get(delete).status_code,200)
        template.refresh_from_db()
        self.assertIsNone(template.deleted_at)
        self.assertEqual(self.client.post(delete,{'confirm':'on'}).status_code,302)
        self.assertEqual(self.client.get(self.url('load',self.target.pk,template.pk)).status_code,404)
        self.assertContains(self.client.get(self.url('library','builder')+'?deleted=1'),'Standard home')
        self.assertEqual(self.target.finish_selections.count(),1)
        self.assertEqual(self.client.post(delete+'?restore=1',{'confirm':'on'}).status_code,302)
        self.assertEqual(self.client.get(self.url('detail','builder',template.pk)).status_code,200)

    def test_save_load_forms_and_options_validation(self):
        response=self.client.post(self.url('save',self.project.pk),{'name':'Saved','description':'','selections':[self.selection.pk]})
        self.assertEqual(response.status_code,302)
        template=SelectionTemplate.objects.get(name='Saved')
        row=template.rows.get()
        option=row.options.get()
        self.assertEqual(self.client.get(self.url('option_edit','builder',template.pk,row.pk,option.pk)).status_code,200)
        create=self.url('option_create','builder',template.pk,row.pk)
        payload={'name':'Painted','price':'-1','sort_order':0}
        self.assertEqual(self.client.post(create,payload).status_code,200)
        self.assertEqual(row.options.count(),1)
        payload.update(price='100',image=SimpleUploadedFile('bad.svg',b'<svg><script>alert(1)</script></svg>',content_type='image/svg+xml'))
        self.assertEqual(self.client.post(create,payload).status_code,200)
        self.assertEqual(row.options.count(),1)
        payload.pop('image')
        self.assertEqual(self.client.post(create,payload).status_code,302)
        self.assertEqual(self.client.post(self.url('load',self.target.pk,template.pk),{'rows':[row.pk],'due_date':'2030-02-03'}).status_code,302)
        self.assertEqual(self.target.finish_selections.get().options.count(),2)

    def test_client_board_hides_drafts_and_company_controls_and_costs(self):
        template=self.make_template()
        load_template(template,self.target,self.admin,template.rows.all())
        selection=self.target.finish_selections.get()
        option=selection.options.get()
        option.cost=987654
        option.save()
        self.client.force_login(self.customer)
        board=reverse('projects:selection_list',args=[self.target.pk])
        for suffix in ('','?view=cards','?status=draft'):
            response=self.client.get(board+suffix)
            self.assertNotContains(response,'Cabinet finish')
            self.assertNotContains(response,'Save as template')
        self.assertEqual(self.client.get(reverse('projects:selection_option_image',args=[self.target.pk,selection.pk,option.pk])).status_code,404)
        selection.status='open'
        selection.opened_by=self.admin
        selection.opened_at=timezone.now()
        selection.save()
        for suffix in ('','?view=cards'):
            response=self.client.get(board+suffix)
            self.assertContains(response,'Cabinet finish')
            self.assertNotContains(response,'987654')
            self.assertNotContains(response,'Load / manage templates')

    def test_board_filter_group_and_printable_choices(self):
        response=self.client.get(reverse('projects:selection_list',args=[self.project.pk])+'?group=location&q=Cabinet&status=draft')
        self.assertContains(response,'Cabinet finish')
        self.assertContains(response,'Kitchen')
        self.assertContains(response,'Expand choices')
        self.assertNotContains(self.client.get(reverse('projects:selection_list',args=[self.project.pk])+'?q=missing'),'Cabinet finish')

    def test_csrf_required_and_media_no_store(self):
        template=self.make_template()
        row=template.rows.get()
        secure=Client(enforce_csrf_checks=True)
        secure.force_login(self.admin)
        self.assertEqual(secure.post(self.url('delete','builder',template.pk),{'confirm':'on'}).status_code,403)
        response=self.client.get(self.url('media','builder',template.pk,row.pk,row.options.get().pk,'image'))
        self.assertEqual(response.status_code,200)
        self.assertEqual(response['Cache-Control'],'private, no-store')
        response.close()

    def test_large_template_page_save_keeps_all_other_rows(self):
        template=SelectionTemplate.objects.create(organization=self.org,name='Large template',created_by=self.admin)
        SelectionTemplateRow.objects.bulk_create([
            SelectionTemplateRow(template=template,title=f'Row {index:03}',sort_order=index) for index in range(142)])
        url=self.url('detail','builder',template.pk)+'?page=2'
        response=self.client.get(url)
        self.assertContains(response,'Rows 51')
        self.assertEqual(len(response.context['formset'].initial_forms),50)
        data={'version':template.updated_at.isoformat(),'rows-TOTAL_FORMS':50,'rows-INITIAL_FORMS':50}
        for index,row in enumerate(template.rows.all()[50:100]):
            data.update({f'rows-{index}-id':row.pk,f'rows-{index}-template':template.pk,
                         f'rows-{index}-title':row.title+' revised',f'rows-{index}-category':'',
                         f'rows-{index}-location':'',f'rows-{index}-allowance_amount':'0',f'rows-{index}-sort_order':row.sort_order})
        self.assertEqual(self.client.post(url,data).status_code,302)
        self.assertEqual(template.rows.count(),142)
        self.assertEqual(template.rows.filter(title__endswith='revised').count(),50)
        self.assertTrue(template.rows.filter(title='Row 000').exists())
        self.assertTrue(template.rows.filter(title='Row 141').exists())

    def test_loaded_photo_option_can_be_edited_without_public_storage_url(self):
        template=self.make_template()
        load_template(template,self.target,self.admin,template.rows.all())
        selection=self.target.finish_selections.get()
        option=selection.options.get()
        url=reverse('projects:selection_option_edit',args=[self.target.pk,selection.pk,option.pk])
        response=self.client.get(url)
        self.assertEqual(response.status_code,200)
        self.assertContains(response,'Current specification file')
        self.assertNotContains(response,option.image.name)

    def test_choose_project_limits_destinations_to_managed_projects(self):
        template=self.make_template()
        self.client.force_login(self.manager)
        url=self.url('choose_project','builder',template.pk)
        response=self.client.get(url)
        self.assertContains(response,'Source')
        self.assertNotContains(response,'Target')
        self.assertContains(self.client.post(url,{'project':self.target.pk}),'Select a valid choice')
        self.assertEqual(self.client.post(url,{'project':self.project.pk}).status_code,302)
