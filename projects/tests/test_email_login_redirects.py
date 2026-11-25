"""Email deep links must survive authentication without exposing project data."""

import inspect
import re
from urllib.parse import parse_qs, urlsplit

from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.models import AnonymousUser
from django.core import mail
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone
from django_otp.oath import totp

from billing import views as billing_views
from projects import views as project_views
from projects.models import FinishSelection
from projects.services import send_selection_review_notification
from projects.tests import test_selections
from subscriptions import views as subscription_views
from users.models import AuthenticatorDevice


class EmailLoginRedirectTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        test_selections.FinishSelectionTests.setUpTestData.__func__(cls)

    def setUp(self):
        self.selection = FinishSelection.objects.create(
            project=self.project,
            number=1,
            title="Choose your countertops",
            status=FinishSelection.Status.OPEN,
            opened_at=timezone.now(),
            opened_by=self.admin_user,
            created_by=self.admin_user,
        )
        self.target = reverse(
            "projects:selection_detail", args=[self.project.pk, self.selection.pk]
        )

    def email_target(self):
        request = RequestFactory().get("/", secure=True)
        request.user = self.admin_user
        send_selection_review_notification(request, self.selection)
        self.assertTrue(mail.outbox)
        links = re.findall(r"https://testserver[^\s<>]+", mail.outbox[0].body)
        self.assertIn("https://testserver" + self.target, links)
        return self.target

    def login_page(self, target):
        response = self.client.get(target, secure=True)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(urlsplit(response.url).path, reverse("login"))
        self.assertEqual(parse_qs(urlsplit(response.url).query)["next"], [target])
        page = self.client.get(response.url, secure=True)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'name="next"')
        self.assertEqual(page.context["next"], target)
        return response.url

    def test_actual_selection_email_login_returns_to_selection(self):
        target = self.email_target()
        login_url = self.login_page(target)
        response = self.client.post(
            login_url,
            {
                "username": self.client_user.email,
                "password": "password",
                "next": target,
            },
            secure=True,
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.request["PATH_INFO"], target)
        self.assertContains(response, self.selection.title)
        self.assertTrue(response.context["can_choose"])

    def test_failed_login_preserves_selection_and_query_string(self):
        target = self.target + "?from=email&view=choices"
        login_url = self.login_page(target)
        response = self.client.post(
            login_url,
            {
                "username": self.client_user.email,
                "password": "wrong",
                "next": target,
            },
            secure=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["next"], target)
        response = self.client.post(
            login_url,
            {
                "username": self.client_user.email,
                "password": "password",
                "next": target,
            },
            secure=True,
        )
        self.assertEqual(response.url, target)

    def test_two_step_sign_in_returns_to_original_selection(self):
        device = AuthenticatorDevice.objects.create(
            account=self.client_user, name="Test", confirmed=True
        )
        login_url = self.login_page(self.target)
        response = self.client.post(
            login_url,
            {
                "username": self.client_user.email,
                "password": "password",
                "next": self.target,
            },
            secure=True,
            follow=True,
        )
        self.assertEqual(response.request["PATH_INFO"], reverse("users:mfa_verify"))
        self.assertEqual(response.context["next"], self.target)
        response = self.client.post(
            reverse("users:mfa_verify"),
            {
                "code": f"{totp(device.bin_key):06d}",
                "next": self.target,
            },
            secure=True,
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.request["PATH_INFO"], self.target)
        self.assertContains(response, self.selection.title)

    def test_signed_in_client_opens_directly_but_other_project_and_draft_stay_private(
        self,
    ):
        self.client.force_login(self.client_user)
        self.assertEqual(self.client.get(self.target).status_code, 200)
        self.selection.status = FinishSelection.Status.DRAFT
        self.selection.opened_at = None
        self.selection.opened_by = None
        self.selection.save()
        self.assertEqual(self.client.get(self.target).status_code, 404)
        self.selection.status = FinishSelection.Status.OPEN
        self.selection.opened_at = timezone.now()
        self.selection.opened_by = self.admin_user
        self.selection.project = self.other_project
        self.selection.save()
        target = reverse(
            "projects:selection_detail", args=[self.other_project.pk, self.selection.pk]
        )
        self.assertEqual(self.client.get(target).status_code, 404)

    def test_external_login_destination_is_rejected(self):
        response = self.client.post(
            reverse("login"),
            {
                "username": self.client_user.email,
                "password": "password",
                "next": "https://untrusted.example/",
            },
            secure=True,
        )
        self.assertEqual(response.url, "/")

    def test_protected_dispatch_checks_login_before_database_lookup(self):
        for module in (project_views, billing_views, subscription_views):
            for name, view in inspect.getmembers(module, inspect.isclass):
                if (
                    view.__module__ != module.__name__
                    or not issubclass(view, LoginRequiredMixin)
                    or "dispatch" not in view.__dict__
                ):
                    continue
                with self.subTest(view=name), self.assertNumQueries(0):
                    request = RequestFactory().get(
                        "/protected/?from=email", secure=True
                    )
                    request.user = AnonymousUser()
                    response = view.as_view()(request)
                    self.assertEqual(response.status_code, 302)
                    self.assertEqual(urlsplit(response.url).path, reverse("login"))
                    self.assertEqual(
                        parse_qs(urlsplit(response.url).query)["next"],
                        ["/protected/?from=email"],
                    )
