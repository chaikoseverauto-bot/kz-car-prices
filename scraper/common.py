"""Общие функции: загрузка страниц с учётом robots.txt и разбор цен."""
import re
import time
import logging
from urllib.parse import urlparse, urljoin
from urllib import robotparser

import requests

USER_AGENT = "kz-car-prices/0.1 (+price monitoring; contact via GitHub)"
log = logging.getLogger("scraper")

_session = requests.Session()
_session.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "ru,kk;q=0.8"})
_robots: dict[str, robotparser.RobotFileParser] = {}

# «8 890 000 тг», «от 12 490 000 ₸», «7 490 000 ₸ бастап», «18 690 000 ₸»
PRICE_RE = re.compile(r"(\d{1,3}(?:[   ]\d{3}){1,3})\s*(?:₸|тг\.?|тенге|KZT)", re.I)


def parse_price(text: str) -> int | None:
    m = PRICE_RE.search(text or "")
    if not m:
        return None
    value = int(re.sub(r"\D", "", m.group(1)))
    # отсекаем ежемесячные платежи и мусор: новая машина в КЗ стоит от ~3 млн ₸
    return value if 2_000_000 <= value <= 300_000_000 else None


def strip_price(text: str) -> str:
    text = PRICE_RE.sub("", text)
    text = re.sub(r"\b(от|бастап|цена|баға)\b", "", text, flags=re.I)
    return re.sub(r"\s+", " ", text).strip(" -–:|/")


def allowed(url: str) -> bool:
    """Проверяет robots.txt. Если сайт запрещает доступ, страницу не трогаем."""
    p = urlparse(url)
    root = f"{p.scheme}://{p.netloc}"
    if root not in _robots:
        rp = robotparser.RobotFileParser()
        try:
            r = _session.get(root + "/robots.txt", timeout=20)
            rp.parse(r.text.splitlines() if r.status_code == 200 else [])
        except requests.RequestException:
            rp.parse([])
        _robots[root] = rp
    return _robots[root].can_fetch(USER_AGENT, url)


def fetch(url: str, binary: bool = False, retries: int = 3):
    if not allowed(url):
        log.warning("robots.txt запрещает: %s", url)
        return None
    for attempt in range(retries):
        try:
            r = _session.get(url, timeout=30)
            if r.status_code == 200:
                time.sleep(1.5)  # вежливая пауза между запросами
                return r.content if binary else r.text
            log.warning("HTTP %s: %s", r.status_code, url)
            if r.status_code in (403, 404, 410):
                return None
        except requests.RequestException as e:
            log.warning("ошибка %s: %s", e.__class__.__name__, url)
        time.sleep(2 ** (attempt + 1))
    return None


def absolute(base: str, href: str) -> str:
    return urljoin(base, href.split("#")[0])
