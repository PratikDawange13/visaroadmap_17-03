from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Dict, Any, Optional
import uuid
import asyncio
from datetime import datetime
import logging
import json
from pprint import pprint

# Import your graph module
from graph import graph, GraphState

app = FastAPI()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # For production, restrict to your frontend domain
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Store active sessions
active_sessions = {}

class QuestionnaireInput(BaseModel):
    questionnaire: str

class FeedbackInput(BaseModel):
    session_id: str
    feedback: str

@app.post("/start_application")
async def start_application(input_data: QuestionnaireInput, background_tasks: BackgroundTasks):
    """Start a new immigration application process"""
    session_id = str(uuid.uuid4())
    
    # Initialize state
    initial_state = {
        "questionnaire": input_data.questionnaire,
        "job_roles": "",
        "noc_codes": "",
        "crs_score": "",
        "roadmap": "",
        "feedback": None
    }
    
    # Create thread_id for this session
    thread_id = str(uuid.uuid4())
    
    active_sessions[session_id] = {
        "thread_id": thread_id,
        "status": "processing",
        "state": initial_state,
        "needs_feedback": False
    }
    
    # Run the graph in the background
    background_tasks.add_task(
        process_application, 
        session_id=session_id, 
        thread_id=thread_id, 
        initial_state=initial_state
    )
    
    return {
        "session_id": session_id, 
        "status": "processing",
        "needs_feedback": False
    }

async def process_application(session_id: str, thread_id: str, initial_state: GraphState):
    try:
        config = {"configurable": {"thread_id": thread_id}}
        last_valid_state = initial_state
        for state in graph.stream(initial_state, config):
            # If this is an interrupt, don't overwrite last_valid_state
            if isinstance(state, dict) and "__interrupt__" in state:
                active_sessions[session_id]["state"] = state
                break
            else:
                active_sessions[session_id]["state"] = state
                last_valid_state = state

        # Save the last valid state separately
        active_sessions[session_id]["last_valid_state"] = last_valid_state

        next_node = graph.get_state(config).next
        if next_node and "human_feedback" in str(next_node):
            active_sessions[session_id]["needs_feedback"] = True
            active_sessions[session_id]["status"] = "waiting_for_feedback"
        else:
            active_sessions[session_id]["needs_feedback"] = False
            active_sessions[session_id]["status"] = "complete"
    except Exception as e:
        print(f"Error processing application for session {session_id}: {e}")
        active_sessions[session_id]["status"] = "error"

@app.get("/session_status/{session_id}")
async def get_session_status(session_id: str):
    if session_id not in active_sessions:
        raise HTTPException(status_code=404, detail="Session not found")
    
    session = active_sessions[session_id]
    state = session.get("last_valid_state", session["state"])
    print(f"DEBUG: Current state for session {session_id} -> {state}")

    # Unwrap the state if it's nested under the node name
    if isinstance(state, dict) and len(state) == 1:
        inner_state = next(iter(state.values()))
        print(f"DEBUG: Unwrapped state -> {inner_state}")
    else:
        inner_state = state
        print(f"DEBUG: State is already unwrapped -> {inner_state}")

    if isinstance(inner_state, tuple):
        inner_state = inner_state[0]

    if isinstance(inner_state, dict):
        noc_codes = inner_state.get("noc_codes", "")
        if isinstance(noc_codes, list):
            noc_codes_out = [dict(n) if not isinstance(n, dict) else n for n in noc_codes]
        else:
            noc_codes_out = noc_codes

        return {
            "session_id": session_id,
            "status": session["status"],
            "needs_feedback": session["needs_feedback"],
            "current_state": {
                "crs_score": inner_state.get("crs_score", ""),
                "job_roles": inner_state.get("job_roles", ""),
                "noc_codes": noc_codes_out,
                "roadmap": inner_state.get("roadmap", "")
            }
        }
    else:
        return {
            "session_id": session_id,
            "status": session["status"],
            "needs_feedback": session["needs_feedback"],
            "current_state": {}
        }

@app.post("/submit_feedback")
async def submit_feedback(feedback_input: FeedbackInput, background_tasks: BackgroundTasks):
    session_id = feedback_input.session_id

    if session_id not in active_sessions:
        raise HTTPException(status_code=404, detail="Session not found")
    session = active_sessions[session_id]

    if not session["needs_feedback"]:
        raise HTTPException(status_code=400, detail="Session not waiting for feedback")

    # Inject feedback into last_valid_state, not interrupt state
    if "last_valid_state" in session:
        last_valid = session["last_valid_state"]
        if isinstance(last_valid, dict) and len(last_valid) == 1:
            node_name = next(iter(last_valid.keys()))
            last_valid_inner = last_valid[node_name]
            if isinstance(last_valid_inner, dict):
                last_valid_inner["feedback"] = feedback_input.feedback
                last_valid[node_name] = last_valid_inner
            else:
                last_valid[node_name] = {"feedback": feedback_input.feedback}
            session["last_valid_state"] = last_valid
        elif isinstance(last_valid, dict):
            last_valid["feedback"] = feedback_input.feedback
            session["last_valid_state"] = last_valid
    else:
        session["state"]["feedback"] = feedback_input.feedback
    session["needs_feedback"] = False
    session["status"] = "processing"

    background_tasks.add_task(
        continue_processing,
        session_id=session_id,
        thread_id=session["thread_id"]
    )

    return {"status": "feedback_received"}

async def continue_processing(session_id: str, thread_id: str):
    try:
        session = active_sessions[session_id]
        config = {"configurable": {"thread_id": thread_id}}
        # Resume from checkpoint, not from explicit state
        for event in graph.stream(None, config, stream_mode="values"):
            session["state"] = event
        session["status"] = "completed"
        logger.info(f"Session {session_id} completed after feedback")
    except Exception as e:
        logger.error(f"Error continuing process for session {session_id}: {str(e)}")
        session["status"] = "error"
        session["error"] = str(e)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)