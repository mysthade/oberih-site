"""Автоматичні тести притиснутого підвалу та цілісності каркасу сторінок (Крок 4)."""

from io import StringIO
from pathlib import Path
import re

from django.conf import settings
from django.core.management import call_command
from django.db import connection
from django.test import RequestFactory, TestCase, override_settings
from django.test.utils import CaptureQueriesContext

from .models import NewsItem, Page, Section
from .views import error_400, error_403, error_500

TEST_STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
    },
}

FOOTER_FIX_MARKER = "/* Додано для Django: підвал притиснутий до низу на коротких сторінках */"


@override_settings(STORAGES=TEST_STORAGES)
class FooterLayoutTests(TestCase):
    """Перевірка CSS-блоку притиснутого підвалу та структури <main> + <footer class="site-footer">."""

    @classmethod
    def setUpTestData(cls):
        call_command("import_site_map", yes=True, stdout=StringIO())
        call_command("import_home", yes=True, stdout=StringIO())

    def _collect_all_public_urls(self) -> list[tuple[str, int]]:
        """Повертає список усіх публічних адрес (головна, контакти, новини, розділи, сторінки, 404)."""
        urls: list[tuple[str, int]] = [
            ("/", 200),
            ("/kontakty/", 200),
            ("/novyny/", 200),
        ]
        for sec in Section.objects.order_by("order", "id"):
            urls.append((f"/{sec.slug}/", 200))
        for page in (
            Page.objects.filter(is_published=True)
            .select_related("section")
            .order_by("section__order", "order", "id")
        ):
            urls.append((f"/{page.section.slug}/{page.slug}/", 200))
        for news in NewsItem.objects.filter(is_published=True).order_by("-published_date", "-id"):
            urls.append((f"/novyny/{news.slug}/", 200))
        urls.append(("/neisnuiucha/", 404))
        return urls

    def test_style_css_footer_sticky_block_at_end_without_duplication(self):
        """
        static/css/style.css містить маркерний коментар рівно один раз, блок стоїть у кінці файлу,
        правила присутні без дублювання, а 100vh іде перед 100dvh.
        """
        css_path = Path(settings.BASE_DIR) / "static" / "css" / "style.css"
        css_text = css_path.read_text(encoding="utf-8")

        self.assertEqual(
            css_text.count(FOOTER_FIX_MARKER),
            1,
            "Маркерний коментар має зустрічатися в static/css/style.css рівно один раз.",
        )

        marker_idx = css_text.index(FOOTER_FIX_MARKER)
        tail_block = css_text[marker_idx:].strip()
        lines = [line.strip() for line in tail_block.splitlines() if line.strip()]

        # Блок має стояти в самому кінці файлу і складатися з коментаря та двох правил
        self.assertEqual(lines[0], FOOTER_FIX_MARKER)
        self.assertEqual(
            len(lines),
            3,
            f"Блок у кінці style.css має містити рівно 3 непорожні рядки, отримано: {lines!r}",
        )

        body_rule = lines[1]
        pin_rule = lines[2]

        self.assertIn("body{", body_rule)
        self.assertIn("display:flex", body_rule)
        self.assertIn("flex-direction:column", body_rule)
        self.assertIn("min-height:100vh", body_rule)
        self.assertIn("min-height:100dvh", body_rule)
        self.assertLess(
            body_rule.index("min-height:100vh"),
            body_rule.index("min-height:100dvh"),
            "Запасний варіант min-height:100vh має стояти перед min-height:100dvh.",
        )

        self.assertIn(
            pin_rule,
            {".site-footer{margin-top:auto}", "main{flex:1 0 auto}"},
            f"Очікувалось правило притискання підвалу, отримано: {pin_rule!r}",
        )

        # Перевіряємо відсутність дублювання цих правил у решті файлу
        before_block = css_text[:marker_idx]
        self.assertNotIn("min-height:100dvh", before_block)
        self.assertNotIn(".site-footer{margin-top:auto}", before_block)
        self.assertNotIn("main{flex:1 0 auto}", before_block)

    def test_all_pages_have_single_main_and_footer_after_main(self):
        """
        Усі опубліковані Page, розділи, головна, новини, контакти і 404 містять
        рівно один <main id="main"> і рівно один <footer class="site-footer"> після <main>.
        """
        urls = self._collect_all_public_urls()
        self.assertGreaterEqual(len(urls), 50)

        for url, expected_status in urls:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, expected_status)
                html = response.content.decode("utf-8")

                main_tags = re.findall(r"<main\b[^>]*>", html)
                footer_tags = re.findall(r"<footer\b[^>]*>", html)

                self.assertEqual(len(main_tags), 1, f"На {url} має бути рівно один тег <main>.")
                self.assertEqual(main_tags[0], '<main id="main">')

                self.assertEqual(len(footer_tags), 1, f"На {url} має бути рівно один тег <footer>.")
                self.assertEqual(footer_tags[0], '<footer class="site-footer">')

                main_close_pos = html.find("</main>")
                footer_open_pos = html.find('<footer class="site-footer">')
                self.assertNotEqual(main_close_pos, -1)
                self.assertGreater(
                    footer_open_pos,
                    main_close_pos,
                    f"На {url} підвал <footer class=\"site-footer\"> має стояти після </main>.",
                )

    def test_500_template_is_autonomous_and_has_same_layout_and_css(self):
        """
        templates/500.html автономний: не наслідує base.html, підключає css/style.css,
        має <main id="main"> та <footer class="site-footer"> і не виконує запитів до БД.
        """
        tpl_500 = Path(settings.BASE_DIR) / "templates" / "500.html"
        raw_tpl = tpl_500.read_text(encoding="utf-8")

        self.assertNotIn("{% extends", raw_tpl)
        self.assertIn('href="{% get_static_prefix %}css/style.css"', raw_tpl)
        self.assertEqual(len(re.findall(r"<main\b[^>]*>", raw_tpl)), 1)
        self.assertIn('<main id="main">', raw_tpl)
        self.assertEqual(len(re.findall(r"<footer\b[^>]*>", raw_tpl)), 1)
        self.assertIn('<footer class="site-footer">', raw_tpl)
        self.assertGreater(
            raw_tpl.find('<footer class="site-footer">'),
            raw_tpl.find("</main>"),
        )

        rf = RequestFactory()
        request = rf.get("/500-test/")
        with CaptureQueriesContext(connection) as ctx:
            response = error_500(request)

        self.assertEqual(response.status_code, 500)
        self.assertEqual(
            len(ctx.captured_queries),
            0,
            "Обробник 500 не повинен звертатися до бази даних.",
        )
        html = response.content.decode("utf-8")
        self.assertIn('<link rel="stylesheet" href="/static/css/style.css">', html)
        self.assertIn('<main id="main">', html)
        self.assertIn('<footer class="site-footer">', html)

    def test_all_pages_and_error_handlers_include_same_style_css(self):
        """Усі сторінки та сторінки помилок (400, 403, 404, 500) підключають той самий style.css."""
        expected_link = '<link rel="stylesheet" href="/static/css/style.css">'

        for url, expected_status in self._collect_all_public_urls():
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, expected_status)
                html = response.content.decode("utf-8")
                self.assertIn(expected_link, html)

        rf = RequestFactory()
        for code, handler in ((400, error_400), (403, error_403), (500, error_500)):
            with self.subTest(error_code=code):
                req = rf.get(f"/err-{code}/")
                resp = handler(req, Exception("test")) if code != 500 else handler(req)
                self.assertEqual(resp.status_code, code)
                self.assertIn(expected_link, resp.content.decode("utf-8"))

