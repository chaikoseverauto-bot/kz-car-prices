"""Ежедневный прогон: собирает цены, обновляет data/ и пишет отчёт об изменениях.

Запуск: python -m scraper.run
"""
import csv
import json
import logging
import re
from datetime import date
from pathlib import Path

from .sites import SITES

DATA = Path(__file__).resolve().parent.parent / "data"
LATEST = DATA / "latest.csv"
HISTORY = DATA / "history.csv"
CHANGES = DATA / "changes.md"
STATUS = DATA / "status.json"
FIELDS = ["date", "brand", "model", "trim", "kind", "price_kzt", "valid_to", "note", "source_url"]

log = logging.getLogger("scraper")


def _key(row: dict) -> tuple:
    return row["brand"], row["model"], row["trim"], row["kind"]


def _read(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _write(path: Path, rows: list[dict], append: bool = False):
    new = not path.exists() or not append
    with path.open("a" if append else "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if new:
            w.writeheader()
        w.writerows(rows)


def _fmt(v) -> str:
    return f"{int(v):,}".replace(",", " ") + " ₸" if v not in (None, "") else "—"


def _discounts(rows: list[dict], top: int = 15) -> list[str]:
    """Самые большие скидки: акционная цена против прайсовой в той же комплектации."""
    listed = {(r["brand"], r["model"], r["trim"]): int(r["price_kzt"]) for r in rows if r["kind"] == "прайс"}
    found = []
    for r in rows:
        base = listed.get((r["brand"], r["model"], r["trim"]))
        if r["kind"] == "акция" and base and base > int(r["price_kzt"]):
            found.append((base - int(r["price_kzt"]), base, r))
    if not found:
        return []
    found.sort(key=lambda x: -x[0])
    out = ["## Самые большие скидки по прайсам", "",
           "| Бренд | Модель | Комплектация | Прайс | Акция | Выгода | До |", "|---|---|---|---|---|---|---|"]
    for diff, base, r in found[:top]:
        out.append(f"| {r['brand']} | {r['model']} | {r['trim']} | {_fmt(base)} | {_fmt(r['price_kzt'])} "
                   f"| {_fmt(diff)} ({diff * 100 // base}%) | {r['valid_to'] or '—'} |")
    return out + [""]


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    DATA.mkdir(exist_ok=True)
    today = date.today().isoformat()
    previous = _read(LATEST)
    prev_by_brand: dict[str, list[dict]] = {}
    for r in previous:
        prev_by_brand.setdefault(r["brand"], []).append(r)

    current, status = [], {"date": today, "brands": {}}
    for site in SITES:
        try:
            rows, pdfs = site.collect()
        except Exception as e:
            log.exception("%s: сбой", site.brand)
            rows, pdfs, error = [], [], f"{e.__class__.__name__}: {e}"
        else:
            error = None
        if not rows:
            # сайт не ответил или поменял вёрстку: оставляем вчерашние цены, а не «удаляем» модели
            kept = prev_by_brand.get(site.brand, [])
            current += kept
            status["brands"][site.brand] = {"ok": False, "rows": 0, "kept_from_previous": len(kept), "error": error}
            continue
        seen = set()
        for r in rows:
            d = r.as_dict()
            d["date"] = today
            if _key(d) in seen:
                continue
            seen.add(_key(d))
            current.append({k: d.get(k) for k in FIELDS})
        for p in pdfs:
            slug = re.sub(r"[^a-z0-9]+", "-", p.model.lower()).strip("-") or "model"
            path = DATA / "pdf" / p.brand.lower() / f"{slug}.pdf"
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists() or path.read_bytes() != p.content:
                path.write_bytes(p.content)
        status["brands"][site.brand] = {"ok": True, "rows": len(seen), "pdfs": len(pdfs), "error": None}

    # сравнение со вчерашним днём
    prev_map = {_key(r): r for r in previous}
    cur_map = {_key(r): r for r in current}
    changed, added = [], []
    for k, r in cur_map.items():
        old = prev_map.get(k)
        if old is None:
            added.append(r)
        elif str(old["price_kzt"]) != str(r["price_kzt"]):
            changed.append((old, r))
    removed = [r for k, r in prev_map.items() if k not in cur_map]

    if previous:
        _write(HISTORY, added + [r for _, r in changed], append=True)
    else:
        _write(HISTORY, current)
    _write(LATEST, current)
    STATUS.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [f"# Изменения цен на {today}", ""]
    if changed:
        lines += ["## Цена изменилась", "", "| Бренд | Модель | Комплектация | Было | Стало | Разница |", "|---|---|---|---|---|---|"]
        for old, new in sorted(changed, key=lambda x: int(x[1]["price_kzt"]) - int(x[0]["price_kzt"])):
            diff = int(new["price_kzt"]) - int(old["price_kzt"])
            lines.append(f"| {new['brand']} | {new['model']} | {new['trim'] or 'от'} | {_fmt(old['price_kzt'])} "
                         f"| {_fmt(new['price_kzt'])} | {'+' if diff > 0 else ''}{_fmt(diff)} |")
        lines.append("")
    if added and previous:
        lines += ["## Новые позиции", ""] + [f"- {r['brand']} {r['model']} {r['trim']}: {_fmt(r['price_kzt'])}" for r in added] + [""]
    if removed:
        lines += ["## Пропали с сайта", ""] + [f"- {r['brand']} {r['model']} {r['trim']}" for r in removed] + [""]
    if not (changed or removed or (added and previous)):
        lines += ["Изменений нет." if previous else f"Первый прогон: собрано {len(current)} строк.", ""]
    lines += _discounts(current)
    lines += ["## Состояние сборщиков", ""]
    for brand, s in status["brands"].items():
        lines.append(f"- {brand}: " + (f"{s['rows']} строк, PDF: {s.get('pdfs', 0)}" if s["ok"]
                                         else f"не собрано, оставлены вчерашние данные ({s['kept_from_previous']}). {s['error'] or ''}"))
    CHANGES.write_text("\n".join(lines) + "\n", encoding="utf-8")
    log.info("готово: %d строк, изменений %d, новых %d, пропало %d", len(current), len(changed), len(added), len(removed))


if __name__ == "__main__":
    main()
