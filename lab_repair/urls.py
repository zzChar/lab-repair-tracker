from django.contrib.auth.views import LoginView, LogoutView
from django.urls import path

from accounts.views import handler_home, home
from repairs.views import claim_repair, my_tickets, submit_repair, ticket_detail, update_ticket

urlpatterns = [
    path('', home, name='home'),
    path('login/', LoginView.as_view(), name='login'),
    path('logout/', LogoutView.as_view(), name='logout'),
    path('handler/', handler_home, name='handler-home'),
    path('handler/assigned/', handler_home, {'assigned': True}, name='handler-assigned'),
    path('repairs/new/', submit_repair, name='submit-repair'),
    path('repairs/mine/', my_tickets, name='my-tickets'),
    path('repairs/<int:ticket_id>/', ticket_detail, name='ticket-detail'),
    path('repairs/<int:ticket_id>/claim/', claim_repair, name='claim-repair'),
    path('repairs/<int:ticket_id>/progress/', update_ticket, {'operation': 'progress'}, name='repair-progress'),
    path('repairs/<int:ticket_id>/submit-result/', update_ticket, {'operation': 'submit-result'}, name='repair-submit-result'),
    path('repairs/<int:ticket_id>/close/', update_ticket, {'operation': 'close'}, name='repair-close'),
    path('repairs/<int:ticket_id>/return/', update_ticket, {'operation': 'return'}, name='repair-return'),
]
