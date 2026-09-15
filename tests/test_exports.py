"""Очередь выгрузок и архив снимков задания.

Сети и почты здесь нет: связка письма со сверкой подменяется
аргументом `split`, как `opener` и `recognizer` в тестах почты.
"""

from __future__ import annotations

import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from app.services.exports import archive_name, photo_zip, safe_part
from app.state.exports import DONE, TAKEN, WAITING, ExportStore


@dataclass
class FakePhoto:
    name: str = ""
    path: str = ""
    size: int = 0
    title: str = ""


@dataclass
class FakeLetter:
    uid: str = "1"
    photos: list = field(default_factory=list)
    files: list = field(default_factory=list)


@dataclass
class FakeResult:
    output_path: Path
    output_name: str = "Сверка.xlsx"
    summary: dict = field(default_factory=dict)


@dataclass
class FakeJob:
    result: FakeResult
    token: str = "abc"
    warehouse: str = "ВыборгРебусПДВ"


class FakeStore:
    def __init__(self, items):
        self.items = items


def make_job(tmp_path: Path) -> FakeJob:
    output = tmp_path / "работа" / "Сверка.xlsx"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(b"xlsx")
    return FakeJob(result=FakeResult(output_path=output, summary={"warehouse": "Выборг", "date": "2026-09-15"}))


def test_queue_marks_states():
    store = ExportStore()
    store.request("t1", "Сверка.xlsx", "Выборг")
    assert [x["status"] for x in store.pending()] == [WAITING]
    store.take("t1")
    assert store.get("t1").status == TAKEN
    assert store.get("t1").tries == 1
    store.done("t1", "положено в папку")
    assert store.get("t1").status == DONE
    assert store.pending() == []


def test_repeated_request_returns_to_queue():
    store = ExportStore()
    store.request("t1", "Сверка.xlsx")
    store.done("t1")
    store.request("t1", "Сверка-2.xlsx")
    item = store.get("t1")
    assert item.status == WAITING
    assert item.name == "Сверка-2.xlsx"
    assert len(store.items()) == 1


def test_unknown_token_is_not_an_error():
    store = ExportStore()
    assert store.get("неттакого") is None
    assert store.done("неттакого") is None
    store.forget("неттакого")


def test_dead_jobs_leave_the_queue():
    store = ExportStore()
    store.request("живой")
    store.request("мертвый")
    gone = store.keep_alive(lambda token: object() if token == "живой" else None)
    assert gone == 1
    assert [x["token"] for x in store.items()] == ["живой"]


def test_archive_name_uses_warehouse_and_date(tmp_path):
    job = make_job(tmp_path)
    assert archive_name(job) == "Фото_ВыборгРебусПДВ_2026-09-15.zip"


def test_safe_part_drops_unsafe_signs():
    assert safe_part('Сигареты/Мальборо:белые') == "Сигареты Мальборо белые"
    assert safe_part("", "склад") == "склад"


def test_photo_zip_collects_only_own_letters(tmp_path):
    job = make_job(tmp_path)
    mine_photo = tmp_path / "mail-photos" / "10" / "01-фото.jpg"
    mine_photo.parent.mkdir(parents=True, exist_ok=True)
    mine_photo.write_bytes(b"jpeg")
    other_photo = tmp_path / "mail-photos" / "11" / "01-чужое.jpg"
    other_photo.parent.mkdir(parents=True, exist_ok=True)
    other_photo.write_bytes(b"jpeg")
    mine = FakeLetter(uid="10", photos=[FakePhoto(name="01-фото.jpg", path=str(mine_photo), title="Мальборо")])
    other = FakeLetter(uid="11", photos=[FakePhoto(name="01-чужое.jpg", path=str(other_photo))])
    store = FakeStore([mine, other])

    path = photo_zip(job, store, split=lambda warehouse, items: {"mine": [mine], "others": [other]})

    assert path is not None and path.is_file()
    with zipfile.ZipFile(path) as pack:
        names = pack.namelist()
    assert names == ["письмо-10/01-Мальборо.jpg"]
    assert not list(path.parent.glob("*.part"))


def test_photo_zip_without_photos_returns_none(tmp_path):
    job = make_job(tmp_path)
    store = FakeStore([])
    assert photo_zip(job, store, split=lambda warehouse, items: {"mine": [], "others": []}) is None


def test_photo_zip_skips_files_gone_from_disk(tmp_path):
    job = make_job(tmp_path)
    letter = FakeLetter(uid="12", photos=[FakePhoto(name="фото.jpg", path=str(tmp_path / "нету.jpg"))])
    store = FakeStore([letter])
    assert photo_zip(job, store, split=lambda warehouse, items: {"mine": [letter], "others": []}) is None
