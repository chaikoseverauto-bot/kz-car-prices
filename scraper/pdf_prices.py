"""Разбор PDF-прайсов: цены по комплектациям, прайсовые и акционные.

Прайсы дистрибьюторов свёрстаны таблицей: сверху названия комплектаций,
ниже строки «Цена по прайсу», «Акционная цена до 31.10.2026» и т. п.
Текст из PDF идёт вперемешку, поэтому работаем с координатами слов:
комплектация для цены — это слова в шапке над той же колонкой.
"""
import io
import re
import logging
from dataclasses import dataclass

import pdfplumber

log = logging.getLogger("scraper")

ROW_TOL = 4         # слова с разницей по вертикали до 4pt считаем одной строкой
HEADER_REACH = 60   # насколько высоко над строкой цен ищем шапку
PROMO_RE = re.compile(r"акци|выгод|спец|скидк|бонус", re.I)
DATE_RE = re.compile(r"до\s+(\d{2})\.(\d{2})\.(\d{4})")


@dataclass
class PdfPrice:
    trim: str
    price_kzt: int
    kind: str            # "прайс" | "акция"
    valid_to: str        # ГГГГ-ММ-ДД или пусто
    note: str            # подпись строки из прайса


def _rows(words: list[dict]) -> list[dict]:
    """Группирует слова в строки по вертикали."""
    rows: list[dict] = []
    for w in sorted(words, key=lambda w: (w["top"], w["x0"])):
        if rows and abs(rows[-1]["top"] - w["top"]) <= ROW_TOL:
            rows[-1]["words"].append(w)
        else:
            rows.append({"top": w["top"], "words": [w]})
    for r in rows:
        r["words"].sort(key=lambda w: w["x0"])
    return rows


def _prices_in_row(words: list[dict]) -> tuple[list[tuple[float, float, int]], list[dict]]:
    """Находит цены вида «9 190 000» (слова-группы цифр рядом друг с другом).

    Возвращает [(x0, x1, значение)] и слова, не вошедшие в цены."""
    prices, rest, i = [], [], 0
    while i < len(words):
        t = words[i]["t"]
        if re.fullmatch(r"\d{1,3}", t):
            j, value, x1 = i + 1, t, words[i]["x1"]
            while (j < len(words) and re.fullmatch(r"\d{3}", words[j]["t"])
                   and words[j]["x0"] - x1 < 14):
                value += words[j]["t"]
                x1 = words[j]["x1"]
                j += 1
            n = int(value)
            if j - i >= 3 and 2_000_000 <= n <= 300_000_000:
                prices.append((words[i]["x0"], x1, n))
                # «тг», «₸» сразу после цены не нужны в подписи
                while j < len(words) and re.fullmatch(r"₸\d?|тг\.?|тенге", words[j]["t"]):
                    j += 1
                i = j
                continue
        rest.append(words[i])
        i += 1
    return prices, rest


def _bounds(prices: list[tuple[float, float, int]]) -> list[tuple[float, float]]:
    centers = [(a + b) / 2 for a, b, _ in prices]
    if len(centers) == 1:
        return [(centers[0] - 70, centers[0] + 70)]
    out = []
    for k, c in enumerate(centers):
        left = (centers[k - 1] + c) / 2 if k else c - (centers[1] - c) / 2
        right = (c + centers[k + 1]) / 2 if k + 1 < len(centers) else c + (c - centers[k - 1]) / 2
        out.append((left, right))
    return out


def _in(w: dict, lo: float, hi: float) -> bool:
    return lo <= (w["x0"] + w["x1"]) / 2 < hi


def _undouble(t: str) -> str:
    # некоторые PDF дублируют каждую букву: «ККооммппллееккттаацциияя»
    if len(t) >= 4 and len(t) % 2 == 0 and t[::2] == t[1::2]:
        return t[::2]
    return t


def _text(words: list[dict]) -> str:
    return " ".join(_undouble(w["t"]) for w in words).strip()


def parse_page(page) -> list[PdfPrice]:
    words = []
    for w in page.extract_words(x_tolerance=1.5, y_tolerance=3):
        t = re.sub(r"\s+", "", w["text"])
        if t:
            words.append({**w, "t": t})
    rows = _rows(words)
    for r in rows:
        r["prices"], r["rest"] = _prices_in_row(r["words"])

    out = []
    for idx, row in enumerate(rows):
        if not row["prices"]:
            continue
        bounds = _bounds(row["prices"])
        left_edge = bounds[0][0]

        # подпись строки: слева от первой цены, плюс соседние строки-подписи (перенос на 2 строки)
        label = [w for w in row["rest"] if w["x1"] <= left_edge]
        for other in rows[max(0, idx - 2): idx + 3]:
            if other is not row and not other["prices"] and abs(other["top"] - row["top"]) <= 12:
                if all(w["x1"] <= left_edge for w in other["words"]):
                    label += other["words"]

        # шапка: ближайшая строка выше со словами над колонками, плюс прилегающие к ней строки
        header_rows, context = [], []
        for other in reversed(rows[:idx]):
            if row["top"] - other["top"] > HEADER_REACH:
                break
            if other["prices"]:
                if header_rows:
                    break
                continue
            in_cols = [w for w in other["words"] if w["x0"] >= left_edge - 5]
            if not in_cols:
                continue
            if header_rows and header_rows[-1]["top"] - other["top"] > 14:
                break
            header_rows.append(other)
            context += [w for w in other["words"] if w["x1"] <= left_edge]

        trims = []
        for lo, hi in bounds:
            parts = [_text([w for w in hr["words"] if _in(w, lo, hi)]) for hr in reversed(header_rows)]
            trims.append(" ".join(p for p in parts if p))
        seen: dict[str, int] = {}
        for k, t in enumerate(trims):  # одинаковые названия в разных колонках (бензин/гибрид)
            if t:
                seen[t] = seen.get(t, 0) + 1
                if seen[t] > 1:
                    trims[k] = f"{t} ({seen[t]})"

        note = _text(sorted(context, key=lambda w: (w["top"], w["x0"])) + label)
        date = DATE_RE.search(note)
        valid_to = f"{date.group(3)}-{date.group(2)}-{date.group(1)}" if date else ""
        kind = "акция" if PROMO_RE.search(note) else "прайс"
        for (_, _, value), trim in zip(row["prices"], trims):
            out.append(PdfPrice(trim, value, kind, valid_to, note[:120]))
    return out


def parse_pdf_prices(content: bytes) -> list[PdfPrice]:
    """Все цены из PDF. Строки без комплектации оставляем, только если других нет."""
    found: list[PdfPrice] = []
    try:
        with pdfplumber.open(io.BytesIO(content)) as pdf:
            for page in pdf.pages:
                found += parse_page(page)
    except Exception as e:  # битый или сканированный PDF не должен ронять весь прогон
        log.warning("не разобран PDF: %s", e)
        return []
    named = [p for p in found if p.trim]
    result = named or found
    unique, keys = [], set()
    for p in result:
        key = (p.trim, p.kind)
        if key not in keys:
            keys.add(key)
            unique.append(p)
    return unique
