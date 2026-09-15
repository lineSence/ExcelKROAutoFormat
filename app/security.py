from __future__ import annotations

import base64
import binascii
import hmac
from urllib.parse import urlsplit

from fastapi import Request
from fastapi.responses import PlainTextResponse

from .config import Settings

SAFE_PUBLIC_PATHS = {"/health"}
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def _is_loopback_host(host: str) -> bool:
    return (host or "").strip().lower() in {"127.0.0.1", "localhost", "::1", "[::1]"}


def _auth_required(settings: Settings) -> bool:
    return not _is_loopback_host(settings.app_host)


def _credentials(request: Request) -> tuple[str, str] | None:
    value = request.headers.get("authorization", "")
    if not value.lower().startswith("basic "):
        return None
    try:
        raw = base64.b64decode(value[6:].strip(), validate=True).decode("utf-8")
    except (ValueError, UnicodeError, binascii.Error):
        return None
    if ":" not in raw:
        return None
    return raw.split(":", 1)


def _authorized(request: Request, settings: Settings) -> bool:
    supplied = _credentials(request)
    expected_user = str(getattr(settings, "web_auth_user", "") or "")
    expected_password = str(getattr(settings, "web_auth_password", "") or "")
    if not expected_user or not expected_password or supplied is None:
        return False
    user, password = supplied
    return hmac.compare_digest(user, expected_user) and hmac.compare_digest(
        password, expected_password
    )


def _origin_matches(request: Request) -> bool:
    origin = request.headers.get("origin") or request.headers.get("referer")
    if not origin:
        return True
    parsed = urlsplit(origin)
    return bool(parsed.netloc) and parsed.netloc == request.headers.get("host", "")


class SecurityMiddleware:
    """Pure ASGI middleware: does not create a per-request task group.

    This is important for application routes which deliberately start a background
    asyncio task and return immediately after accepting an upload.
    """

    def __init__(self, app, settings: Settings) -> None:
        self.app = app
        self.settings = settings

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive=receive)
        path = request.url.path
        if path not in SAFE_PUBLIC_PATHS and _auth_required(self.settings):
            if not _authorized(request, self.settings):
                response = PlainTextResponse(
                    "Требуется авторизация.",
                    status_code=401,
                    headers={"WWW-Authenticate": 'Basic realm="ExcelKROAutoFormat"'},
                )
                await response(scope, receive, send)
                return

        if request.method not in SAFE_METHODS and not _origin_matches(request):
            response = PlainTextResponse(
                "Запрос заблокирован: неверный источник.",
                status_code=403,
            )
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)


def install_security(app, settings: Settings) -> None:
    app.add_middleware(SecurityMiddleware, settings=settings)
