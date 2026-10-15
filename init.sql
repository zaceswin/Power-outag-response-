-- Enable PostGIS first
CREATE EXTENSION IF NOT EXISTS postgis;

-- 1. Users (Customers & Technicians)
CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    phone VARCHAR(15) UNIQUE NOT NULL,
    role VARCHAR(20) DEFAULT 'customer',
    trust_score INT DEFAULT 10,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 2. Clustered Outage Events
CREATE TABLE IF NOT EXISTS outage_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    area_name VARCHAR(100),
    status VARCHAR(20) DEFAULT 'unconfirmed',
    assigned_tech_id UUID REFERENCES users(id),
    location_center GEOMETRY(Point, 4326) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 3. Individual Customer Reports
CREATE TABLE IF NOT EXISTS reports (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id),
    event_id UUID REFERENCES outage_events(id),
    location GEOMETRY(Point, 4326) NOT NULL, 
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS reports_location_idx ON reports USING GIST (location);

-- 4. Technician Status Updates
CREATE TABLE IF NOT EXISTS status_updates (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_id UUID REFERENCES outage_events(id) ON DELETE CASCADE,
    message TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);