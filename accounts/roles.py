HANDLER_GROUP = 'repair_handlers'


def is_handler(user):
    return user.groups.filter(name=HANDLER_GROUP).exists()
