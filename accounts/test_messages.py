import random
import string
from unittest.mock import patch

from django.contrib import messages
from django.contrib.auth import get_user_model, login, logout
from django.contrib.messages.middleware import MessageMiddleware
from django.contrib.messages.storage import default_storage
from django.contrib.sessions.backends.db import SessionStore
from django.contrib.sessions.models import Session
from django.db import OperationalError
from django.http import HttpResponse
from django.test import Client, RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone


class ReceiptStorageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.member = get_user_model().objects.create_user(username='receipt-member')
        cls.other = get_user_model().objects.create_user(username='receipt-other')

    def submit(self):
        return self.client.post(reverse('submit-repair'), {
            'device_name': 'Printer', 'location': 'Lab', 'description': 'Jam',
        })

    def setUp(self):
        self.client.force_login(self.member)

    def test_receipt_needs_no_session_save_and_is_consumed(self):
        with patch.object(SessionStore, 'save', side_effect=OperationalError('injected')) as save:
            response = self.submit()
            self.assertEqual(response.status_code, 302)
            self.assertContains(self.client.get(response.url), '报修已提交')
            self.assertNotContains(self.client.get(response.url), '报修已提交')
        save.assert_not_called()

    def test_unread_cookie_is_rejected_after_account_switch_or_relogin(self):
        for user in (self.other, self.member):
            with self.subTest(user=user.username):
                self.client.force_login(self.member)
                self.submit()
                cookie = self.client.cookies['messages'].value
                self.client.post(reverse('logout'))
                self.client.force_login(user)
                self.client.cookies['messages'] = cookie
                self.assertNotContains(self.client.get(reverse('home')), '报修已提交')

    def test_direct_account_switch_rejects_receipt(self):
        self.submit()
        self.client.force_login(self.other)
        self.assertNotContains(self.client.get(reverse('home')), '报修已提交')

    def test_two_active_sessions_for_same_account_do_not_share_receipts(self):
        self.submit()
        other_session = Client()
        other_session.force_login(self.member)
        self.assertNotEqual(self.client.session.session_key, other_session.session.session_key)
        other_session.cookies['messages'] = self.client.cookies['messages'].value
        self.assertNotContains(other_session.get(reverse('home')), '报修已提交')
        self.assertContains(self.client.get(reverse('home')), '报修已提交')

    def test_invalid_old_cookie_does_not_discard_new_receipt(self):
        for old_session in (False, True):
            with self.subTest(old_session=old_session):
                self.submit()
                cookie = self.client.cookies['messages'].value
                if old_session:
                    self.client.post(reverse('logout'))
                    self.client.force_login(self.member)
                else:
                    cookie = 'invalid-' + cookie
                self.client.cookies['messages'] = cookie
                response = self.submit()
                self.assertEqual(response.status_code, 302)
                self.assertContains(self.client.get(response.url), '报修已提交', count=1)
                self.assertNotContains(self.client.get(response.url), '报修已提交')

    def test_tampered_cookie_is_rejected(self):
        self.submit()
        cookie = self.client.cookies['messages'].value
        self.client.cookies['messages'] = ('a' if cookie[0] != 'a' else 'b') + cookie[1:]
        self.assertNotContains(self.client.get(reverse('home')), '报修已提交')

    def test_expired_session_cannot_read_receipt_on_login_page(self):
        self.submit()
        Session.objects.filter(session_key=self.client.session.session_key).update(expire_date=timezone.now())
        self.assertNotContains(self.client.get(reverse('login')), '报修已提交')
        self.client.force_login(self.member)
        self.assertNotContains(self.client.get(reverse('home')), '报修已提交')

    def request_with_storage(self):
        request = RequestFactory().get('/')
        request.session = self.client.session
        request.user = self.member
        request._messages = default_storage(request)
        return request

    def test_storage_does_not_query_or_modify_loaded_login_session(self):
        request = self.request_with_storage()
        request.session.modified = False
        with self.assertNumQueries(0):
            messages.success(request, 'receipt')
            request._messages.update(HttpResponse())
        self.assertFalse(request.session.modified)

    def test_rotation_discards_loaded_and_queued_old_receipts(self):
        for read_after_rotation in (False, True):
            with self.subTest(read_after_rotation=read_after_rotation):
                request = self.request_with_storage()
                messages.success(request, 'old receipt')
                list(request._messages)
                messages.success(request, 'old queued receipt')
                logout(request)
                login(request, self.other)
                if read_after_rotation:
                    self.assertEqual(list(request._messages), [])
                    messages.success(request, 'new receipt')
                response = HttpResponse()
                request._messages.update(response)
                request.COOKIES['messages'] = response.cookies['messages'].value
                request._messages = default_storage(request)
                expected = ['new receipt'] if read_after_rotation else []
                self.assertEqual([str(message) for message in request._messages], expected)

    @override_settings(DEBUG=True)
    def test_overflow_keeps_newest_hint_without_response_error(self):
        request = self.request_with_storage()
        rng = random.Random(42)
        for _ in range(30):
            messages.info(request, ''.join(rng.choices(string.ascii_letters, k=300)))
        messages.success(request, 'newest receipt')
        response = MessageMiddleware(lambda request: HttpResponse()).process_response(request, HttpResponse())
        cookie = response.cookies['messages'].value
        self.assertLessEqual(len(cookie), 2048)
        request.COOKIES['messages'] = cookie
        request._messages = default_storage(request)
        stored = [str(message) for message in request._messages]
        self.assertEqual(stored[-1], 'newest receipt')
        self.assertLess(len(stored), 31)

        # 连续提交时，下一次响应还需合并旧 Cookie 与新队列。
        request._messages = default_storage(request)
        messages.success(request, 'next request receipt')
        response = MessageMiddleware(lambda request: HttpResponse()).process_response(request, HttpResponse())
        self.assertLessEqual(len(response.cookies['messages'].value), 2048)
        request.COOKIES['messages'] = response.cookies['messages'].value
        request._messages = default_storage(request)
        self.assertEqual(str(list(request._messages)[-1]), 'next request receipt')

    @override_settings(DEBUG=True)
    def test_single_oversized_hint_does_not_raise(self):
        request = self.request_with_storage()
        rng = random.Random(43)
        messages.info(request, ''.join(rng.choices(string.ascii_letters, k=10000)))
        response = MessageMiddleware(lambda request: HttpResponse()).process_response(request, HttpResponse())
        self.assertEqual(response.status_code, 200)
        self.assertLessEqual(len(response.cookies['messages'].value), 2048)
