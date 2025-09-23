from fastapi import FastAPI, HTTPException, UploadFile, File, Query
from pydantic import BaseModel, field_validator, Field
import os
from agent4forCRS import graph_app
from prompt import travel_visa, study_visa, work_visa
from dotenv import load_dotenv
from fastapi.middleware.cors import CORSMiddleware
from typing import Optional, List, Literal, Dict, Any
import io
import re
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain.output_parsers import PydanticOutputParser
from langchain.prompts import PromptTemplate

load_dotenv()

# ---------- Data models ----------
class CustomerData(BaseModel):
    name: Optional[str] = None
    date_of_birth: Optional[str] = None
    education_level: Optional[str] = None
    service_type: Optional[str] = None
    marital_status: Optional[str] = None
    other_fields: Dict[str, Any] = Field(default_factory=dict)

class ProcessDocumentResponse(BaseModel):
    extracted_data: CustomerData
    missing_fields: List[str]
    warnings: List[str] = []
    raw_text: Optional[str] = None

class RoadmapRequest(BaseModel):
    method: Literal["manual", "document"]
    roadmap_type: str
    questionnaire: Optional[str] = None
    customer_data: Optional[CustomerData] = None

app = FastAPI()

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, replace with specific origins
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------- Helper functions ----------
def extract_pdf_text(pdf_bytes: bytes) -> str:
    """Extract text from PDF using a simple library."""
    try:
        import pypdf
        reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
        texts = []
        for page in reader.pages:
            texts.append(page.extract_text() or "")
        text = "\n".join(texts).strip()
        if not text:
            raise Exception("No text extracted")
        return text
    except Exception:
        raise HTTPException(
            status_code=400, 
            detail="Unable to extract text from PDF. The file might be scanned or corrupted."
        )

class LLMCustomerData(BaseModel):
    name: Optional[str] = None
    date_of_birth: Optional[str] = None
    education_level: Optional[str] = None
    service_type: Optional[str] = None
    marital_status: Optional[str] = None
    other_fields: Dict[str, Any] = Field(default_factory=dict)

def llm_extract_customer_data(text: str) -> CustomerData:
    """Use LLM to extract all customer data from text."""
    parser = PydanticOutputParser(pydantic_object=LLMCustomerData)
    prompt = PromptTemplate(
        template=(
            "Extract customer information from this visa questionnaire text. "
            "Find the full name, date of birth (format as 'Xth Month YYYY' like '4th April 1998'), education level, service/visa type, marital status, "
            "and any other relevant information. Put additional fields in other_fields.\n\n"
            "{format_instructions}\n\n"
            "Text:\n{input_text}"
        ),
        input_variables=["input_text"],
        partial_variables={"format_instructions": parser.get_format_instructions()},
    )
    
    llm = ChatGoogleGenerativeAI(model="gemini-2.0-flash-exp", temperature=0.4)
    chain = prompt | llm | parser
    
    try:
        result = chain.invoke({"input_text": text})
        return CustomerData(
            name=result.name,
            date_of_birth=result.date_of_birth,
            education_level=result.education_level,
            service_type=result.service_type,
            marital_status=result.marital_status,
            other_fields=result.other_fields or {},
        )
    except Exception as e:
        raise HTTPException(
            status_code=500, 
            detail=f"Failed to extract data using LLM: {str(e)}"
        )

def validate_completeness(cd: CustomerData) -> List[str]:
    """Check which mandatory fields are missing."""
    missing = []
    if not cd.name:
        missing.append("name")
    if cd.date_of_birth is None:
        missing.append("date_of_birth")
    if not cd.education_level:
        missing.append("education_level")
    if not cd.service_type:
        missing.append("service_type")
    if not cd.marital_status:
        missing.append("marital_status")
    return missing

def build_questionnaire_from_customer_data(cd: CustomerData) -> str:
    """Convert structured data back to questionnaire format."""
    parts = [
        f"Name: {cd.name}",
        f"Date of Birth: {cd.date_of_birth}",
        f"Education Level: {cd.education_level}",
        f"Service Type: {cd.service_type}",
        f"Marital Status: {cd.marital_status}",
    ]
    for k, v in (cd.other_fields or {}).items():
        title_k = " ".join([w.capitalize() for w in k.split()])
        parts.append(f"{title_k}: {v}")
    return "\n".join(parts)

