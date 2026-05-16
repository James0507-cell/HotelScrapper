import asyncio
import re
from pathlib import Path
from dataclasses import asdict, dataclass
from playwright.async_api import async_playwright
from playwright_stealth import Stealth
from main import HotelRecord, extract_detail_page, accept_google_dialogs, dismiss_google_dialogs

@dataclass
class PricingOffer:
    provider_name: str
    price: str
    booking_url: str
    provider_logo_url: str = ""
    is_official: bool = False

class MiniHotelScraper:
    """
    A lightweight scraper for targeted extraction of specific hotel data sections.
    """
    
    def __init__(self, headless: bool = True):
        self.headless = headless

    async def _scrape_section(self, url: str, sections: list[str]) -> HotelRecord:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=self.headless)
            context = await browser.new_context(
                locale="en-US", 
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            )
            await Stealth().apply_stealth_async(context)
            page = await context.new_page()
            
            try:
                record = await extract_detail_page(page, url=url, sections=sections)
                return record
            finally:
                await context.close()
                await browser.close()

    async def get_all_prices(self, url: str) -> list[PricingOffer]:
        """
        Extracts ALL available pricing offers from the "Prices" tab.
        Uses the tokens in the provided URL to ensure consistency with search results.
        """
        # Ensure we target the prices tab (Base64 for 'prices')
        price_url = url
        if "ap=" not in url:
            price_url += "&ap=ugEGcHJpY2Vz"
        elif "ap=ugEGcHJpY2Vz" not in url:
            # Replace existing ap token if necessary
            price_url = re.sub(r"ap=[^&]+", "ap=ugEGcHJpY2Vz", url)

        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=self.headless)
            context = await browser.new_context(
                locale="en-US",
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            )
            await Stealth().apply_stealth_async(context)
            page = await context.new_page()
            
            offers = []
            try:
                await page.goto(price_url, wait_until="domcontentloaded", timeout=60000)
                await page.wait_for_timeout(3000)
                
                from main import accept_google_dialogs, dismiss_google_dialogs, clean_google_redirect
                await accept_google_dialogs(page)
                await dismiss_google_dialogs(page)

                # Scroll to ensure all prices are loaded
                await page.evaluate(r'''async () => {
                    for (let i = 0; i < 3; i++) {
                        window.scrollBy(0, 1000);
                        await new Promise(r => setTimeout(r, 500));
                    }
                }''')

                # Extract offers using evaluate for high fidelity
                data = await page.evaluate(r'''() => {
                    const results = [];
                    
                    // 1. Identify the main pricing container to avoid ads/nearby/reviews
                    const pricingContainer = Array.from(document.querySelectorAll('c-wiz')).find(cw => 
                        cw.innerText.toLowerCase().includes('featured options') || 
                        cw.innerText.toLowerCase().includes('all options')
                    ) || document.querySelector('.xQwpfc') || document.body;

                    // 2. Find elements that are likely pricing links or buttons within that container
                    const elements = Array.from(pricingContainer.querySelectorAll('a, button[role="link"]')).filter(el => {
                        const text = (el.innerText || el.getAttribute('aria-label') || '').toLowerCase();
                        return text.includes('visit site') || text.includes('visit booking.com') || 
                               text.includes('visit agoda') || text.includes('visit expedia');
                    });

                    const noise = ['visit site', 'official', 'website', 'view more', 'free cancellation', 
                                 'reviews', 'customer service', 'breakfast', 'refundable', 'deals',
                                 'room rates', 'more room', 'stay+ offers', 'unlock', 'book and earn',
                                 'guest reviews', 'nightly', 'total', 'taxes', 'fees', 'guests', 'person', 'people'];

                    for (const el of elements) {
                        // Find the most relevant container for this pricing offer
                        const container = el.closest('div[role="listitem"]') || 
                                        el.closest('.KUIwfc') || 
                                        el.closest('.IJxDxc') || 
                                        el.parentElement.parentElement;
                        
                        if (!container) continue;

                        const containerText = container.innerText;
                        const priceMatch = containerText.match(/[₱$€£]\s?[\d,]+/);
                        let price = priceMatch ? priceMatch[0] : "N/A";
                        
                        // Detect official site
                        const isOfficial = containerText.toLowerCase().includes('official site') || 
                                          containerText.toLowerCase().includes('official website') ||
                                          (el.getAttribute('aria-label') || '').toLowerCase().includes('official');

                        let providerName = "Unknown";
                        
                        // 1. Try aria-label (highest signal for specific links)
                        const ariaLabel = el.getAttribute('aria-label') || "";
                        if (ariaLabel) {
                            const nameMatch = ariaLabel.match(/Visit site (?:for|at|on)\s+(.*)/i) || 
                                            ariaLabel.match(/Visit\s+(.*)/i);
                            if (nameMatch) {
                                providerName = nameMatch[1].trim();
                            }
                        }

                        // 2. Search container and ancestors for headers or logos
                        if (providerName === "Unknown" || providerName === "site") {
                            let curr = container;
                            for (let i = 0; i < 5 && curr; i++) {
                                const h3 = curr.querySelector('h3, h4');
                                if (h3 && h3.innerText && h3.innerText.length < 60) {
                                    providerName = h3.innerText.split('\n')[0].trim();
                                    break;
                                }
                                
                                const img = curr.querySelector('img');
                                if (img && img.getAttribute('alt')) {
                                    const alt = img.getAttribute('alt');
                                    if (alt.length > 2 && !alt.toLowerCase().includes('logo') && 
                                        !alt.toLowerCase().includes('visit site')) {
                                        providerName = alt;
                                        break;
                                    }
                                }
                                curr = curr.parentElement;
                            }
                        }

                        // 3. Look for plain text in container (first non-noise line)
                        if (providerName === "Unknown" || providerName === "site") {
                            const lines = containerText.split('\n').map(l => l.trim()).filter(l => l.length > 0);
                            for (const line of lines) {
                                const lowerLine = line.toLowerCase();
                                if (line.length > 1 && line.length < 50 && 
                                    !/[₱$€£]/.test(line) && 
                                    !noise.some(n => lowerLine.includes(n))) {
                                    providerName = line;
                                    break;
                                }
                            }
                        }

                        // 4. Fallback: parse domain from URL
                        if (providerName === "Unknown" || providerName === "site") {
                            try {
                                let urlStr = el.href || el.getAttribute('data-url') || "";
                                if (urlStr) {
                                    if (urlStr.startsWith('/')) urlStr = window.location.origin + urlStr;
                                    const url = new URL(urlStr);
                                    let host = url.hostname.replace('www.', '');
                                    const parts = host.split('.');
                                    const commonSubdomains = ['api', 'ph', 'm', 'mobile', 'en', 'id', 'id-id', 'com', 'google'];
                                    let namePart = parts[0];
                                    if (commonSubdomains.includes(namePart.toLowerCase()) && parts.length > 2) {
                                        namePart = parts[1];
                                    }
                                    providerName = namePart.charAt(0).toUpperCase() + namePart.slice(1);
                                }
                            } catch (e) {}
                        }

                        providerName = providerName.replace(/Visit (?:site for )?/i, '').trim();
                        if (isOfficial) {
                            providerName = "Official Website";
                        }
                        
                        // Skip if name is still Unknown or generic 'Google' (unless it's truly official)
                        if ((providerName.toLowerCase() === 'google' || providerName === 'Unknown') && !isOfficial) {
                             continue;
                        }

                        if (price !== "N/A" || isOfficial) {
                            results.push({
                                provider_name: providerName,
                                price: price,
                                booking_url: el.href || el.getAttribute('data-url') || "",
                                provider_logo_url: container.querySelector('img') ? container.querySelector('img').src : "",
                                is_official: isOfficial
                            });
                        }
                    }
                    return results;
                }''')

                for item in (data or []):
                    # Robust deduplication: check provider and price
                    is_dup = False
                    for o in offers:
                        # Same provider and price is almost certainly the same offer
                        if o.provider_name == item['provider_name'] and o.price == item['price']:
                            is_dup = True
                            # If existing one has no URL but new one does, update it
                            if not o.booking_url and item['booking_url']:
                                o.booking_url = clean_google_redirect(item['booking_url'])
                            break
                        # Same URL is definitely dup
                        if item['booking_url'] and o.booking_url == clean_google_redirect(item['booking_url']):
                            is_dup = True
                            break
                    
                    if is_dup:
                        continue
                        
                    offers.append(PricingOffer(
                        provider_name=item['provider_name'],
                        price=item['price'],
                        booking_url=clean_google_redirect(item['booking_url']) or "",
                        provider_logo_url=item['provider_logo_url'],
                        is_official=item['is_official']
                    ))
            finally:
                await context.close()
                await browser.close()
        return offers

    async def get_cheapest_price(self, url: str):
        """Extracts ONLY the pricing information (nightly price, total, and booking URL)."""
        record = await self._scrape_section(url, sections=['pricing'])
        return record.pricing

    async def get_location(self, url: str):
        """Extracts ONLY location data (coordinates and nearby places)."""
        record = await self._scrape_section(url, sections=['location', 'info'])
        return record.location

    async def get_basic_info(self, url: str):
        """Extracts ONLY basic hotel info (name, stars, rating, about)."""
        record = await self._scrape_section(url, sections=['info'])
        return record.hotel_info

    async def get_contact_info(self, url: str):
        """Extracts ONLY contact details (address, phone, website)."""
        record = await self._scrape_section(url, sections=['contact'])
        return record.contact

