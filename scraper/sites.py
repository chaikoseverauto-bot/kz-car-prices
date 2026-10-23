"""Сборщики цен по сайтам дистрибьюторов."""
import io
import re
import logging
from dataclasses import dataclass, asdict
from urllib.parse import urlparse

import pdfplumber
from bs4 import BeautifulSoup

from .common import fetch, absolute, parse_price, strip_price, PRICE_RE

log = logging.getLogger("scraper")


@dataclass
class PriceRow:
    brand: str
    model: str
    trim: str          # пусто для цены «от»
    price_kzt: int
    price2_kzt: int | None  # вторая цена в строке прайса (часто акционная)
    kind: str          # "от" | "прайс-лист" | "конфигуратор"
    source_url: str

    def as_dict(self):
        return asdict(self)


@dataclass
class PdfFile:
    brand: str
    model: str
    url: str
    content: bytes


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


def _slug_name(url: str) -> str:
    slug = [s for s in urlparse(url).path.split("/") if s][-1]
    slug = re.sub(r"-(19|20)\d{2}$", "", slug)          # elantra-2023 -> elantra
    slug = re.sub(r"^(new|chery|haval|gwm)-", "", slug)  # new-tiggo-7 -> tiggo-7
    return slug.replace("-", " ").title()


def _model_name(soup: BeautifulSoup, url: str) -> str:
    h1 = soup.find("h1")
    text = strip_price(h1.get_text(" ", strip=True)) if h1 else ""
    return text if 1 < len(text) <= 40 else _slug_name(url)


def _headline_price(soup: BeautifulSoup) -> int | None:
    """Цена рядом с заголовком модели, иначе первая цена на странице."""
    h1 = soup.find("h1")
    if h1 and h1.parent:
        price = parse_price(h1.parent.get_text(" ", strip=True))
        if price:
            return price
    return parse_price(soup.get_text(" ", strip=True))


def _pdf_links(soup: BeautifulSoup, page_url: str) -> list[str]:
    """Ссылки на PDF, похожие на прайс-лист."""
    out = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if ".pdf" not in href.lower():
            continue
        label = (a.get_text(" ", strip=True) + " " + href).lower()
        if any(k in label for k in ("прайс", "price", "баға", "pricelist", "цен")):
            out.append(absolute(page_url, href))
    return list(dict.fromkeys(out))


def parse_pdf(brand: str, model: str, url: str, content: bytes) -> list[PriceRow]:
    rows = []
    try:
        with pdfplumber.open(io.BytesIO(content)) as pdf:
            for page in pdf.pages:
                for line in (page.extract_text() or "").splitlines():
                    prices = [p for p in (parse_price(m.group(0)) for m in PRICE_RE.finditer(line)) if p]
                    if not prices:
                        continue
                    trim = strip_price(line)[:120] or "—"
                    rows.append(PriceRow(brand, model, trim, prices[0],
                                         prices[1] if len(prices) > 1 else None,
                                         "прайс-лист", url))
    except Exception as e:  # битый или сканированный PDF не должен ронять весь прогон
        log.warning("не разобран PDF %s: %s", url, e)
    return rows


class AstanaMotorsSite:
    """Hyundai, Chery, Haval: сайты Astana Motors на одном движке.

    Модельный ряд берём из ссылок на главной, на странице модели берём цену «от»
    и ссылки на PDF-прайс."""

    MODEL_PATH = re.compile(r"/(cars|crossovers|suv|minivans|models|find-a-car/[^/]+)/[^/]+/?$")
    SKIP = re.compile(r"price-configuration|test-drive|compare|promo|service", re.I)

    def __init__(self, brand: str, base: str):
        self.brand, self.base = brand, base.rstrip("/")

    def model_pages(self) -> list[str]:
        for start in (self.base + "/ru/", self.base + "/"):
            html = fetch(start)
            if not html:
                continue
            host = urlparse(start).netloc
            links = []
            for a in _soup(html).find_all("a", href=True):
                url = absolute(start, a["href"])
                p = urlparse(url)
                if p.netloc == host and self.MODEL_PATH.search(p.path) and not self.SKIP.search(p.path):
                    links.append(url if url.endswith("/") else url + "/")
            links = list(dict.fromkeys(links))
            if links:
                return links
        return []

    def collect(self) -> tuple[list[PriceRow], list[PdfFile]]:
        rows, pdfs = [], []
        pages = self.model_pages()
        log.info("%s: найдено страниц моделей: %d", self.brand, len(pages))
        for url in pages:
            html = fetch(url)
            if not html:
                continue
            soup = _soup(html)
            model = _model_name(soup, url)
            price = _headline_price(soup)
            if price:
                rows.append(PriceRow(self.brand, model, "", price, None, "от", url))
            for pdf_url in _pdf_links(soup, url):
                content = fetch(pdf_url, binary=True)
                if content and content[:4] == b"%PDF":
                    pdfs.append(PdfFile(self.brand, model, pdf_url, content))
                    rows += parse_pdf(self.brand, model, pdf_url, content)
        return rows, pdfs


class ToyotaSite:
    """toyotakz.com: страницы /new-cars/{модель}, цены конфигураций лежат в JSON
    внутри страницы (TotalPrice), прайс-листы в PDF на local.toyotakz.com."""

    BASE = "https://www.toyotakz.com"

    def __init__(self, brand: str = "Toyota"):
        self.brand = brand

    def model_pages(self) -> list[str]:
        html = fetch(self.BASE + "/new-cars")
        if not html:
            return []
        links = []
        for a in _soup(html).find_all("a", href=True):
            url = absolute(self.BASE, a["href"]).rstrip("/")
            if re.fullmatch(re.escape(self.BASE) + r"/new-cars/[a-z0-9-]+", url) and not url.endswith("brochures"):
                links.append(url)
        return list(dict.fromkeys(links))

    def collect(self) -> tuple[list[PriceRow], list[PdfFile]]:
        rows, pdfs = [], []
        pages = self.model_pages()
        log.info("%s: найдено страниц моделей: %d", self.brand, len(pages))
        for url in pages:
            html = fetch(url)
            if not html:
                continue
            soup = _soup(html)
            model = _model_name(soup, url)
            base = parse_price(soup.get_text(" ", strip=True))
            if base:
                rows.append(PriceRow(self.brand, model, "", base, None, "от", url))
            totals = sorted({int(float(v)) for v in re.findall(r'"TotalPrice"\s*:\s*"?([\d.]+)', html)})
            for i, total in enumerate(t for t in totals if t >= 2_000_000):
                rows.append(PriceRow(self.brand, model, f"конфигурация {i + 1}", total, None, "конфигуратор", url))
            slug = url.rsplit("/", 1)[-1]
            pdf_urls = _pdf_links(soup, url) or [f"https://local.toyotakz.com/pdf/pricelist/{slug}.pdf"]
            for pdf_url in pdf_urls:
                content = fetch(pdf_url, binary=True)
                if content and content[:4] == b"%PDF":
                    pdfs.append(PdfFile(self.brand, model, pdf_url, content))
                    rows += parse_pdf(self.brand, model, pdf_url, content)
        return rows, pdfs


SITES = [
    AstanaMotorsSite("Hyundai", "https://hyundai.kz"),
    AstanaMotorsSite("Chery", "https://chery.kz"),
    AstanaMotorsSite("Haval", "https://haval-motor.kz"),
    ToyotaSite(),
]
