"""
scraper.py - Playwright-based Riyasewana car scraping logic

scrape_riyasewana_task() now returns the raw car list in memory
and emits real-time progress via a queue.Queue.
"""

import re
import queue as _queue
from datetime import datetime
from playwright.async_api import async_playwright


async def close_popup_ad(page):
    """Close any popup or overlay ad that might block interaction."""
    try:
        # 1. Try standard close-button selectors
        close_selectors = [
            '#dismiss-button',
            '.close-button',
            '.close-button-outer',
            '[aria-label="Close ad"]',
            '[aria-label="Close"]',
            '.dismiss-button',
            'button:has-text("Close")',
            'div[role="button"][aria-label="Close ad"]',
            '#dismiss-button-element',
            '.continue-prompt-text',
        ]

        for selector in close_selectors:
            try:
                close_button = await page.query_selector(selector)
                if close_button and await close_button.is_visible():
                    print("  [!] Popup ad detected, closing...")
                    await close_button.click()
                    await page.wait_for_timeout(800)
                    return True
            except Exception:
                continue

        # 2. Try close button inside #ad_iframe
        try:
            iframe_ad = await page.query_selector('#ad_iframe')
            if iframe_ad:
                frame = await iframe_ad.content_frame()
                if frame:
                    close_in_iframe = await frame.query_selector('#dismiss-button')
                    if close_in_iframe:
                        print("  [!] Iframe ad detected, closing inside frame...")
                        await close_in_iframe.click()
                        await page.wait_for_timeout(800)
                        return True
        except Exception:
            pass

        # 3. Force-remove Google vignette / adsbygoogle overlay ads via JS
        removed = await page.evaluate("""
            () => {
                let removed = 0;
                const selectors = [
                    'ins.adsbygoogle[data-vignette-loaded]',
                    '#google_esf',
                    '#aswift_1_host',
                    '.adsbygoogle-noablate',
                ];
                for (const sel of selectors) {
                    document.querySelectorAll(sel).forEach(el => {
                        const r = el.getBoundingClientRect();
                        if (r.width > 200 && r.height > 200) {
                            el.remove();
                            removed++;
                        }
                    });
                }
                return removed;
            }
        """)
        if removed:
            print(f"  [!] Removed {removed} overlay ad element(s) via JS")
            await page.wait_for_timeout(500)
            return True

        return False

    except Exception as e:
        print(f"  [!] Error handling popup: {e}")
        return False


async def extract_car_details(page):
    """Extract car price, year and title from the current page."""
    cars_data = []

    await close_popup_ad(page)
    await page.wait_for_timeout(2000)

    car_items = await page.query_selector_all('li.v-card')
    if not car_items:
        car_items = await page.query_selector_all('.v-card')

    print(f"Found {len(car_items)} car listings on this page")

    for idx, item in enumerate(car_items, 1):
        try:
            price_elem = await item.query_selector('.v-card-price')
            if not price_elem:
                price_elem = await item.query_selector('[class*="price"]')

            price_text = await price_elem.inner_text() if price_elem else ""

            if price_text and "Negotiable" not in price_text:
                price = re.sub(r'[^0-9]', '', price_text)
            else:
                price = ""

            year_elem = await item.query_selector('.v-card-year')
            if year_elem:
                year_text = await year_elem.inner_text()
                year = year_text.strip() if year_text else ""
            else:
                year = ""

            title_elem = await item.query_selector('.v-card-title a')
            if title_elem:
                title_text = await title_elem.inner_text()
            else:
                title_elem = await item.query_selector('.v-card-title')
                title_text = await title_elem.inner_text() if title_elem else ""

            if not year and title_text:
                year_match = re.search(r'\b(19|20)\d{2}\b', title_text)
                year = year_match.group(0) if year_match else ""

            if price:
                cars_data.append({
                    'price': price,
                    'year': year if year else "Unknown",
                    'full_title': title_text.strip()[:200] if title_text else "Unknown",
                })
                print(f"  [+] Item {idx}: Year={year}, Price=Rs. {int(price):,}")

        except Exception:
            continue

    return cars_data


async def scrape_riyasewana_task(search_params: dict, progress_q: _queue.Queue) -> list:
    """
    Scrape Riyasewana and return all car data as a list of dicts.
    Emits real-time progress events to progress_q.
    Raises on unrecoverable errors.
    """

    def emit(step: str, message: str, **extra):
        payload = {'step': step, 'message': message, **extra}
        print(f"[STATUS] {step}: {message}")
        progress_q.put(payload)

    emit('navigating', 'Opening browser and navigating to Riyasewana...')

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            context = await browser.new_context(
                viewport={'width': 1280, 'height': 800},
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            )
            page = await context.new_page()

            await page.goto('https://riyasewana.com/', wait_until='networkidle')
            await page.wait_for_timeout(3000)
            await close_popup_ad(page)

            emit('filtering', 'Filling search filters and submitting...')

            # Fill select fields
            field_selectors = {
                'make':     'select[name="make"]',
                'vtype':    'select[name="vtype"]',
                'vcat':     'select[name="vcat"]',
                'city':     'select[name="city"]',
                'fuel':     'select[name="fuel"]',
                'trans':    'select[name="trans"]',
                'year_min': 'select[name="year"]',
                'year_max': 'select[name="year_max"]',
            }

            for param, selector in field_selectors.items():
                value = search_params.get(param)
                if value:
                    await page.select_option(selector, value)
                    await page.wait_for_timeout(400)

            if search_params.get('model'):
                model_input = await page.query_selector('input[name="model"]')
                if model_input:
                    await model_input.fill(search_params['model'])
                    await page.wait_for_timeout(400)

            if search_params.get('price_min'):
                el = await page.query_selector('input[name="pricemmin"]')
                if el:
                    await el.fill(str(search_params['price_min']))
                    await page.wait_for_timeout(400)

            if search_params.get('price_max'):
                el = await page.query_selector('input[name="pricemmax"]')
                if el:
                    await el.fill(str(search_params['price_max']))
                    await page.wait_for_timeout(400)

            # Submit search
            search_button = await page.query_selector(
                'button[name="srch_btn"], button[type="submit"]'
            )
            if not search_button:
                raise Exception("Could not find search button on the page")
            await search_button.click()
            await page.wait_for_timeout(5000)

            all_cars = []
            page_number = 1

            while True:
                emit(
                    'scraping',
                    f'Collecting listings — page {page_number}...',
                    page=page_number,
                    collected=len(all_cars),
                )
                await close_popup_ad(page)

                cars_on_page = await extract_car_details(page)
                all_cars.extend(cars_on_page)
                print(f"[+] Page {page_number}: {len(cars_on_page)} cars | Total: {len(all_cars)}")

                next_link = await page.query_selector('.pagination a:has-text("Next")')
                if not next_link:
                    break

                href = await next_link.get_attribute('href')
                if not href or href == '#':
                    break

                # Navigate by URL — never click (ads can't intercept navigation)
                if href.startswith('http'):
                    next_url = href
                else:
                    # Strip any accidental domain prefix (e.g. '//riyasewana.com/...')
                    href_clean = re.sub(r'^/+(?:riyasewana\.com)?', '', href)
                    next_url = 'https://riyasewana.com/' + href_clean.lstrip('/')
                print(f"[>] Navigating to page {page_number + 1}: {next_url}")
                await page.goto(next_url, wait_until='domcontentloaded')
                await page.wait_for_timeout(3000)
                page_number += 1

                if page_number > 50:
                    break

            return all_cars

        finally:
            await browser.close()
