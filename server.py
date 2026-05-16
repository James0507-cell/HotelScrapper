from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field
from typing import Optional, List, Dict
import asyncio
from pathlib import Path

# Import existing logic
from main import HotelRecord, HotelInfo, ContactInfo, LocationInfo, PricingInfo, PricingOffer, SearchSuggestion
from mini_scrapers import MiniHotelScraper

app = FastAPI(
    title="Hotel Scrapper API",
    description="API for extracting hotel data from Google Travel",
    version="1.0.0"
)

# --- Models ---

class ScrapeRequest(BaseModel):
    url: str
    adults: int = 2
    children: int = 0
    check_in: Optional[str] = None
    check_out: Optional[str] = None

class SectionRequest(BaseModel):
    url: str

class SuggestionRequest(BaseModel):
    query: str

# --- Endpoints ---

@app.get("/")
async def root():
    return {"message": "Hotel Scrapper API is running. Visit /docs for documentation."}

@app.post("/suggestions", response_model=List[SearchSuggestion])
async def get_search_suggestions(request: SuggestionRequest):
    """
    Extracts search suggestions for a given query (destination, hotel name, etc.)
    """
    scraper = MiniHotelScraper(headless=True)
    try:
        suggestions = await scraper.get_search_suggestions(request.query)
        return suggestions
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/scrape", response_model=List[HotelRecord])
async def scrape_full(request: ScrapeRequest, limit: int = 5):
    """
    Performs a full scrape using the main scraper logic.
    Handles listing pages or direct detail pages.
    """
    from main import scrape_hotels
    
    try:
        results = await scrape_hotels(
            source=request.url,
            limit=limit,
            photo_limit=10,
            headless=True,
            download_images=False,
            image_dir=Path("output/photos"),
            adults=request.adults,
            children=request.children,
            check_in=request.check_in,
            check_out=request.check_out
        )
        return results
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/pricing/all", response_model=List[PricingOffer])
async def get_all_prices(request: SectionRequest):
    """
    Extracts all available pricing offers from different providers.
    """
    scraper = MiniHotelScraper(headless=True)
    try:
        offers = await scraper.get_all_prices(request.url)
        return offers
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/pricing/cheapest", response_model=PricingInfo)
async def get_cheapest_price(request: SectionRequest):
    """
    Quickly extracts only the cheapest price info.
    """
    scraper = MiniHotelScraper(headless=True)
    try:
        pricing = await scraper.get_cheapest_price(request.url)
        return pricing
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/location", response_model=LocationInfo)
async def get_location(request: SectionRequest):
    """
    Extracts coordinates and nearby places.
    """
    scraper = MiniHotelScraper(headless=True)
    try:
        location = await scraper.get_location(request.url)
        return location
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/info", response_model=HotelInfo)
async def get_basic_info(request: SectionRequest):
    """
    Extracts basic hotel info (name, stars, rating, about).
    """
    scraper = MiniHotelScraper(headless=True)
    try:
        info = await scraper.get_basic_info(request.url)
        return info
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/contact", response_model=ContactInfo)
async def get_contact_info(request: SectionRequest):
    """
    Extracts address, phone, and website.
    """
    scraper = MiniHotelScraper(headless=True)
    try:
        contact = await scraper.get_contact_info(request.url)
        return contact
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