if __name__ == "__main__":
    # Quick demo
    async def demo():
        # Manila Marriott Hotel
        url = "https://www.google.com/travel/search?q=Hotels%20in%20Manila&qs=CAEyJ0Noa1FpWkhIN09IdC1iWTdHZzB2Wnk4eE1XMXpYMnh5YTNGNEVBSTgASAA&ved=0CBkQrsMEahgKEwj41uT89LuUAxUAAAAAHQAAAAAQ2QE&ts=CAESDgoCCAMKAggDCgIIAxABGlEKMxIvMiUweDMzOTdjYTAzNTcxZWMzOGI6MHg2OWQxZDU3NTEwNjljMTFmOgZNYW5pbGEaABIaEhQKBwjqDxAFGBQSBwjqDxAFGBYYAjICCAEqCQoFOgNQSFAaAA&ap=MAE"
        scraper = MiniHotelScraper(headless=True)
        
        print(f"--- Fetching CHEAPEST PRICE for {url} ---")
        pricing = await scraper.get_cheapest_price(url)
        print(f"Price: {pricing.cheapest_price_per_night} (via {pricing.booking_url})")
        
        print(f"\n--- Fetching LOCATION ---")
        location = await scraper.get_location(url)
        print(f"Coords: {location.latitude}, {location.longitude}")
        
    asyncio.run(demo())
