import asyncio
import sqlite3
from app.services.topics import compute_topic_clusters
from dataclasses import asdict
import json
import sqlite_vec

async def main():
    conn = sqlite3.connect('data/EventTracker.db')
    conn.row_factory = sqlite3.Row
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    
    g = await compute_topic_clusters(conn, 1)
    print(json.dumps(asdict(g), indent=2))

if __name__ == "__main__":
    asyncio.run(main())
