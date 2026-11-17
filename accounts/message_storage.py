from django.contrib.messages.storage.cookie import CookieStorage
from django.core import signing


class SessionBoundCookieStorage(CookieStorage):
    """Short-lived receipts scoped to the current authenticated login session.

    No session mutation: business commits must not depend on a second database
    write just to deliver feedback. Cookies are signed, not encrypted.
    """

    def _sync_session(self):
        # Resolve normal authentication before using the key: expired sessions
        # and logout invalidate it. Never freeze the key at middleware startup,
        # since login can rotate it later in the same request.
        key = self.request.session.session_key if self.request.user.is_authenticated else None
        if hasattr(self, '_session_key') and key != self._session_key:
            self._loaded_data = []
            self._queued_messages = []
            self.used = True
            self.added_new = False
        self._session_key = key
        self.signer = signing.get_cookie_signer(salt=f'{self.key_salt}.{key}')

    def _get(self, *args, **kwargs):
        self._sync_session()
        if self._session_key is None:
            self.used = bool(self.request.COOKIES.get(self.cookie_name)) or self.used
            return [], True
        return super()._get(*args, **kwargs)

    @property
    def _loaded_messages(self):
        # Cached messages must also be invalidated if authentication changes
        # between two reads during one request.
        self._sync_session()
        return super()._loaded_messages

    def add(self, level, message, extra_tags=''):
        self._sync_session()
        if self._session_key is not None:
            super().add(level, message, extra_tags)

    def update(self, response):
        # Don't initiate authentication/database reads for an unused storage.
        if self.used or self.added_new or hasattr(self, '_session_key'):
            self._sync_session()
        return super().update(response)

    def _store(self, messages, response, *args, **kwargs):
        if self._session_key is None:
            self._update_cookie(None, response)
        else:
            # CookieStorage drops oldest messages to fit its 2048-byte bound.
            # Deliberately accept that loss (including a single oversized hint)
            # instead of MessageMiddleware raising ValueError under DEBUG.
            # The ticket/event history remains the durable record.
            super()._store(messages, response, remove_oldest=True)
        return []
