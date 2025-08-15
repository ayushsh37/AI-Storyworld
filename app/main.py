# backend/app/main.py
import os
import json
import sqlite3
import uuid
import datetime
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from app.orchestrator import load_world, persist_world, init_db, tick_world
from app.db_init import init_db as init_db_table

# Ensure DB tables exist
init_db_table()

app = FastAPI(title="MCP Orchestrator API")

# ------------------- CORS Configuration -------------------
# Allow both local dev and Vercel frontend to call backend
origins = [
    "http://localhost:3000",  # local dev
    "https://ai-storyworld-frontend.vercel.app",  # replace with actual vercel URL
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,        # URLs allowed
    allow_credentials=True,
    allow_methods=["*"],          # All HTTP methods
    allow_headers=["*"],          # All headers
)
# ------------------------------------------------------------

class CreateWorldReq(BaseModel):
    name: str = "Demo World"

@app.post("/worlds")
def create_world(req: CreateWorldReq):
    wid = str(uuid.uuid4())
    state = {
        "id": wid,
        "name": req.name,
        "time": 0,
        "locations": [],
        "global_facts": [],
        "agents": [],
        "agents_map": {},
        "event_log": []
    }
    conn = sqlite3.connect(os.getenv("STORY_DB", "storyworld.db"))
    c = conn.cursor()
    c.execute(
        "INSERT INTO worlds (id,state,created_at) VALUES (?,?,?)",
        (wid, json.dumps(state), datetime.datetime.utcnow().isoformat())
    )
    conn.commit()
    conn.close()
    return {"id": wid, "state": state}

class RegisterAgentReq(BaseModel):
    name: str
    endpoint: str
    type: str = "NPC"

@app.post("/agents")
def register_agent(req: RegisterAgentReq, world_id: str):
    aid = str(uuid.uuid4())
    conn = sqlite3.connect(os.getenv("STORY_DB", "storyworld.db"))
    c = conn.cursor()
    c.execute(
        "INSERT INTO agents (id,name,endpoint,world_id,type) VALUES (?,?,?,?,?)",
        (aid, req.name, req.endpoint, world_id, req.type)
    )
    conn.commit()
    conn.close()
    world = load_world(world_id)
    if world:
        world["agents"].append({
            "id": aid,
            "type": req.type,
            "role": req.name,
            "state": {}
        })
        world["agents_map"][aid] = {}
        persist_world(world_id, world)
    return {
        "id": aid,
        "name": req.name,
        "endpoint": req.endpoint,
        "type": req.type
    }

@app.post("/worlds/{world_id}/tick")
async def tick(world_id: str):
    try:
        res = await tick_world(world_id)
        return res
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/worlds/{world_id}")
def get_world(world_id: str):
    world = load_world(world_id)
    if not world:
        raise HTTPException(status_code=404, detail="world not found")
    return world

@app.get("/worlds/{world_id}/events")
def get_events(world_id: str):
    conn = sqlite3.connect(os.getenv("STORY_DB", "storyworld.db"))
    c = conn.cursor()
    c.execute(
        "SELECT event_json, created_at FROM events WHERE world_id=? ORDER BY created_at",
        (world_id,)
    )
    rows = c.fetchall()
    conn.close()
    return [{"event": json.loads(r[0]), "created_at": r[1]} for r in rows]

@app.get("/")
def root():
    return {"status": "ok"}
