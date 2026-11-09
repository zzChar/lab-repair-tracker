import sqlite3
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import IntegrityError, OperationalError
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone
from django.utils.html import escape

from accounts.roles import HANDLER_GROUP

from .models import Ticket, TicketEvent
from .services import create_ticket


class SubmitRepairTests(TestCase):
    report = {
        'device_name': '公共打印机',
        'location': '实验室公共区',
        'description': '卡纸，无法打印',
    }

    @classmethod
    def setUpTestData(cls):
        users = get_user_model().objects
        cls.reporter = users.create_user(username='member')
        cls.other_user = users.create_user(username='other_member')

    def setUp(self):
        self.url = reverse('submit-repair')

    def test_success_creates_one_ticket_and_event_using_session_identity(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.reporter)
        self.assertContains(client.get(reverse('home')), self.url)
        self.assertContains(client.get(self.url), 'name="csrfmiddlewaretoken"')
        server_time = datetime(2026, 11, 9, 8, tzinfo=UTC)
        with patch('repairs.services.timezone') as clock:
            clock.now.return_value = server_time
            response = client.post(self.url, {
                **self.report,
                'csrfmiddlewaretoken': client.cookies['csrftoken'].value,
                'reporter': self.other_user.pk,
                'assignee': self.other_user.pk,
                'status': 'closed',
                'version': 99,
                'occurred_at': '2000-01-01T00:00:00Z',
                'updated_at': '2000-01-01T00:00:00Z',
            }, follow=True)

        self.assertEqual(response.redirect_chain, [(reverse('home'), 302)])
        ticket = Ticket.objects.get()
        self.assertContains(response, f'工单 #{ticket.pk}，状态：待接单。')
        self.assertEqual(ticket.device_name, self.report['device_name'])
        self.assertEqual(ticket.location, self.report['location'])
        self.assertEqual(ticket.description, self.report['description'])
        self.assertEqual(ticket.reporter, self.reporter)
        self.assertIsNone(ticket.assignee)
        self.assertEqual(ticket.status, Ticket.Status.PENDING)
        self.assertEqual(ticket.version, 1)
        event = TicketEvent.objects.get()
        self.assertEqual(event.ticket, ticket)
        self.assertEqual(event.actor, self.reporter)
        self.assertEqual(event.action, TicketEvent.Action.CREATED)
        self.assertEqual(event.version, ticket.version)
        self.assertEqual(ticket.updated_at, server_time)
        self.assertEqual(event.occurred_at, server_time)
        client.get(reverse('home'))
        self.assertEqual(Ticket.objects.count(), 1)
        self.assertEqual(TicketEvent.objects.count(), 1)

    def test_nonblank_input_is_saved_without_trimming(self):
        self.client.force_login(self.reporter)
        report = {field: f'  {value}  ' for field, value in self.report.items()}

        self.assertRedirects(self.client.post(self.url, report), reverse('home'))
        ticket = Ticket.objects.get()
        self.assertEqual({field: getattr(ticket, field) for field in report}, report)

    def test_missing_and_whitespace_only_fields_do_not_write(self):
        self.client.force_login(self.reporter)
        for field in self.report:
            for blank in (None, ' \t\n'):
                with self.subTest(field=field, blank=blank):
                    data = self.report.copy()
                    if blank is None:
                        data.pop(field)
                    else:
                        data[field] = blank
                    response = self.client.post(self.url, data)
                    self.assertEqual(response.status_code, 200)
                    errors = response.context['form'].errors.as_data()
                    self.assertEqual(list(errors), [field])
                    self.assertEqual(errors[field][0].code, 'required')
                    self.assertContains(response, f'id_{field}_error')
                    self.assert_no_writes()

    def test_anonymous_get_and_post_require_login(self):
        self.assertRedirects(self.client.get(self.url), f"{reverse('login')}?next={self.url}")
        self.assertRedirects(self.client.post(self.url, self.report), f"{reverse('login')}?next={self.url}")
        self.assert_no_writes()

    def test_post_requires_csrf_token(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.reporter)
        self.assertEqual(client.post(self.url, self.report).status_code, 403)
        self.assert_no_writes()

    def test_failed_event_insert_rolls_back_ticket(self):
        self.client.force_login(self.reporter)
        with patch('repairs.services.TicketEvent.objects.create', side_effect=IntegrityError('event insert failed')):
            with self.assertRaises(IntegrityError):
                self.client.post(self.url, self.report)
        self.assert_no_writes()

    def test_lock_error_returns_retry_message_and_keeps_form_values(self):
        self.client.force_login(self.reporter)
        cause = sqlite3.OperationalError('database is locked')
        cause.sqlite_errorcode = sqlite3.SQLITE_BUSY
        error = OperationalError('database is locked')
        error.__cause__ = cause
        with patch('repairs.services.TicketEvent.objects.create', side_effect=error):
            response = self.client.post(self.url, self.report)
        self.assertContains(response, '暂时无法提交报修', status_code=503)
        self.assertContains(response, self.report['description'], status_code=503)
        self.assert_no_writes()

    def assert_no_writes(self):
        self.assertFalse(Ticket.objects.exists())
        self.assertFalse(TicketEvent.objects.exists())

    def test_same_form_retry_creates_only_one_ticket_and_event(self):
        self.client.force_login(self.reporter)
        page = self.client.get(self.url)
        token = page.context['form']['submission_id'].value()
        self.assertContains(page, 'name="submission_id"')
        self.assertTrue(token)
        payload = {**self.report, 'submission_id': token}
        for _ in range(2):
            self.assertRedirects(self.client.post(self.url, payload), reverse('home'))
        original = Ticket.objects.get()
        self.assertEqual(original.events.count(), 1)
        self.assertEqual(self.client.post(self.url, {**payload, 'description': '不同故障'}).status_code, 409)
        self.assertEqual(Ticket.objects.count(), 1)
        self.assertEqual(TicketEvent.objects.count(), 1)
        new_token = self.client.get(self.url).context['form']['submission_id'].value()
        self.assertNotEqual(new_token, token)
        self.assertRedirects(self.client.post(self.url, {**payload, 'submission_id': new_token}), reverse('home'))
        self.assertEqual(Ticket.objects.count(), 2)

    def test_unread_submission_receipt_is_cleared_when_switching_account(self):
        self.client.force_login(self.reporter)
        self.assertRedirects(self.client.post(self.url, self.report), reverse('home'), fetch_redirect_response=False)
        self.assertRedirects(self.client.post(reverse('logout')), reverse('login'))
        self.client.force_login(self.other_user)
        self.assertNotContains(self.client.get(reverse('home')), '报修已提交')

    def test_device_name_over_limit_is_rejected_without_writes(self):
        self.client.force_login(self.reporter)
        response = self.client.post(self.url, {**self.report, 'device_name': '长' * 161})
        self.assertEqual(response.context['form'].errors.as_data()['device_name'][0].code, 'max_length')
        self.assert_no_writes()


