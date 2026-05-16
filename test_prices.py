import asyncio
import json
from mini_scrapers import MiniHotelScraper

async def test_get_all_prices():
    # Mahagiri Resort Nusa Lembongan
    url = "https://www.google.com/travel/search?q=hotels%20in%20bali&qs=MiZDaGdJOExfeTY5VGhvZUl1R2d3dlp5OHhhbXQ1TjJabmNqUVFBUTgA&ved=2ahUKEwik2ZLItLiUAxUmjGYCHQ_uOKEQrsMEegQIAxBI&ts=CAESCgoCCAMKAggDEAAaTQovEi0yJTB4MmRkMTQxZDNlODEwMGZhMToweDI0OTEwZmIxNGIyNGU2OTA6BEJhbGkSGhIUCgcI6g8QBRgZEgcI6g8QBRgaGAEyAhAAKgcKBToDUEhQ"
    
    scraper = MiniHotelScraper(headless=True)
    print(f"Testing get_all_prices for URL: {url}\n")
    
    try:
        offers = await scraper.get_all_prices(url)
        
        print(f"Found {len(offers)} offers:")
        for i, offer in enumerate(offers, 1):
            name = offer.provider_name or "Unknown"
            price = offer.price or "N/A"
            url = offer.booking_url or ""
            print(f"{i}. Provider: {name}")
            print(f"   Price: {price}")
            print(f"   Official: {offer.is_official}")
            print(f"   Logo: {offer.provider_logo_url}")
            print(f"   URL: {url[:100]}...")
            print("-" * 20)
            
        # Also save to file for easier inspection
        output_file = "output/test_prices_all.json"
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump([obj.__dict__ for obj in offers], f, indent=2)
        print(f"\nDetailed results saved to {output_file}")
            
    except Exception as e:
        print(f"An error occurred: {e}")

if __name__ == "__main__":
    asyncio.run(test_get_all_prices())
