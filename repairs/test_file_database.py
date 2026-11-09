import re
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier, Event
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management import call_command
from django.db import IntegrityError, connections
from django.db.migrations.executor import MigrationExecutor
from django.test import Client, SimpleTestCase
from django.urls import reverse
from django.utils import timezone

from accounts.roles import HANDLER_GROUP

from . import services
from .models import Ticket, TicketEvent


class FileDatabaseAcceptanceTests(SimpleTestCase):
    def test_concurrent_outcomes_and_failed_writes(self):
        # 子进程单独配置真实文件数据库，不影响 Django 默认的内存测试库。
        runner = """
import os
import sys
os.environ['DJANGO_SETTINGS_MODULE'] = 'lab_repair.settings'
from django.conf import settings
settings.DATABASES['default']['NAME'] = sys.argv[1]
settings.DATABASES['default']['OPTIONS']['timeout'] = 0.5
import django
django.setup()
from repairs.test_file_database import verify_file_database
verify_file_database()
"""
        with TemporaryDirectory() as temp:
            result = subprocess.run(
                [sys.executable, '-E', '-c', runner, str(Path(temp) / 'workflow.sqlite3')],
                cwd=settings.BASE_DIR, capture_output=True, text=True, encoding='utf-8',
                errors='replace', timeout=45,
            )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('close wins; return wins; rollback; locked write', result.stdout)


def verify_file_database():
    _migrate_legacy_ticket()
    users = get_user_model().objects
    reporter = users.create_user(username='file_reporter')
    handler = users.create_user(username='file_handler')
    Group.objects.create(name=HANDLER_GROUP).user_set.add(handler)

    def waiting_ticket():
        ticket = services.create_ticket(
            reporter=reporter, device_name='公共打印机', location='公共区', description='卡纸',
        )
        services.claim_ticket(ticket_id=ticket.pk, handler=handler, expected_version=1)
        services.submit_result(ticket_id=ticket.pk, actor=handler, expected_version=2, content='请试打')
        ticket.refresh_from_db()
        return ticket

    try:
        _verify_concurrent_submission_retry(reporter)
        for winner in ('close', 'return'):
            _verify_concurrent_requests(waiting_ticket(), reporter, handler, winner)
        _verify_failed_writes(waiting_ticket(), reporter)
    finally:
        connections.close_all()
    print('close wins; return wins; rollback; locked write')


def _verify_concurrent_submission_retry(reporter):
    url = reverse('submit-repair')
    clients = [
        Client(enforce_csrf_checks=True, raise_request_exception=False, HTTP_HOST='localhost')
        for _ in range(2)
    ]
    forms = []
    for client in clients:
        client.force_login(reporter)
        form = client.get(url)
        assert form.status_code == 200
        forms.append(form)
    token = re.search(r'name="submission_id" value="([^"]+)"', forms[0].content.decode())
    assert token is not None
    submission_id = token[1]
    assert clients[0].session.session_key != clients[1].session.session_key
    gate = Barrier(2, timeout=4)

    def send(client):
        try:
            gate.wait()
            return client.post(url, {
                'device_name': '并发重试打印机', 'location': '公共区', 'description': '卡纸',
                'submission_id': submission_id,
                'csrfmiddlewaretoken': client.cookies['csrftoken'].value,
            }).status_code
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(send, clients)) == [302, 302]
    ticket = Ticket.objects.get(reporter=reporter, submission_id=submission_id)
    assert list(ticket.events.values_list('action', flat=True)) == [TicketEvent.Action.CREATED]


def _migrate_legacy_ticket():
    call_command('migrate', 'auth', interactive=False, verbosity=0)
    call_command('migrate', 'repairs', '0003', interactive=False, verbosity=0)
    old = MigrationExecutor(connections['default']).loader.project_state(('repairs', '0003_alter_ticketevent_action'))
    old_ticket = old.apps.get_model('repairs', 'Ticket')
    old_event = old.apps.get_model('repairs', 'TicketEvent')
    reporter = get_user_model().objects.create_user(username='legacy_reporter')
    stamp = timezone.now()
    ticket = old_ticket.objects.create(
        reporter_id=reporter.pk, device_name='历史工单', location='公共区',
        description='卡纸', updated_at=stamp,
    )
    event = old_event.objects.create(
        ticket_id=ticket.pk, actor_id=reporter.pk, action='created', version=1, occurred_at=stamp,
    )
    call_command('migrate', interactive=False, verbosity=0)
    upgraded = Ticket.objects.get(pk=ticket.pk)
    assert upgraded.submission_id is None and upgraded.device_name == '历史工单'
    assert upgraded.updated_at == stamp and upgraded.reporter_id == reporter.pk
    assert list(upgraded.events.values_list('pk', 'action', 'version', 'actor_id', 'occurred_at', 'content')) == [
        (event.pk, 'created', 1, reporter.pk, stamp, ''),
    ]


