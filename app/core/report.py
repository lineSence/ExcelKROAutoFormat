"""Отчёты для страницы (разделы 6.8–6.10). В файл отчёты не записываются."""

from __future__ import annotations

from .format import FormatResult


def group_rows(result: FormatResult) -> list[dict]:
    """Таблица по группам товаров (6.8)."""
    rows = []
    for group in result.groups:
        rows.append(
            {
                "group": group.name,
                "pieces": round(group.resort_pieces, 1),
                "resort_sum": round(group.zeroed_sum, 2),
                "shortage_sum": round(group.shortage_sum, 2),
                "single_rows": group.single_rows,
            }
        )
    return rows


def cluster_rows(result: FormatResult) -> list[dict]:
    """Таблица состава гроздей (6.9)."""
    rows = []
    for group in result.groups:
        for number, cluster in enumerate(group.clusters, start=1):
            for item in cluster.items:
                keeps_sum = cluster.decision == "недостача" and item.diff < 0
                rows.append(
                    {
                        "group": group.name,
                        "cluster": f"{number}. {cluster.brand}",
                        "row": item.row,
                        "name": item.name,
                        "diff": item.diff,
                        "sum_before": round(item.sum_diff, 2),
                        "sum_after": round(item.sum_diff, 2) if keeps_sum else 0.0,
                        "decision": cluster.decision,
                    }
                )
    return rows


def doubtful_rows(result: FormatResult) -> list[dict]:
    """Таблица сомнительных совпадений (6.10)."""
    rows = []
    for group in result.groups:
        for pair in group.doubtful:
            rows.append(
                {
                    "group": group.name,
                    "first": f"{pair.first_name} (стр. {pair.first_row})",
                    "second": f"{pair.second_name} (стр. {pair.second_row})",
                    "ratio": pair.ratio,
                    "decision": "связаны" if pair.linked else "не связаны",
                }
            )
    return rows


def summary(result: FormatResult) -> dict:
    rows = group_rows(result)
    return {
        "warehouse": result.warehouse,
        "date": result.document.doc_date,
        "groups": len(rows),
        "pieces": round(sum(row["pieces"] for row in rows), 1),
        "resort_sum": round(sum(row["resort_sum"] for row in rows), 2),
        "shortage_sum": round(sum(row["shortage_sum"] for row in rows), 2),
    }
