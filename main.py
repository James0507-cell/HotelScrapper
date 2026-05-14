from __future__ import annotations

import argparse
import json
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote_plus, urljoin, urlparse

import requests
from playwright.sync_api import Page, TimeoutError, sync_playwright
from playwright_stealth import Stealth


HOTELS_BASE_URL = "https://www.google.com/travel/hotels"


@dataclass
class HotelRecord:
    name: str | None = None
    price: str | None = None
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


def first_text(scope: Any, selectors: list[str], timeout: int = 1500) -> str | None:
    for selector in selectors:
        try:
            locator = scope.locator(selector).first
            if locator.count() and locator.is_visible(timeout=timeout):
                text = locator.inner_text(timeout=timeout).strip()
                if text:
                    return re.sub(r"\s+", " ", text)
        except Exception:
            continue
    return None


def all_texts(scope: Any, selectors: list[str], limit: int = 100) -> list[str]:
    collected: list[str] = []
    for selector in selectors:
        try:
            locator = scope.locator(selector)
            count = min(locator.count(), limit)
            for index in range(count):
                text = locator.nth(index).inner_text(timeout=1000).strip()
                if text:
                    collected.append(text)
        except Exception:
            continue
    return unique_strings(collected)


def all_attributes(scope: Any, selectors: list[str], attribute: str, limit: int = 100) -> list[str]:
    values: list[str] = []
    for selector in selectors:
        try:
            locator = scope.locator(selector)
            count = min(locator.count(), limit)
            for index in range(count):
                value = locator.nth(index).get_attribute(attribute, timeout=1000)
                if value:
                    values.append(value)
        except Exception:
            continue
    return unique_strings(values)


def first_attribute(scope: Any, selectors: list[str], attribute: str, timeout: int = 1500) -> str | None:
    for selector in selectors:
        try:
            locator = scope.locator(selector).first
            if locator.count():
                value = locator.get_attribute(attribute, timeout=timeout)
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
    # Fallback: if it's just numbers but we know the context is PHP (based on search)
    # But better to return None if unsure.
    return None


def compact_whitespace(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def parse_rating_label(label: str | None) -> tuple[str | None, str | None]:
    if not label:
        return None, None
    match = re.search(r"(\d\.\d) out of 5 stars from ([\d,]+) reviews", label)
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
        "Travel",
        "Explore",
        "Flights",
        "Hotels",
        "Vacation rentals",
        "Flight Deals",
        "Tracked ",
        "Change ",
        "Feedback",
        "Help",
        "Photos for ",
        "View prices for ",
        "Prices starting from ",
        "Sponsored",
        "Excellent location",
        "GREAT PRICE",
        "DEAL",
        "Visit",
        "Eco-certified",
    )
    if normalized.startswith(ignored_prefixes):
        return None
    
    # Blacklist exact matches
    blacklist = {
        "Excellent location",
        "Great location",
        "Good location",
        "View prices",
        "Check availability",
        "Sponsored",
        "More results",
        "Back to list",
        "Skip to main content",
        "Eco-certified",
    }
    if normalized in blacklist:
        return None

    # Trim "DEAL X% less than usual"
    normalized = re.sub(r" DEAL \d+% less than usual.*$", "", normalized).strip()

    if re.search(r"out of 5 stars|reviews,", normalized, re.IGNORECASE):
        return None
    if normalized.lower().startswith(("view prices", "check availability", "visit ")):
        return None
    
    # If it's too short or just numbers/symbols
    if len(normalized) < 3:
        return None
    if re.fullmatch(r"[\d., ₱$€£¥]+", normalized):
        return None
        
    return normalized


