"""Сохраняет сырой HTML страниц в debug/ для разбора вёрстки.

Запуск: python -m scraper.debug URL [URL ...]
"""
import re
import sys
from pathlib import Path

from .common import fetch

OUT = Path(__file__).resolve().parent.parent / "debug"

if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    for url in sys.argv[1:]:
        name = re.sub(r"[^a-z0-9]+", "_", url.lower()).strip("_")[:80] + ".html"
        html = fetch(url)
        (OUT / name).write_text(html or "", encoding="utf-8")
        print(url, "->", name, len(html or ""))
