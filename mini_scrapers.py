import asyncio
import re
from pathlib import Path
from dataclasses import asdict, dataclass
from playwright.async_api import async_playwright
from playwright_stealth import Stealth
from main import (
    HotelRecord, 
    extract_detail_page, 
    accept_google_dialogs, 
    dismiss_google_dialogs, 
    clean_google_redirect,
    PricingOffer,
    extract_all_pricing_offers
)

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
            
            try:
                await page.goto(price_url, wait_until="domcontentloaded", timeout=60000)
                await page.wait_for_timeout(3000)
                
                await accept_google_dialogs(page)
                await dismiss_google_dialogs(page)

                # Use the logic now centralized in main.py
                return await extract_all_pricing_offers(page)
            finally:
                await context.close()
                await browser.close()

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
