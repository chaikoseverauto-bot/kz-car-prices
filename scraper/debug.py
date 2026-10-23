"""Печатает в лог выжимку по страницам для разбора вёрстки (ссылки, цены, ключи JSON).

Сырой HTML не сохраняем: в нём бывают ключи API сайтов.
Запуск: python -m scraper.debug URL [URL ...]
"""
import re
import sys
from collections import Counter

from bs4 import BeautifulSoup

from .common import fetch, PRICE_RE, allowed

if __name__ == "__main__":
    for url in sys.argv[1:]:
        print("=" * 20, url, "robots:", allowed(url))
        html = fetch(url) or ""
        print("длина:", len(html))
        soup = BeautifulSoup(html, "html.parser")
        hrefs = [a["href"] for a in soup.find_all("a", href=True)]
        print("ссылок:", len(hrefs))
        for h in list(dict.fromkeys(hrefs))[:150]:
            print("  href", h[:120])
        text = soup.get_text(" ", strip=True)
        for m in list(PRICE_RE.finditer(text))[:30]:
            print("  цена:", text[max(0, m.start() - 60): m.end()].replace("\n", " "))
        keys = Counter(re.findall(r'"([A-Za-z_]*[Pp]rice[A-Za-z_]*)"\s*:', html))
        print("ключи JSON с price:", dict(keys.most_common(15)))
        for s in soup.find_all("script", src=True)[:20]:
            print("  script", s["src"][:120])
        print("  api-похожие:", sorted(set(re.findall(r'["\'](/(?:api|ajax|local)[^"\']{0,80})["\']', html)))[:20])
