from django.conf import settings
from django.db import models


DEVICE_NAME_LIMIT = 160
LOCATION_LIMIT = 240
NOTE_LIMIT = 4000


class Ticket(models.Model):
    class Status(models.TextChoices):
        PENDING = 'pending', '待接单'
        IN_PROGRESS = 'in_progress', '处理中'
        AWAITING_CONFIRMATION = 'awaiting_confirm', '待确认'
        CLOSED = 'closed', '已关闭'

    device_name = models.TextField('设备名称', max_length=DEVICE_NAME_LIMIT)
    location = models.TextField('位置', max_length=LOCATION_LIMIT)
    description = models.TextField('故障描述', max_length=NOTE_LIMIT)
    reporter = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='reported_tickets',
    )
    assignee = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='assigned_tickets',
        null=True, blank=True,
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    version = models.PositiveIntegerField(default=1)
    updated_at = models.DateTimeField()
    submission_id = models.UUIDField(null=True, editable=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['reporter', 'submission_id'], name='unique_report_submission'),
        ]


class TicketEvent(models.Model):
    class Action(models.TextChoices):
        CREATED = 'created', '创建'
        CLAIMED = 'claimed', '接单'
        PROGRESS = 'progress', '更新进度'
        SUBMITTED = 'submitted', '提交验收'
        CLOSED = 'closed', '确认关闭'
        RETURNED = 'returned', '验收失败退回'

    ticket = models.ForeignKey(Ticket, on_delete=models.PROTECT, related_name='events')
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    action = models.CharField(max_length=20, choices=Action.choices)
    content = models.TextField(blank=True, default='')
    version = models.PositiveIntegerField()
    occurred_at = models.DateTimeField()

    class Meta:
        ordering = ['version']
        constraints = [
            models.UniqueConstraint(fields=['ticket', 'version'], name='one_event_per_ticket_version'),
        ]
