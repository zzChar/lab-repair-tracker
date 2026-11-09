from functools import wraps
from uuid import uuid4

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import OperationalError
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods, require_POST, require_safe

from accounts.roles import is_handler

from .forms import TicketNoteForm, TicketSubmissionForm, TicketVersionForm
from .models import NOTE_LIMIT, TicketEvent
from .queries import ticket_list_page, tickets_reported_by, tickets_visible_to
from .services import (
    TicketWriteConflict, TicketWriteUnavailable, append_progress, claim_ticket,
    confirm_closed, create_ticket, is_sqlite_lock_error, return_for_repair, submit_result,
)


TICKET_ACTIONS = {
    'progress': (TicketNoteForm, append_progress, '处理进度已更新。'),
    'submit-result': (TicketNoteForm, submit_result, '已提交验收，请报修人确认。'),
    'close': (TicketVersionForm, confirm_closed, '工单已关闭。'),
    'return': (TicketNoteForm, return_for_repair, '已退回原负责人继续处理。'),
}


def _handle_locked_database(view):
    @wraps(view)
    def guarded(request, *args, **kwargs):
        try:
            return view(request, *args, **kwargs)
        except OperationalError as error:
            if not is_sqlite_lock_error(error):
                raise
            # 会话读取也可能被 SQLite 排他锁阻塞，此时尚未进入工单服务。
            return render(request, 'repairs/ticket_action_error.html', {
                'message': '暂时无法更新工单，请稍后重试。',
            }, status=503)
    return guarded


@never_cache
@_handle_locked_database
@login_required
@require_http_methods(['GET', 'HEAD', 'POST'])
def submit_repair(request):
    form = TicketSubmissionForm(request.POST) if request.method == 'POST' else TicketSubmissionForm(
        initial={'submission_id': uuid4()},
    )
    if request.method == 'POST' and form.is_valid():
        try:
            ticket = create_ticket(reporter=request.user, **form.cleaned_data)
        except TicketWriteUnavailable:
            form.add_error(None, '暂时无法提交报修，请稍后重试。')
            return render(request, 'repairs/submit.html', {'form': form}, status=503)
        except TicketWriteConflict:
            form.add_error(None, '这份表单已提交。请重新打开报修表单，再填写另一张工单。')
            return render(request, 'repairs/submit.html', {'form': form}, status=409)
        messages.success(request, f'报修已提交。工单 #{ticket.pk}，状态：{ticket.get_status_display()}。')
        return redirect('home')
    return render(request, 'repairs/submit.html', {'form': form})


@never_cache
@login_required
@require_safe
def my_tickets(request):
    page = ticket_list_page(tickets_reported_by(request.user), request.GET.get('page'))
    return render(request, 'repairs/my_tickets.html', {'page': page, 'tickets': page.object_list})


@never_cache
@login_required
@require_safe
def ticket_detail(request, ticket_id):
    handler = is_handler(request.user)
    visible = tickets_visible_to(request.user, include_handler_queues=handler)
    ticket = visible.select_related('assignee').filter(pk=ticket_id).first()
    if ticket is None:
        return render(request, 'repairs/ticket_unavailable.html', status=404)
    # 工单和历史分别查询，按已读版本截断，避免把随后提交的事件配到旧状态上。
    events = list(ticket.events.filter(version__lte=ticket.version).select_related('actor'))
    latest_progress = next((event for event in reversed(events) if event.action == TicketEvent.Action.PROGRESS), None)
    return render(request, 'repairs/ticket_detail.html', {
        'ticket': ticket, 'is_handler': handler, 'events': events, 'latest_progress': latest_progress,
        'note_limit': NOTE_LIMIT,
    })


@never_cache
@_handle_locked_database
@login_required
@require_POST
def claim_repair(request, ticket_id):
    if not is_handler(request.user):
        raise PermissionDenied
    form = TicketVersionForm(request.POST)
    if not form.is_valid():
        return render(request, 'repairs/claim_error.html', {
            'message': '接单请求缺少有效的工单版本，请重新打开待接单工单。',
        }, status=400)
    try:
        claim_ticket(ticket_id=ticket_id, handler=request.user, expected_version=form.cleaned_data['version'])
    except TicketWriteConflict:
        return render(request, 'repairs/claim_error.html', {
            'message': '工单已变化或不可接单，请刷新待接单列表。',
        }, status=409)
    except TicketWriteUnavailable:
        return render(request, 'repairs/claim_error.html', {
            'message': '暂时无法接单，请稍后重试。',
        }, status=503)
    messages.success(request, '接单成功。')
    return redirect('handler-assigned')


@never_cache
@_handle_locked_database
@login_required
@require_POST
def update_ticket(request, ticket_id, operation):
    form_class, write, success_message = TICKET_ACTIONS[operation]
    form = form_class(request.POST)
    valid = form.is_valid()
    recovery = {
        'draft_content': form.cleaned_data.get('content'),
        'detail_url': reverse('ticket-detail', args=[ticket_id]),
    }
    if not valid:
        return render(request, 'repairs/ticket_action_error.html', {**recovery, 'form': form}, status=400)

    fields = form.cleaned_data.copy()
    version = fields.pop('version')
    try:
        write(ticket_id=ticket_id, actor=request.user, expected_version=version, **fields)
    except PermissionDenied:
        return render(request, 'repairs/ticket_action_error.html', {
            'message': '你无权执行这项工单操作。',
        }, status=403)
    except TicketWriteConflict:
        return render(request, 'repairs/ticket_action_error.html', {
            **recovery, 'message': '工单已变化，请刷新详情后再试。',
        }, status=409)
    except TicketWriteUnavailable:
        return render(request, 'repairs/ticket_action_error.html', {
            **recovery, 'message': '暂时无法更新工单，请稍后重试。',
        }, status=503)
    messages.success(request, success_message)
    return redirect('ticket-detail', ticket_id=ticket_id)

