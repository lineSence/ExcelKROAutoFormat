"""Разговор с сервером по машинным маршрутам `/api/companion/*`.

Заголовок `Origin` не посылается нарочно: проверка источника в
`app/security.py` блокирует чужие `Origin`, а запросы без него пропускает.
Текст ошибки сервер отдаёт по-русски, поэтому его можно показывать
человеку как есть.
"""

from __future__ import annotations

import logging
from pathlib import Path

import httpx

logger = logging.getLogger("companion.client")

XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
GONE = "Сервер больше не знает эту работу. Залейте сверку заново."


class ServerError(RuntimeError):
    """Сервер ответил ошибкой."""


class Gone(ServerError):
    """Билет или сверка уже забыты (срок или перезапуск службы)."""


class Client:
    """Тонкая обёртка над httpx. Тесты подменяют `transport`."""

    def __init__(self, settings, transport=None) -> None:
        self.settings = settings
        auth = None
        if str(settings.auth_user or "").strip():
            auth = (settings.auth_user, settings.auth_password)
        self._http = httpx.Client(
            base_url=str(settings.server_url or "").rstrip("/"),
            auth=auth,
            timeout=float(settings.timeout_seconds or 120),
            transport=transport,
            follow_redirects=False,
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "Client":
        return self

    def __exit__(self, *_args) -> None:
        self.close()

    # --- служебное ---

    @staticmethod
    def _message(response) -> str:
        try:
            data = response.json()
        except ValueError:
            return (response.text or "").strip()[:300] or f"Ответ сервера {response.status_code}."
        if isinstance(data, dict):
            return str(data.get("error") or data.get("detail") or data)[:300]
        return str(data)[:300]

    def _check(self, response):
        if response.status_code == 404:
            raise Gone(self._message(response) or GONE)
        if response.status_code >= 400:
            raise ServerError(self._message(response))
        return response

    def _json(self, response) -> dict:
        self._check(response)
        try:
            data = response.json()
        except ValueError as error:
            raise ServerError(
                "Сервер ответил не машинным ответом. Проверьте адрес сервера."
            ) from error
        if not isinstance(data, dict):
            raise ServerError("Сервер ответил непонятно.")
        return data

    # --- действия ---

    def health(self) -> dict:
        """Проверка связи. `/health` открыт без пароля."""
        return self._json(self._http.get("/health"))

    def upload(self, path: Path, prev: Path | None = None) -> dict:
        """Отдаёт сверку серверу и возвращает ответ с билетом."""
        path = Path(path)
        data = {
            "strict": "on" if self.settings.strict else "off",
            "verify": str(self.settings.verify or "off"),
            "logic": str(int(self.settings.logic or 100)),
        }
        handles = [path.open("rb")]
        files = [("file", (path.name, handles[0], XLSX_TYPE))]
        if prev is not None:
            handles.append(Path(prev).open("rb"))
            files.append(("prev", (Path(prev).name, handles[-1], XLSX_TYPE)))
        try:
            response = self._http.post("/api/companion/jobs", data=data, files=files)
        finally:
            for handle in handles:
                handle.close()
        return self._json(response)

    def job(self, ticket: str) -> dict:
        return self._json(self._http.get(f"/api/companion/jobs/{ticket}"))

    def exports(self) -> list[dict]:
        data = self._json(self._http.get("/api/companion/exports"))
        rows = data.get("exports")
        return [dict(row) for row in rows] if isinstance(rows, list) else []

    def fetch(self, token: str, kind: str, target: Path) -> Path | None:
        """Скачивает файл в `target`. Файла нет на сервере — `None`.

        Пишется сначала `.part`, потом переименовывается: в папку
        с готовыми сверками смотрят люди, недокачанный файл там
        появляться не должен.
        """
        target = Path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(target.name + ".part")
        url = f"/api/companion/exports/{token}/{kind}"
        with self._http.stream("GET", url) as response:
            if response.status_code == 404:
                return None
            if response.status_code >= 400:
                response.read()
                raise ServerError(self._message(response))
            with temporary.open("wb") as handle:
                for chunk in response.iter_bytes():
                    handle.write(chunk)
        temporary.replace(target)
        return target

    def done(self, token: str, note: str = "") -> dict:
        return self._json(
            self._http.post(f"/api/companion/exports/{token}/done", data={"note": note})
        )

    def failed(self, token: str, note: str = "") -> dict:
        return self._json(
            self._http.post(f"/api/companion/exports/{token}/failed", data={"note": note})
        )
