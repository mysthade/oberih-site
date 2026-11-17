"""Тести пункту «На головну» (іконка-будиночок) на початку головного меню."""

from html.parser import HTMLParser
from io import StringIO
import re

from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse


class _HomeMenuItemParser(HTMLParser):
    """Витягує перший <li> усередині <ul id='menu'> та всі <li> з класом menu-home."""

    def __init__(self):
        super().__init__()
        self._in_menu = False
        self._menu_ul_depth = 0
        self._li_depth = 0
        self.top_li_classes: list[list[str]] = []
        self._capturing_home_li = False
        self._current_home: dict | None = None
        self.home_items: list[dict] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]):
        attr_map = dict(attrs)
        if tag == "ul":
            if not self._in_menu and attr_map.get("id") == "menu":
                self._in_menu = True
                self._menu_ul_depth = 1
            elif self._in_menu:
                self._menu_ul_depth += 1
        elif self._in_menu and tag == "li":
            self._li_depth += 1
            if self._li_depth == 1:
                classes = (attr_map.get("class") or "").split()
                self.top_li_classes.append(classes)
                if "menu-home" in classes:
                    self._capturing_home_li = True
                    self._current_home = {
                        "li_classes": classes,
                        "a_attrs": None,
                        "svg_attrs": None,
                    }
        elif self._capturing_home_li and tag == "a" and self._current_home["a_attrs"] is None:
            self._current_home["a_attrs"] = attr_map
        elif self._capturing_home_li and tag == "svg" and self._current_home["svg_attrs"] is None:
            self._current_home["svg_attrs"] = attr_map

    def handle_endtag(self, tag: str):
        if not self._in_menu:
            return
        if tag == "li":
            if self._li_depth == 1 and self._capturing_home_li:
                self.home_items.append(self._current_home)
                self._capturing_home_li = False
                self._current_home = None
            if self._li_depth > 0:
                self._li_depth -= 1
        elif tag == "ul":
            self._menu_ul_depth -= 1
            if self._menu_ul_depth <= 0:
                self._in_menu = False


class MenuHomeIconTests(TestCase):
    """Перевірка наявності, адреси, доступності, активного стану й відсутності дублювання іконки «На головну»."""

    @classmethod
    def setUpTestData(cls):
        call_command("createcachetable", stdout=StringIO())
        call_command("import_site_map", stdout=StringIO())
        call_command("import_home", stdout=StringIO())

    def setUp(self):
        cache.clear()

    def test_home_icon_present_first_accessible_and_not_duplicated(self):
        home_url = reverse("core:home")
        resp = self.client.get(home_url)
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode("utf-8")

        parser = _HomeMenuItemParser()
        parser.feed(html)

        self.assertGreaterEqual(len(parser.top_li_classes), 3)
        self.assertIn("menu-home", parser.top_li_classes[0])
        self.assertEqual(len(parser.home_items), 1)

        item = parser.home_items[0]
        self.assertIn("is-active", item["li_classes"])
        self.assertEqual(item["a_attrs"]["href"], home_url)
        self.assertEqual(item["a_attrs"].get("aria-label"), "На головну")
        self.assertEqual(item["a_attrs"].get("title"), "На головну")
        self.assertEqual(item["a_attrs"].get("aria-current"), "page")
        self.assertIsNotNone(item["svg_attrs"])
        self.assertEqual(item["svg_attrs"].get("aria-hidden"), "true")

        # У розмітці #menu текст мобільної підказки «Головна» присутній
        menu_html = re.search(r'<ul class="menu" id="menu">(.*?)</ul>', html, re.S)
        self.assertIsNotNone(menu_html)
        self.assertIn("Головна", menu_html.group(1))

    def test_home_icon_not_active_on_other_pages(self):
        resp = self.client.get(reverse("core:news_list"))
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode("utf-8")

        parser = _HomeMenuItemParser()
        parser.feed(html)
        self.assertEqual(len(parser.home_items), 1)

        item = parser.home_items[0]
        self.assertNotIn("is-active", item["li_classes"])
        self.assertNotIn("aria-current", item["a_attrs"])

