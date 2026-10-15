from typing import Any, Generator, cast
import os
from fastapi import FastAPI, HTTPException, Depends
from pydantic import BaseModel
import psycopg
from psycopg.rows import dict_row

app = FastAPI()

DB_DSN = os.environ["DB_DSN"]

class OutageReport(BaseModel):
    user_id: str
    latitude: float
    longitude: float

def get_db() -> Generator[psycopg.Connection[Any], None, None]:
    row_factory = cast(Any, dict_row)
    with psycopg.connect(DB_DSN, row_factory=row_factory) as conn:
        yield conn

@app.post("/api/reports")
def submit_report(
    report: OutageReport, 
    db: psycopg.Connection[dict[str, Any]] = Depends(get_db)
):
    cursor = db.cursor()

    # --- 1. SPAM FILTER: Check Trust Score & Shadowban ---
    cursor.execute("SELECT trust_score FROM users WHERE id = %s", (report.user_id,))
    user = cursor.fetchone()
    
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
        
    if user['trust_score'] < 0:
        # Shadowban: Pretend it succeeded to confuse malicious scripts
        return {"status": "success", "message": "Report received."}

    # --- 2. SPAM FILTER: Rate Limiting ---
    cursor.execute("""
        SELECT id FROM reports 
        WHERE user_id = %s AND created_at > NOW() - INTERVAL '30 minutes'
    """, (report.user_id,))
    
    if cursor.fetchone():
        raise HTTPException(status_code=429, detail="You can only submit one report every 30 minutes.")

    # --- 3. GEOSPATIAL CLUSTERING: Find nearby active events (500 meters) ---
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
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Database transaction failed.")
