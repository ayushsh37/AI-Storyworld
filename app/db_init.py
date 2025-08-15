# backend/app/db_init.py
# Initialize sqlite DB tables used by orchestrator
import sqlite3
import os
DB_FILE = os.getenv("STORY_DB", "storyworld.db")
def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS worlds (id TEXT PRIMARY KEY, state TEXT, created_at TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS agents (id TEXT PRIMARY KEY, name TEXT, endpoint TEXT, world_id TEXT, type TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS events (id TEXT PRIMARY KEY, world_id TEXT, actor_id TEXT, event_json TEXT, created_at TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS snapshots (id TEXT PRIMARY KEY, world_id TEXT, snapshot_json TEXT, created_at TEXT)""")
    conn.commit()
    conn.close()

if __name__ == "__main__":
    init_db()
