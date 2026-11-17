from django.core.paginator import Paginator
from django.db.models.functions import Substr

from .models import DEVICE_NAME_LIMIT, Ticket, TicketEvent


def tickets_reported_by(member):
    return Ticket.objects.filter(reporter=member)


def tickets_pending_assignment():
    return Ticket.objects.filter(status=Ticket.Status.PENDING, assignee__isnull=True)


def tickets_assigned_to(handler):
    return Ticket.objects.filter(assignee=handler)


def tickets_visible_to(member, *, include_handler_queues):
    reported = tickets_reported_by(member)
    if include_handler_queues:
        return reported | tickets_pending_assignment() | tickets_assigned_to(member)
    return reported


def ticket_history(ticket):
    # 历史按已读工单版本截断，避免把随后提交的事件配到旧状态上。
    events = list(ticket.events.filter(version__lte=ticket.version).select_related('actor'))
    latest_progress = next((event for event in reversed(events) if event.action == TicketEvent.Action.PROGRESS), None)
    return events, latest_progress


def ticket_list_page(tickets, page_number):
    rows = (
        tickets.select_related('assignee')
        .only('status', 'updated_at', 'assignee__username')
        .annotate(device_preview=Substr('device_name', 1, DEVICE_NAME_LIMIT + 1))
        .order_by('-updated_at', '-pk')
    )
    return Paginator(rows, 25).get_page(page_number)
