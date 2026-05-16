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
            context = await browser.new_context(locale="en-US")
            await Stealth().apply_stealth_async(context)
            page = await context.new_page()
            
            offers = []
            try:
                await page.goto(price_url, wait_until="domcontentloaded", timeout=60000)
                await page.wait_for_timeout(3000)
                
                from main import accept_google_dialogs, dismiss_google_dialogs, clean_google_redirect
                await accept_google_dialogs(page)
                await dismiss_google_dialogs(page)

                # Extract offers using evaluate for high fidelity
                data = await page.evaluate(r'''() => {
                    const results = [];
                    // Find all price rows/blocks.
                    const links = Array.from(document.querySelectorAll('a')).filter(a => {
                        const text = a.innerText || a.getAttribute('aria-label') || '';
                        return text.includes('Visit site');
                    });

                    for (const link of links) {
                        const container = link.closest('div[role="listitem"]') || link.parentElement.parentElement;
                        if (!container) continue;

                        // Find provider name - look for text inside specific spans or images
                        let providerName = "";
                        
                        // 0. Check aria-label of the link
                        const ariaLabel = link.getAttribute('aria-label') || "";
                        if (ariaLabel && ariaLabel.toLowerCase().includes('visit site')) {
                            // Common pattern: "Visit site for [Provider Name]"
                            const nameMatch = ariaLabel.match(/Visit site (?:for|at)\s+(.*)/i);
                            if (nameMatch) providerName = nameMatch[1].trim();
                        }
                        
                        // 1. Check for 'Official' indicators
                        const isOfficial = container.innerText.toLowerCase().includes('official') || 
                                           container.innerText.toLowerCase().includes('website');
                        
                        // 2. Try to find the provider name in images
                        if (!providerName || providerName === "Unknown") {
                            const images = Array.from(container.querySelectorAll('img[alt]'));
                            for (const img of images) {
                                const alt = img.getAttribute('alt').trim();
                                if (alt && alt.length > 2 && alt.length < 40 && !alt.toLowerCase().includes('visit site')) {
                                    providerName = alt;
                                    break;
                                }
                            }
                        }
                        
                        // 3. Look for a span that isn't the price and isn't the 'Visit site' text
                        if (!providerName || providerName === "Unknown") {
                            const candidateEls = Array.from(container.querySelectorAll('span, div'));
                            // Filter for marketing text and other noise
                            const noise = ['visit site', 'official', 'website', 'view more', 'free cancellation', 
                                         'reviews', 'customer service', 'breakfast', 'refundable', 'deals',
                                         'room rates', 'more room', 'stay+ offers', 'unlock', 'book and earn',
                                         'guest reviews'];
                                         
                            for (const el of candidateEls) {
                                if (el.children.length > 0) continue; // Prefer leaf nodes
                                
                                const text = el.innerText.trim();
                                const lowerText = text.toLowerCase();
                                
                                if (text && 
                                    text.length > 2 && 
                                    text.length < 50 && 
                                    !/[₱$€£]/.test(text) && 
                                    !noise.some(n => lowerText.includes(n))) {
                                    providerName = text;
                                    break;
                                }
                            }
                        }
                        
                        if (isOfficial && (!providerName || providerName === "Unknown")) {
                            providerName = "Official Website";
                        }

                        // Refine provider name
                        providerName = (providerName || "Unknown").split('\n')[0].trim();
                        if (providerName.length > 60) providerName = providerName.substring(0, 60);

                        // Find price - look for the span that matches common currency patterns
                        let price = "N/A";
                        const allSpans = Array.from(container.querySelectorAll('span, div'));
                        
                        // Regex for price: currency symbol followed by numbers and optional commas
                        const priceRegex = /[₱$€£]\s?[\d,]+/;
                        
                        const priceMatches = allSpans
                            .map(s => s.innerText.trim())
                            .filter(t => priceRegex.test(t))
                            .sort((a, b) => a.length - b.length); // Shortest string usually the price itself
                        
                        if (priceMatches.length > 0) {
                            price = priceMatches[0];
                        } else {
                            // Search the whole container text for a price pattern
                            const fullText = container.innerText;
                            const match = fullText.match(priceRegex);
                            if (match) price = match[0];
                        }

                        results.push({
                            provider_name: providerName,
                            price: price,
                            booking_url: link.href,
                            is_official: isOfficial
                        });
                    }
                    return results;
                }''')

                for item in (data or []):
                    offers.append(PricingOffer(
                        provider_name=item['provider_name'],
                        price=item['price'],
                        booking_url=clean_google_redirect(item['booking_url']),
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
