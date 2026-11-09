from io import StringIO

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client, TestCase
from django.urls import reverse

from .management.commands.seed_demo_accounts import DEMO_ACCOUNTS
from .roles import HANDLER_GROUP, is_handler


class DemoAccountSeedTests(TestCase):
    def test_seeding_twice_preserves_existing_password_and_roles(self):
        call_command('seed_demo_accounts', stdout=StringIO())
        users = get_user_model().objects
        member = users.get(username='demo_member')
        member.set_password('Local-Changed-Passphrase!')
        member.save(update_fields=['password'])

        call_command('seed_demo_accounts', stdout=StringIO())

        member.refresh_from_db()
        self.assertTrue(member.check_password('Local-Changed-Passphrase!'))
        self.assertFalse(is_handler(member))
        self.assertTrue(is_handler(users.get(username='demo_handler')))
        self.assertEqual(users.count(), 2)

    def test_conflicting_username_rolls_back_seed_without_escalating_user(self):
        users = get_user_model().objects
        existing = users.create_user(username='demo_handler', password='Existing-Password!')

        with self.assertRaises(CommandError):
            call_command('seed_demo_accounts', stdout=StringIO())

        self.assertFalse(is_handler(existing))
        self.assertFalse(users.filter(username='demo_member').exists())

    def test_seed_rejects_unusable_demo_account(self):
        get_user_model().objects.create_user(username='demo_member')

        with self.assertRaises(CommandError):
            call_command('seed_demo_accounts', stdout=StringIO())

        self.assertFalse(get_user_model().objects.filter(username='demo_handler').exists())

    def test_seed_rejects_handler_group_with_extra_permissions(self):
        group = Group.objects.create(name=HANDLER_GROUP)
        group.permissions.add(Permission.objects.get(content_type__app_label='auth', codename='delete_user'))

        with self.assertRaises(CommandError):
            call_command('seed_demo_accounts', stdout=StringIO())

        self.assertFalse(get_user_model().objects.exists())


class LoginTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command('seed_demo_accounts', stdout=StringIO())

    def test_anonymous_users_are_redirected_to_login(self):
        for endpoint in ('home', 'handler-home'):
            with self.subTest(endpoint=endpoint):
                url = reverse(endpoint)
                self.assertRedirects(self.client.get(url), f"{reverse('login')}?next={url}")
        self.assertContains(self.client.get(reverse('login')), '登录实验室报修跟踪')

    def test_real_login_sets_session_and_enforces_handler_permission(self):
        for username, password, handler in DEMO_ACCOUNTS:
            with self.subTest(username=username):
                response = self.client.post(reverse('login'), {'username': username, 'password': password})
                self.assertRedirects(response, reverse('home'))
                home = self.client.get(reverse('home'))
                self.assertEqual(home.wsgi_request.user.get_username(), username)
                self.assertEqual(is_handler(home.wsgi_request.user), handler)
                self.assertContains(home, '负责人' if handler else '普通成员')
                self.assertContains(self.client.get(reverse('login')), f'已登录为 <strong>{username}</strong>')
                self.assertEqual(self.client.get(reverse('handler-home')).status_code, 200 if handler else 403)
                self.assertRedirects(self.client.post(reverse('logout')), reverse('login'))
                self.assertEqual(self.client.get(reverse('home')).status_code, 302)

    def test_incorrect_password_does_not_create_authenticated_session(self):
        response = self.client.post(reverse('login'), {'username': 'demo_member', 'password': 'incorrect'})
        self.assertTrue(response.context['form'].errors)
        self.assertEqual(self.client.get(reverse('home')).status_code, 302)

    def test_login_from_handler_link_still_requires_csrf_and_handler_role(self):
        browser = Client(enforce_csrf_checks=True)
        target = reverse('handler-home')
        login_url = f"{reverse('login')}?next={target}"
        form = {'username': 'demo_member', 'password': DEMO_ACCOUNTS[0][1], 'next': target}
        self.assertEqual(browser.get(target).url, login_url)
        self.assertEqual(browser.get(login_url).status_code, 200)
        self.assertEqual(browser.post(login_url, form).status_code, 403)

        form['csrfmiddlewaretoken'] = browser.cookies['csrftoken'].value
        response = browser.post(login_url, form, follow=True)

        self.assertEqual(response.redirect_chain, [(target, 302)])
        self.assertContains(response, '此入口仅供负责人使用。', status_code=403)
        self.assertContains(response, '返回首页', status_code=403)