def _signed_request(ticket, reporter, action):
    client = Client(enforce_csrf_checks=True, raise_request_exception=False, HTTP_HOST='localhost')
    client.force_login(reporter)
    detail = client.get(reverse('ticket-detail', args=[ticket.pk]))
    assert detail.status_code == 200
    url = reverse(f'repair-{action}', args=[ticket.pk])
    data = {'version': ticket.version, 'csrfmiddlewaretoken': client.cookies['csrftoken'].value}
    if action == 'return':
        data['content'] = '试打仍卡纸'
    return client, url, data


def _verify_concurrent_requests(ticket, reporter, handler, winner):
    loser = 'return' if winner == 'close' else 'close'
    requests = {action: _signed_request(ticket, reporter, action) for action in (winner, loser)}
    assert requests[winner][0].session.session_key != requests[loser][0].session.session_key
    winner_action = TicketEvent.Action.CLOSED if winner == 'close' else TicketEvent.Action.RETURNED
    saved = Event()
    competitor_started = Event()
    change_ticket = services._change_ticket
    handles = {}

    def hold_first_write(*args, **kwargs):
        change_ticket(*args, **kwargs)
        if kwargs['action'] == winner_action:
            saved.set()
            assert competitor_started.wait(4), 'competing connection did not attempt a write'

    def post(action):
        connection = connections['default']

        def observe_begin(execute, sql, params, many, context):
            if sql == 'BEGIN IMMEDIATE':
                handles[action] = connection.connection
                if action == loser:
                    _assert_write_lock_held(connection.connection)
                    competitor_started.set()
            return execute(sql, params, many, context)

        try:
            client, url, data = requests[action]
            with connection.execute_wrapper(observe_begin):
                return client.post(url, data)
        finally:
            connection.close()

    with patch('repairs.services._change_ticket', side_effect=hold_first_write):
        with ThreadPoolExecutor(max_workers=2) as pool:
            winning_request = pool.submit(post, winner)
            assert saved.wait(5), 'first request did not reach event insertion'
            losing_request = pool.submit(post, loser)
            won = winning_request.result(timeout=6)
            lost = losing_request.result(timeout=6)

    assert handles[winner] is not handles[loser]
    assert (won.status_code, lost.status_code) == (302, 409), (won.status_code, lost.status_code)
    assert '工单已变化' in lost.content.decode()
    before_version = ticket.version
    ticket.refresh_from_db()
    expected_status = Ticket.Status.CLOSED if winner == 'close' else Ticket.Status.IN_PROGRESS
    assert (ticket.status, ticket.assignee_id, ticket.version) == (expected_status, handler.pk, before_version + 1)
    events = list(ticket.events.filter(action__in=(TicketEvent.Action.CLOSED, TicketEvent.Action.RETURNED)))
    assert len(events) == 1
    event = events[0]
    assert (event.action, event.actor_id, event.version, event.occurred_at) == (
        winner_action, reporter.pk, ticket.version, ticket.updated_at,
    )
    assert event.content == ('试打仍卡纸' if winner == 'return' else '')


def _assert_write_lock_held(connection):
    # 先在同一个竞争连接上做一次非阻塞探测，确认已碰到先手持有的真实写锁。
    previous_timeout, = connection.execute('PRAGMA busy_timeout').fetchone()
    connection.execute('PRAGMA busy_timeout = 0')
    try:
        try:
            connection.execute('BEGIN IMMEDIATE')
        except sqlite3.OperationalError as error:
            assert error.sqlite_errorcode & 0xFF == sqlite3.SQLITE_BUSY
        else:
            connection.rollback()
            raise AssertionError('first connection did not hold a write lock')
    finally:
        connection.execute(f'PRAGMA busy_timeout = {previous_timeout}')


def _verify_failed_writes(ticket, reporter):
    client, url, data = _signed_request(ticket, reporter, 'close')
    with closing(sqlite3.connect(settings.DATABASES['default']['NAME'])) as observer:
        def snapshot():
            return (
                observer.execute('SELECT * FROM repairs_ticket WHERE id = ?', (ticket.pk,)).fetchall(),
                observer.execute(
                    'SELECT * FROM repairs_ticketevent WHERE ticket_id = ? ORDER BY version', (ticket.pk,),
                ).fetchall(),
            )

        original = snapshot()
        with patch('repairs.services.TicketEvent.objects.create', side_effect=IntegrityError('injected')):
            failed = client.post(url, data)
        assert failed.status_code == 500
        assert snapshot() == original

        for lock in ('BEGIN IMMEDIATE', 'BEGIN EXCLUSIVE'):
            observer.execute(lock)
            try:
                response = client.post(url, data)
                assert response.status_code == 503 and '暂时无法更新工单' in response.content.decode(), lock
            finally:
                observer.rollback()
            assert snapshot() == original
