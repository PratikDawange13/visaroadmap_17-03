import os
from prompt import system_prompt, crs_calculation_prompt, filtering_prompt_template
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
    roadmap: str
    feedback: Optional[str]  # New field for human feedback

# Add no-op function for human feedback
def human_feedback(state: GraphState):
    """No-op function that will be interrupted for human feedback"""
    return state

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
    job_roles_list = """Dentist, Dietitian, Nutritionist, Family Physician, General Practitioner, Medical Doctor, Resident Doctor, Medical Laboratory Assistant, Medical Laboratory Technologist, Medical Laboratory Scientist, Nurse Assistant, Nurse Aide, Optometrist, Pharmacist, Pharmacy Assistant, Registered Nurse, Nurse, Veterinarian, Social Service Worker, Butcher, Retail Butcher, Architectural Manager, Architectural Service Manager, Landscape Architecture Manager, Scientific Research Manager, Civil Engineer, Construction Engineer, Consulting Civil Engineer, Cybersecurity Analyst, Cybersecurity Specialist, Network Security Analyst, Systems Security Analyst, Electrical Engineer, Electronics Engineer, Mechanical Engineer, Project Mechanical Engineer, Classroom Assistant, Teacher's Assistant, Early Childhood Assistant, Primary School Teacher, Elementary School Teacher, Secondary School Teacher, Subject Teacher, Construction Project Manager, Construction Site Manager, Cook, Quantity Surveyor, Bricklayer, Furniture Cabinetmaker, Cabinetmaker, Gas Servicer, Gas Technician, Plumber, Industrial Electrician, Electrician, Floor Tiler, Rug Layer, Wood Floor Installer, Painter, Decorator, Building Painter."""

    prompt = ChatPromptTemplate.from_template(
            f"""You are an immigration consultant helping a client.
            Based ONLY on the following client questionnaire, determine the MOST SUITABLE job roles for immigration purposes.

            **IMPORTANT RULES:**
            - You MUST ONLY pick job roles from this approved list: {job_roles_list}
            - The selected job roles should match the client's education, work experience, or transferable skills.
            - If the client’s profile fits multiple roles, recommend multiple.
            - If no direct match is found, you MUST select the CLOSEST possible job roles based on skills transferability and reasonable career transition.
            - Always prioritize recommending roles that would realistically suit the client's professional background and abilities.

            **VERY IMPORTANT STRUCTURE RULES:**
            - STRICTLY list the roles as:
                - Role Name: Reason
            - Each role must start with a dash (`-`).
            - No numbering like 1., 2., 3.
            - No bold text (**).
            - No extra line breaks between roles.

            **Example Output:**
            - Subject Teacher: Based on the client's legal background, they could teach social studies.
            - Secondary School Teacher: The client's communication skills are transferable to teaching roles.
            - Social Service Worker: The client's law background fits advocacy and support roles.

            Client Questionnaire:
            {{questionnaire}}
            """
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

def feedback_condition(state: GraphState) -> str:
    """
    Determines the next node based on human feedback
    Returns:
    - "generate_roadmap" if feedback is "yes" or if no feedback is provided
    - "calculate_crs_score" if feedback contains specific changes/corrections
    """
    feedback = state.get("feedback", "").strip().lower()
    # Move to roadmap if feedback is "yes" or empty/None
    if not feedback or feedback == "yes":
        return "generate_roadmap"
    return "calculate_crs_score"


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
    noc_codes_text = state["noc_codes"]  # now a single clean text block
    crs_score = state.get("crs_score", "")
    
    roadmap_prompt = system_prompt
    prompt = ChatPromptTemplate.from_template(roadmap_prompt)
    chain = prompt | llm_roadmap | StrOutputParser()
    roadmap = chain.invoke({
        "questionnaire": questionnaire,
        "noc_codes": noc_codes_text,
        "crs_score": crs_score
    })

    state["roadmap"] = roadmap
    print("DEBUG: Generated final roadmap:", roadmap)
    return state

# Define the graph
workflow = StateGraph(GraphState)

# Add nodes (removed filter_feasible_nocs)
workflow.add_node("determine_job_roles", determine_job_roles)
workflow.add_node("retrieve_noc_codes", retrieve_noc_codes)
workflow.add_node("calculate_crs_score", calculate_crs_score)
workflow.add_node("human_feedback", human_feedback)
workflow.add_node("generate_roadmap", generate_roadmap)

# Define edges (updated to skip filter_feasible_nocs)
workflow.set_entry_point("determine_job_roles")
workflow.add_edge("determine_job_roles", "retrieve_noc_codes")
workflow.add_edge("retrieve_noc_codes", "calculate_crs_score")
workflow.add_edge("calculate_crs_score", "human_feedback")

# Add conditional edges from human_feedback
workflow.add_conditional_edges(
    "human_feedback",
    feedback_condition,
    {
        "generate_roadmap": "generate_roadmap",
        "calculate_crs_score": "calculate_crs_score"
    }
)
workflow.add_edge("generate_roadmap", END)

# Compile the graph with interrupt
graph = workflow.compile(interrupt_before=["human_feedback"],  checkpointer=MemorySaver())
