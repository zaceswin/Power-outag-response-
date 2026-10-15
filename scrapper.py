import os
import re
import tweepy
import psycopg
from typing import Any, cast
from psycopg.rows import dict_row
from geopy.geocoders import Nominatim
from geopy.exc import GeocoderTimedOut
from geopy.location import Location
from typing import Any


# 1. Configuration & Credentials
# You need a free Developer Account on X (developer.twitter.com) for a Bearer Token
TWITTER_BEARER_TOKEN = os.getenv("TWITTER_BEARER_TOKEN", "your_twitter_bearer_token_here")
DB_DSN = os.environ["DB_DSN"]
KPLC_USER_ID = "KenyaPower_Care"

# 2. Setup Clients
client = tweepy.Client(bearer_token=TWITTER_BEARER_TOKEN)
geolocator = Nominatim(user_agent="kplop_outage_tracker")

# Common Nairobi/Kenya estates to match against tweet text
ESTATES = [
    "Kilimani", "Kileleshwa", "Langata", "Roysambu", "Kasarani", 
    "Ruaka", "Westlands", "South B", "South C", "Embakasi", 
    "Donholm", "Buruburu", "Ngong Road", "Kikuyu", "Syokimau"
]

def extract_locations(text: str) -> list[str]:
    """Finds known estate names within the tweet text."""
    found_estates = []
    text_lower = text.lower()
    for estate in ESTATES:
        if estate.lower() in text_lower:
            found_estates.append(estate)
    return found_estates

def extract_locations_nlp(text: str) -> list[str]:
    """Return known estate names found in text."""
    return extract_locations(text)

def geocode_location(estate_name: str):
    """Converts a detected estate name into exact GPS Coordinates."""
    try:
        query = f"{estate_name}, Nairobi, Kenya"
        location = geolocator.geocode(query)
        
        # This check silences Pylance by proving it is a Location object, not a Coroutine
        if isinstance(location, Location):
            return (location.latitude, location.longitude)
    except GeocoderTimedOut:
        pass
    return None


def extract_text_from_image(img_url: str) -> str:
    """Fetch an image from a URL and OCR it; returns an empty string if OCR support is unavailable."""
    try:
        import io
        import requests
        from PIL import Image  # type: ignore[reportMissingImports]
        import pytesseract  # type: ignore[reportMissingImports]
    except Exception:
        return ""

    try:
        response = requests.get(img_url, timeout=15)
        response.raise_for_status()
        image = Image.open(io.BytesIO(response.content))
        return pytesseract.image_to_string(image)
    except Exception:
        return ""

def fetch_and_process_tweets():
    print("Running scheduled KPLC data ingestion (OCR & NLP)...")
    
    # Pre-flight check: If database isn't reachable right now, exit safely and wait for next interval
    try:
        with psycopg.connect(DB_DSN) as conn:
            pass
    except Exception as e:
        print(f"Skipping scrape interval: Database connection unavailable ({e})")
        return

    # Execute Twitter API extraction logic...
