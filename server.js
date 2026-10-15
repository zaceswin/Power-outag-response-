const express = require('express');
const { Pool } = require('pg');
const cors = require('cors');

const app = express();
app.use(express.json());
app.use(cors());

// Configure your local PostgreSQL cluster connection details
const pool = new Pool({
    user: 'postgres',
    host: 'localhost',
    database: 'outage_tracker',
    port: 5432,
    // Note: Since we are running on local peer connections via Unix Sockets, 
    // a password flag might not be required depending on your pg_hba.conf rules.
});

/**
 * POST /api/report-outage
 * Handles Core Step 4 Logic:
 * Finds unresolved outages within 500 meters using ST_DWithin.
 */
app.post('/api/report-outage', async (req, res) => {
    const { longitude, latitude, area_name } = req.body;

    if (!longitude || !latitude || !area_name) {
        return res.status(400).json({ error: "Missing required parameters: longitude, latitude, area_name" });
    }

    const client = await pool.connect();

    try {
        await client.query('BEGIN');

        // Look for existing active clusters inside a 500m bubble
        const findClusterQuery = `
            SELECT id, report_count FROM outage_events 
            WHERE status != 'resolved'
              AND ST_DWithin(
                  location_centroid::geography, 
                  ST_SetSRID(ST_MakePoint($1, $2), 4326)::geography, 
                  500
              )
            ORDER BY ST_Distance(location_centroid::geography, ST_SetSRID(ST_MakePoint($1, $2), 4326)::geography) ASC
            LIMIT 1;
        `;
        
        const existingCluster = await client.query(findClusterQuery, [longitude, latitude]);
        let eventId;
        let finalStatus = 'investigating';

        if (existingCluster.rows.length > 0) {
            // MERGE WORKFLOW: Found an active cluster nearby!
            eventId = existingCluster.rows[0].id;
            const updatedCount = existingCluster.rows[0].report_count + 1;
            
            if (updatedCount >= 5) {
                finalStatus = 'ongoing';
            }

            const updateClusterQuery = `
                UPDATE outage_events 
                SET report_count = $1, status = $2, updated_at = CURRENT_TIMESTAMP
                WHERE id = $3;
            `;
            await client.query(updateClusterQuery, [updatedCount, finalStatus, eventId]);

        } else {
            // NEW OUTAGE WORKFLOW: No active events near this coordinate. Create one.
            const createClusterQuery = `
                INSERT INTO outage_events (area_name, status, location_centroid, report_count)
                VALUES ($1, 'investigating', ST_SetSRID(ST_MakePoint($2, $3), 4326), 1)
                RETURNING id;
            `;
            const newCluster = await client.query(createClusterQuery, [area_name, longitude, latitude]);
            eventId = newCluster.rows[0].id;
        }

        // Always log the raw individual report pointing to the correct event ID
        const insertReportQuery = `
            INSERT INTO reports (event_id, location)
            VALUES ($1, ST_SetSRID(ST_MakePoint($2, $3), 4326));
        `;
        await client.query(insertReportQuery, [eventId, longitude, latitude]);

        await client.query('COMMIT');

        res.status(201).json({
            success: true,
            message: "Incident logged and clustered successfully.",
            data: { eventId, status: finalStatus }
        });

    } catch (error) {
        await client.query('ROLLBACK');
        console.error("Cluster Engine Exception:", error);
        res.status(500).json({ error: "Internal processing failure." });
    } finally {
        client.release();
    }
});

// GET /api/active-outages (To draw clusters onto our web map later)
app.get('/api/active-outages', async (req, res) => {
    try {
        const activeEvents = await pool.query(`
            SELECT id, area_name, status, report_count,
                   ST_X(location_centroid) as longitude,
                   ST_Y(location_centroid) as latitude
            FROM outage_events WHERE status != 'resolved';
        `);
        res.status(200).json(activeEvents.rows);
    } catch (error) {
        res.status(500).json({ error: "Failed to fetch map markers." });
    }
});

app.listen(3000, () => console.log('Server online on port 3000'));
