import re
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import Client, TestCase
from django.urls import reverse

from accounts.roles import HANDLER_GROUP

from .models import Ticket, TicketEvent
from .services import submit_result


class RepairWorkflowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        users = get_user_model().objects
        cls.reporter = users.create_user(username='workflow_reporter')
        cls.handler = users.create_user(username='workflow_handler')
        cls.other_handler = users.create_user(username='workflow_other_handler')
        group = Group.objects.create(name=HANDLER_GROUP)
        group.user_set.add(cls.handler, cls.other_handler)

    def setUp(self):
        self.reporter_client = self.client_for(self.reporter)
        self.handler_client = self.client_for(self.handler)
        self.other_client = self.client_for(self.other_handler)
        self.assertRedirects(self.reporter_client.post(reverse('submit-repair'), {
            'device_name': '公共打印机', 'location': '实验室公共区', 'description': '卡纸，无法打印',
        }), reverse('home'))
        self.ticket = Ticket.objects.get()
        self.detail = reverse('ticket-detail', args=[self.ticket.pk])
        self.assertRedirects(self.handler_client.post(reverse('claim-repair', args=[self.ticket.pk]), {
            'version': self.ticket.version,
        }), reverse('handler-assigned'))
        self.ticket.refresh_from_db()

    @staticmethod
    def client_for(user):
        client = Client()
        client.force_login(user)
        return client

    def post(self, client, action, *, version=None, **fields):
        if version is None:
            version = self.ticket.version
        return client.post(reverse(f'repair-{action}', args=[self.ticket.pk]), {'version': version, **fields})

    def save(self, client, action, **fields):
        self.assertRedirects(self.post(client, action, **fields), self.detail)
        self.ticket.refresh_from_db()

    def snapshot(self):
        self.ticket.refresh_from_db()
        return (
            self.ticket.pk, self.ticket.status, self.ticket.assignee_id, self.ticket.version,
            self.ticket.updated_at, list(self.ticket.events.values()),
        )

    def test_progress_history_and_successful_acceptance(self):
        first = datetime(2026, 11, 9, 8, tzinfo=UTC)
        with patch('repairs.services.timezone') as clock:
            clock.now.side_effect = [first, first + timedelta(minutes=2)]
            self.save(self.handler_client, 'progress', content='已取出卡纸')
            self.save(self.handler_client, 'progress', content='正在检查进纸口')
        self.assertEqual(self.ticket.status, Ticket.Status.IN_PROGRESS)
        self.assertEqual(self.ticket.version, 4)
        self.assertEqual(list(self.ticket.events.filter(action=TicketEvent.Action.PROGRESS).values_list(
            'version', 'actor_id', 'content', 'occurred_at',
        )), [
            (3, self.handler.pk, '已取出卡纸', first),
            (4, self.handler.pk, '正在检查进纸口', first + timedelta(minutes=2)),
        ])
        page = self.reporter_client.get(self.detail)
        self.assertContains(page, self.handler.username)
        self.assertContains(page, '最新进度')
        self.assertEqual(page.context['latest_progress'].content, '正在检查进纸口')
        latest, history = page.content.decode().split('id="ticket-history-heading"', 1)
        self.assertIn('2026-11-09 16:02:00', latest.split('id="latest-progress-heading"', 1)[1])
        self.assertLess(history.index('已取出卡纸'), history.index('正在检查进纸口'))

        self.save(self.handler_client, 'submit-result', content='已清理进纸口，请试打一页')
        self.assertEqual(self.ticket.status, Ticket.Status.AWAITING_CONFIRMATION)
        self.assertEqual(self.ticket.version, 5)
        page = self.reporter_client.get(self.detail)
        self.assertContains(page, '已清理进纸口，请试打一页')
        self.assertContains(page, '已取出卡纸')
        before = self.snapshot()
        self.assertEqual(self.post(self.handler_client, 'close').status_code, 403)
        self.assertEqual(self.post(self.handler_client, 'submit-result', content='重复验收').status_code, 409)
        self.assertEqual(self.snapshot(), before)
        self.save(self.reporter_client, 'close')
        self.assertEqual(self.ticket.status, Ticket.Status.CLOSED)
        self.assertEqual(self.ticket.version, 6)
        closed = self.snapshot()
        self.assertEqual(self.post(self.reporter_client, 'close').status_code, 409)
        self.assertEqual(self.post(self.handler_client, 'progress', content='关闭后进度').status_code, 409)
        self.assertEqual(self.snapshot(), closed)
        self.assertEqual(self.ticket.events.filter(action=TicketEvent.Action.CLOSED).count(), 1)
        page = self.reporter_client.get(self.detail)
        self.assertContains(page, '已清理进纸口，请试打一页')
        self.assertContains(page, '已取出卡纸')
        self.assertContains(page, '已关闭')

    def test_return_retry_and_old_version_rejection(self):
        self.save(self.handler_client, 'submit-result', content='第一次清理，请试打')
        first_round = self.ticket.version
        before = self.snapshot()
        for fields in ({}, {'content': ' \t\n'}):
            self.assertEqual(self.post(self.reporter_client, 'return', **fields).status_code, 400)
        self.assertEqual(self.post(self.other_client, 'return', content='无权退回').status_code, 403)
        self.assertEqual(self.snapshot(), before)

        self.save(self.reporter_client, 'return', content='试打仍卡纸')
        self.assertEqual((self.ticket.status, self.ticket.assignee_id, self.ticket.version), (
            Ticket.Status.IN_PROGRESS, self.handler.pk, first_round + 1,
        ))
        handler_detail = self.handler_client.get(self.detail)
        self.assertContains(handler_detail, '试打仍卡纸')
        self.assertContains(handler_detail, '第一次清理，请试打')
        self.save(self.handler_client, 'progress', content='重新调整进纸轮')
        self.save(self.handler_client, 'submit-result', content='第二次处理完毕，请复测')
        second_round = self.snapshot()
        self.assertEqual(self.post(self.reporter_client, 'close', version=first_round).status_code, 409)
        conflict = self.post(self.reporter_client, 'return', version=first_round, content='旧页面退回')
        self.assertContains(conflict, '旧页面退回', status_code=409)
        self.assertContains(conflict, f'href="{self.detail}"', status_code=409)
        self.assertEqual(self.snapshot(), second_round)

        self.save(self.reporter_client, 'close')
        self.assertEqual(self.ticket.status, Ticket.Status.CLOSED)
        self.assertEqual(self.ticket.assignee_id, self.handler.pk)
        self.assertEqual(list(self.ticket.events.values_list('action', 'content')), [
            (TicketEvent.Action.CREATED, ''),
            (TicketEvent.Action.CLAIMED, ''),
            (TicketEvent.Action.SUBMITTED, '第一次清理，请试打'),
            (TicketEvent.Action.RETURNED, '试打仍卡纸'),
            (TicketEvent.Action.PROGRESS, '重新调整进纸轮'),
            (TicketEvent.Action.SUBMITTED, '第二次处理完毕，请复测'),
            (TicketEvent.Action.CLOSED, ''),
        ])
        page = self.reporter_client.get(self.detail)
        for text in ('第一次清理，请试打', '试打仍卡纸', '第二次处理完毕，请复测', '已关闭'):
            self.assertContains(page, text)

    def test_progress_permission_and_blank_result_keep_ticket_unchanged(self):
        before = self.snapshot()
        self.assertEqual(self.post(self.other_client, 'progress', content='越权进度').status_code, 403)
        self.assertEqual(self.post(self.other_client, 'submit-result', content='越权结果').status_code, 403)
        for fields in ({}, {'content': '   '}):
            self.assertEqual(self.post(self.handler_client, 'submit-result', **fields).status_code, 400)
        self.assertEqual(self.snapshot(), before)

    def test_rendered_forms_submit_all_four_actions_with_csrf(self):
        handler = Client(enforce_csrf_checks=True)
        handler.force_login(self.handler)
        reporter = Client(enforce_csrf_checks=True)
        reporter.force_login(self.reporter)
        for browser, action, note in (
            (handler, 'progress', '取出卡纸'),
            (handler, 'submit-result', '请试打'),
            (reporter, 'return', '仍然卡纸'),
            (handler, 'submit-result', '已修复，请复测'),
            (reporter, 'close', None),
        ):
            url = reverse(f'repair-{action}', args=[self.ticket.pk])
            page = browser.get(self.detail).content.decode()
            form = re.search(rf'<form method="post" action="{re.escape(url)}">(.*?)</form>', page, re.S)
            self.assertIsNotNone(form, action)
            fields = dict(re.findall(r'type="hidden" name="([^"]+)" value="([^"]+)"', form[1]))
            if note is not None:
                textarea = re.search(r'<textarea [^>]*name="([^"]+)"', form[1])
                self.assertIsNotNone(textarea, action)
                fields[textarea[1]] = note
            self.assertRedirects(browser.post(url, fields), self.detail)
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.status, Ticket.Status.CLOSED)

    def test_detail_does_not_mix_new_event_with_an_old_ticket_version(self):
        old = Ticket.objects.select_related('assignee').get(pk=self.ticket.pk)

        def read_old_then_commit():
            submit_result(ticket_id=old.pk, actor=self.handler, expected_version=old.version, content='新验收事件')
            return old

        with patch('repairs.views.tickets_visible_to') as visible:
            visible.return_value.select_related.return_value.filter.return_value.first.side_effect = read_old_then_commit
            page = self.reporter_client.get(self.detail)
        self.assertContains(page, '<dd>处理中</dd>', html=True)
        self.assertNotContains(page, '新验收事件')
        current = self.reporter_client.get(self.detail)
        self.assertContains(current, '<dd>待确认</dd>', html=True)
        self.assertContains(current, '新验收事件')