def parse_amenities_from_panel_text(panel_text: str) -> list[str]:
    start = panel_text.find("Amenities")
    if start == -1:
        return []
    health_index = panel_text.find("Health & safety")
    if health_index != -1 and health_index > start:
        next_start = panel_text.find("Amenities", health_index)
        if next_start != -1:
            start = next_start

    end_candidates = [
        index
        for index in (
            panel_text.find("Loading results", start),
            panel_text.find("View prices", start),
            panel_text.find("Google review summary", start),
            panel_text.find("Reviews on other travel sites", start),
        )
        if index != -1
    ]
    end = min(end_candidates) if end_candidates else len(panel_text)
    section_text = panel_text[start:end]

    section_headers = {
        "popular amenities",
        "internet",
        "activities",
        "services",
        "parking & transportation",
        "accessibility",
        "pets",
        "rooms",
        "food & drink",
        "children",
        "pools",
        "wellness",
        "business & events",
        "languages spoken",
        "show details",
    }
    ignored_values = {"free", "extra charge", "24 hour", "daily"}
    amenities: list[str] = []
    for item in normalized_lines(section_text):
        lowered = item.lower()
        if lowered in section_headers or lowered in ignored_values:
            continue
        if lowered == "amenities":
            continue
        if item.startswith("₱") or item == "View prices":
            break
        if re.search(r"google collects information|errors, let us know", lowered):
            continue
        if len(item) <= 60 and len(item.split()) <= 5:
            amenities.append(item)
    amenities = unique_strings(amenities)
    if amenities:
        return [item for item in amenities if item not in {"Diamond Hotel Philippines", "Swiss-Belhotel Blulane"}]

    known_labels = [
        "Pool",
        "Spa",
        "Parking",
        "Breakfast",
        "Wi-Fi",
        "Nightclub",
        "Front desk",
        "Concierge",
        "Full-service laundry",
        "Elevator",
        "Wake up calls",
        "Housekeeping",
        "Turndown service",
        "Self parking",
        "Valet parking",
        "Private car service",
        "Car rental onsite",
        "Local shuttle",
        "Accessible",
        "No pets",
        "Air conditioning",
        "Restaurant",
        "Bar",
        "Table service",
        "Buffet dinner",
        "Room service",
        "Breakfast buffet",
        "Kid-friendly",
        "Outdoor pool",
        "Wading pool",
        "Lifeguard",
        "Hot tub",
        "Fitness center",
        "Business center",
        "English",
        "Filipino",
        "Credit cards",
        "Debit cards",
        "Cash",
        "Airport shuttle",
        "No pools",
        "No hot tub",
        "No fitness center",
        "No spa",
    ]
    fallback = [label for label in known_labels if label.lower() in section_text.lower()]
    return unique_strings(fallback)


