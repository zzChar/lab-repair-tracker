from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from accounts.roles import HANDLER_GROUP, is_handler


DEMO_ACCOUNTS = (
    ('demo_member', 'LabDemo-Member-2026!', False),
    ('demo_handler', 'LabDemo-Handler-2026!', True),
)


class Command(BaseCommand):
    help = '创建本地演示用的普通成员和负责人账号。'

    @transaction.atomic
    def handle(self, *args, **options):
        users = get_user_model().objects
        handler_group, _ = Group.objects.get_or_create(name=HANDLER_GROUP)
        if handler_group.permissions.exists():
            raise CommandError('负责人组已有额外权限，请核对本地演示配置。')

        created_names = []
        for username, password, needs_handler_role in DEMO_ACCOUNTS:
            user, created = users.get_or_create(username=username)
            if created:
                user.set_password(password)
                user.save(update_fields=['password'])
                if needs_handler_role:
                    user.groups.add(handler_group)
                created_names.append(username)
            elif (
                not user.is_active
                or not user.has_usable_password()
                or user.is_staff
                or user.is_superuser
                or is_handler(user) != needs_handler_role
            ):
                raise CommandError(f'现有账号 {username} 的状态或角色与演示配置冲突。')

        if created_names:
            self.stdout.write(self.style.SUCCESS('已创建 ' + '、'.join(created_names)))
        else:
            self.stdout.write('演示账号已存在。')
