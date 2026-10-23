"""Сборщики цен по сайтам дистрибьюторов."""
import re
import logging
from dataclasses import dataclass, asdict
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from .common import fetch, absolute, parse_price, strip_price
from .pdf_prices import parse_pdf_prices

log = logging.getLogger("scraper")


@dataclass
class PriceRow:
    brand: str
    model: str
    trim: str          # пусто для цены «от»
    kind: str          # "от" | "прайс" | "акция"
    price_kzt: int
    source_url: str
    valid_to: str = ""  # срок акции из прайса, ГГГГ-ММ-ДД
    note: str = ""      # подпись строки в прайсе

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


def _model_name(soup: BeautifulSoup, url: str, brand: str) -> str:
    h1 = soup.find("h1")
    text = strip_price(h1.get_text(" ", strip=True)) if h1 else ""
    # «Новый HAVAL H5», «Абсолютно новый Hilux», «Новая Toyota Camry» -> «H5», «Hilux», «Camry»
    text = re.sub(r"^(абсолютно\s+)?нов(ый|ая|ое)\s+", "", text, flags=re.I)
    text = re.sub(rf"^{re.escape(brand)}\s+", "", text, flags=re.I)
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
    # в шапке прайса часто стоит название модели перед комплектацией: «TIGGO 4 Style»
    drop = {t.lower() for t in (brand + " " + model).split()}
    rows = []
    for p in parse_pdf_prices(content):
        words = p.trim.split()
        # и обрывки соседнего текста вроде «до следующего изменения ПРЕСТИЖ 4.0»
        while len(words) > 1 and (words[0].lower() in drop or re.fullmatch(r"[а-яё]+", words[0])):
            words.pop(0)
        rows.append(PriceRow(brand, model, " ".join(words), p.kind, p.price_kzt, url, p.valid_to, p.note))
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
            model = _model_name(soup, url, self.brand)
            price = _headline_price(soup)
            if price:
                rows.append(PriceRow(self.brand, model, "", "от", price, url))
            for pdf_url in _pdf_links(soup, url):
                content = fetch(pdf_url, binary=True)
                if content and content[:4] == b"%PDF":
                    pdfs.append(PdfFile(self.brand, model, pdf_url, content))
                    rows += parse_pdf(self.brand, model, pdf_url, content)
        return rows, pdfs


class HyundaiSite:
    """hyundai.kz: страницы моделей не отдают цены в HTML, зато в меню на главной
    есть пары «TUCSON от 12 490 000 тг» по всему модельному ряду, включая Genesis."""

    MENU_RE = re.compile(r"\b([A-Z][A-Z0-9-]*(?:\s[A-Z0-9][A-Za-z0-9-]*)*)\s+(?:Нов\w+\s+)?от\s+"
                         r"(\d{1,3}(?:[ \u00a0]\d{3}){2})\s*(?:тг|₸)")

    def __init__(self, brand: str = "Hyundai", base: str = "https://hyundai.kz"):
        self.brand, self.base = brand, base

    def collect(self) -> tuple[list[PriceRow], list[PdfFile]]:
        url = self.base + "/ru/"
        html = fetch(url)
        if not html:
            return [], []
        text = _soup(html).get_text(" ", strip=True)
        rows, seen = [], set()
        for m in self.MENU_RE.finditer(text):
            model, price = m.group(1).strip(), parse_price(m.group(2) + " тг")
            if not price or model in seen:
                continue
            seen.add(model)
            brand = "Genesis" if re.match(r"GV?\d", model) else self.brand
            rows.append(PriceRow(brand, model if re.search(r"\d", model) else model.title(), "", "от", price, url))
        log.info("%s: моделей в меню: %d", self.brand, len(rows))
        return rows, []


class ToyotaSite:
    """toyotakz.com: страницы /new-cars/{модель}, цены по комплектациям берём
    из PDF-прайсов на local.toyotakz.com."""

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
            model = _model_name(soup, url, self.brand)
            slug = url.rsplit("/", 1)[-1]
            pdf_urls = _pdf_links(soup, url) or [f"https://local.toyotakz.com/pdf/pricelist/{slug}.pdf"]
            for pdf_url in pdf_urls:
                content = fetch(pdf_url, binary=True)
                if content and content[:4] == b"%PDF":
                    pdfs.append(PdfFile(self.brand, model, pdf_url, content))
                    rows += parse_pdf(self.brand, model, pdf_url, content)
            # цену «от» на страницах Toyota не отличить от цен в меню, берём минимум из прайса
            listed = [r.price_kzt for r in rows if r.model == model and r.kind == "прайс"]
            if listed:
                rows.append(PriceRow(self.brand, model, "", "от", min(listed), url))
        return rows, pdfs


SITES = [
    HyundaiSite(),
    AstanaMotorsSite("Chery", "https://chery.kz"),
    AstanaMotorsSite("Haval", "https://haval-motor.kz"),
    ToyotaSite(),
]
