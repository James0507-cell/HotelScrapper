
import asyncio
from pathlib import Path
from main import scrape_hotels

def main():
    # Configuration
    query = "Hotels in Manila"
    check_in = "2026-05-20"  # Format: YYYY-MM-DD
    check_out = "2026-05-24"
    adults = 3
    children = 0
    limit = 5  # Number of hotels to scrape
    
    output_file = Path("output/manila_custom_dates.json")
    output_file.parent.mkdir(exist_ok=True)

    print(f"Starting scrape for '{query}' from {check_in} to {check_out}...")
    
    results = scrape_hotels(
        source=query,
        limit=limit,
        photo_limit=3,
        headless=False,
        download_images=False,
        image_dir=Path("output/photos"),
        adults=adults,
        children=children,
        check_in=check_in,
        check_out=check_out
    )

    print(f"\nScraping complete. Found {len(results)} hotels.")
    for hotel in results:
        print(f"- {hotel.name}: {hotel.price} (Total: {hotel.total_price})")

if __name__ == "__main__":
    main()
