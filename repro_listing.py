from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth
import re

def test_extraction():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        )
        Stealth().apply_stealth_sync(context)
        page = context.new_page()
        page.goto("https://www.google.com/travel/search?q=hotels+in+manila", wait_until="domcontentloaded")
        
        # Wait for some content
        page.wait_for_timeout(5000)
        
        # Try finding links with aria-label that look like hotel listings
        # Based on previous evaluate results, they have href containing /travel/search?
        candidates = page.locator('a[role="link"][aria-label][href*="/travel/search?"]').all()
        print(f"Found {len(candidates)} candidates")
        
        results = []
        seen_names = set()
        
        for item in candidates:
            aria_label = item.get_attribute("aria-label") or ""
            text = item.inner_text() or ""
            
            # Use aria-label to identify hotel name
            # Usually "Hotel Name" or "Prices starting from ₱..., Hotel Name"
            name = None
            price = None
            rating = None
            review_count = None
            
            # Try to extract price from text or aria-label
            price_match = re.search(r"[$€£¥₱]\s?\d[\d,]*", text + " | " + aria_label)
            if price_match:
                price = price_match.group(0)
            
            # Try to extract name
            # If aria-label starts with "Prices starting from", name is after the comma
            if "Prices starting from" in aria_label:
                parts = aria_label.split(",")
                if len(parts) > 1:
                    name = parts[1].strip()
            elif "out of 5 stars from" in aria_label:
                parts = aria_label.split(",")
                if len(parts) > 1:
                    name = parts[1].strip()
            elif aria_label and "View prices" not in aria_label and "Photos for" not in aria_label:
                name = aria_label.strip()
            
            if name and name not in seen_names:
                seen_names.add(name)
                # Try to extract rating/reviews
                rating_match = re.search(r"(\d\.\d)\s*\(([\d,Kk.]+)\)", text)
                if rating_match:
                    rating = rating_match.group(1)
                    review_count = rating_match.group(2)
                
                print(f"Found: {name} | Price: {price} | Rating: {rating} ({review_count})")
                results.append({"name": name, "price": price, "rating": rating, "reviews": review_count})

        browser.close()

if __name__ == "__main__":
    test_extraction()
