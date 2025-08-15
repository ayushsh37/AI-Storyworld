# backend/app/orchestrator.py
import os
import json
import sqlite3
import uuid
import datetime
import asyncio
from pathlib import Path
import httpx
from jsonschema import validate, ValidationError

DB_FILE = os.getenv("STORY_DB", "storyworld.db")
MCP_SCHEMA = json.load(open(Path(__file__).parent / "mcp" / "mcp_v1.json"))
AGENT_ACTION_SCHEMA = json.load(open(Path(__file__).parent / "mcp" / "agent_action_schema.json"))

def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS worlds (id TEXT PRIMARY KEY, state TEXT, created_at TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS agents (id TEXT PRIMARY KEY, name TEXT, endpoint TEXT, world_id TEXT, type TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS events (id TEXT PRIMARY KEY, world_id TEXT, actor_id TEXT, event_json TEXT, created_at TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS snapshots (id TEXT PRIMARY KEY, world_id TEXT, snapshot_json TEXT, created_at TEXT)""")
    conn.commit(); conn.close()

init_db()

def json_str(obj):
    return json.dumps(obj)

def json_load(s):
    return json.loads(s)

def load_world(world_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT state FROM worlds WHERE id=?", (world_id,))
    r = c.fetchone()
    conn.close()
    if not r:
        return None
    return json_load(r[0])

def persist_world(world_id, state):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("UPDATE worlds SET state=? WHERE id=?", (json_str(state), world_id))
    conn.commit(); conn.close()

def append_event(world_id, actor_id, event_obj):
    eid = str(uuid.uuid4())
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("INSERT INTO events (id,world_id,actor_id,event_json,created_at) VALUES (?,?,?,?,?)",
              (eid, world_id, actor_id, json.dumps(event_obj), datetime.datetime.utcnow().isoformat()))
    conn.commit(); conn.close()
    return eid

def snapshot_world(world_id, state):
    sid = str(uuid.uuid4())
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("INSERT INTO snapshots (id,world_id,snapshot_json,created_at) VALUES (?,?,?,?)",
              (sid, world_id, json.dumps(state), datetime.datetime.utcnow().isoformat()))
    conn.commit(); conn.close()
    return sid

async def call_agent(endpoint, mcp_payload, timeout=12):
    async with httpx.AsyncClient() as client:
        try:
            r = await client.post(endpoint, json=mcp_payload, timeout=timeout)
            return r.status_code, r.json()
        except Exception as e:
            return 500, {"error": str(e)}

def validate_mcp_input(mcp):
    validate(instance=mcp, schema=MCP_SCHEMA)

def validate_agent_action(action):
    validate(instance=action, schema=AGENT_ACTION_SCHEMA)

AGENT_PRIORITY = {"World":100, "Planner":90, "Narrator":10, "NPC":50, "Player":80}

def merge_agent_responses(world_state, agent_responses):
    events_applied = []
    def priority_key(res):
        typ = res.get("agent_type", "NPC")
        return -AGENT_PRIORITY.get(typ, 50), res.get("timestamp", "")
    agent_responses_sorted = sorted(agent_responses, key=priority_key)
    for res in agent_responses_sorted:
        for ev in res.get("append_events", []):
            append_event(world_state["id"], ev.get("actor_id", "unknown"), ev)
            world_state.setdefault("event_log", []).append(ev)
            events_applied.append(ev.get("id"))
        state_updates = res.get("state_updates") or {}
        for key, value in state_updates.items():
            path = key.split(".")
            target = world_state
            for p in path[:-1]:
                if p not in target:
                    target[p] = {}
                target = target[p]
            target[path[-1]] = value
    return world_state, events_applied

def memory_fetch_stub(world, actor_id, k=3):
    bullets = [e.get("desc", "") for e in world.get("event_log", [])]
    return [{"id": str(i), "text": b, "score": 1.0 - (i * 0.01)} for i, b in enumerate(bullets[-k:])]

async def tick_world(world_id, intent="tick", actor_id=None):
    world = load_world(world_id)
    if not world:
        raise ValueError("world not found")
    now = datetime.datetime.utcnow().isoformat()
    mcp_input = {
        "mcp_version": "1.0",
        "timestamp": now,
        "world": {
            "id": world["id"],
            "time": world.get("time", 0),
            "locations": world.get("locations", []),
            "global_facts": world.get("global_facts", [])
        },
        "agents": world.get("agents", []),
        "event_log": world.get("event_log", []),
        "query": {"intent": intent, "actor_id": actor_id},
        "short_term_context": " ".join([e.get("desc", "") for e in world.get("event_log", [])[-10:]]),
        "memory_fetches": memory_fetch_stub(world, actor_id)
    }

    try:
        validate_mcp_input(mcp_input)
    except ValidationError as e:
        # log and continue best-effort
        append_event(world["id"], "orchestrator", {"id": str(uuid.uuid4()), "actor_id": "orchestrator", "action": {"type": "mcp_input_invalid", "payload": str(e)}, "desc": "mcp_input_validation_error"})
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT id,name,endpoint,type FROM agents WHERE world_id=?", (world_id,))
    rows = c.fetchall()
    conn.close()

    tasks = []
    for aid, name, endpoint, atype in rows:
        payload = dict(mcp_input)
        payload["query"]["actor_id"] = aid
        payload["agents"] = [{"id": aid, "type": atype, "role": name, "state": world.get("agents_map", {}).get(aid, {})}]
        tasks.append(call_agent(endpoint, payload))

    responses = await asyncio.gather(*tasks)
    agent_responses = []
    for status, body in responses:
        if status != 200:
            append_event(world_id, "orchestrator", {"id": str(uuid.uuid4()), "actor_id": "orchestrator", "action": {"type": "agent_call_failed", "payload": {"status": status, "body": body}}, "desc": f"agent_call_failed_{status}"})
            continue
        try:
            validate_agent_action(body)
            agent_responses.append(body)
        except ValidationError as e:
            append_event(world_id, "orchestrator", {"id": str(uuid.uuid4()), "actor_id": "orchestrator", "action": {"type": "invalid_agent_output", "payload": str(e)}, "desc": "agent_output_validation_failed"})
            continue

    new_state, events_applied = merge_agent_responses(world, agent_responses)
    new_state["time"] = new_state.get("time", 0) + 1
    persist_world(world_id, new_state)
    snapshot_world(world_id, new_state)
    return {"applied_event_ids": events_applied, "world": new_state}
