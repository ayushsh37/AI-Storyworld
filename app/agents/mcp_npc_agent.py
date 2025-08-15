# backend/app/agents/mcp_npc_agent.py
import os
import openai
import datetime
import uuid
import json
from fastapi import FastAPI, Request
from pathlib import Path

openai.api_key = os.getenv("OPENAI_API_KEY")

MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")  # change as available

app = FastAPI(title="MCP NPC Agent")

PROMPT_TEMPLATE_PATH = Path(__file__).parent.parent / "prompts" / "mcp_npc_prompt.txt"
PROMPT_TEMPLATE = PROMPT_TEMPLATE_PATH.read_text()

SYSTEM_GUARD = (
    "You are an NPC in a fictional simulation. Avoid any harmful, sexual, illegal, or disallowed content. "
    "You must output strictly valid JSON exactly matching the requested schema. If uncertain, output a 'noop' event."
)

@app.post("/act_mcp")
async def act_mcp(req: Request):
    mcp = await req.json()
    # minimal compatibility check
    if mcp.get("mcp_version") != "1.0":
        return {"error": "incompatible_mcp_version"}
    # determine actor
    actor_id = mcp.get("query", {}).get("actor_id")
    if not actor_id and mcp.get("agents"):
        actor_id = mcp["agents"][0].get("id")
    actor = None
    for a in mcp.get("agents", []):
        if a.get("id") == actor_id:
            actor = a
            break
    if not actor:
        # safe fallback
        fallback = {
            "mcp_version": "1.0",
            "timestamp": datetime.datetime.utcnow().isoformat(),
            "agent_id": actor_id or "unknown",
            "agent_type": "NPC",
            "append_events": [
                {"id": str(uuid.uuid4()), "actor_id": actor_id or "unknown", "action": {"type": "noop"}, "desc": "no_actor_found"}
            ],
            "state_updates": {}
        }
        return fallback

    prompt = PROMPT_TEMPLATE.format(
        npc_name=actor.get("role", "NPC"),
        actor_id=actor.get("id"),
        world_id=mcp.get("world", {}).get("id"),
        world_time=mcp.get("world", {}).get("time"),
        short_term_context=mcp.get("short_term_context", ""),
        global_facts=", ".join(mcp.get("world", {}).get("global_facts", []))
    )

    try:
        resp = openai.ChatCompletion.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_GUARD},
                {"role": "user", "content": prompt}
            ],
            temperature=0.6,
            max_tokens=400
        )
        text = resp.choices[0].message.get("content", "").strip()
    except Exception as e:
        # LLM error -> fallback
        fallback = {
            "mcp_version": "1.0",
            "timestamp": datetime.datetime.utcnow().isoformat(),
            "agent_id": actor.get("id"),
            "agent_type": "NPC",
            "append_events": [
                {"id": str(uuid.uuid4()), "actor_id": actor.get("id"), "action": {"type": "noop"}, "desc": f"llm_error:{str(e)[:120]}"}
            ],
            "state_updates": {}
        }
        return fallback

    # Try to parse JSON
    try:
        out = json.loads(text)
    except Exception:
        # If parse fails, wrap the text as an utterance in a safe event
        fallback = {
            "mcp_version": "1.0",
            "timestamp": datetime.datetime.utcnow().isoformat(),
            "agent_id": actor.get("id"),
            "agent_type": "NPC",
            "append_events": [
                {"id": str(uuid.uuid4()), "actor_id": actor.get("id"), "action": {"type": "utterance", "payload": text[:400]}, "desc": "fallback_parsed_text"}
            ],
            "state_updates": {}
        }
        return fallback

    # Normalise and ensure required fields exist
    out.setdefault("mcp_version", "1.0")
    out.setdefault("timestamp", datetime.datetime.utcnow().isoformat())
    out.setdefault("agent_id", actor.get("id"))
    out.setdefault("agent_type", "NPC")
    out.setdefault("append_events", [])
    out.setdefault("state_updates", {})

    return out

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8002")))
