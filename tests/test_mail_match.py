"""Тесты связки письма ревизора со сверкой по названию магазина."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core import mail_match
from app.core.mail import Letter


def _letter(uid: str, subject: str, store: str = "") -> Letter:
    return Letter(uid=uid, subject=subject, hints={"store": store})


def test_parts_splits_camel_case() -> None:
    assert mail_match.parts("ВыборгРебусПДВ") == ["выборг", "ребус", "пдв"]


def test_subject_words_drop_service_words() -> None:
    said = mail_match.store_parts("Магазин Ребус, фото излишков")
    assert "ребус" in said
    assert "магазин" not in said
    assert "фото" not in said


def test_store_from_subject_matches_warehouse() -> None:
    good, reason = mail_match.fits(
        "ВыборгРебусПДВ", _letter("1", "магазин Ребус излишки", "Ребус")
    )
    assert good
    assert "ребус" in reason


def test_other_store_does_not_match() -> None:
    good, _ = mail_match.fits("ВыборгРебусПДВ", _letter("2", "магазин Охта фото", "Охта"))
    assert not good


def test_typo_in_subject_still_matches() -> None:
    good, _ = mail_match.fits(
        "ПушкинБогатырский", _letter("3", "магазин Богатырской, излишки", "Богатырской")
    )
    assert good


def test_store_number_matches() -> None:
    good, reason = mail_match.fits("Магазин 47", _letter("4", "маг. 47 фото товара", "47"))
    assert good
    assert "47" in reason


def test_split_keeps_only_own_letters() -> None:
    mine = _letter("1", "магазин Ребус", "Ребус")
    other = _letter("2", "магазин Охта", "Охта")
    report = mail_match.split("ВыборгРебусПДВ", [mine, other])
    assert report["mine"] == [mine]
    assert report["others"] == [other]
    assert report["note"] == ""


def test_split_without_match_explains_why() -> None:
    report = mail_match.split("ВыборгРебусПДВ", [_letter("2", "магазин Охта", "Охта")])
    assert report["mine"] == []
    assert "вручную" in report["note"]


def test_split_without_warehouse_sends_nothing() -> None:
    report = mail_match.split("", [_letter("1", "магазин Ребус", "Ребус")])
    assert report["mine"] == []
    assert report["note"]
