"""HTTPS for the standard-library callers (Telegram, the model-size lookup).

A freshly installed Windows can lack root certificates: its store fetches them on demand, but Python's
default SSL context only reads what is already there, so the first HTTPS call fails with a certificate error.
The retry below uses the certifi bundle (shipped with the packaged app). It only happens after a certificate
error, i.e. before any request body was sent, so repeating a POST cannot duplicate it.
"""

from __future__ import annotations

import ssl
import urllib.error
import urllib.request
from http.client import HTTPResponse


def certifi_context() -> ssl.SSLContext | None:
    try:
        import certifi
    except ImportError:
        return None
    return ssl.create_default_context(cafile=certifi.where())


def urlopen(request: urllib.request.Request, timeout: float) -> HTTPResponse:
    """urllib.request.urlopen, retried once with the certifi bundle after a certificate failure."""
    try:
        return urllib.request.urlopen(request, timeout=timeout)
    except urllib.error.URLError as exc:
        failure: Exception = exc
        if not isinstance(exc.reason, ssl.SSLError):
            raise
    except ssl.SSLError as exc:
        failure = exc
    context = certifi_context()
    if context is None:
        raise failure
    return urllib.request.urlopen(request, timeout=timeout, context=context)
