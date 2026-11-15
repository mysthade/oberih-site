#!/usr/bin/env python
"""
Інструмент вимірювання геометрії верстки та порівняння «до / після» (Кроки 1–3).

Працює ЛИШЕ зі стандартною бібліотекою Python та Playwright із зовнішнього
середовища ..\\layout-venv. Не імпортує Django і не змінює даних у базі
(виконує виключно GET-запити до 127.0.0.1 або localhost).

Приклади запуску:
  ..\\layout-venv\\Scripts\\python.exe tools/layout_check.py measure --base-url http://127.0.0.1:8000 --out-dir ..\\layout\\before
  ..\\layout-venv\\Scripts\\python.exe tools/layout_check.py measure --base-url http://127.0.0.1:8000 --out-dir ..\\layout\\after
  ..\\layout-venv\\Scripts\\python.exe tools/layout_check.py compare --before-dir ..\\layout\\before --after-dir ..\\layout\\after
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import struct
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zlib
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent

ALLOWED_HOSTS = {"127.0.0.1", "localhost"}

VIEWPORTS: list[tuple[int, int]] = [
    (320, 568),
    (360, 640),
    (375, 667),
    (390, 844),
    (412, 915),
    (667, 375),
    (844, 390),
    (768, 1024),
    (820, 1180),
    (1024, 768),
    (1280, 720),
    (1366, 768),
    (1440, 900),
    (1536, 864),
    (1600, 900),
    (1920, 1080),
    (2560, 1080),
    (2560, 1300),
    (2560, 1440),
    (3840, 2160),
]

SCREENSHOT_VIEWPORTS: set[tuple[int, int]] = {
    (360, 640),
    (768, 1024),
    (1280, 720),
    (1920, 1080),
    (2560, 1440),
}

def select_typical_paths(paths: list[str]) -> list[str]:
    """Вибирає 6 типових адрес (головна, коротка сторінка, довга сторінка, новина, контакти, 404)."""
    news_detail = next(
        (p for p in paths if p.startswith("/novyny/") and p != "/novyny/"),
        "/novyny/",
    )
    short_page = (
        "/bezpeka-ta-zakhyst/"
        if "/bezpeka-ta-zakhyst/" in paths
        else next((p for p in paths if p not in {"/", "/novyny/", "/kontakty/", "/neisnuiucha/"}), "/")
    )
    long_page = (
        "/ofitsiina-storinka/pravyla-pryiomu/"
        if "/ofitsiina-storinka/pravyla-pryiomu/" in paths
        else next((p for p in paths if p not in {"/", short_page, "/novyny/", "/kontakty/", "/neisnuiucha/"}), "/")
    )
    candidates = [
        "/",
        short_page,
        long_page,
        news_detail,
        "/kontakty/",
        "/neisnuiucha/",
    ]
    result: list[str] = []
    for c in candidates:
        if c not in result:
            result.append(c)
    return result

EXTRA_STATES: list[str] = [
    "scale_130",
    "contrast_high",
    "links_underline",
    "theme_dark",
    "mobile_menu_open",
    "zoom_200",
    "no_reduced_motion",
]


def validate_base_url(base_url: str) -> str:
    """Перевіряє, що базова адреса веде виключно на 127.0.0.1 або localhost."""
    parsed = urllib.parse.urlparse(base_url)
    if parsed.scheme not in {"http", "https"}:
        sys.exit(f"ПОМИЛКА: Непідтримувана схема URL: {parsed.scheme!r}. Дозволено лише http/https.")
    hostname = (parsed.hostname or "").lower()
    if hostname not in ALLOWED_HOSTS:
        sys.exit(
            f"ПОМИЛКА БЕЗПЕКИ: Хост {hostname!r} заборонено! "
            f"Скрипт працює виключно з локальним сервером ({', '.join(sorted(ALLOWED_HOSTS))})."
        )
    return base_url.rstrip("/")


def validate_out_dir(out_dir: Path) -> Path:
    """Перевіряє, що каталог результатів розташований поза межами проєкту."""
    resolved = out_dir.resolve()
    try:
        resolved.relative_to(PROJECT_ROOT)
        is_inside = True
    except ValueError:
        is_inside = False
    if is_inside:
        sys.exit(
            f"ПОМИЛКА: Каталог результатів {resolved} лежить усередині проєкту {PROJECT_ROOT}. "
            f"Використовуйте каталог поза проєктом (наприклад, ..\\layout\\before)."
        )
    return resolved


def fetch_sitemap_paths(base_url: str, timeout_s: float = 20.0) -> tuple[list[str], int]:
    """Завантажує /sitemap.xml через GET і повертає список шляхів + кількість адрес у sitemap."""
    sitemap_url = f"{base_url}/sitemap.xml"
    req = urllib.request.Request(sitemap_url, method="GET")
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        xml_bytes = resp.read()

    root = ET.fromstring(xml_bytes)
    ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    locs = [el.text.strip() for el in root.findall(".//sm:loc", ns) if el.text]
    if not locs:
        locs = [el.text.strip() for el in root.iter() if el.tag.endswith("loc") and el.text]

    paths: list[str] = []
    seen: set[str] = set()
    for loc in locs:
        parsed = urllib.parse.urlparse(loc)
        path = parsed.path or "/"
        if not path.startswith("/"):
            path = "/" + path
        if path not in seen:
            seen.add(path)
            paths.append(path)

    sitemap_count = len(paths)

    for extra_path in ("/kontakty/", "/neisnuiucha/"):
        if extra_path not in seen:
            seen.add(extra_path)
            paths.append(extra_path)

    return paths, sitemap_count


def slugify_path(path: str) -> str:
    if path == "/":
        return "home"
    cleaned = path.strip("/").replace("/", "__")
    return cleaned or "home"


MEASURE_JS = """
() => {
  const docEl = document.documentElement;
  const body = document.body;
  const vw = window.innerWidth;
  const vh = window.innerHeight;
  const scrollHeight = docEl.scrollHeight;
  const scrollWidth = docEl.scrollWidth;
  const clientWidth = docEl.clientWidth;
  const clientHeight = docEl.clientHeight;

  function rectOf(sel) {
    const el = document.querySelector(sel);
    if (!el) return null;
    const r = el.getBoundingClientRect();
    const cs = window.getComputedStyle(el);
    return {
      top: Math.round(r.top * 100) / 100,
      bottom: Math.round(r.bottom * 100) / 100,
      left: Math.round(r.left * 100) / 100,
      right: Math.round(r.right * 100) / 100,
      width: Math.round(r.width * 100) / 100,
      height: Math.round(r.height * 100) / 100,
      position: cs.position,
      display: cs.display,
      visibility: cs.visibility
    };
  }

  const a11ybar = rectOf('.a11ybar');
  const header = rectOf('.site-header');
  const mainNav = rectOf('nav.main-nav');
  const main = rectOf('main');
  const footer = rectOf('.site-footer');
  const firstMainChild = rectOf('main > *:first-child');
  const lastMainChild = rectOf('main > *:last-child');

  const footerGapToViewportBottom = footer
    ? Math.round((vh - footer.bottom) * 100) / 100
    : null;
  const mainToFooterGap = (main && footer)
    ? Math.round((footer.top - main.bottom) * 100) / 100
    : null;
  const lastChildToFooterGap = (lastMainChild && footer)
    ? Math.round((footer.top - lastMainChild.bottom) * 100) / 100
    : null;
  const mainInternalBottomGap = (main && lastMainChild)
    ? Math.round((main.bottom - lastMainChild.bottom) * 100) / 100
    : null;

  // Природна висота вмісту потоку (без штучного розтягнення main або margin-top:auto підвалу)
  const contentBottomWithoutStretch = lastMainChild ? lastMainChild.bottom : (main ? main.bottom : 0);
  const naturalTotalHeight = footer
    ? Math.round((contentBottomWithoutStretch + footer.height) * 100) / 100
    : scrollHeight;

  return {
    viewport: { width: vw, height: vh },
    innerHeight: vh,
    innerWidth: vw,
    scrollHeight: scrollHeight,
    scrollWidth: scrollWidth,
    clientWidth: clientWidth,
    clientHeight: clientHeight,
    devicePixelRatio: window.devicePixelRatio || 1,
    hasHorizontalScroll: scrollWidth > clientWidth,
    hasVerticalScroll: scrollHeight > vh,
    naturalTotalHeight: naturalTotalHeight,
    isNaturallyShort: naturalTotalHeight < vh - 0.5,
    rects: {
      a11ybar: a11ybar,
      siteHeader: header,
      mainNav: mainNav,
      main: main,
      siteFooter: footer,
      firstMainChild: firstMainChild,
      lastMainChild: lastMainChild
    },
    footerGapToViewportBottom: footerGapToViewportBottom,
    mainToFooterGap: mainToFooterGap,
    lastChildToFooterGap: lastChildToFooterGap,
    mainInternalBottomGap: mainInternalBottomGap
  };
}
"""


async def wait_page_settled(page: Any, wait_preload_Gone: bool = False) -> None:
    """Дочікується завантаження всіх начертань шрифтів, синхронного перерахунку меню (fitNav) і прелоадера."""
    await page.evaluate(
        """async () => {
            if (document.fonts) {
                await Promise.all([
                    document.fonts.load('700 1rem "Unbounded"'),
                    document.fonts.load('400 1rem "Nunito"'),
                    document.fonts.load('700 1rem "Nunito"'),
                    document.fonts.load('800 1rem "Nunito"'),
                    document.fonts.ready
                ]);
            }
            window.dispatchEvent(new Event('oberih:scale'));
        }"""
    )
    if wait_preload_Gone:
        await page.wait_for_function(
            """() => {
                const cl = document.documentElement.classList;
                return !cl.contains('preload') && !cl.contains('preload-intro') &&
                       !cl.contains('preload-quiet') && !cl.contains('preload-out');
            }""",
            timeout=10000,
        )
        # Зупиняємо автопрокрутку слайдера новин через подію pointerdown на треку (без зміни коду сайту)
        await page.evaluate(
            """() => {
                const track = document.getElementById('news-track');
                if (track) {
                    track.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true }));
                    track.scrollTo({ left: 0, behavior: 'instant' });
                }
            }"""
        )
    await page.evaluate("() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))")


async def measure_url_worker(
    sem: asyncio.Semaphore,
    browser: Any,
    base_url: str,
    path: str,
    is_typical: bool,
    screenshots_dir: Path,
    timeout_ms: int,
) -> list[dict[str, Any]]:
    """Вимірює одну адресу у всіх розмірах вікна та додаткових станах (якщо типова)."""
    records: list[dict[str, Any]] = []
    full_url = f"{base_url}{path}"

    async with sem:
        # 1. Основний контекст (reduced_motion='reduce')
        context = await browser.new_context(
            viewport={"width": 1280, "height": 720},
            reduced_motion="reduce",
            device_scale_factor=1,
        )
        page = await context.new_page()
        page.set_default_timeout(timeout_ms)
        page.set_default_navigation_timeout(timeout_ms)

        try:
            resp = await page.goto(full_url, wait_until="load", timeout=timeout_ms)
            status_code = resp.status if resp else 0
            await wait_page_settled(page, wait_preload_Gone=False)

            for vw, vh in VIEWPORTS:
                await page.set_viewport_size({"width": vw, "height": vh})
                await page.evaluate("() => window.scrollTo(0, 0)")
                await wait_page_settled(page, wait_preload_Gone=False)

                data = await page.evaluate(MEASURE_JS)
                data["path"] = path
                data["status"] = status_code
                data["state"] = "normal"
                data["viewport_key"] = f"{vw}x{vh}"
                records.append(data)

                if is_typical and (vw, vh) in SCREENSHOT_VIEWPORTS:
                    slug = slugify_path(path)
                    vp_shot = screenshots_dir / f"{slug}__{vw}x{vh}__viewport.png"
                    fp_shot = screenshots_dir / f"{slug}__{vw}x{vh}__full.png"
                    await page.screenshot(path=str(vp_shot), full_page=False)
                    await page.screenshot(path=str(fp_shot), full_page=True)

            has_a11y = await page.evaluate("() => !!document.querySelector('.a11ybar')")
            if is_typical and has_a11y:
                # Додаткові стани доступності на 6 типових адресах
                for state in ("scale_130", "contrast_high", "links_underline", "theme_dark", "mobile_menu_open"):
                    for vw, vh in VIEWPORTS:
                        if state == "mobile_menu_open" and vw >= 1200:
                            continue
                        await page.set_viewport_size({"width": vw, "height": vh})
                        await page.evaluate("() => window.scrollTo(0, 0)")

                        # Скидаємо попередні стани до чистого звичайного стану
                        await page.evaluate(
                            """() => {
                                const resetBtn = document.getElementById('btn-reset');
                                if (resetBtn) resetBtn.click();
                                if (document.documentElement.getAttribute('data-scheme') === 'dark') {
                                    const tb = document.querySelector('.a11y-tools .theme-toggle');
                                    if (tb) tb.click();
                                }
                                const nav = document.getElementById('main-nav');
                                if (nav && nav.classList.contains('open')) {
                                    const closeBtn = nav.querySelector('.drawer-close');
                                    if (closeBtn) closeBtn.click();
                                }
                            }"""
                        )

                        if state == "scale_130":
                            await page.click('button[data-scale="1.3"]')
                        elif state == "contrast_high":
                            await page.click("#btn-contrast")
                        elif state == "links_underline":
                            await page.click("#btn-links")
                        elif state == "theme_dark":
                            await page.evaluate("() => document.querySelector('.a11y-tools .theme-toggle').click()")
                        elif state == "mobile_menu_open":
                            await page.click(".burger")

                        await wait_page_settled(page, wait_preload_Gone=False)
                        data = await page.evaluate(MEASURE_JS)
                        data["path"] = path
                        data["status"] = status_code
                        data["state"] = state
                        data["viewport_key"] = f"{vw}x{vh}"
                        records.append(data)

                    # Повертаємо сторінку у чистий стан після серії
                    await page.evaluate(
                        """() => {
                            const resetBtn = document.getElementById('btn-reset');
                            if (resetBtn) resetBtn.click();
                            if (document.documentElement.getAttribute('data-scheme') === 'dark') {
                                const tb = document.querySelector('.a11y-tools .theme-toggle');
                                if (tb) tb.click();
                            }
                            const nav = document.getElementById('main-nav');
                            if (nav && nav.classList.contains('open')) {
                                const closeBtn = nav.querySelector('.drawer-close');
                                if (closeBtn) closeBtn.click();
                            }
                        }"""
                    )
        finally:
            await context.close()

        # 2. Масштаб 200% (device_scale_factor=2 та перевірка у типових розмірах)
        if is_typical:
            zoom_ctx = await browser.new_context(
                viewport={"width": 1280, "height": 720},
                reduced_motion="reduce",
                device_scale_factor=2,
            )
            zpage = await zoom_ctx.new_page()
            zpage.set_default_timeout(timeout_ms)
            zpage.set_default_navigation_timeout(timeout_ms)
            try:
                resp = await zpage.goto(full_url, wait_until="load", timeout=timeout_ms)
                status_code = resp.status if resp else 0
                await wait_page_settled(zpage, wait_preload_Gone=False)
                for vw, vh in VIEWPORTS:
                    await zpage.set_viewport_size({"width": vw, "height": vh})
                    await zpage.evaluate("() => window.scrollTo(0, 0)")
                    await wait_page_settled(zpage, wait_preload_Gone=False)
                    data = await zpage.evaluate(MEASURE_JS)
                    data["path"] = path
                    data["status"] = status_code
                    data["state"] = "zoom_200"
                    data["viewport_key"] = f"{vw}x{vh}"
                    records.append(data)
            finally:
                await zoom_ctx.close()

            # 3. Контрольне вимірювання БЕЗ reduced_motion
            norm_ctx = await browser.new_context(
                viewport={"width": 1280, "height": 720},
                reduced_motion="no-preference",
                device_scale_factor=1,
            )
            npage = await norm_ctx.new_page()
            npage.set_default_timeout(timeout_ms)
            npage.set_default_navigation_timeout(timeout_ms)
            try:
                resp = await npage.goto(full_url, wait_until="load", timeout=timeout_ms)
                status_code = resp.status if resp else 0
                await wait_page_settled(npage, wait_preload_Gone=True)
                for vw, vh in VIEWPORTS:
                    await npage.set_viewport_size({"width": vw, "height": vh})
                    await npage.evaluate("() => window.scrollTo(0, 0)")
                    await wait_page_settled(npage, wait_preload_Gone=True)
                    data = await npage.evaluate(MEASURE_JS)
                    data["path"] = path
                    data["status"] = status_code
                    data["state"] = "no_reduced_motion"
                    data["viewport_key"] = f"{vw}x{vh}"
                    records.append(data)
            finally:
                await norm_ctx.close()

    return records


async def run_measure(
    base_url: str,
    out_dir: Path,
    concurrency: int = 5,
    timeout_s: float = 20.0,
) -> int:
    from playwright.async_api import async_playwright

    base_url = validate_base_url(base_url)
    out_dir = validate_out_dir(out_dir)
    screenshots_dir = out_dir / "screenshots"
    screenshots_dir.mkdir(parents=True, exist_ok=True)

    paths, sitemap_count = fetch_sitemap_paths(base_url, timeout_s=timeout_s)
    typical_paths = select_typical_paths(paths)
    typical_set = set(typical_paths)

    t0 = time.perf_counter()
    timeout_ms = int(timeout_s * 1000)
    sem = asyncio.Semaphore(max(1, min(concurrency, 5)))

    all_records: list[dict[str, Any]] = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            tasks = [
                measure_url_worker(
                    sem=sem,
                    browser=browser,
                    base_url=base_url,
                    path=path,
                    is_typical=(path in typical_set),
                    screenshots_dir=screenshots_dir,
                    timeout_ms=timeout_ms,
                )
                for path in paths
            ]
            results = await asyncio.gather(*tasks)
            for res in results:
                all_records.extend(res)
        finally:
            await browser.close()

    elapsed = round(time.perf_counter() - t0, 2)

    normal_records = [r for r in all_records if r["state"] == "normal"]
    short_normal_combos = [
        r for r in normal_records if (r["footerGapToViewportBottom"] or 0) > 1.0
    ]
    naturally_short_normal_combos = [
        r for r in normal_records if r.get("isNaturallyShort")
    ]
    short_pages_unfixed = sorted({r["path"] for r in short_normal_combos})
    naturally_short_pages = sorted({r["path"] for r in naturally_short_normal_combos})

    summary = {
        "base_url": base_url,
        "sitemap_urls_count": sitemap_count,
        "total_urls_count": len(paths),
        "urls": paths,
        "typical_urls": typical_paths,
        "viewports": [f"{w}x{h}" for w, h in VIEWPORTS],
        "total_combinations": len(all_records),
        "normal_combinations": len(normal_records),
        "extra_state_combinations": len(all_records) - len(normal_records),
        "unpinned_short_combinations_normal": len(short_normal_combos),
        "unpinned_short_pages_count": len(short_pages_unfixed),
        "unpinned_short_pages": short_pages_unfixed,
        "naturally_short_combinations_normal": len(naturally_short_normal_combos),
        "naturally_short_pages_count": len(naturally_short_pages),
        "naturally_short_pages": naturally_short_pages,
        "elapsed_seconds": elapsed,
    }

    payload = {
        "summary": summary,
        "records": all_records,
    }
    results_path = out_dir / "metrics.json"
    results_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print("=== ПІДСУМОК ВИМІРЮВАННЯ ===")
    print(f"Базова адреса: {base_url}")
    print(f"Каталог результатів: {out_dir}")
    print(f"Адрес у /sitemap.xml: {sitemap_count}")
    print(f"Усього перевірених адрес (разом із /neisnuiucha/): {len(paths)}")
    print(f"Розмірів екрана в матриці: {len(VIEWPORTS)}")
    print(f"Усього виміряних комбінацій: {len(all_records)}")
    print(f"  - у звичайному стані (всі адреси × {len(VIEWPORTS)} розмірів): {len(normal_records)}")
    print(f"  - у додаткових станах (6 типових адрес): {len(all_records) - len(normal_records)}")
    print(f"Коротких комбінацій за природною висотою вмісту (звичайний стан): {len(naturally_short_normal_combos)}")
    print(f"Сторінок, які є короткими хоча б на одному розмірі вікна: {len(naturally_short_pages)} з {len(paths)}")
    print(f"Комбінацій (звичайний стан), де підвал НЕ притиснутий до низу (відступ > 1 px): {len(short_normal_combos)}")
    print(f"Сторінок, де підвал НЕ притиснутий до низу хоча б на одному розмірі вікна: {len(short_pages_unfixed)}")
    if short_pages_unfixed:
        print("Список сторінок, де виявлено непритиснутий підвал:")
        for p_item in short_pages_unfixed:
            gaps = [
                f"{r['viewport_key']}: +{r['footerGapToViewportBottom']}px"
                for r in short_normal_combos
                if r["path"] == p_item
            ]
            print(f"  {p_item} ({len(gaps)} розм.): {', '.join(gaps)}")
    print(f"Час виконання: {elapsed} с")
    return 0


# ---------------------------------------------------------------------------
# Декодування та попіксельне порівняння PNG (лише стандартна бібліотека)
# ---------------------------------------------------------------------------

def _paeth_predictor(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa = abs(p - a)
    pb = abs(p - b)
    pc = abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    if pb <= pc:
        return b
    return c


def decode_png_raw_scanlines(png_path: Path) -> tuple[int, int, int, list[bytes]]:
    """Повертає (width, height, bytes_per_pixel, list_of_row_bytes) для 8-бітного PNG."""
    raw = png_path.read_bytes()
    if raw[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"Не PNG-файл: {png_path}")

    pos = 8
    width = height = bit_depth = color_type = 0
    idat_chunks: list[bytes] = []

    while pos < len(raw):
        length = struct.unpack(">I", raw[pos : pos + 4])[0]
        chunk_type = raw[pos + 4 : pos + 8]
        chunk_data = raw[pos + 8 : pos + 8 + length]
        pos += 12 + length

        if chunk_type == b"IHDR":
            width, height, bit_depth, color_type, _, _, _ = struct.unpack(">IIBBBBB", chunk_data)
        elif chunk_type == b"IDAT":
            idat_chunks.append(chunk_data)
        elif chunk_type == b"IEND":
            break

    if bit_depth != 8:
        raise ValueError(f"Підтримується лише bit_depth=8, отримано {bit_depth} у {png_path}")

    channels_map = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}
    if color_type not in channels_map:
        raise ValueError(f"Непідтримуваний color_type={color_type} у {png_path}")
    bpp = channels_map[color_type]

    decompressed = zlib.decompress(b"".join(idat_chunks))
    stride = width * bpp
    rows: list[bytes] = []
    offset = 0
    prev_row = bytearray(stride)

    for _ in range(height):
        filter_type = decompressed[offset]
        offset += 1
        cur = bytearray(decompressed[offset : offset + stride])
        offset += stride

        if filter_type == 1:  # Sub
            for i in range(bpp, stride):
                cur[i] = (cur[i] + cur[i - bpp]) & 0xFF
        elif filter_type == 2:  # Up
            for i in range(stride):
                cur[i] = (cur[i] + prev_row[i]) & 0xFF
        elif filter_type == 3:  # Average
            for i in range(stride):
                left = cur[i - bpp] if i >= bpp else 0
                up = prev_row[i]
                cur[i] = (cur[i] + ((left + up) >> 1)) & 0xFF
        elif filter_type == 4:  # Paeth
            for i in range(stride):
                left = cur[i - bpp] if i >= bpp else 0
                up = prev_row[i]
                up_left = prev_row[i - bpp] if i >= bpp else 0
                cur[i] = (cur[i] + _paeth_predictor(left, up, up_left)) & 0xFF

        rows.append(bytes(cur))
        prev_row = cur

    return width, height, bpp, rows


def compare_png_files(before_png: Path, after_png: Path) -> dict[str, Any]:
    """Порівнює два PNG-файли попіксельно та повертає статистику розбіжностей."""
    if before_png.read_bytes() == after_png.read_bytes():
        w, h, _, _ = decode_png_raw_scanlines(before_png)
        return {
            "identical_bytes": True,
            "same_dimensions": True,
            "width": w,
            "height": h,
            "diff_pixels": 0,
            "total_pixels": w * h,
            "diff_percent": 0.0,
            "bbox": None,
        }

    w1, h1, bpp1, rows1 = decode_png_raw_scanlines(before_png)
    w2, h2, bpp2, rows2 = decode_png_raw_scanlines(after_png)
    if (w1, h1, bpp1) != (w2, h2, bpp2):
        return {
            "identical_bytes": False,
            "same_dimensions": False,
            "before_size": f"{w1}x{h1}",
            "after_size": f"{w2}x{h2}",
            "diff_pixels": abs(w1 * h1 - w2 * h2),
            "total_pixels": max(w1 * h1, w2 * h2),
            "diff_percent": 100.0,
            "bbox": (0, 0, max(w1, w2) - 1, max(h1, h2) - 1),
        }

    diff_pixels = 0
    min_x, min_y = w1, h1
    max_x, max_y = -1, -1

    for y in range(h1):
        r1 = rows1[y]
        r2 = rows2[y]
        if r1 == r2:
            continue
        for x in range(w1):
            i = x * bpp1
            if r1[i : i + bpp1] != r2[i : i + bpp1]:
                diff_pixels += 1
                if x < min_x:
                    min_x = x
                if x > max_x:
                    max_x = x
                if y < min_y:
                    min_y = y
                if y > max_y:
                    max_y = y

    total_pixels = w1 * h1
    diff_percent = round((diff_pixels / total_pixels) * 100.0, 4) if total_pixels else 0.0
    bbox = (min_x, min_y, max_x, max_y) if diff_pixels > 0 else None
    return {
        "identical_bytes": False,
        "same_dimensions": True,
        "width": w1,
        "height": h1,
        "diff_pixels": diff_pixels,
        "total_pixels": total_pixels,
        "diff_percent": diff_percent,
        "bbox": bbox,
    }


def run_compare(before_dir: Path, after_dir: Path) -> int:
    before_dir = validate_out_dir(before_dir)
    after_dir = validate_out_dir(after_dir)

    before_data = json.loads((before_dir / "metrics.json").read_text(encoding="utf-8"))
    after_data = json.loads((after_dir / "metrics.json").read_text(encoding="utf-8"))

    b_map = {(r["path"], r["viewport_key"], r["state"]): r for r in before_data["records"]}
    a_map = {(r["path"], r["viewport_key"], r["state"]): r for r in after_data["records"]}

    common_keys = sorted(set(b_map.keys()) & set(a_map.keys()))

    # Лічильники для критеріїв приймання
    crit_a_short_total = 0
    crit_a_short_pass = 0
    crit_a_short_max_dev = 0.0
    crit_a_short_failures: list[str] = []

    crit_a_long_total = 0
    crit_a_long_pass = 0
    crit_a_long_max_dev = 0.0
    crit_a_long_failures: list[str] = []

    crit_b_total = 0
    crit_b_pass = 0
    crit_b_max_dev = 0.0
    crit_b_failures: list[str] = []

    crit_c_new_hscroll = 0
    crit_c_new_spurious_vscroll = 0
    crit_c_failures: list[str] = []

    crit_d_long_total = 0
    crit_d_long_pass = 0
    crit_d_long_max_dev = 0.0
    crit_d_failures: list[str] = []

    # Окремо для додаткових станів (критерій д)
    crit_e_states_total = 0
    crit_e_states_pass = 0
    crit_e_states_failures: list[str] = []

    # Окремо для малих екранів (320x568 ... 1280x720) і станів доступності:
    # перевірка .a11ybar, .site-header, nav.main-nav, main і scrollHeight
    small_screens = {
        "320x568", "360x640", "375x667", "390x844", "412x915",
        "667x375", "844x390", "768x1024", "820x1180", "1024x768", "1280x720"
    }
    small_screen_total = 0
    small_screen_pass = 0
    small_screen_max_dev = 0.0

    # Перевірка фонів секцій усередині main (firstMainChild, lastMainChild)
    main_children_total = 0
    main_children_pass = 0
    main_children_max_dev = 0.0

    # Перевірка внутрішнього відступу main (для порівняння Варіанта А і Варіанта Б)
    short_main_internal_gaps: list[float] = []
    short_main_to_footer_gaps: list[float] = []

    for key in common_keys:
        path, vp_key, state = key
        b = b_map[key]
        a = a_map[key]

        vh = b["innerHeight"]
        b_footer = b["rects"]["siteFooter"]
        a_footer = a["rects"]["siteFooter"]

        # Визначаємо, чи була комбінація короткою (нижчою за вікно у «до»)
        was_short = (b["footerGapToViewportBottom"] is not None and b["footerGapToViewportBottom"] > 1.0) or (
            b["scrollHeight"] <= vh and (b["footerGapToViewportBottom"] or 0) >= 0
        )

        state_ok = True

        # (а) Положення підвалу
        if was_short:
            crit_a_short_total += 1
            gap_after = abs(a["footerGapToViewportBottom"] or 0.0)
            crit_a_short_max_dev = max(crit_a_short_max_dev, gap_after)
            if gap_after <= 1.0:
                crit_a_short_pass += 1
            else:
                state_ok = False
                crit_a_short_failures.append(f"{path} [{vp_key} {state}]: footerGap={a['footerGapToViewportBottom']}px")
            if state == "normal" and (b["footerGapToViewportBottom"] or 0) > 1.0:
                short_main_internal_gaps.append(a.get("mainInternalBottomGap") or 0.0)
                short_main_to_footer_gaps.append(a.get("mainToFooterGap") or 0.0)
        else:
            crit_a_long_total += 1
            top_diff = abs(a_footer["top"] - b_footer["top"]) if (a_footer and b_footer) else 999.0
            bot_diff = abs(a_footer["bottom"] - b_footer["bottom"]) if (a_footer and b_footer) else 999.0
            dev = max(top_diff, bot_diff)
            crit_a_long_max_dev = max(crit_a_long_max_dev, dev)
            if dev <= 1.0:
                crit_a_long_pass += 1
            else:
                state_ok = False
                crit_a_long_failures.append(f"{path} [{vp_key} {state}]: footer dev={dev:.2f}px")

        # (б) Висота й положення .a11ybar, .site-header, nav.main-nav
        crit_b_total += 1
        el_max_dev = 0.0
        for el_name in ("a11ybar", "siteHeader", "mainNav"):
            br = b["rects"].get(el_name)
            ar = a["rects"].get(el_name)
            if br is None and ar is None:
                continue
            if br is None or ar is None:
                el_max_dev = 999.0
                break
            d = max(
                abs(ar["top"] - br["top"]),
                abs(ar["bottom"] - br["bottom"]),
                abs(ar["height"] - br["height"]),
            )
            el_max_dev = max(el_max_dev, d)
        crit_b_max_dev = max(crit_b_max_dev, el_max_dev)
        if el_max_dev <= 1.0:
            crit_b_pass += 1
        else:
            state_ok = False
            crit_b_failures.append(f"{path} [{vp_key} {state}]: header/nav/a11y dev={el_max_dev:.2f}px")

        # Перевірка фонів секцій усередині main (firstMainChild, lastMainChild)
        main_children_total += 1
        mc_dev = 0.0
        for child_name in ("firstMainChild", "lastMainChild"):
            br = b["rects"].get(child_name)
            ar = a["rects"].get(child_name)
            if br and ar:
                mc_dev = max(
                    mc_dev,
                    abs(ar["top"] - br["top"]),
                    abs(ar["bottom"] - br["bottom"]),
                    abs(ar["height"] - br["height"]),
                )
        main_children_max_dev = max(main_children_max_dev, mc_dev)
        if mc_dev <= 1.0:
            main_children_pass += 1

        # Окрема перевірка малих екранів (320x568 ... 1280x720): .a11ybar, .site-header, nav.main-nav, main, scrollHeight
        if vp_key in small_screens:
            small_screen_total += 1
            bm = b["rects"].get("main")
            am = a["rects"].get("main")
            m_dev = max(
                abs(am["top"] - bm["top"]),
                abs(am["bottom"] - bm["bottom"]),
                abs(am["height"] - bm["height"]),
            ) if (bm and am) else 999.0
            sh_d = abs(a["scrollHeight"] - b["scrollHeight"])
            ss_dev = max(el_max_dev, m_dev, float(sh_d))
            small_screen_max_dev = max(small_screen_max_dev, ss_dev)
            if ss_dev <= 1.0:
                small_screen_pass += 1

        # (в) Горизонтальна прокрутка та зайвий вертикальний скрол на коротких сторінках
        if a["hasHorizontalScroll"] and not b["hasHorizontalScroll"]:
            crit_c_new_hscroll += 1
            state_ok = False
            crit_c_failures.append(f"Новий hscroll: {path} [{vp_key} {state}]")
        if was_short and a["scrollHeight"] > a["innerHeight"] + 1:
            crit_c_new_spurious_vscroll += 1
            state_ok = False
            crit_c_failures.append(
                f"Зайвий vscroll на короткій сторінці: {path} [{vp_key} {state}] "
                f"(scrollHeight={a['scrollHeight']}, innerHeight={a['innerHeight']})"
            )

        # (г) На сторінках, вищих за вікно: scrollHeight однаковий із «до» (<= 1 px)
        if not was_short:
            crit_d_long_total += 1
            sh_diff = abs(a["scrollHeight"] - b["scrollHeight"])
            crit_d_long_max_dev = max(crit_d_long_max_dev, float(sh_diff))
            if sh_diff <= 1:
                crit_d_long_pass += 1
            else:
                state_ok = False
                crit_d_failures.append(
                    f"{path} [{vp_key} {state}]: scrollHeight до={b['scrollHeight']}, після={a['scrollHeight']}"
                )

        # (д) Стани доступності, темна тема, мобільне меню, масштаб 200%, без reduced_motion
        if state != "normal":
            crit_e_states_total += 1
            if state_ok:
                crit_e_states_pass += 1
            else:
                crit_e_states_failures.append(f"{path} [{vp_key} {state}]")

    # (е) Порівняння скріншотів
    b_shots_dir = before_dir / "screenshots"
    a_shots_dir = after_dir / "screenshots"
    shot_comparisons: list[dict[str, Any]] = []
    if b_shots_dir.exists() and a_shots_dir.exists():
        for b_shot in sorted(b_shots_dir.glob("*.png")):
            a_shot = a_shots_dir / b_shot.name
            if not a_shot.exists():
                continue
            cmp_res = compare_png_files(b_shot, a_shot)
            cmp_res["filename"] = b_shot.name
            cmp_res["before_path"] = str(b_shot)
            cmp_res["after_path"] = str(a_shot)
            shot_comparisons.append(cmp_res)

    print("=== РЕЗУЛЬТАТИ ПОРІВНЯННЯ «ДО» ТА «ПІСЛЯ» ===")
    print(f"Усього порівняних комбінацій: {len(common_keys)}")
    print()
    print(
        f"Критерій а (короткі сторінки — низ підвалу біля низу вікна, <= 1 px): "
        f"{crit_a_short_pass}/{crit_a_short_total} пройдено (макс. відхилення: {crit_a_short_max_dev:.2f} px)"
    )
    print(
        f"Критерій а (довгі сторінки — положення підвалу не змінилось, <= 1 px): "
        f"{crit_a_long_pass}/{crit_a_long_total} пройдено (макс. відхилення: {crit_a_long_max_dev:.2f} px)"
    )
    print(
        f"Критерій б (.a11ybar, .site-header, nav.main-nav без змін, <= 1 px): "
        f"{crit_b_pass}/{crit_b_total} пройдено (макс. відхилення: {crit_b_max_dev:.2f} px)"
    )
    print(
        f"Критерій в (нова горизонтальна прокрутка / зайвий 1px вертикальний скрол): "
        f"нових hscroll={crit_c_new_hscroll}, зайвих vscroll={crit_c_new_spurious_vscroll}"
    )
    print(
        f"Критерій г (довгі сторінки — scrollHeight однаковий із «до», <= 1 px): "
        f"{crit_d_long_pass}/{crit_d_long_total} пройдено (макс. відхилення: {crit_d_long_max_dev:.2f} px)"
    )
    print(
        f"Критерій д (додаткові стани A++, контраст, підкреслення, темна тема, меню, 200%, без reduced-motion): "
        f"{crit_e_states_pass}/{crit_e_states_total} пройдено"
    )
    print(
        f"Малі екрани (320x568..1280x720, усі стани — .a11ybar, .site-header, nav.main-nav, main, scrollHeight <= 1 px): "
        f"{small_screen_pass}/{small_screen_total} пройдено (макс. відхилення: {small_screen_max_dev:.2f} px)"
    )
    print(
        f"Фони та розміри секцій усередині <main> (firstMainChild, lastMainChild <= 1 px): "
        f"{main_children_pass}/{main_children_total} пройдено (макс. відхилення: {main_children_max_dev:.2f} px)"
    )
    if short_main_internal_gaps:
        max_internal_gap = max(short_main_internal_gaps)
        max_m2f_gap = max(short_main_to_footer_gaps)
        print(
            f"Геометрія на коротких сторінках: макс. порожня смуга всередині <main> = {max_internal_gap:.2f} px; "
            f"макс. відступ між <main> і <footer> (margin-top:auto) = {max_m2f_gap:.2f} px"
        )

    print()
    print("=== КРИТЕРІЙ Е: ПОПІКСЕЛЬНЕ ПОРІВНЯННЯ СКРІНШОТІВ ===")
    for sc in shot_comparisons:
        bbox_str = f", область змін={sc['bbox']}" if sc.get("bbox") else ""
        print(
            f"  {sc['filename']}: розбіжність={sc['diff_percent']:.4f}% "
            f"({sc['diff_pixels']}/{sc['total_pixels']} пікс.){bbox_str}"
        )

    failures = (
        crit_a_short_failures
        + crit_a_long_failures
        + crit_b_failures
        + crit_c_failures
        + crit_d_failures
        + crit_e_states_failures
    )
    if failures:
        print("\nВИЯВЛЕНО ПОРУШЕННЯ КРИТЕРІЇВ:")
        for f in failures[:25]:
            print("  -", f)
        return 1

    print("\nУСІ КРИТЕРІЇ ПРИЙМАННЯ (а–е) ВИКОНАНО УСПІШНО.")
    return 0


def run_repeatability(dir1: Path, dir2: Path) -> int:
    """Перевіряє повторюваність двох запусків (різниця між двома замірами «до» <= 1 px у всіх комбінаціях)."""
    dir1 = validate_out_dir(dir1)
    dir2 = validate_out_dir(dir2)

    d1 = json.loads((dir1 / "metrics.json").read_text(encoding="utf-8"))
    d2 = json.loads((dir2 / "metrics.json").read_text(encoding="utf-8"))

    m1 = {(r["path"], r["viewport_key"], r["state"]): r for r in d1["records"]}
    m2 = {(r["path"], r["viewport_key"], r["state"]): r for r in d2["records"]}

    keys = sorted(set(m1.keys()) & set(m2.keys()))
    max_diff = 0.0
    passed = 0
    failures: list[str] = []

    for key in keys:
        r1 = m1[key]
        r2 = m2[key]
        comb_diff = max(
            abs(r1["scrollHeight"] - r2["scrollHeight"]),
            abs(r1["scrollWidth"] - r2["scrollWidth"]),
            abs((r1["footerGapToViewportBottom"] or 0.0) - (r2["footerGapToViewportBottom"] or 0.0)),
            abs((r1["mainToFooterGap"] or 0.0) - (r2["mainToFooterGap"] or 0.0)),
        )
        for el_name in ("a11ybar", "siteHeader", "mainNav", "main", "siteFooter", "firstMainChild", "lastMainChild"):
            b1 = r1["rects"].get(el_name)
            b2 = r2["rects"].get(el_name)
            if b1 and b2:
                comb_diff = max(
                    comb_diff,
                    abs(b1["top"] - b2["top"]),
                    abs(b1["bottom"] - b2["bottom"]),
                    abs(b1["height"] - b2["height"]),
                    abs(b1["width"] - b2["width"]),
                )
        max_diff = max(max_diff, float(comb_diff))
        if comb_diff <= 1.0:
            passed += 1
        else:
            failures.append(f"{key}: diff={comb_diff:.2f}px")

    print("=== ПЕРЕВІРКА ПОВТОРЮВАНОСТІ ДВОХ ЗАМІРІВ «ДО» ===")
    print(f"Каталог 1: {dir1}")
    print(f"Каталог 2: {dir2}")
    print(f"Порівняно комбінацій: {passed}/{len(keys)} у межах <= 1 px")
    print(f"Максимальна розбіжність між двома запусками за всіма метриками: {max_diff:.2f} px")
    if failures:
        print("Перевищення 1 px:")
        for f in failures[:20]:
            print("  -", f)
        return 1
    print("ПОВТОРЮВАНІСТЬ ПІДТВЕРДЖЕНО (різниця <= 1 px у 100% комбінацій).")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Перевірка верстки підвалу НРЦ «Оберіг».")
    sub = parser.add_subparsers(dest="cmd", required=True)

    m_parser = sub.add_parser("measure", help="Зняти метрики та скріншоти у каталог.")
    m_parser.add_argument("--base-url", default="http://127.0.0.1:8000", help="Базова адреса локального сервера.")
    m_parser.add_argument("--out-dir", required=True, type=Path, help="Каталог результатів поза проєктом.")
    m_parser.add_argument("--concurrency", type=int, default=5, help="Кількість паралельних сторінок (до 5).")
    m_parser.add_argument("--timeout", type=float, default=20.0, help="Таймаут очікування на сторінку (с).")

    c_parser = sub.add_parser("compare", help="Порівняти каталоги before та after.")
    c_parser.add_argument("--before-dir", required=True, type=Path, help="Каталог «до».")
    c_parser.add_argument("--after-dir", required=True, type=Path, help="Каталог «після».")

    r_parser = sub.add_parser("repeatability", help="Порівняти два запуски одного стану (до/до).")
    r_parser.add_argument("--dir1", required=True, type=Path, help="Перший каталог.")
    r_parser.add_argument("--dir2", required=True, type=Path, help="Другий каталог.")

    args = parser.parse_args()
    if args.cmd == "measure":
        return asyncio.run(
            run_measure(
                base_url=args.base_url,
                out_dir=args.out_dir,
                concurrency=args.concurrency,
                timeout_s=args.timeout,
            )
        )
    if args.cmd == "compare":
        return run_compare(before_dir=args.before_dir, after_dir=args.after_dir)
    if args.cmd == "repeatability":
        return run_repeatability(dir1=args.dir1, dir2=args.dir2)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

