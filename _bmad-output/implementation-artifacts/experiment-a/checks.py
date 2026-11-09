"""Run the common first-pass review against either isolated experiment arm."""

import os
import re
import sys
from unittest.mock import patch

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "lab_repair.settings")
django.setup()

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import Client, TransactionTestCase
from django.test.utils import get_runner
from django.conf import settings
from django.urls import reverse

from accounts.roles import HANDLER_GROUP
from repairs.models import Ticket, TicketEvent
from repairs.services import claim_ticket, create_ticket


ARM = os.environ["EXPERIMENT_ARM"]
BUTTON = {"x": "追加处理进度", "y": "提交验收"}[ARM]
ACTION = {"x": TicketEvent.Action.PROGRESS, "y": TicketEvent.Action.SUBMITTED}[ARM]
TARGET = {"x": Ticket.Status.IN_PROGRESS, "y": Ticket.Status.AWAITING_CONFIRMATION}[ARM]


class ReviewChecks(TransactionTestCase):
    def setUp(self):
        users = get_user_model().objects
        self.reporter = users.create_user("reporter")
        self.handler = users.create_user("handler")
        self.other = users.create_user("other_handler")
        group = Group.objects.create(name=HANDLER_GROUP)
        self.handler.groups.add(group)
        self.other.groups.add(group)
        self.ticket = create_ticket(
            reporter=self.reporter, device_name="公共打印机", location="实验室公共区", description="卡纸",
        )
        claim_ticket(ticket_id=self.ticket.pk, handler=self.handler, expected_version=self.ticket.version)
        self.ticket.refresh_from_db()
        self.detail_url = reverse("ticket-detail", args=[self.ticket.pk])
        self.client = Client(enforce_csrf_checks=True)
        self.client.force_login(self.handler)
        detail = self.client.get(self.detail_url).content.decode()
        forms = re.findall(r'<form[^>]*action="([^"]+)"[^>]*>(.*?)</form>', detail, re.S)
        self.url, markup = next((url, body) for url, body in forms if BUTTON in body)
        self.token = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', markup).group(1)

    def snapshot(self):
        self.ticket.refresh_from_db()
        return (
            self.ticket.status, self.ticket.version, self.ticket.assignee_id, self.ticket.updated_at,
            list(self.ticket.events.values_list("action", "version", "actor_id", "content", "occurred_at")),
        )

    def post(self, content="已清理进纸口", *, version=None, csrf=True):
        data = {"content": content, "version": self.ticket.version if version is None else version}
        if csrf:
            data["csrfmiddlewaretoken"] = self.token
        return self.client.post(self.url, data)

    def test_current_owner_action_and_readback(self):
        previous_version = self.ticket.version
        if ARM == "y":
            TicketEvent.objects.create(
                ticket=self.ticket, actor=self.handler, version=previous_version + 1,
                occurred_at=self.ticket.updated_at, action=TicketEvent.Action.PROGRESS, content="原处理进度",
            )
            self.ticket.version += 1
            self.ticket.save(update_fields=["version"])
            previous_version = self.ticket.version
        self.assertEqual(self.post().status_code, 302)
        status, version, assignee, updated_at, events = self.snapshot()
        self.assertEqual((status, version, assignee), (TARGET, previous_version + 1, self.handler.pk))
        self.assertEqual(events[-1], (ACTION, version, self.handler.pk, "已清理进纸口", updated_at))
        if ARM == "x":
            self.assertEqual(self.post("正在复测").status_code, 302)
        reader = Client()
        reader.force_login(self.reporter)
        detail = reader.get(self.detail_url)
        self.assertContains(detail, "已清理进纸口")
        if ARM == "x":
            self.assertContains(detail, "最新进度")
            self.assertContains(detail, "正在复测")
            latest = re.search(r'<section aria-labelledby="latest-progress-heading">(.*?)</section>', detail.content.decode(), re.S)
            self.assertIsNotNone(latest)
            self.assertIn("正在复测", latest.group(1))
            self.assertIn("<time", latest.group(1))
            self.assertEqual(self.snapshot()[1], previous_version + 2)
        else:
            self.assertContains(detail, "原处理进度")

    def test_bad_inputs_and_stale_version_leave_no_changes(self):
        snapshot = self.snapshot()
        for bad_content in ("   ", "长" * 4001):
            self.assertEqual(self.post(bad_content).status_code, 400)
        self.assertEqual(self.post(version=self.ticket.version - 1).status_code, 409)
        self.assertEqual(self.post(csrf=False).status_code, 403)
        self.assertEqual(self.snapshot(), snapshot)

    def test_other_actors_cannot_write(self):
        snapshot = self.snapshot()
        for actor in (self.other, self.reporter):
            self.client.force_login(actor)
            response = self.client.get(self.detail_url)
            if response.status_code == 200:
                self.assertNotContains(response, BUTTON)
            self.assertEqual(self.post().status_code, 403)
            self.assertEqual(self.snapshot(), snapshot)

    def test_event_failure_rolls_back_ticket(self):
        snapshot = self.snapshot()
        with patch("repairs.services.TicketEvent.objects.create", side_effect=RuntimeError("forced event failure")):
            with self.assertRaisesRegex(RuntimeError, "forced event failure"):
                self.post()
        self.assertEqual(self.snapshot(), snapshot)


if __name__ == "__main__":
    runner = get_runner(settings)(verbosity=2)
    sys.exit(bool(runner.run_tests(["checks.ReviewChecks"])))
