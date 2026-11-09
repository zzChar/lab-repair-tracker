import sqlite3
from contextlib import contextmanager

from django.core.exceptions import PermissionDenied
from django.db import OperationalError, transaction
from django.utils import timezone

from accounts.roles import is_handler

from .models import Ticket, TicketEvent
from .queries import tickets_assigned_to, tickets_pending_assignment, tickets_reported_by


class TicketWriteUnavailable(Exception):
    """工单数据库暂时无法写入。"""


class TicketWriteConflict(Exception):
    """工单状态或版本已不符合本次写入的条件。"""


def is_sqlite_lock_error(error):
    sqlite_code = getattr(error.__cause__, 'sqlite_errorcode', 0) & 0xFF
    return sqlite_code in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED)


@contextmanager
def _ticket_transaction():
    try:
        with transaction.atomic():
            yield
    except OperationalError as exc:
        if is_sqlite_lock_error(exc):
            raise TicketWriteUnavailable from exc
        raise


def create_ticket(*, reporter, device_name, location, description, submission_id=None):
    with _ticket_transaction():
        if submission_id is not None:
            existing = Ticket.objects.filter(reporter=reporter, submission_id=submission_id).first()
            if existing is not None:
                if (existing.device_name, existing.location, existing.description) != (
                    device_name, location, description,
                ):
                    raise TicketWriteConflict
                return existing
        now = timezone.now()
        ticket = Ticket.objects.create(
            reporter=reporter,
            device_name=device_name,
            location=location,
            description=description,
            submission_id=submission_id,
            updated_at=now,
        )
        TicketEvent.objects.create(
            ticket=ticket, actor=reporter, action=TicketEvent.Action.CREATED,
            version=ticket.version, occurred_at=now,
        )
    return ticket


def claim_ticket(*, ticket_id, handler, expected_version):
    with _ticket_transaction():
        if not is_handler(handler):
            raise PermissionDenied

        _change_ticket(
            tickets_pending_assignment(), ticket_id=ticket_id, expected_version=expected_version,
            actor=handler, action=TicketEvent.Action.CLAIMED,
            assignee=handler,
            status=Ticket.Status.IN_PROGRESS,
        )


def append_progress(*, ticket_id, actor, expected_version, content):
    _update_handling(
        ticket_id=ticket_id, actor=actor, expected_version=expected_version,
        action=TicketEvent.Action.PROGRESS, content=content,
    )


def submit_result(*, ticket_id, actor, expected_version, content):
    _update_handling(
        ticket_id=ticket_id, actor=actor, expected_version=expected_version,
        action=TicketEvent.Action.SUBMITTED, content=content,
        status=Ticket.Status.AWAITING_CONFIRMATION,
    )


def _update_handling(*, ticket_id, actor, expected_version, action, content, **changes):
    with _ticket_transaction():
        owned = tickets_assigned_to(actor).filter(pk=ticket_id)
        if not is_handler(actor) or not owned.exists():
            raise PermissionDenied
        _change_ticket(
            owned.filter(status=Ticket.Status.IN_PROGRESS), ticket_id=ticket_id,
            expected_version=expected_version, actor=actor, action=action, content=content, **changes,
        )


def confirm_closed(*, ticket_id, actor, expected_version):
    _resolve_confirmation(
        ticket_id=ticket_id, actor=actor, expected_version=expected_version,
        action=TicketEvent.Action.CLOSED, status=Ticket.Status.CLOSED,
    )


def return_for_repair(*, ticket_id, actor, expected_version, content):
    _resolve_confirmation(
        ticket_id=ticket_id, actor=actor, expected_version=expected_version,
        action=TicketEvent.Action.RETURNED, status=Ticket.Status.IN_PROGRESS, content=content,
    )


def _resolve_confirmation(*, ticket_id, actor, expected_version, action, status, content=''):
    with _ticket_transaction():
        owned = tickets_reported_by(actor).filter(pk=ticket_id)
        if not owned.exists():
            raise PermissionDenied
        _change_ticket(
            owned.filter(status=Ticket.Status.AWAITING_CONFIRMATION), ticket_id=ticket_id,
            expected_version=expected_version, actor=actor, action=action, status=status, content=content,
        )


def _change_ticket(eligible, *, ticket_id, expected_version, actor, action, content='', **changes):
    now = timezone.now()
    next_version = expected_version + 1
    updated = eligible.filter(pk=ticket_id, version=expected_version).update(
        version=next_version, updated_at=now, **changes,
    )
    if updated != 1:
        raise TicketWriteConflict
    TicketEvent.objects.create(
        ticket_id=ticket_id, actor=actor, action=action, content=content,
        version=next_version, occurred_at=now,
    )
