"""Офлайн-тесты разбора: без сети, на образцах разметки."""
import unittest
from unittest import mock

from scraper import sites
from scraper.common import parse_price, strip_price


class PriceTests(unittest.TestCase):
    def test_formats(self):
        self.assertEqual(parse_price("ELANTRA от 8 890 000 тг"), 8_890_000)
        self.assertEqual(parse_price("Tiggo 4 / 7 490 000 ₸ бастап"), 7_490_000)
        self.assertEqual(parse_price("18 690 000 ₸ Максимальная цена"), 18_690_000)

    def test_monthly_payment_ignored(self):
        self.assertIsNone(parse_price("платёж от 79 652 ₸ в месяц"))

    def test_strip(self):
        self.assertEqual(strip_price("Comfort 1.5T CVT 7 490 000 ₸"), "Comfort 1.5T CVT")


HOME = """<html><body>
<a href="/ru/find-a-car/cars/elantra-2023/">ELANTRA</a>
<a href="/ru/find-a-car/cars/elantra-2023/price-configuration/">Все цены</a>
<a href="/ru/find-a-car/crossovers/tucson-2024/">TUCSON</a>
<a href="/ru/promo/">Акции</a>
<a href="https://other.kz/ru/cars/x/">чужой</a>
</body></html>"""

MODEL = """<html><body><div><h1>TUCSON</h1><span>от 12 490 000 тг</span></div>
<a href="/userdata/rubrics/file_ru.pdf">Прайс-лист</a>
<a href="/userdata/rubrics/pdf_ru.pdf">Брошюра</a></body></html>"""


class AstanaTests(unittest.TestCase):
    def test_collect(self):
        def fake_fetch(url, binary=False):
            if url == "https://hyundai.kz/ru/":
                return HOME
            if "tucson" in url:
                return MODEL
            if url.endswith("elantra-2023/"):
                return "<h1>ELANTRA</h1><p>от 8 890 000 тг</p>"
            return None

        with mock.patch.object(sites, "fetch", fake_fetch):
            site = sites.AstanaMotorsSite("Hyundai", "https://hyundai.kz")
            pages = site.model_pages()
            self.assertEqual(pages, ["https://hyundai.kz/ru/find-a-car/cars/elantra-2023/",
                                     "https://hyundai.kz/ru/find-a-car/crossovers/tucson-2024/"])
            rows, pdfs = site.collect()
        prices = {(r.model, r.price_kzt) for r in rows}
        self.assertEqual(prices, {("ELANTRA", 8_890_000), ("TUCSON", 12_490_000)})

    def test_pdf_links_only_price_lists(self):
        soup = sites._soup(MODEL)
        self.assertEqual(sites._pdf_links(soup, "https://hyundai.kz/ru/x/"),
                         ["https://hyundai.kz/userdata/rubrics/file_ru.pdf"])


class ToyotaTests(unittest.TestCase):
    def test_configurator_prices(self):
        page = ('<h1>Camry</h1><p>18 690 000 ₸</p><script>{"TotalPrice":18690000.0},'
                '{"TotalPrice":"20790000"},{"TotalPrice":150000}</script>')

        def fake_fetch(url, binary=False):
            if url.endswith("/new-cars"):
                return '<a href="/new-cars/camry">Camry</a><a href="/new-cars/brochures">x</a>'
            if url.endswith("/camry"):
                return page
            return None

        with mock.patch.object(sites, "fetch", fake_fetch):
            rows, _ = sites.ToyotaSite().collect()
        self.assertEqual([(r.kind, r.price_kzt) for r in rows],
                         [("от", 18_690_000), ("конфигуратор", 18_690_000), ("конфигуратор", 20_790_000)])


if __name__ == "__main__":
    unittest.main()
