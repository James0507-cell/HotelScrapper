import asyncio
import argparse
import json
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote_plus, urljoin, urlparse

import requests
from playwright.async_api import Page, TimeoutError, async_playwright
from playwright_stealth import Stealth


HOTELS_BASE_URL = "https://www.google.com/travel/hotels"


@dataclass
class HotelRecord:
    name: str | None = None
    price: str | None = None
    total_price: str | None = None
    currency: str | None = None
    rating: str | None = None
    review_count: str | None = None
    stars: str | None = None
    address: str | None = None
    phone: str | None = None
    website: str | None = None
    about: str | None = None
    amenities: dict[str, list[str]] = field(default_factory=dict)
    nearby_places: list[str] = field(default_factory=list)
    check_in: str | None = None
    check_out: str | None = None
    photos: list[str] = field(default_factory=list)
    source_url: str | None = None
    listing_url: str | None = None
    adults: int = 2
    children: int = 0
    search_check_in: str | None = None
    search_check_out: str | None = None


def unique_strings(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        normalized = re.sub(r"\s+", " ", item).strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result


async def first_text(scope: Any, selectors: list[str], timeout: int = 1500) -> str | None:
    for selector in selectors:
        try:
            locator = scope.locator(selector).first
            if await locator.count() and await locator.is_visible(timeout=timeout):
                text = await locator.inner_text(timeout=timeout)
                text = text.strip()
                if text:
                    return re.sub(r"\s+", " ", text)
        except Exception:
            continue
    return None


async def all_texts(scope: Any, selectors: list[str], limit: int = 100) -> list[str]:
    collected: list[str] = []
    for selector in selectors:
        try:
            locator = scope.locator(selector)
            count = min(await locator.count(), limit)
            for index in range(count):
                text = await locator.nth(index).inner_text(timeout=1000)
                text = text.strip()
                if text:
                    collected.append(text)
        except Exception:
            continue
    return unique_strings(collected)


async def all_attributes(scope: Any, selectors: list[str], attribute: str, limit: int = 100) -> list[str]:
    values: list[str] = []
    for selector in selectors:
        try:
            locator = scope.locator(selector)
            count = min(await locator.count(), limit)
            for index in range(count):
                value = await locator.nth(index).get_attribute(attribute, timeout=1000)
                if value:
                    values.append(value)
        except Exception:
            continue
    return unique_strings(values)


async def first_attribute(scope: Any, selectors: list[str], attribute: str, timeout: int = 1500) -> str | None:
    for selector in selectors:
        try:
            locator = scope.locator(selector).first
            if await locator.count():
                value = await locator.get_attribute(attribute, timeout=timeout)
                if value:
                    return compact_whitespace(value)
        except Exception:
            continue
    return None


def safe_filename(value: str, max_len: int = 80) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._")
    cleaned = cleaned[:max_len] or "hotel"
    return cleaned


def extract_currency(price: str | None) -> str | None:
    if not price:
        return None
    match = re.search(r"([A-Z]{3}|[$€£¥₱])", price)
    if match:
        return match.group(1)
    return None


def compact_whitespace(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def parse_rating_label(label: str | None) -> tuple[str | None, str | None]:
    if not label:
        return None, None
    match = re.search(r"(\d(?:\.\d)?)\s*out of 5 stars from\s*([\d,]+)\s*reviews", label)
    if not match:
        return None, None
    rating = match.group(1)
    review_count = match.group(2)
    return rating, review_count


def normalized_lines(value: str) -> list[str]:
    lines: list[str] = []
    for line in value.splitlines():
        normalized = compact_whitespace(line)
        if normalized:
            lines.append(normalized)
    return lines


def parse_primary_hotel_label(label: str | None) -> str | None:
    if not label:
        return None
    normalized = compact_whitespace(label)
    ignored_prefixes = (
        "Travel", "Explore", "Flights", "Hotels", "Vacation rentals",
        "Flight Deals", "Tracked ", "Change ", "Feedback", "Help",
        "Photos for ", "View prices for ", "Prices starting from ",
        "Sponsored", "Excellent location", "GREAT PRICE", "DEAL",
        "Visit", "Eco-certified",
    )
    if normalized.startswith(ignored_prefixes):
        return None
    
    blacklist = {
        "Excellent location", "Great location", "Good location",
        "View prices", "Check availability", "Sponsored", "More results",
        "Back to list", "Skip to main content", "Eco-certified",
    }
    if normalized in blacklist:
        return None

    normalized = re.sub(r" DEAL \d+% less than usual.*$", "", normalized).strip()

    if re.search(r"out of 5 stars|reviews,", normalized, re.IGNORECASE):
        return None
    if normalized.lower().startswith(("view prices", "check availability", "visit ")):
        return None
    
    if len(normalized) < 3:
        return None
    if re.fullmatch(r"[\d., ₱$€£¥]+", normalized):
        return None
        
    return normalized


def parse_address_and_phone_from_panel_text(panel_text: str) -> tuple[str | None, str | None]:
    match = re.search(
        r"Address & contact information\s*(.+?)(?:Health & safety|Amenities|About|Nearby|Sustainability|Website|Directions|$)",
        panel_text,
        re.DOTALL | re.IGNORECASE,
    )
    address = None
    phone = None
    
    if match:
        section_text = match.group(1)
        lines = [l.strip() for l in section_text.splitlines() if l.strip()]
        if lines:
            address = lines[0]
            # Strip phone number if it's appended to the address line
            address = re.sub(r"\(?\d{2,4}\)?\s*[\d\s\u202f\u00a0-]{7,}$", "", address).strip()
            address = re.sub(r"(?:Website|Directions|Share|Check availability|Loading|Visit).*$", "", address, flags=re.IGNORECASE).strip()
            
            phone_match = re.search(r"(\(?\d{2,4}\)?\s*[\d\s\u202f\u00a0-]{7,})", section_text)
            if phone_match:
                phone = compact_whitespace(phone_match.group(1))

    if not address or len(address) > 150 or "Back to list" in address:
        lines = normalized_lines(panel_text)
        for line in lines:
            if re.search(r"(?:Metro Manila|Philippines|Manila|Kalakhang Maynila|Cebu|Davao)$", line, re.IGNORECASE):
                if 10 < len(line) < 120 and not line.startswith(("About", "Set in", "Boasting", "Located", "Details")):
                    address = line
                    # Strip phone from fallback line too
                    address = re.sub(r"\(?\d{2,4}\)?\s*[\d\s\u202f\u00a0-]{7,}$", "", address).strip()
                    break
        
    if not phone:
        phone_match = re.search(r"(\(?\d{2,4}\)?\s*\d[\d\s\u202f\u00a0-]{7,})", panel_text)
        if phone_match:
            phone = compact_whitespace(phone_match.group(1))

    return address, phone


def parse_about_from_panel_text(panel_text: str) -> str | None:
    match = re.search(
        r"(?:About this hotel|About this property|Details)\s*(.+?)(?:Check-in time:|Popular amenities|View more hotel details|Web results|Nearby places|Google review summary|Essential info|Policies|Amenities|Website|Directions)",
        panel_text,
        re.DOTALL | re.IGNORECASE,
    )
    if not match:
        return None
    text = compact_whitespace(match.group(1))
    if len(text) < 10:
        return None
    return text


def parse_amenities_from_text(panel_text: str) -> dict[str, list[str]]:
    match = re.search(
        r"Amenities\s*(.+?)(?:Sources include:|Vacation rentals nearby|Frequently asked questions|Google collects|$)",
        panel_text,
        re.DOTALL | re.IGNORECASE,
    )
    if not match:
        return {}
    
    section_text = match.group(1)
    lines = normalized_lines(section_text)
    items = []
    for line in lines:
        if len(line) < 60 and not line.startswith(("₱", "View", "About", "Details")):
            items.append(line)
    
    if items:
        return {"Amenities": unique_strings(items)}
    return {}


async def extract_structured_amenities(page: Page, panel_text: str) -> dict[str, list[str]]:
    try:
        data = await page.evaluate('''() => {
            const amenitiesHeading = Array.from(document.querySelectorAll('h2, h3, h4')).find(h => 
                h.innerText.trim() === 'Amenities' || h.innerText.trim() === 'Property amenities'
            );
            if (!amenitiesHeading) return null;
            
            const container = amenitiesHeading.closest('section') || amenitiesHeading.parentElement;
            const headings = Array.from(container.querySelectorAll('h3, h4, h5')).filter(h => 
                h.innerText.trim() !== 'Amenities' && h.innerText.trim() !== 'Property amenities'
            );
            
            if (headings.length > 0) {
                return headings.map(h => {
                    let list = h.nextElementSibling;
                    while (list && !list.querySelector('[role="listitem"]') && list.tagName !== 'UL' && !list.innerText.includes('\\n')) {
                        list = list.nextElementSibling;
                    }
                    let items = [];
                    if (list) {
                        items = Array.from(list.querySelectorAll('[role="listitem"], li, div[class*="title"]')).map(li => li.innerText.trim());
                    }
                    return { category: h.innerText.trim(), items: items };
                }).filter(c => c.items.length > 0);
            } else {
                const items = Array.from(container.querySelectorAll('[role="listitem"], li, div')).map(li => li.innerText.trim());
                return [{ category: 'Amenities', items: items.filter(t => t.length > 2 && t.length < 60) }];
            }
        }''')
        
        if not data:
            return parse_amenities_from_text(panel_text)
            
        result: dict[str, list[str]] = {}
        noise_keywords = {"show details", "http", "google collects", "errors, let us know", "back to list", "close dialog"}
        
        for section in data:
            name = section["category"]
            items = []
            for item in section["items"]:
                lowered = item.lower()
                if any(noise in lowered for noise in noise_keywords):
                    continue
                if len(item) > 100 or len(item) < 2: continue
                cleaned = compact_whitespace(item)
                if cleaned: items.append(cleaned)
            
            if items:
                result[name] = unique_strings(items)
                
        if not result:
            return parse_amenities_from_text(panel_text)
        return result
    except Exception as e:
        print(f"[warn] failed to extract structured amenities: {e}")
        return parse_amenities_from_text(panel_text)


async def open_about_tab(page: Page) -> None:
    await click_if_visible(
        page,
        [
            '[role="tab"][aria-label="About"]',
            '[role="tab"]:has-text("About")',
            'button:has-text("About")',
            '[role="button"]:has-text("About")',
        ],
        timeout=3000,
    )
    await page.wait_for_timeout(2000)


def build_search_url(query: str) -> str:
    normalized = quote_plus(query.strip())
    return f"{HOTELS_BASE_URL}?q={normalized}"


def normalize_google_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.netloc.endswith("google.com") and parsed.path == "/url":
        target = parse_qs(parsed.query).get("q")
        if target:
            return target[0]
    return url


async def click_if_visible(page: Page, selectors: list[str], timeout: int = 1500) -> bool:
    for selector in selectors:
        try:
            locator = page.locator(selector).first
            if await locator.count() and await locator.is_visible(timeout=timeout):
                await locator.click(timeout=timeout)
                return True
        except Exception:
            continue
    return False


async def accept_google_dialogs(page: Page) -> None:
    await click_if_visible(
        page,
        [
            'button:has-text("Accept all")',
            'button:has-text("I agree")',
            'button:has-text("Accept")',
            '[role="button"]:has-text("Accept all")',
        ],
        timeout=2000,
    )


async def get_hotel_listings(page: Page, limit: int) -> list[HotelRecord]:
    link_locators = await page.locator('a[href*="/travel/search?"], a[href*="/travel/hotels/"]').all()
    records: list[HotelRecord] = []
    seen_names: set[str] = set()
    
    for link in link_locators:
        if len(records) >= limit:
            break
            
        try:
            name_el = link.locator('h2').first
            if not await name_el.count():
                aria_label = await link.get_attribute("aria-label")
                if aria_label and "Price" in aria_label:
                    match = re.search(r",\s*([^,]+)$", aria_label)
                    name = match.group(1).strip() if match else None
                else:
                    name = None
            else:
                name = (await name_el.inner_text(timeout=500)).strip()
            
            if not name or not parse_primary_hotel_label(name) or name in seen_names:
                continue
                
            record = HotelRecord(name=name)
            
            href = await link.get_attribute("href")
            if href:
                record.listing_url = normalize_google_url(urljoin("https://www.google.com", href))
                seen_names.add(name)
            else:
                continue
            
            price_el = link.locator('span[aria-label*="per night"], span[role="button"] span, span:has-text("₱")').first
            if await price_el.count():
                record.price = (await price_el.inner_text(timeout=500)).strip()
                record.currency = extract_currency(record.price)
                
            rating_el = link.locator('span[aria-label*="stars"]').first
            if await rating_el.count():
                label = await rating_el.get_attribute("aria-label", timeout=500)
                r, c = parse_rating_label(label)
                if r:
                    record.rating = r
                    record.review_count = c
            
            stars_text = await link.inner_text(timeout=500)
            stars_match = re.search(r"(\d)-star hotel", stars_text, re.IGNORECASE)
            if stars_match:
                record.stars = f"{stars_match.group(1)}-star hotel"
                
            records.append(record)
        except Exception:
            continue
            
    return records


async def scroll_listing_page(page: Page, passes: int) -> None:
    for _ in range(passes):
        await page.mouse.wheel(0, 2500)
        await page.wait_for_timeout(1200)


async def maybe_expand_about(page: Page) -> None:
    await click_if_visible(
        page,
        [
            '[role="button"]:has-text("About")',
            '[role="button"]:has-text("Read more")',
            'button:has-text("Read more")',
            'button:has-text("More")',
        ],
        timeout=1000,
    )


async def dismiss_google_dialogs(page: Page) -> None:
    await click_if_visible(
        page,
        [
            '[aria-label="Close"]',
            'button:has-text("Close")',
            'button:has-text("Got it")',
            'button:has-text("OK")',
        ],
        timeout=1200,
    )


async def extract_nearby_places(page: Page) -> list[str]:
    section = page.locator('section:has-text("Nearby places"), div:has-text("Nearby places")').last
    try:
        if await section.count() and await section.is_visible(timeout=2000):
            candidates = await section.locator('[role="listitem"]').all()
            places = []
            for item in candidates:
                try:
                    name_locator = item.locator('div[role="heading"], b, [class*="title"]').first
                    if await name_locator.count():
                        name = await name_locator.inner_text(timeout=500)
                        name = name.strip()
                    else:
                        text = await item.inner_text(timeout=500)
                        name = text.split("\n")[0].strip()
                    
                    if name and 2 < len(name) <= 120:
                        places.append(name)
                except Exception:
                    continue
            if places:
                return unique_strings(places)[:20]
    except Exception:
        pass

    body_text = await page.locator("body").inner_text(timeout=2000)
    match = re.search(
        r"Nearby places\s+(.+?)(?:Google review summary|Reviews on other travel sites|Photos|About this hotel|Amenities)",
        body_text,
        re.DOTALL | re.IGNORECASE,
    )
    if not match: return []
    lines = normalized_lines(match.group(1))
    places: list[str] = []
    for index, line in enumerate(lines[:-1]):
        next_line = lines[index + 1]
        if re.fullmatch(r"\d(?:\.\d)?", next_line) and 2 < len(line) <= 120:
            places.append(line)
    return unique_strings(places)[:20]


async def extract_website_url(page: Page) -> str | None:
    try:
        url = await page.evaluate('''() => {
            const allLinks = Array.from(document.querySelectorAll('a'));
            const websiteLink = allLinks.find(a => 
                (a.innerText && a.innerText.includes('Website')) || 
                (a.getAttribute('aria-label') && a.getAttribute('aria-label').includes('Website'))
            );
            return websiteLink ? websiteLink.href : null;
        }''')
        return url
    except Exception:
        return None


async def extract_photos(scope: Any, limit: int) -> list[str]:
    photo_urls = await all_attributes(
        scope,
        [
            'img[src^="https://lh3.googleusercontent.com/"]',
            'img[src^="https://lh5.googleusercontent.com/"]',
            'img[src^="https://encrypted-tbn"]',
            'img[data-src^="https://"]',
        ],
        "src",
        limit=200,
    )
    
    blacklist = {
        "plan_your_stay", "no_destination", "no_results", 
        "where_to_stay", "when_to_visit", "what_youll_pay",
        "google_logo", "cleardot", "maps/vt", "overlay"
    }
    
    filtered = []
    for url in unique_strings(photo_urls):
        lowered = url.lower()
        if any(token in lowered for token in blacklist):
            continue
        if "=s" in url and "w" in url:
            try:
                size_match = re.search(r"=s(\d+)", url)
                if size_match and int(size_match.group(1)) < 100:
                    continue
            except: pass
            
        if "gstatic.com" in url or "googleusercontent.com" in url or "ggpht.com" in url or "encrypted-tbn" in url:
            filtered.append(url)
            
    return filtered[:limit]


async def extract_hotel_name(page: Page, expected_name: str | None = None) -> str | None:
    h1 = page.locator("h1").last
    try:
        if await h1.count():
            name = await h1.inner_text(timeout=1000)
            name = name.strip()
            if name and "results" not in name.lower() and len(name) > 3:
                return name
    except Exception:
        pass
            
    try:
        headings = await page.locator('[role="heading"][aria-level="1"]').all()
        for h in headings:
            name = await h.inner_text(timeout=500)
            name = name.strip()
            if name and "results" not in name.lower() and len(name) > 3:
                return name
    except Exception:
        pass
            
    try:
        about_tab = page.locator('[role="tab"][aria-selected="true"]').first
        if await about_tab.count():
            label = await about_tab.get_attribute("aria-label")
            if label and "About" in label:
                name = label.replace("About", "").strip()
                if name and len(name) > 3:
                    return name
    except Exception:
        pass

    return expected_name


async def extract_detail_page(
    page: Page,
    url: str | None,
    photo_limit: int,
    initial_record: HotelRecord | None = None,
) -> HotelRecord:
    record = initial_record or HotelRecord()
    
    if url:
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(2500)
        except Exception as e:
            print(f"[warn] failed to navigate to {url}: {e}")
            return record

    await accept_google_dialogs(page)
    await dismiss_google_dialogs(page)
    await open_about_tab(page)
    await maybe_expand_about(page)
    
    await page.wait_for_timeout(1000)
    
    about_panel = page.locator('[role="tabpanel"], div[jsname="wtxWD"]').first
    for _ in range(5):
        try:
            text = await about_panel.inner_text(timeout=500)
            if "Loading" in text:
                await page.wait_for_timeout(1000)
            else:
                break
        except: break

    if not await about_panel.count() or not await about_panel.is_visible(timeout=1000):
        about_panel = page.locator('div[jsname="wtxWD"]').last
        if not await about_panel.count():
            about_panel = page

    if not record.name:
        record.name = await extract_hotel_name(page)
    
    panel_text = await about_panel.inner_text(timeout=3000)
    
    if not record.stars:
        stars_match = re.search(r"(\d)-star hotel", panel_text, re.IGNORECASE)
        if stars_match:
            record.stars = f"{stars_match.group(1)}-star hotel"
        else:
            record.stars = await first_text(page, ['[aria-label*="star hotel"]', r'text=/\d-star hotel/'])

    if not record.rating:
        rating_match = re.search(r"(\d\.\d)\s*\(([\d,Kk.]+)\)", panel_text)
        if rating_match:
            record.rating = rating_match.group(1)
            record.review_count = rating_match.group(2)

    if not record.price:
        price_match = re.search(r"Prices starting from\s+([$€£¥₱][\d,\u202f\u00a0]+)", panel_text)
        if price_match:
            record.price = compact_whitespace(price_match.group(1))
        
        if not record.price:
            record.price = await first_text(about_panel, [r'text=/[$€£¥₱]\s?\d[\d,]*/', r'text=/[A-Z]{3}\s?\d[\d,]*/'])
        
        if record.price:
            record.currency = extract_currency(record.price)

    if not record.total_price:
        total_match = re.search(r"([$€£¥₱][\d,\u202f\u00a0]+)\s?total", panel_text, re.IGNORECASE)
        if total_match:
            record.total_price = compact_whitespace(total_match.group(1))

    address, phone = parse_address_and_phone_from_panel_text(panel_text)
    if not address:
        desc_match = re.search(r"situated ([\d.]+ km from [^.]+)", panel_text, re.IGNORECASE)
        if desc_match:
            address = f"Manila (near {desc_match.group(1)})"
        else:
            address = await first_text(about_panel, ['[data-tooltip*="Address"]', 'button[aria-label*="Address"]'])
    
    record.address = address or record.address
    record.phone = phone or record.phone
    record.website = await extract_website_url(page)
    record.about = parse_about_from_panel_text(panel_text) or record.about

    check_in_match = re.search(r"Check-in(?: time)?[:\s]+([\d: \u202f\u00a0]+[AP]M)", panel_text, re.IGNORECASE)
    check_out_match = re.search(r"Check-out(?: time)?[:\s]+([\d: \u202f\u00a0]+[AP]M)", panel_text, re.IGNORECASE)
    if check_in_match: record.check_in = compact_whitespace(check_in_match.group(1))
    if check_out_match: record.check_out = compact_whitespace(check_out_match.group(1))

    record.amenities = await extract_structured_amenities(page, panel_text)
    record.nearby_places = await extract_nearby_places(page)
    record.photos = await extract_photos(about_panel, limit=photo_limit)
    record.source_url = page.url

    return record


async def open_listing_page(context, source: str, adults: int = 2, children: int = 0, check_in: str | None = None, check_out: str | None = None) -> Page:
    page = await context.new_page()
    target_url = source if source.startswith("http") else build_search_url(source)
    await page.goto(target_url, wait_until="domcontentloaded", timeout=60000)
    await page.wait_for_timeout(3000)
    await accept_google_dialogs(page)
    await dismiss_google_dialogs(page)
    
    if check_in or check_out:
        try:
            ci_input = page.locator('input[placeholder="Check-in"]').first
            if await ci_input.count():
                await ci_input.click(force=True)
                await page.wait_for_timeout(500)
                if check_in:
                    await ci_input.click(force=True)
                    await page.keyboard.press("Control+A")
                    await page.keyboard.press("Backspace")
                    await page.keyboard.type(check_in, delay=20)
                    await page.wait_for_timeout(300)
                if check_out:
                    co_input = page.locator('input[placeholder="Check-out"]').filter(visible=True).first
                    if await co_input.count():
                        await co_input.click(force=True)
                        await page.wait_for_timeout(300)
                        await page.keyboard.press("Control+A")
                        await page.keyboard.press("Backspace")
                        await page.keyboard.type(check_out, delay=20)
                        await page.keyboard.press("Enter")
                        await page.wait_for_timeout(300)
            
            done_btn = page.locator('button:has-text("Done"), [role="button"]:has-text("Done")').filter(visible=True).first
            if await done_btn.count():
                await done_btn.click()
                await page.wait_for_timeout(500)
            else:
                await page.keyboard.press("Escape")
            print(f"[info] set dates to {check_in} - {check_out}")
        except Exception as e:
            print(f"[warn] failed to set dates: {e}")

    if adults != 2 or children > 0:
        try:
            travelers_btn = page.locator('button[aria-label*="traveler"], button[aria-label*="Traveler"]').first
            if not await travelers_btn.count():
                travelers_btn = page.locator('button, [role="button"]').filter(has_text=re.compile(r"^\d+$")).filter(has_not=page.locator('[aria-label*="May"], [aria-label*="June"]')).first
            
            if await travelers_btn.count():
                await travelers_btn.click()
                await page.wait_for_timeout(600)
                
                async def adjust_count(label: str, target: int, current: int):
                    if target > current:
                        btn = page.locator(f'button[aria-label="Add {label}"], button[aria-label="Increase {label}s"]').filter(visible=True).first
                        for _ in range(target - current):
                            if await btn.count(): await btn.click(); await page.wait_for_timeout(150)
                    elif target < current:
                        btn = page.locator(f'button[aria-label="Remove {label}"], button[aria-label="Decrease {label}s"]').filter(visible=True).first
                        for _ in range(current - target):
                            if await btn.count(): await btn.click(); await page.wait_for_timeout(150)

                await adjust_count("adult", adults, 2)
                await adjust_count("child", children, 0)
                
                age_select = page.locator('select').filter(visible=True).first
                if await age_select.count():
                    await age_select.select_option("5")
                    await page.wait_for_timeout(200)

                done_btn = page.locator('button:has-text("Done"), [role="button"]:has-text("Done")').filter(visible=True).first
                if await done_btn.count():
                    await done_btn.click()
                    print(f"[info] adjusted occupancy to {adults} adults, {children} children")
                else:
                    await page.keyboard.press("Escape")
        except Exception as e:
            print(f"[warn] failed to adjust occupancy: {e}")

    await page.wait_for_timeout(2500)
    return page


async def download_photos(hotel: HotelRecord, output_dir: Path, timeout: int = 20) -> list[str]:
    if not hotel.photos or not hotel.name:
        return []
    hotel_dir = output_dir / safe_filename(hotel.name)
    hotel_dir.mkdir(parents=True, exist_ok=True)
    saved_files: list[str] = []
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"})
    for index, photo_url in enumerate(hotel.photos, start=1):
        suffix = Path(urlparse(photo_url).path).suffix or ".jpg"
        photo_path = hotel_dir / f"{index:03d}{suffix}"
        try:
            def fetch():
                response = session.get(photo_url, timeout=timeout)
                response.raise_for_status()
                return response.content
            content = await asyncio.to_thread(fetch)
            photo_path.write_bytes(content)
            saved_files.append(str(photo_path))
        except Exception:
            continue
    return saved_files


async def scrape_hotels(source: str, limit: int, photo_limit: int, headless: bool, download_images: bool, image_dir: Path, adults: int = 2, children: int = 0, check_in: str | None = None, check_out: str | None = None, concurrency: int = 5) -> list[HotelRecord]:
    records: list[HotelRecord] = []
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=headless)
        context = await browser.new_context(locale="en-US", user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
        await Stealth().apply_stealth_async(context)
        page = await open_listing_page(context, source, adults=adults, children=children, check_in=check_in, check_out=check_out)
        if "/travel/hotels/" in page.url and ("qs=" in page.url or "q=" not in page.url):
            try:
                record = await extract_detail_page(page, url=None, photo_limit=photo_limit)
                if record.name:
                    record.adults, record.children, record.search_check_in, record.search_check_out = adults, children, check_in, check_out
                    records.append(record)
                    if download_images: await download_photos(record, image_dir)
                    print(f"[1/1] scraped: {record.name}")
                return records
            finally:
                await page.close()
        await scroll_listing_page(page, passes=max(2, limit // 5))
        hotel_listings = await get_hotel_listings(page, limit=limit)
        await page.close()
        semaphore = asyncio.Semaphore(concurrency)
        async def scrape_hotel_task(index, initial_record):
            async with semaphore:
                initial_record.adults, initial_record.children, initial_record.search_check_in, initial_record.search_check_out = adults, children, check_in, check_out
                detail_page = await context.new_page()
                try:
                    record = await extract_detail_page(detail_page, url=initial_record.listing_url, photo_limit=photo_limit, initial_record=initial_record)
                    if record.name:
                        if download_images: await download_photos(record, image_dir)
                        print(f"[{index}/{len(hotel_listings)}] scraped: {record.name}")
                        return record
                except Exception as exc: print(f"[warn] failed to scrape: {initial_record.listing_url} - {exc}")
                finally: await detail_page.close()
                return None
        tasks = [scrape_hotel_task(i, rec) for i, rec in enumerate(hotel_listings, start=1)]
        results = await asyncio.gather(*tasks)
        records = [r for r in results if r is not None]
        await context.close()
        await browser.close()
    return records


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scrape Google Hotels listing and detail data into JSON.")
    parser.add_argument("source", help="Search text like 'hotels in manila' or a direct Google Hotels URL.")
    parser.add_argument("--limit", type=int, default=10, help="Maximum number of hotels to scrape.")
    parser.add_argument("--photo-limit", type=int, default=20, help="Maximum number of photo URLs to keep per hotel.")
    parser.add_argument("--output", default="output/hotels.json", help="Path to write the JSON output.")
    parser.add_argument("--images-dir", default="output/photos", help="Directory for downloaded hotel images.")
    parser.add_argument("--download-images", action="store_true", help="Download discovered photo URLs to disk.")
    parser.add_argument("--headed", action="store_true", help="Run a visible browser instead of headless mode.")
    parser.add_argument("--adults", type=int, default=2, help="number of adults (default: 2)")
    parser.add_argument("--children", type=int, default=0, help="number of children (default: 0)")
    parser.add_argument("--check-in", help="Check-in date (e.g., '2026-06-01' or 'Jun 1, 2026')")
    parser.add_argument("--check-out", help="Check-out date (e.g., '2026-06-05' or 'Jun 5, 2026')")
    parser.add_argument("--concurrency", type=int, default=3, help="Number of concurrent tabs (default: 3)")
    return parser.parse_args()


async def async_main() -> None:
    args = parse_args()
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    image_dir = Path(args.images_dir)
    image_dir.mkdir(parents=True, exist_ok=True)
    records = await scrape_hotels(source=args.source, limit=args.limit, photo_limit=args.photo_limit, headless=not args.headed, download_images=args.download_images, image_dir=image_dir, adults=args.adults, children=args.children, check_in=args.check_in, check_out=args.check_out, concurrency=args.concurrency)
    payload: list[dict[str, Any]] = [asdict(record) for record in records]
    output_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"saved {len(records)} hotel records to {output_path}")


def main() -> None:
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
