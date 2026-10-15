import os
import time
from contextlib import asynccontextmanager
from typing import Any, Generator, cast
from fastapi import APIRouter, FastAPI, HTTPException, Depends
from pydantic import BaseModel
import psycopg
from psycopg.rows import dict_row
from fastapi.middleware.cors import CORSMiddleware

try:
    from apscheduler.schedulers.background import BackgroundScheduler  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - optional dependency for local dev/test environments
    class BackgroundScheduler:
        def add_job(self, *args, **kwargs):
            return None

        def start(self):
            return None

        def shutdown(self):
            return None

# Database connection
DB_DSN = os.environ["DB_DSN"]
router = APIRouter()

# Pydantic model for incoming requests
class OutageReport(BaseModel):
    user_id: str
    latitude: float
    longitude: float

# Database dependency
def get_db() -> Generator[psycopg.Connection[Any], None, None]:
    with psycopg.connect(DB_DSN) as conn:
        yield conn


@router.post("/api/reports")
def submit_report(
    report: OutageReport, 
    db: psycopg.Connection[Any] = Depends(get_db)  # <-- Changed here
):
    cursor = db.cursor(row_factory=dict_row)       # <-- Changed here

    # ... keep the rest of your code exactly the same ...
    # --- 1. SPAM FILTER & USER CHECK ---
    cursor.execute("SELECT trust_score FROM users WHERE id = %s", (report.user_id,))
    user = cursor.fetchone()

    # Auto-create user for testing purposes if they don't exist
    if not user:
        try:
            # We use the user_id as their 'phone' for testing
            cursor.execute(
                "INSERT INTO users (id, phone, role, trust_score) VALUES (%s, %s, 'customer', 10) RETURNING trust_score",
                (report.user_id, report.user_id[:15]),
            )
            user = cursor.fetchone()
        except Exception:
            raise HTTPException(
                status_code=400,
                detail="Invalid user_id format. Must be a valid UUID like '123e4567-e89b-12d3-a456-426614174000'",
            )

    if user is None:
        raise HTTPException(status_code=500, detail="Unable to load or create user record.")

    if user['trust_score'] < 0:
        return {"status": "success", "message": "Report received."}

    # --- 2. RATE LIMITING ---
    cursor.execute("""
        SELECT id FROM reports 
        WHERE user_id = %s AND created_at > NOW() - INTERVAL '30 minutes'
    """, (report.user_id,))
    
    if cursor.fetchone():
        raise HTTPException(status_code=429, detail="You can only submit one report every 30 minutes.")

    # --- 3. GEOSPATIAL CLUSTERING (500 meters) ---
    cursor.execute("""
        SELECT id, status FROM outage_events 
        WHERE status != 'resolved'
        AND ST_DWithin(
            location_center::geography, 
            ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography, 
            500
        )
        LIMIT 1;
    """, (report.longitude, report.latitude))
    
    nearby_event = cursor.fetchone()

    try:
        if nearby_event:
            event_id = nearby_event['id']
            cursor.execute("""
                INSERT INTO reports (user_id, event_id, location)
                VALUES (%s, %s, ST_SetSRID(ST_MakePoint(%s, %s), 4326))
            """, (report.user_id, event_id, report.longitude, report.latitude))
            
            # --- 4. THRESHOLD ACTIVATION ---
            if nearby_event['status'] == 'unconfirmed':
                cursor.execute("SELECT COUNT(id) AS count FROM reports WHERE event_id = %s", (event_id,))
                count_row = cursor.fetchone()
                if count_row and count_row['count'] >= 3:
                    cursor.execute("UPDATE outage_events SET status = 'verified' WHERE id = %s", (event_id,))
        else:
            cursor.execute("""
                INSERT INTO outage_events (area_name, status, location_center)
                VALUES ('Pending Area', 'unconfirmed', ST_SetSRID(ST_MakePoint(%s, %s), 4326))
                RETURNING id;
            """, (report.longitude, report.latitude))
            
            new_event_row = cursor.fetchone()
            if not new_event_row:
                raise HTTPException(status_code=500, detail="Failed to create outage event.")
                
            new_event_id = new_event_row['id']
            
            cursor.execute("""
                INSERT INTO reports (user_id, event_id, location)
                VALUES (%s, %s, ST_SetSRID(ST_MakePoint(%s, %s), 4326))
            """, (report.user_id, new_event_id, report.longitude, report.latitude))

        db.commit()
        return {"status": "success", "message": "Outage reported successfully."}

    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------
# ENDPOINT 2: Get active outages for the map
# ---------------------------------------------------------
@router.get("/api/outages")
def get_active_outages(db: psycopg.Connection[Any] = Depends(get_db)): # <-- Changed here
    cursor = db.cursor(row_factory=dict_row)                           # <-- Changed here
    
    cursor.execute("""
        SELECT 
            id, 
            area_name, 
            status, 
            ST_X(location_center::geometry) as longitude, 
            ST_Y(location_center::geometry) as latitude 
        FROM outage_events 
        WHERE status != 'resolved'
    """)
    events = cursor.fetchall()
    return {"outages": events}
    
    
import time
import psycopg

# Connection Guard function
def wait_for_db(dsn: str, max_retries: int = 12, delay: int = 5):
    """Blocks execution until PostgreSQL is fully initialized and accepting queries."""
    print("Waiting for PostGIS database readiness...")
    for attempt in range(1, max_retries + 1):
        try:
            with psycopg.connect(dsn) as conn:
                with conn.cursor() as cursor:
                    cursor.execute("SELECT 1 FROM outage_events LIMIT 1;")
                    print("✅ PostGIS Database is online and tables are initialized!")
                    return True
        except Exception as e:
            print(f"⏳ DB not ready yet (Attempt {attempt}/{max_retries}). Retrying in {delay}s...")
            time.sleep(delay)
            
    raise RuntimeError("Could not connect to the database after maximum retries.")

# Placeholder background task for tweet ingestion.
# Replace this with the real scraper implementation when available.
def fetch_and_process_tweets() -> None:
    """Scheduled job hook for ingesting and processing tweet data."""
    print("Tweet ingestion job not configured yet.")


# Updated Lifespan Handler
scheduler = BackgroundScheduler()

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 1. Wait until DB is online and init.sql has executed
    wait_for_db(DB_DSN)

    # 2. Start APScheduler background jobs safely
    scheduler.add_job(fetch_and_process_tweets, 'interval', minutes=15, id='kplc_scraper')
    scheduler.start()

    # 3. Trigger initial scrape immediately after startup
    scheduler.add_job(fetch_and_process_tweets, id='kplc_scraper_boot')

    yield

    scheduler.shutdown()


app = FastAPI(lifespan=lifespan)
app.include_router(router)

# Allow frontend HTML maps to talk to this API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
