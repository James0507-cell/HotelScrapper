import requests
import json

base_url = "https://romance-newton-september-evaluations.trycloudflare.com"

# URL from test_prices.py
sample_url = "https://www.google.com/travel/search?q=hotels%20in%20bali&qs=MiZDaGdJOExfeTY5VGhvZUl1R2d3dlp5OHhhbXQ1TjJabmNqUVFBUTgA&ved=2ahUKEwik2ZLItLiUAxUmjGYCHQ_uOKEQrsMEegQIAxBI&ts=CAESCgoCCAMKAggDEAAaTQovEi0yJTB4MmRkMTQxZDNlODEwMGZhMToweDI0OTEwZmIxNGIyNGU2OTA6BEJhbGkSGhIUCgcI6g8QBRgZEgcI6g8QBRgaGAEyAhAAKgcKBToDUEhQ"

def test_root():
    print("Testing Root...")
    r = requests.get(f"{base_url}/")
    print(f"Status: {r.status_code}")
    print(f"Response: {r.json()}")

def test_suggestions():
    print("\nTesting Suggestions...")
    payload = {"query": "Manila"}
    r = requests.post(f"{base_url}/suggestions", json=payload)
    print(f"Status: {r.status_code}")
    if r.status_code == 200:
        print(f"Response: {json.dumps(r.json()[:3], indent=2)} ...")
    else:
        print(f"Error: {r.text}")

def test_info():
    print("\nTesting Info...")
    payload = {"url": sample_url}
    r = requests.post(f"{base_url}/info", json=payload)
    print(f"Status: {r.status_code}")
    if r.status_code == 200:
        print(f"Response: {json.dumps(r.json(), indent=2)}")
    else:
        print(f"Error: {r.text}")

def test_pricing_all():
    print("\nTesting Pricing All...")
    payload = {"url": sample_url}
    r = requests.post(f"{base_url}/pricing/all", json=payload)
    print(f"Status: {r.status_code}")
    if r.status_code == 200:
        print(f"Response: Found {len(r.json())} offers. First 2:")
        print(json.dumps(r.json()[:2], indent=2))
    else:
        print(f"Error: {r.text}")

if __name__ == "__main__":
    test_root()
    test_suggestions()
    test_info()
    test_pricing_all()
