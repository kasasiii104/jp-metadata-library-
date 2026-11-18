from unittest.mock import MagicMock

from requests import Session

from sources.net_policy import _PinnedAdapter


def recording_session():
    """Return a real session and a mock recording each prepared request."""
    session = Session()
    adapter = _PinnedAdapter()
    adapter.send = MagicMock()
    adapter.send.return_value.raw = None
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session, adapter.send