class MyTicketsTests(TestCase):
    password = 'Local-List-Test-2026!'

    @classmethod
    def setUpTestData(cls):
        users = get_user_model().objects
        cls.member_a = users.create_user(username='member_a', password=cls.password)
        cls.member_b = users.create_user(username='member_b', password=cls.password)

    def test_two_members_see_only_the_tickets_they_submitted(self):
        url = reverse('my-tickets')
        clients_and_tickets = []
        for member, device in ((self.member_a, '公共打印机 A'), (self.member_b, '公共投影仪 B')):
            client = Client()
            self.assertRedirects(client.post(reverse('login'), {
                'username': member.username, 'password': self.password,
            }), reverse('home'))
            self.assertContains(client.get(reverse('home')), f'<a href="{url}">我提交的工单</a>', html=True)
            self.assertRedirects(client.post(reverse('submit-repair'), {
                'device_name': device, 'location': '实验室公共区', 'description': '无法启动',
            }), reverse('home'))
            clients_and_tickets.append((client, Ticket.objects.get(reporter=member)))

        for index, (client, own_ticket) in enumerate(clients_and_tickets):
            other_ticket = clients_and_tickets[1 - index][1]
            with self.subTest(member=own_ticket.reporter_id):
                response = client.get(url, {'reporter': other_ticket.reporter_id})
                self.assertContains(response, own_ticket.device_name)
                own_url = reverse('ticket-detail', args=[own_ticket.pk])
                other_url = reverse('ticket-detail', args=[other_ticket.pk])
                self.assertContains(response, f'<a href="{own_url}">#{own_ticket.pk}</a>', html=True)
                self.assertNotContains(response, other_ticket.device_name)
                self.assertNotContains(response, f'href="{other_url}"')
                self.assertIn('no-store', response.headers['Cache-Control'])

    def test_pending_ticket_displays_status_and_local_update_time(self):
        ticket = create_ticket(
            reporter=self.member_a, device_name='公共打印机',
            location='实验室公共区', description='卡纸，无法打印',
        )
        self.client.force_login(self.member_a)
        response = self.client.get(reverse('my-tickets'))
        updated_at = timezone.localtime(ticket.updated_at)

        self.assertContains(response, '公共打印机')
        self.assertContains(response, '<td>待接单</td>', html=True)
        self.assertContains(response, '<td>尚无跟进人</td>', html=True)
        self.assertContains(
            response,
            f'<time datetime="{updated_at:%Y-%m-%dT%H:%M:%S%z}">{updated_at:%Y-%m-%d %H:%M:%S}</time>',
            html=True,
        )

    def test_empty_list_does_not_show_other_members_tickets(self):
        create_ticket(
            reporter=self.member_b, device_name='其他成员的公共打印机',
            location='实验室公共区', description='卡纸',
        )
        self.client.force_login(self.member_a)
        response = self.client.get(reverse('my-tickets'))
        self.assertContains(response, '你还没有提交过工单。')
        self.assertNotContains(response, '其他成员的公共打印机')

    def test_saved_assignee_is_displayed_without_granting_ownership(self):
        self.member_b.groups.add(Group.objects.create(name=HANDLER_GROUP))
        ticket = create_ticket(
            reporter=self.member_a, device_name='公共  打印机',
            location='实验室公共区', description='卡纸',
        )
        # 为读取测试构造已有跟进人的工单。
        ticket.assignee = self.member_b
        ticket.save(update_fields=['assignee'])
        self.client.force_login(self.member_a)
        url = reverse('my-tickets')
        response = self.client.get(url)
        self.assertContains(response, '<td class="ticket-device">公共  打印机</td>')
        self.assertContains(response, f'<td>{self.member_b.username}</td>', html=True)
        self.assertNotContains(response, '尚无跟进人')
        self.client.force_login(self.member_b)
        detail_url = reverse('ticket-detail', args=[ticket.pk])
        self.assertNotContains(self.client.get(url), f'href="{detail_url}"')

    def test_sorts_by_update_time_then_ticket_id(self):
        latest = datetime(2026, 11, 9, 8, tzinfo=UTC)
        with patch('repairs.services.timezone') as clock:
            clock.now.side_effect = [latest, latest - timedelta(days=1), latest]
            tickets = [
                create_ticket(reporter=self.member_a, device_name=device, location='实验室', description='故障')
                for device in ('公共打印机', '公共投影仪', '公共路由器')
            ]
        self.client.force_login(self.member_a)
        response = self.client.get(reverse('my-tickets'))
        self.assertEqual(list(response.context['tickets']), [tickets[2], tickets[0], tickets[1]])

    def test_anonymous_request_redirects_to_login(self):
        url = reverse('my-tickets')
        self.assertRedirects(self.client.get(url), f"{reverse('login')}?next={url}")


class TicketDetailTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        users = get_user_model().objects
        cls.reporter = users.create_user(username='detail_reporter')
        cls.other_member = users.create_user(username='detail_other_member')
        cls.ticket = create_ticket(
            reporter=cls.reporter, device_name='公共打印机 A',
            location='二楼实验室东侧', description='卡纸，面板显示 <E-101>',
        )

    def setUp(self):
        self.url = reverse('ticket-detail', args=[self.ticket.pk])

    def test_reporter_can_open_link_and_view_saved_fields(self):
        self.client.force_login(self.reporter)
        listing = self.client.get(reverse('my-tickets'))
        self.assertContains(listing, f'<a href="{self.url}">#{self.ticket.pk}</a>', html=True)

        response = self.client.get(self.url)

        self.assertContains(response, f'<h1>工单 #{self.ticket.pk}</h1>', html=True)
        for value in (self.ticket.device_name, self.ticket.location, self.ticket.description, '待接单'):
            self.assertContains(response, f'<dd>{escape(value)}</dd>', html=True)
        updated_at = timezone.localtime(self.ticket.updated_at)
        self.assertContains(
            response,
            f'<time datetime="{updated_at:%Y-%m-%dT%H:%M:%S%z}">{updated_at:%Y-%m-%d %H:%M:%S}</time>',
            html=True,
        )
        self.assertIn('no-store', response.headers['Cache-Control'])

    def test_other_member_gets_the_same_404_as_an_unknown_id(self):
        self.client.force_login(self.other_member)
        missing_url = reverse('ticket-detail', args=[self.ticket.pk + 1])
        updated_at = timezone.localtime(self.ticket.updated_at)
        for debug in (True, False):
            with self.subTest(debug=debug), self.settings(DEBUG=debug):
                denied = self.client.get(self.url)
                forged = self.client.get(self.url, {'reporter': self.reporter.pk})
                missing = self.client.get(missing_url)
                self.assertContains(denied, '这条工单不存在或你无权查看。', status_code=404)
                self.assertEqual(forged.status_code, 404)
                self.assertEqual(missing.status_code, 404)
                self.assertEqual(denied.content, forged.content)
                self.assertEqual(denied.content, missing.content)
                self.assertIn('no-store', denied.headers['Cache-Control'])
                self.assertTemplateNotUsed(denied, 'repairs/ticket_detail.html')
                with self.assertRaises(KeyError):
                    denied.context['ticket']
                for value in (
                    self.ticket.device_name, self.ticket.location, self.ticket.description,
                    f'工单 #{self.ticket.pk}', '待接单', f'{updated_at:%Y-%m-%d %H:%M:%S}',
                ):
                    self.assertNotContains(denied, escape(value), status_code=404)

    def test_anonymous_request_redirects_to_login(self):
        self.assertRedirects(self.client.get(self.url), f"{reverse('login')}?next={self.url}")


class HandlerQueueTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        users = get_user_model().objects
        cls.reporter = users.create_user(username='queue_reporter')
        cls.unrelated = users.create_user(username='queue_unrelated')
        cls.handler = users.create_user(username='queue_handler')
        cls.other_handler = users.create_user(username='queue_other_handler')
        group = Group.objects.create(name=HANDLER_GROUP)
        group.user_set.add(cls.handler, cls.other_handler)

        cls.pending = create_ticket(
            reporter=cls.reporter, device_name='待接单打印机',
            location='公共区', description='无法打印',
        )
        cls.assigned = create_ticket(
            reporter=cls.reporter, device_name='本人跟进投影仪',
            location='二楼会议室', description='无信号',
        )
        cls.assigned_elsewhere = create_ticket(
            reporter=cls.reporter, device_name='他人跟进路由器',
            location='机房', description='专用网络中断',
        )
        # 预置接单后的读取场景。
        for ticket, assignee in ((cls.assigned, cls.handler), (cls.assigned_elsewhere, cls.other_handler)):
            ticket.status = Ticket.Status.IN_PROGRESS
            ticket.assignee = assignee
            ticket.save(update_fields=['status', 'assignee'])

    def test_handler_queue_displays_only_the_correct_ticket_and_fields(self):
        self.client.force_login(self.handler)
        pending_url = reverse('handler-home')
        assigned_url = reverse('handler-assigned')
        self.assertContains(self.client.get(reverse('home')), f'href="{pending_url}"')
        for route, expected, handler_name in (
            (pending_url, self.pending, '尚无跟进人'),
            (assigned_url, self.assigned, self.handler.username),
        ):
            with self.subTest(route=route):
                response = self.client.get(route, {'assignee': self.other_handler.pk})
                self.assertContains(response, f'href="{pending_url}"')
                self.assertContains(response, f'href="{assigned_url}"')
                self.assertEqual(list(response.context['tickets']), [expected])
                detail_url = reverse('ticket-detail', args=[expected.pk])
                self.assertContains(response, f'<a href="{detail_url}">#{expected.pk}</a>', html=True)
                self.assertContains(response, expected.device_name)
                self.assertContains(response, f'<td>{expected.get_status_display()}</td>', html=True)
                self.assertContains(response, f'<td>{handler_name}</td>', html=True)
                self.assertContains(response, f'{timezone.localtime(expected.updated_at):%Y-%m-%d %H:%M:%S}')
                self.assertIn('no-store', response.headers['Cache-Control'])

    def test_handler_detail_matches_queue_scope(self):
        self.client.force_login(self.handler)
        for ticket, route in ((self.pending, 'handler-home'), (self.assigned, 'handler-assigned')):
            with self.subTest(ticket=ticket.pk):
                detail = self.client.get(reverse('ticket-detail', args=[ticket.pk]))
                self.assertContains(detail, ticket.device_name)
                self.assertContains(detail, ticket.description)
                self.assertContains(detail, f'href="{reverse(route)}"')
        denied = self.client.get(reverse('ticket-detail', args=[self.assigned_elsewhere.pk]))
        missing = self.client.get(reverse('ticket-detail', args=[self.assigned_elsewhere.pk + 1]))
        self.assertEqual(denied.status_code, 404)
        self.assertEqual(denied.content, missing.content)
        self.assertNotContains(denied, self.assigned_elsewhere.device_name, status_code=404)
        self.assertNotContains(denied, self.assigned_elsewhere.description, status_code=404)

    def test_non_handler_cannot_read_handler_queues(self):
        self.client.force_login(self.unrelated)
        for route in ('handler-home', 'handler-assigned'):
            with self.subTest(route=route):
                response = self.client.get(reverse(route))
                self.assertNotContains(response, self.pending.device_name, status_code=403)
        response = self.client.get(reverse('ticket-detail', args=[self.pending.pk]))
        self.assertNotContains(response, self.pending.device_name, status_code=404)

    def test_reporter_keeps_detail_access_after_another_user_is_assigned(self):
        self.client.force_login(self.reporter)
        detail = self.client.get(reverse('ticket-detail', args=[self.assigned_elsewhere.pk]))
        self.assertContains(detail, self.assigned_elsewhere.device_name)
        self.assertContains(detail, f'href="{reverse("my-tickets")}"')

    def test_handler_who_is_also_reporter_can_return_to_either_list(self):
        self.client.force_login(self.handler)
        ticket = create_ticket(
            reporter=self.handler, device_name='负责人报修打印机',
            location='公共区', description='卡纸',
        )
        detail_url = reverse('ticket-detail', args=[ticket.pk])
        personal_url = reverse('my-tickets')
        pending = self.client.get(detail_url)
        self.assertContains(pending, f'href="{personal_url}"')
        self.assertContains(pending, f'href="{reverse("handler-home")}"')

        Ticket.objects.filter(pk=ticket.pk).update(status=Ticket.Status.IN_PROGRESS, assignee=self.handler)
        assigned = self.client.get(detail_url)
        self.assertContains(assigned, f'href="{personal_url}"')
        self.assertContains(assigned, f'href="{reverse("handler-assigned")}"')

    def test_pending_queue_paginates_and_bounds_preview_of_legacy_names(self):
        updated_at = timezone.now()
        older = [
            Ticket(reporter=self.reporter, device_name=f'历史设备-{index}', updated_at=updated_at)
            for index in range(25)
        ]
        legacy = Ticket(reporter=self.reporter, device_name='长' * 160 + '详情尾部', updated_at=updated_at)
        Ticket.objects.bulk_create([*older, legacy])
        self.client.force_login(self.handler)
        url = reverse('handler-home')
        first_page = self.client.get(url)
        self.assertEqual(len(first_page.context['tickets']), 25)
        self.assertContains(first_page, '?page=2')
        self.assertNotContains(first_page, '详情尾部')
        self.assertContains(first_page, '长' * 157)
        self.assertNotContains(first_page, '长' * 161)
        second_page = self.client.get(url, {'page': 2})
        self.assertEqual(len(second_page.context['tickets']), 2)
        self.assertContains(self.client.get(reverse('ticket-detail', args=[legacy.pk])), '详情尾部')


class ClaimTicketTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        users = get_user_model().objects
        cls.reporter = users.create_user(username='claim_reporter')
        cls.handler = users.create_user(username='claim_handler')
        cls.other_handler = users.create_user(username='claim_other_handler')
        group = Group.objects.create(name=HANDLER_GROUP)
        group.user_set.add(cls.handler, cls.other_handler)
        cls.ticket = create_ticket(
            reporter=cls.reporter, device_name='待接单打印机',
            location='公共区', description='卡纸',
        )

    def setUp(self):
        self.url = reverse('claim-repair', args=[self.ticket.pk])
        self.detail_url = reverse('ticket-detail', args=[self.ticket.pk])

    def test_claim_updates_ticket_event_queues_and_reporter_detail(self):
        browser = Client(enforce_csrf_checks=True)
        browser.force_login(self.handler)
        detail = browser.get(self.detail_url)
        self.assertContains(detail, f'action="{self.url}"')
        self.assertContains(detail, f'name="version" value="{self.ticket.version}"')
        self.assertEqual(browser.post(self.url, {'version': self.ticket.version}).status_code, 403)

        updated_at = datetime(2026, 11, 9, 9, tzinfo=UTC)
        with patch('repairs.services.timezone') as clock:
            clock.now.return_value = updated_at
            response = browser.post(self.url, {
                'version': self.ticket.version,
                'csrfmiddlewaretoken': browser.cookies['csrftoken'].value,
                'assignee': self.other_handler.pk,
            }, follow=True)

        self.assertEqual(response.redirect_chain, [(reverse('handler-assigned'), 302)])
        self.assertContains(response, '接单成功。')
        self.assertContains(response, self.ticket.device_name)
        self.assertNotContains(browser.get(reverse('handler-home')), self.ticket.device_name)
        current = Ticket.objects.get(pk=self.ticket.pk)
        self.assertEqual((current.status, current.assignee, current.version, current.updated_at), (
            Ticket.Status.IN_PROGRESS, self.handler, self.ticket.version + 1, updated_at,
        ))
        self.assertEqual(list(current.events.values_list('action', flat=True)), [
            TicketEvent.Action.CREATED, TicketEvent.Action.CLAIMED,
        ])
        event = current.events.get(action=TicketEvent.Action.CLAIMED)
        self.assertEqual((event.actor, event.version, event.occurred_at), (
            self.handler, current.version, updated_at,
        ))

        self.client.force_login(self.reporter)
        detail = self.client.get(self.detail_url)
        self.assertContains(detail, '<dd>处理中</dd>', html=True)
        self.assertContains(detail, f'<dd>{self.handler.username}</dd>', html=True)
        self.assertNotContains(detail, f'action="{self.url}"')

    def test_old_version_cannot_claim_again_or_change_the_ticket(self):
        self.client.force_login(self.handler)
        self.assertRedirects(self.client.post(self.url, {'version': self.ticket.version}), reverse('handler-assigned'))
        current = Ticket.objects.get(pk=self.ticket.pk)
        self.client.force_login(self.other_handler)

        response = self.client.post(self.url, {'version': self.ticket.version})

        self.assertContains(response, '请刷新待接单列表', status_code=409)
        self.assertNotContains(response, self.ticket.device_name, status_code=409)
        self.assert_ticket_unchanged(current, event_count=2)

    def test_other_user_cannot_claim_and_get_does_not_write(self):
        self.client.force_login(self.reporter)
        self.assertEqual(self.client.post(self.url, {'version': self.ticket.version}).status_code, 403)
        self.client.force_login(self.handler)
        self.assertEqual(self.client.get(self.url).status_code, 405)
        self.assert_ticket_unchanged(self.ticket, event_count=1)

    def test_invalid_and_mismatching_versions_cannot_claim_pending_ticket(self):
        self.client.force_login(self.handler)
        self.assertEqual(self.client.post(self.url).status_code, 400)
        self.assertEqual(self.client.post(self.url, {'version': self.ticket.version + 1}).status_code, 409)
        self.assert_ticket_unchanged(self.ticket, event_count=1)

    def test_claim_event_failure_rolls_back_update(self):
        self.client.force_login(self.handler)
        with patch('repairs.services.TicketEvent.objects.create', side_effect=IntegrityError('event insert failed')):
            with self.assertRaises(IntegrityError):
                self.client.post(self.url, {'version': self.ticket.version})
        self.assert_ticket_unchanged(self.ticket, event_count=1)

    def assert_ticket_unchanged(self, original, *, event_count):
        current = Ticket.objects.get(pk=original.pk)
        self.assertEqual((current.status, current.assignee_id, current.version, current.updated_at), (
            original.status, original.assignee_id, original.version, original.updated_at,
        ))
        self.assertEqual(current.events.count(), event_count)