def parse_address_and_phone_from_panel_text(panel_text: str) -> tuple[str | None, str | None]:
    # Look for Address & contact information section explicitly
    match = re.search(
        r"Address & contact information\s*(.+?)(?:Health & safety|Amenities|About|Nearby|$)",
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
            phone_match = re.search(r"(\(?\d{2,4}\)?\s*[\d\s-]{7,})", section_text)
            if phone_match:
                phone = phone_match.group(1).strip()

    if not address:
        # Fallback: look for lines ending in Philippines or Metro Manila or Manila
        address_match = re.search(r"([^\n•]+?(?:Metro Manila|Philippines|Manila|Kalakhang Maynila))", panel_text)
        if address_match:
            address = compact_whitespace(address_match.group(1))
            # Clean up leading noise
            address = re.sub(r"^[\d. ]+\([\d,Kk.]+\)[• ]*(?:\d-star hotel)?", "", address).strip()
        
    if not phone:
        phone_match = re.search(r"(\(?\d{2,4}\)?\s*\d[\d\s-]{7,})", panel_text)
        if phone_match:
            phone = phone_match.group(1).strip()

    # Final cleanup of address to remove trailing common labels
    if address:
        address = re.sub(r"(?:Website|Directions|Share|Check availability|Loading).*$", "", address, flags=re.IGNORECASE).strip()
        address = address.rstrip("•").strip()

    return address, phone


def parse_about_from_panel_text(panel_text: str) -> str | None:
    match = re.search(
        r"About this hotel\s*(.+?)(?:Check-in time:|Popular amenities|View more hotel details|Web results|Nearby places|Google review summary)",
        panel_text,
        re.DOTALL | re.IGNORECASE,
    )
    if not match:
        return None
    return compact_whitespace(match.group(1))


def parse_nearby_places_from_panel_text(panel_text: str) -> list[str]:
    match = re.search(
        r"Nearby places\s+(.+?)(?:Google review summary|Reviews on other travel sites|Photos|About this hotel|Amenities)",
        panel_text,
        re.DOTALL | re.IGNORECASE,
    )
    if not match:
        return []

    lines = normalized_lines(match.group(1))
    places: list[str] = []
    for index, line in enumerate(lines[:-1]):
        next_line = lines[index + 1]
        if "loading results" in line.lower():
            continue
        if re.fullmatch(r"\d(?:\.\d)?", next_line) and 2 < len(line) <= 120:
            places.append(line)
    return unique_strings(places)[:20]


def open_about_tab(page: Page) -> None:
    click_if_visible(
        page,
        [
            '[role="tab"][aria-label="About"]',
            '[role="tab"]:has-text("About")',
            'button:has-text("About")',
            '[role="button"]:has-text("About")',
        ],
        timeout=3000,
    )
    page.wait_for_timeout(1500)


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


def click_if_visible(page: Page, selectors: list[str], timeout: int = 1500) -> bool:
    for selector in selectors:
        try:
            locator = page.locator(selector).first
            if locator.count() and locator.is_visible(timeout=timeout):
                locator.click(timeout=timeout)
                return True
        except Exception:
            continue
    return False


def accept_google_dialogs(page: Page) -> None:
    click_if_visible(
        page,
        [
            'button:has-text("Accept all")',
            'button:has-text("I agree")',
            'button:has-text("Accept")',
            '[role="button"]:has-text("Accept all")',
        ],
        timeout=2000,
    )


def get_hotel_listings(page: Page, limit: int) -> list[HotelRecord]:
    # Collect data from all potential listing links
    candidates = page.locator('a[role="link"][href*="/travel/search?"]').all()
    
    # Map name -> partial HotelRecord
    hotels_map: dict[str, HotelRecord] = {}
    
    for locator in candidates:
        try:
            aria_label = (locator.get_attribute("aria-label", timeout=300) or "").strip()
            text = (locator.inner_text(timeout=300) or "").strip()
            href = (locator.get_attribute("href", timeout=300) or "").strip()
            
            if not text and not aria_label:
                continue
                
            # Attempt to extract name
            name = None
            if "Prices starting from" in aria_label:
                m = re.search(r"Prices starting from\s+[$€£¥₱]?[\d,.]+\s?,\s*(.+)", aria_label)
                if m: name = m.group(1).strip()
            elif "out of 5 stars from" in aria_label:
                m = re.search(r"out of 5 stars from\s+[\d,.]+\s?reviews,\s*(.+)", aria_label)
                if m: name = m.group(1).strip()
            elif aria_label and not aria_label.startswith(("View prices", "Photos", "Prices", "Visit", "Sponsored")):
                name = aria_label.strip()
            
            if not name:
                lines = text.split("\n")
                if lines:
                    first_line = lines[0].strip()
                    if first_line and len(first_line) > 3 and not first_line.startswith(("Prices", "View", "Photos", "Sponsored")):
                        name = first_line
            
            if name:
                # Clean name immediately for better deduplication
                name = re.sub(r" DEAL \d+% less than usual.*$", "", name).strip()
                name = re.sub(r" GREAT PRICE for a \d-star hotel.*$", "", name).strip()

            if not name or not parse_primary_hotel_label(name):
                continue

            if name not in hotels_map:
                hotels_map[name] = HotelRecord(name=name)
            
            record = hotels_map[name]
            
            # Extract price if missing
            if not record.price:
                combined_text = text + " " + aria_label
                price_match = re.search(r"([$€£¥₱]\s?[\d,]+)", combined_text)
                if price_match:
                    record.price = price_match.group(1).strip()
                    record.currency = extract_currency(record.price)
                elif "Prices starting from" in aria_label:
                    m = re.search(r"Prices starting from\s+([\d,.]+)", aria_label)
                    if m: record.price = m.group(1).strip()
            
            # Extract rating and reviews if missing
            if not record.rating:
                rating_match = re.search(r"(\d\.\d)\s*\(([\d,Kk.]+)\)", text)
                if rating_match:
                    record.rating = rating_match.group(1)
                    record.review_count = rating_match.group(2)
                else:
                    label_match = re.search(r"(\d(?:\.\d)?)\s*out of 5 stars from\s*([\d,]+)\s*reviews", aria_label)
                    if label_match:
                        record.rating = label_match.group(1)
                        record.review_count = label_match.group(2)

            # Extract stars if missing
            if not record.stars:
                stars_match = re.search(r"(\d)-star hotel", text + " " + aria_label, re.IGNORECASE)
                if stars_match:
                    record.stars = f"{stars_match.group(1)}-star hotel"

            # Set listing URL if missing (prefer the ones with qs=)
            if not record.listing_url or "qs=" in href:
                record.listing_url = normalize_google_url(urljoin("https://www.google.com", href))

        except Exception:
            continue
            
    # Convert map to list, respecting limit
    final_records: list[HotelRecord] = []
    for name, record in hotels_map.items():
        if record.listing_url: # Must have a link to be useful
            final_records.append(record)
            if len(final_records) >= limit:
                break
                
    return final_records


def scroll_listing_page(page: Page, passes: int) -> None:
    for _ in range(passes):
        page.mouse.wheel(0, 2500)
        page.wait_for_timeout(1200)


def maybe_expand_about(page: Page) -> None:
    click_if_visible(
        page,
        [
            '[role="button"]:has-text("About")',
            '[role="button"]:has-text("Read more")',
            'button:has-text("Read more")',
            'button:has-text("More")',
        ],
        timeout=1000,
    )


def dismiss_google_dialogs(page: Page) -> None:
    click_if_visible(
        page,
        [
            '[aria-label="Close"]',
            'button:has-text("Close")',
            'button:has-text("Got it")',
            'button:has-text("OK")',
        ],
        timeout=1200,
    )


def extract_nearby_places(page: Page) -> list[str]:
    # Try to find the section by heading
    section = page.locator('section:has-text("Nearby places"), div:has-text("Nearby places")').last
    try:
        if section.count() and section.is_visible(timeout=2000):
            # Items are often in a listitem role
            candidates = section.locator('[role="listitem"]').all()
            places = []
            for item in candidates:
                try:
                    # Place name is usually in a bold or heading element
                    name_locator = item.locator('div[role="heading"], b, [class*="title"]').first
                    if name_locator.count():
                        name = name_locator.inner_text(timeout=500).strip()
                    else:
                        name = item.inner_text(timeout=500).split("\n")[0].strip()
                    
                    if name and 2 < len(name) <= 120:
                        places.append(name)
                except Exception:
                    continue
            if places:
                return unique_strings(places)[:20]
    except Exception:
        pass

    # Fallback to panel text parsing
    body_text = page.locator("body").inner_text(timeout=2000)
    return parse_nearby_places_from_panel_text(body_text)


def extract_structured_amenities(page: Page) -> dict[str, list[str]]:
    try:
        # Use page.evaluate to get structured data directly from the DOM
        categories = page.evaluate('''() => {
            const amenitiesHeading = Array.from(document.querySelectorAll('h2, h3')).find(h => h.innerText.trim() === 'Amenities');
            if (!amenitiesHeading) return null;
            
            const container = amenitiesHeading.closest('section') || amenitiesHeading.parentElement;
            const headings = Array.from(container.querySelectorAll('h3, h4')).filter(h => h.innerText.trim() !== 'Amenities');
            
            return headings.map(h => {
                let list = h.nextElementSibling;
                while (list && !list.querySelector('[role="listitem"]') && list.tagName !== 'UL') {
                    list = list.nextElementSibling;
                }
                
                let items = [];
                if (list) {
                    items = Array.from(list.querySelectorAll('[role="listitem"], li')).map(li => li.innerText.trim());
                }
                
                return {
                    category: h.innerText.trim(),
                    items: items
                };
            }).filter(c => c.items.length > 0);
        }''')
        
        if not categories:
            return {}
            
        result: dict[str, list[str]] = {}
        noise_keywords = {"show details", "http", "google collects", "errors, let us know"}
        
        for cat in categories:
            name = cat["category"]
            items = []
            for item in cat["items"]:
                lowered = item.lower()
                if any(noise in lowered for noise in noise_keywords):
                    continue
                
                # We want to keep descriptive terms like 'free' as requested
                # but we can still trim excessive whitespace
                cleaned = item.strip()
                if cleaned:
                    items.append(cleaned)
            
            if items:
                result[name] = unique_strings(items)
                
        return result
    except Exception as e:
        print(f"[warn] failed to extract structured amenities: {e}")
        return {}


def extract_website_url(page: Page) -> str | None:
    try:
        url = page.evaluate('''() => {
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


def extract_photos(scope: Any, limit: int) -> list[str]:
    photo_urls = all_attributes(
        scope,
        [
            'img[src^="https://"]',
            'img[data-src^="https://"]',
        ],
        "src",
        limit=200,
    )
    if len(photo_urls) < limit:
        photo_urls.extend(
            all_attributes(
                scope,
                ['img[data-src^="https://"]'],
                "data-src",
                limit=200,
            )
        )
    filtered = [
        url
        for url in unique_strings(photo_urls)
        if "gstatic.com" in url or "googleusercontent.com" in url or "ggpht.com" in url
    ]
    return filtered[:limit]


def extract_hotel_name(page: Page, expected_name: str | None = None) -> str | None:
    # First priority: H1
    h1 = page.locator("h1").last
    try:
        if h1.count():
            name = h1.inner_text(timeout=1000).strip()
            if name and "results" not in name.lower() and len(name) > 3:
                return name
    except Exception:
        pass
            
    # Second priority: Heading role
    try:
        headings = page.locator('[role="heading"][aria-level="1"]').all()
        for h in headings:
            name = h.inner_text(timeout=500).strip()
            if name and "results" not in name.lower() and len(name) > 3:
                return name
    except Exception:
        pass
            
    # Third priority: ARIA label of the active tab
    try:
        about_tab = page.locator('[role="tab"][aria-selected="true"]').first
        if about_tab.count():
            label = about_tab.get_attribute("aria-label")
            if label and "About" in label:
                # Often "About Diamond Hotel Philippines"
                name = label.replace("About", "").strip()
                if name and len(name) > 3:
                    return name
    except Exception:
        pass

    return expected_name


def extract_detail_page(
    page: Page,
    url: str | None,
    photo_limit: int,
    initial_record: HotelRecord | None = None,
) -> HotelRecord:
    record = initial_record or HotelRecord()
    
    if url:
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(2500)
        except Exception as e:
            print(f"[warn] failed to navigate to {url}: {e}")
            return record

    accept_google_dialogs(page)
    dismiss_google_dialogs(page)
    
    # We want to be on the About tab for most metadata
    open_about_tab(page)
    maybe_expand_about(page)
    
    # Try to find the panel that contains the hotel details
    about_panel = page.locator('[role="tabpanel"]').first
    # Wait for "Loading..." to disappear if possible
    try:
        if about_panel.count() and "Loading" in about_panel.inner_text(timeout=1000):
            page.wait_for_timeout(3000)
    except:
        pass

    if not about_panel.count() or not about_panel.is_visible(timeout=2000):
        about_panel = page.locator('div[jsname="wtxWD"]').last
        if not about_panel.count():
            about_panel = page

    # Name extraction if missing
    if not record.name:
        record.name = extract_hotel_name(page)
    
    # Text-based extraction from the panel
    panel_text = compact_whitespace(about_panel.inner_text(timeout=3000))
    
    # Stars if missing
    if not record.stars:
        stars_match = re.search(r"(\d)-star hotel", panel_text, re.IGNORECASE)
        if stars_match:
            record.stars = f"{stars_match.group(1)}-star hotel"
        else:
            record.stars = first_text(page, ['[aria-label*="star hotel"]', r'text=/\d-star hotel/'])

    # Rating and Review Count if missing
    if not record.rating:
        rating_match = re.search(r"(\d\.\d)\s*\(([\d,Kk.]+)\)", panel_text)
        if rating_match:
            record.rating = rating_match.group(1)
            record.review_count = rating_match.group(2)
        
        if not record.rating:
            rating_label = first_attribute(page, ['a[aria-label*="reviews"]'], "aria-label")
            r, c = parse_rating_label(rating_label)
            if r:
                record.rating = r
                record.review_count = c

    # Price if missing
    if not record.price:
        # Check for "Prices starting from ₱6,532"
        price_match = re.search(r"Prices starting from\s+([^\s,]+)", panel_text)
        if not price_match:
            # Check aria-labels of links in the panel
            aria_labels = all_attributes(about_panel, ['a[aria-label]'], "aria-label", limit=10)
            for label in aria_labels:
                m = re.search(r"Prices starting from\s+([$€£¥₱]\s?\d[\d,]*)", label)
                if m:
                    record.price = m.group(1)
                    break
        else:
            record.price = price_match.group(1)
        
        if not record.price:
            record.price = first_text(about_panel, [r'text=/[$€£¥₱]\s?\d[\d,]*/', r'text=/[A-Z]{3}\s?\d[\d,]*/'])
        
        if record.price:
            record.currency = extract_currency(record.price)

    # Address & Phone
    address, phone = parse_address_and_phone_from_panel_text(panel_text)
    if not address:
        address = first_text(about_panel, ['[data-tooltip*="Address"]', 'button[aria-label*="Address"]'])
    
    record.address = address or record.address
    record.phone = phone or record.phone
    
    # Website
    record.website = extract_website_url(page)

    # About
    record.about = parse_about_from_panel_text(panel_text) or record.about

    # Check-in / Check-out
    check_in_match = re.search(r"Check-in time:\s*([0-9: ]+[AP]M)", panel_text, re.IGNORECASE)
    check_out_match = re.search(r"Check-out time:\s*([0-9: ]+[AP]M)", panel_text, re.IGNORECASE)
    if check_in_match: record.check_in = check_in_match.group(1)
    if check_out_match: record.check_out = check_out_match.group(1)

    # Amenities - Use structured extraction
    record.amenities = extract_structured_amenities(page)

    # Nearby Places
    record.nearby_places = extract_nearby_places(page)

    # Photos
    record.photos = extract_photos(page, limit=photo_limit)
    record.source_url = page.url

    return record


def open_listing_page(context, source: str) -> Page:
    page = context.new_page()
    target_url = source if source.startswith("http") else build_search_url(source)
    page.goto(target_url, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(3000)
    accept_google_dialogs(page)
    dismiss_google_dialogs(page)
    return page


def download_photos(hotel: HotelRecord, output_dir: Path, timeout: int = 20) -> list[str]:
    if not hotel.photos or not hotel.name:
        return []

    hotel_dir = output_dir / safe_filename(hotel.name)
    hotel_dir.mkdir(parents=True, exist_ok=True)
    saved_files: list[str] = []

    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
        }
    )

    for index, photo_url in enumerate(hotel.photos, start=1):
        suffix = Path(urlparse(photo_url).path).suffix or ".jpg"
        photo_path = hotel_dir / f"{index:03d}{suffix}"
        try:
            response = session.get(photo_url, timeout=timeout)
            response.raise_for_status()
            photo_path.write_bytes(response.content)
            saved_files.append(str(photo_path))
        except Exception:
            continue

    return saved_files


def scrape_hotels(
    source: str,
    limit: int,
    photo_limit: int,
    headless: bool,
    download_images: bool,
    image_dir: Path,
) -> list[HotelRecord]:
    records: list[HotelRecord] = []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=headless)
        context = browser.new_context(
            locale="en-US",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        )
        Stealth().apply_stealth_sync(context)
        page = open_listing_page(context, source)

        # Check if we are already on a detail page
        if "/travel/hotels/" in page.url and ("qs=" in page.url or "q=" not in page.url):
            try:
                record = extract_detail_page(page, url=None, photo_limit=photo_limit)
                if record.name:
                    records.append(record)
                    if download_images:
                        download_photos(record, image_dir)
                    print(f"[1/1] scraped: {record.name}")
                return records
            finally:
                page.close()

        scroll_listing_page(page, passes=max(2, limit // 5))
        hotel_listings = get_hotel_listings(page, limit=limit)
        page.close()

        for index, initial_record in enumerate(hotel_listings, start=1):
            detail_page = context.new_page()
            try:
                record = extract_detail_page(
                    detail_page,
                    url=initial_record.listing_url,
                    photo_limit=photo_limit,
                    initial_record=initial_record
                )
                if not record.name:
                    continue
                records.append(record)
                if download_images:
                    download_photos(record, image_dir)
                print(f"[{index}/{len(hotel_listings)}] scraped: {record.name}")
            except TimeoutError:
                print(f"[warn] timeout while scraping: {initial_record.listing_url}")
            except Exception as exc:
                print(f"[warn] failed to scrape: {initial_record.listing_url} - {exc}")
            finally:
                detail_page.close()

        context.close()
        browser.close()

    return records


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Scrape Google Hotels listing and detail data into JSON."
    )
    parser.add_argument(
        "source",
        help="Search text like 'hotels in manila' or a direct Google Hotels URL.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Maximum number of hotels to scrape.",
    )
    parser.add_argument(
        "--photo-limit",
        type=int,
        default=20,
        help="Maximum number of photo URLs to keep per hotel.",
    )
    parser.add_argument(
        "--output",
        default="output/hotels.json",
        help="Path to write the JSON output.",
    )
    parser.add_argument(
        "--images-dir",
        default="output/photos",
        help="Directory for downloaded hotel images.",
    )
    parser.add_argument(
        "--download-images",
        action="store_true",
        help="Download discovered photo URLs to disk.",
    )
    parser.add_argument(
        "--headed",
        action="store_true",
        help="Run a visible browser instead of headless mode.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    image_dir = Path(args.images_dir)
    image_dir.mkdir(parents=True, exist_ok=True)

    started = time.time()
    records = scrape_hotels(
        source=args.source,
        limit=args.limit,
        photo_limit=args.photo_limit,
        headless=not args.headed,
        download_images=args.download_images,
        image_dir=image_dir,
    )
    payload: list[dict[str, Any]] = [asdict(record) for record in records]
    output_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    duration = time.time() - started
    print(f"saved {len(records)} hotel records to {output_path} in {duration:.1f}s")


if __name__ == "__main__":
    main()
