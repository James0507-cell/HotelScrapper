import asyncio
import json
from mini_scrapers import MiniHotelScraper

async def test_suggestions():
    scraper = MiniHotelScraper(headless=True)
    
    test_queries = ["Manila", "Boracay"]
    
    for query in test_queries:
        print(f"\nTesting search suggestions for: '{query}'")
        try:
            suggestions = await scraper.get_search_suggestions(query)
            
            print(f"Found {len(suggestions)} suggestions:")
            for i, sug in enumerate(suggestions, 1):
                print(f"{i}. [{sug.suggestion_type}] {sug.text}")
                if sug.subtext:
                    print(f"   Subtext: {sug.subtext}")
            
            # Save to file
            output_file = f"output/suggestions_{query.lower()}.json"
            with open(output_file, "w", encoding="utf-8") as f:
                json.dump([obj.__dict__ for obj in suggestions], f, indent=2, ensure_ascii=False)
            print(f"Results saved to {output_file}")
                
        except Exception as e:
            print(f"An error occurred for query '{query}': {e}")

if __name__ == "__main__":
    asyncio.run(test_suggestions())
