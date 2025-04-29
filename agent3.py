import os
from prompt import system_prompt, crs_calculation_prompt, additional_notes_prompt, determine_job_roles_prompt
from typing import List, Dict, Any
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_core.prompts import ChatPromptTemplate
from langchain_community.document_loaders import PyPDFLoader  # Change to PDF loader
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import RunnablePassthrough
from langgraph.graph import StateGraph, END
from langchain_community.vectorstores import FAISS
from langgraph.checkpoint.memory import MemorySaver
from langchain.text_splitter import CharacterTextSplitter
from dotenv import load_dotenv
import re
from pydantic import BaseModel
from typing import List, Optional

load_dotenv()

# Initialize LLMs and embeddings
llm_job_roles = ChatOpenAI(model="gpt-4o")
llm_crs_score = ChatOpenAI(model="gpt-4o",temperature=0.2)
llm_roadmap = ChatOpenAI(model="gpt-4o",temperature=0.6)
# Add a new LLM instance for filtering if needed, or reuse one
llm_filter = ChatOpenAI(model="gpt-4o", temperature=0)

embeddings = OpenAIEmbeddings()

from typing_extensions import TypedDict
# Update GraphState to include feedback
class GraphState(TypedDict):
    questionnaire: str
    job_roles: str
    noc_codes: str
    crs_score: str
    additional_notes: str  # <-- Add this line
    roadmap: str

# Load NOC codes from PDF (adjust file path accordingly)
file_path = "nocs (4).pdf"  # Path to your PDF file
loader = PyPDFLoader(file_path)  # Use PyPDFLoader instead of CSVLoader
documents = loader.load()

# Create a text splitter (adjust chunk size as needed)
text_splitter = CharacterTextSplitter(chunk_size=1000, chunk_overlap=0)
texts = text_splitter.split_documents(documents)

# Create the FAISS index
noc_db = FAISS.from_documents(texts, embeddings)

# Define node functions (same as before)
def determine_job_roles(state):
    questionnaire = state["questionnaire"]
   

    prompt = ChatPromptTemplate.from_template(
            determine_job_roles_prompt
            )

    chain = prompt | llm_job_roles | StrOutputParser()
    job_roles = chain.invoke({"questionnaire": questionnaire})
    state["job_roles"] = job_roles
    return state

def retrieve_noc_codes(state):
    import re

    job_roles_text = state["job_roles"]
    roles = []

    # Step 1: Parse job roles properly
    for line in job_roles_text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("-") and ":" in line:
            role = line.split(":")[0].replace("-", "").strip()
            if role:
                roles.append(role)

    print(f"DEBUG: Extracted Roles -> {roles}")

    # Step 2: Search the NOC database for each extracted role
    noc_infos = []
    for role in roles:
        try:
            relevant_docs = noc_db.similarity_search(role, k=1)  # top 1 match
            if not relevant_docs:
                print(f"DEBUG: No relevant NOC found for role: {role}")
                continue

            for doc in relevant_docs:
                text = doc.page_content.replace('\n', ' ').strip()
                text = re.sub(' +', ' ', text)  # remove extra spaces
                noc_infos.append(f"**{role}:** {text}\n")

        except Exception as e:
            print(f"ERROR: Issue during FAISS search for role '{role}': {e}")
            continue

    # Step 3: Merge all retrieved NOC info
    if not noc_infos:
        print("DEBUG: No NOC info collected, returning empty.")
        combined_noc_info = "No relevant NOC codes found for the recommended job roles."
    else:
        combined_noc_info = "\n".join(noc_infos)

    state["noc_codes"] = combined_noc_info
    return state

# Add a new LLM instance for filtering if needed, or reuse one
llm_filter = ChatOpenAI(model="gpt-4o", temperature=0)
# You might need to import re if not already done globally
import re
import ast # For parsing LLM string output - use JSON/Pydantic parser for production


def calculate_crs_score(state):
    from datetime import datetime
    current_date = datetime.now()
    
    questionnaire = state["questionnaire"]
    

    
    prompt = ChatPromptTemplate.from_template(crs_calculation_prompt)
    chain = prompt | llm_crs_score | StrOutputParser()
    crs_score = chain.invoke({"questionnaire": questionnaire})
    print("DEBUG: Received CRS score in generate_roadmap:", crs_score)
    state["crs_score"] = crs_score
    return state

def generate_roadmap(state):
    questionnaire = state["questionnaire"]
    noc_codes_text = state["noc_codes"]
    crs_score = state.get("crs_score", "")
    additional_notes = state.get("additional_notes", "")

    roadmap_prompt = system_prompt
    prompt = ChatPromptTemplate.from_template(roadmap_prompt)
    chain = prompt | llm_roadmap | StrOutputParser()
    roadmap = chain.invoke({
        "questionnaire": questionnaire,
        "noc_codes": noc_codes_text,
        "crs_score": crs_score,
        "additional_notes": additional_notes  # <-- Pass this in!
    })

    state["roadmap"] = roadmap
    print("DEBUG: Generated final roadmap:", roadmap)
    return state

def generate_additional_notes(state):
    questionnaire = state["questionnaire"]
    noc_codes = state["noc_codes"]
    crs_score = state["crs_score"]

    prompt = ChatPromptTemplate.from_template(
        additional_notes_prompt
    )
    chain = prompt | llm_roadmap | StrOutputParser()
    additional_notes = chain.invoke({
        "questionnaire": questionnaire,
        "noc_codes": noc_codes,
        "crs_score": crs_score
    })
    state["additional_notes"] = additional_notes
    return state

# Define the graph
workflow = StateGraph(GraphState)
workflow.add_node("determine_job_roles", determine_job_roles)
workflow.add_node("retrieve_noc_codes", retrieve_noc_codes)
workflow.add_node("calculate_crs_score", calculate_crs_score)
workflow.add_node("generate_additional_notes", generate_additional_notes)  # <-- Add this
workflow.add_node("generate_roadmap", generate_roadmap)

workflow.set_entry_point("determine_job_roles")
workflow.add_edge("determine_job_roles", "retrieve_noc_codes")
workflow.add_edge("retrieve_noc_codes", "calculate_crs_score")
workflow.add_edge("calculate_crs_score", "generate_additional_notes")  # <-- Add this
workflow.add_edge("generate_additional_notes", "generate_roadmap")     # <-- Add this
workflow.add_edge("generate_roadmap", END)

# Compile the graph with interrupt
graph_app = workflow.compile( )
