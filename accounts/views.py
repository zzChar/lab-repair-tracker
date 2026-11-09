from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_safe

from repairs.queries import ticket_list_page, tickets_assigned_to, tickets_pending_assignment

from .roles import is_handler


@never_cache
@login_required
def home(request):
    return render(request, 'accounts/home.html', {'is_handler': is_handler(request.user)})


@never_cache
@login_required
@require_safe
def handler_home(request, assigned=False):
    if not is_handler(request.user):
        raise PermissionDenied
    tickets = tickets_assigned_to(request.user) if assigned else tickets_pending_assignment()
    page = ticket_list_page(tickets, request.GET.get('page'))
    return render(request, 'accounts/handler_home.html', {
        'assigned': assigned,
        'page': page,
        'tickets': page.object_list,
    })
