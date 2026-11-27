from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from projects.models import (
    ChangeOrder,
    Estimate,
    NotificationPreference,
    Organization,
    OrganizationMembership,
    Project,
    ProjectMembership,
)


class ClientDraftPrivacyTests(TestCase):
    """Primary clients and additional signers share the same draft boundary."""

    def test_both_client_types_cannot_see_drafts_in_lists_or_direct_links(self):
        company = Organization.objects.create(name='Test builder', slug='privacy-builder')
        project = Project.objects.create(organization=company, name='Test home')
        customer = get_user_model().objects.create_user('privacy@example.com', 'password')
        membership = ProjectMembership.objects.create(
            project=project, user=customer, role=OrganizationMembership.Role.CLIENT,
        )
        records = (
            ('estimate', Estimate.objects.create(
                project=project, number=1, title='PRIVATE draft estimate',
                price_total=Decimal('87654.32'), status=Estimate.Status.DRAFT,
                created_by=customer,
            )),
            ('change_order', ChangeOrder.objects.create(
                project=project, number=1, title='PRIVATE draft change',
                price_delta=Decimal('87654.32'), status=ChangeOrder.Status.DRAFT,
                created_by=customer,
            )),
        )
        self.client.force_login(customer)
        for access_type in ProjectMembership.ClientAccessType.values:
            membership.client_access_type = access_type
            membership.save(update_fields=['client_access_type'])
            for kind, record in records:
                with self.subTest(access_type=access_type, kind=kind):
                    response = self.client.get(reverse(f'projects:{kind}_list', args=[project.pk]))
                    self.assertEqual(response.status_code, 200)
                    self.assertNotContains(response, record.title)
                    self.assertNotContains(response, '87654.32')
                    response = self.client.get(reverse(f'projects:{kind}_detail', args=[project.pk, record.pk]))
                    self.assertEqual(response.status_code, 404)

    def test_client_cannot_subscribe_to_internal_notification_topics(self):
        company = Organization.objects.create(name='Test builder', slug='privacy-builder')
        project = Project.objects.create(organization=company, name='Test home')
        customer = get_user_model().objects.create_user('privacy@example.com', 'password')
        ProjectMembership.objects.create(project=project, user=customer, role='client')
        self.client.force_login(customer)
        url = reverse('projects:notification_settings', args=[project.pk])
        response = self.client.get(url)
        self.assertContains(response, 'Documents, plans, and signatures')
        self.assertNotContains(response, 'Schedule changes')
        self.assertNotContains(response, 'Project assignments')
        self.client.post(url, {'schedule_email': 'on', 'project_assignments_email': 'on'})
        self.assertFalse(NotificationPreference.objects.filter(
            user=customer, event_type__in=['schedule', 'project_assignments']
        ).exists())