def get_roadmap_from_type(prompt):
    """Generate roadmap using Gemini."""
    # Ensure Gemini API key is present
    if not os.getenv("GOOGLE_API_KEY"):
        raise HTTPException(status_code=500, detail="GOOGLE_API_KEY is not set. Please configure it to generate roadmaps (Gemini).")
    try:
        llm = ChatGoogleGenerativeAI(model="gemini-1.5-flash", temperature=0.6)
        resp = llm.invoke(prompt)
        return resp.content if hasattr(resp, "content") else str(resp)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Gemini generation failed: {str(e)}")

# ---------- Endpoints ----------
@app.post("/process_document", response_model=ProcessDocumentResponse)
async def process_document(file: UploadFile = File(...)):
    """Process PDF and extract customer data using LLM."""
    # Validate file type
    if file.content_type not in ("application/pdf", "application/octet-stream"):
        raise HTTPException(
            status_code=400, 
            detail="Unsupported file type. Please upload a PDF document."
        )

    # Check if API key is configured
    if not os.getenv("GOOGLE_API_KEY"):
        raise HTTPException(
            status_code=500, 
            detail="GOOGLE_API_KEY is not set. Please configure it to process documents."
        )
    
    try:
        pdf_bytes = await file.read()
        if not pdf_bytes:
            raise HTTPException(status_code=400, detail="Empty file uploaded.")
        
        # Extract text from PDF
        text = extract_pdf_text(pdf_bytes)
        
        # Use LLM to extract customer data
        customer_data = llm_extract_customer_data(text)
        
        # Check for missing fields
        missing_fields = validate_completeness(customer_data)
        
        warnings = []
        if missing_fields:
            warnings.append("Some mandatory fields are missing. Please complete them manually before generating the roadmap.")
        
        return ProcessDocumentResponse(
            extracted_data=customer_data,
            missing_fields=missing_fields,
            warnings=warnings,
            raw_text=text,
        )
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to process document: {str(e)}")

@app.post("/generate_roadmap")
async def generate_visa_roadmap(request: RoadmapRequest):
    """Generate visa roadmap based on customer data."""
    try:
        # Build questionnaire text based on method
        if request.method == "manual":
            if not request.questionnaire:
                raise HTTPException(
                    status_code=400, 
                    detail="Missing questionnaire text for manual method."
                )
            questionnaire_text = request.questionnaire
            
        elif request.method == "document":
            if not request.customer_data:
                raise HTTPException(
                    status_code=400, 
                    detail="Missing customer_data for document method."
                )
            
            # Validate completeness
            missing = validate_completeness(request.customer_data)
            if missing:
                raise HTTPException(
                    status_code=400,
                    detail={
                        "message": "Mandatory fields are missing. Please complete them before proceeding.",
                        "missing_fields": missing,
                    },
                )
            
            questionnaire_text = build_questionnaire_from_customer_data(request.customer_data)
        else:
            raise HTTPException(status_code=400, detail="Invalid method. Use 'manual' or 'document'.")

        # Generate roadmap based on type
        roadmap_type = request.roadmap_type.lower()
        
        if roadmap_type == "immigration visa":
            result = graph_app.invoke({"questionnaire": questionnaire_text})
            return result
        elif roadmap_type == "study visa":
            prompt_text = study_visa(questionnaire_text)
            return get_roadmap_from_type(prompt_text)
        elif roadmap_type == "travel visa":
            prompt_text = travel_visa(questionnaire_text)
            return get_roadmap_from_type(prompt_text)
        elif roadmap_type == "work visa":
            prompt_text = work_visa(questionnaire_text)
            return get_roadmap_from_type(prompt_text)
        else:
            raise HTTPException(
                status_code=400, 
                detail="Invalid roadmap_type. Expected: Immigration Visa, Study Visa, Travel Visa, or Work Visa."
            )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)