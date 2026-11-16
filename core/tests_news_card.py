"""Тести клікабельності всієї картки новини (CSS stretched link, розмітка, доступність, чернетки)."""

from datetime import date
from html.parser import HTMLParser
from io import StringIO
from pathlib import Path

from django.conf import settings
from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from .models import NewsItem


CSS_MARKER = "/* Додано для Django: уся картка новини клікабельна */"


class _NewsCardHtmlParser(HTMLParser):
    """Збирає всі картки <li class='news-card ...'>, посилання <a> усередині них та перевіряє вкладеність <a>."""

    def __init__(self):
        super().__init__()
        self.cards: list[list[dict]] = []
        self._li_depth = 0
        self._in_card = False
        self._card_li_depth = 0
        self._a_depth = 0
        self.nested_anchors_found = False
        self._current_links: list[dict] = []
        self._current_link: dict | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]):
        attr_map = dict(attrs)
        if tag == "li":
            self._li_depth += 1
            classes = (attr_map.get("class") or "").split()
            if not self._in_card and "news-card" in classes:
                self._in_card = True
                self._card_li_depth = self._li_depth
                self._current_links = []
        elif tag == "a":
            if self._a_depth > 0:
                self.nested_anchors_found = True
            self._a_depth += 1
            if self._in_card:
                self._current_link = {
                    "href": attr_map.get("href") or "",
                    "text_parts": [],
                }

    def handle_data(self, data: str):
        if self._in_card and self._current_link is not None:
            self._current_link["text_parts"].append(data)

    def handle_endtag(self, tag: str):
        if tag == "a":
            if self._in_card and self._current_link is not None:
                self._current_links.append(
                    {
                        "href": self._current_link["href"],
                        "text": "".join(self._current_link["text_parts"]).strip(),
                    }
                )
                self._current_link = None
            if self._a_depth > 0:
                self._a_depth -= 1
        elif tag == "li":
            if self._in_card and self._li_depth == self._card_li_depth:
                self.cards.append(self._current_links)
                self._in_card = False
                self._card_li_depth = 0
                self._current_links = []
            if self._li_depth > 0:
                self._li_depth -= 1


class NewsCardClickableTests(TestCase):
    """Перевірка CSS-блоку stretched link, структури карток на / та /novyny/ і приховування чернеток."""

    @classmethod
    def setUpTestData(cls):
        call_command("createcachetable", stdout=StringIO())
        call_command("import_site_map", stdout=StringIO())
        call_command("import_home", stdout=StringIO())
        cls.pub_news = NewsItem.objects.create(
            title="Опублікована новина для перевірки картки",
            slug="opublikovana-novyna-kartka",
            category="Події",
            summary="Короткий анонс опублікованої новини.",
            body="Повний текст опублікованої новини.",
            published_date=date(2026, 11, 10),
            is_published=True,
        )
        cls.draft_news = NewsItem.objects.create(
            title="Таємна чернетка новини",
            slug="taiemna-chernetka-novyny",
            category="Оголошення",
            summary="Ця чернетка не повинна з'являтися у картках.",
            body="Текст чернетки.",
            published_date=date(2026, 11, 11),
            is_published=False,
        )

    def setUp(self):
        cache.clear()

    def test_style_css_has_clickable_news_card_block_once_at_end(self):
        css_path = Path(settings.BASE_DIR) / "static" / "css" / "style.css"
        css_text = css_path.read_text(encoding="utf-8")

        self.assertEqual(css_text.count(CSS_MARKER), 1)
        marker_idx = css_text.index(CSS_MARKER)
        self.assertGreater(marker_idx, len(css_text) * 0.9)
        tail = css_text[marker_idx:].strip()
        self.assertIn(".news-card{position:relative;cursor:pointer}", tail)
        self.assertIn(
            '.news-body :is(h2,h3) a::after{content:"";position:absolute;inset:0;z-index:1}',
            tail,
        )
        self.assertIn(
            ".news-card:has(:is(h2,h3) a:focus-visible) .news-body :is(h2,h3) a:focus-visible{outline:none}",
            tail,
        )

    def test_news_cards_have_single_link_accessible_name_and_no_nested_anchors(self):
        expected_href = reverse("core:news_detail", kwargs={"slug": self.pub_news.slug})
        for url in (reverse("core:home"), reverse("core:news_list")):
            with self.subTest(url=url):
                resp = self.client.get(url)
                self.assertEqual(resp.status_code, 200)
                html = resp.content.decode("utf-8")

                parser = _NewsCardHtmlParser()
                parser.feed(html)
                self.assertFalse(parser.nested_anchors_found)
                self.assertGreaterEqual(len(parser.cards), 1)

                matching_cards = 0
                for links in parser.cards:
                    self.assertEqual(
                        len(links),
                        1,
                        f"Картка новини на {url} повинна мати рівно одне посилання <a>.",
                    )
                    link = links[0]
                    self.assertTrue(link["href"].startswith("/novyny/"))
                    self.assertTrue(link["text"])
                    if link["href"] == expected_href:
                        matching_cards += 1
                        self.assertEqual(link["text"], self.pub_news.title)

                self.assertEqual(matching_cards, 1)

    def test_draft_news_not_rendered_in_cards(self):
        draft_href = reverse("core:news_detail", kwargs={"slug": self.draft_news.slug})
        for url in (reverse("core:home"), reverse("core:news_list")):
            with self.subTest(url=url):
                resp = self.client.get(url)
                self.assertEqual(resp.status_code, 200)
                html = resp.content.decode("utf-8")
                self.assertNotIn(self.draft_news.title, html)
                self.assertNotIn(draft_href, html)

