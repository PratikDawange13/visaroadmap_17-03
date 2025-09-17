from datetime import datetime, date, timedelta
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_google_genai import ChatGoogleGenerativeAI

# Initialize LLM for CRS calculations
llm_crs_score = ChatGoogleGenerativeAI(model="gemini-2.0-flash-exp", temperature=0.4)

def calculate_age(birth_date_str):
    """
    Calculate age from birth date string (expected format: YYYY-MM-DD)
    """
    try:
        birth_date = datetime.strptime(birth_date_str, '%Y-%m-%d').date()
        today = date.today()
        age = today.year - birth_date.year - ((today.month, today.day) < (birth_date.month, birth_date.day))
        return age
    except ValueError:
        return None

def calculate_projected_age(birth_date_str, reference_date=None):
    """
    Calculate projected age considering if birthday falls within 3 months.
    If birthday is within next 3 months, use the upcoming age for CRS calculations.
    
    Args:
        birth_date_str: Birth date in YYYY-MM-DD format
        reference_date: Reference date for calculation (defaults to today)
    
    Returns:
        dict: {
            'current_age': int,
            'projected_age': int,
            'is_projected': bool,
            'birthday_within_3_months': bool,
            'next_birthday': date
        }
    """
    try:
        birth_date = datetime.strptime(birth_date_str, '%Y-%m-%d').date()
        ref_date = reference_date or date.today()
        
        # Calculate current age
        current_age = ref_date.year - birth_date.year - ((ref_date.month, ref_date.day) < (birth_date.month, birth_date.day))
        
        # Calculate next birthday (handle leap year edge case)
        try:
            next_birthday = birth_date.replace(year=ref_date.year)
        except ValueError:
            # Handle Feb 29 in non-leap years - use Feb 28 instead
            next_birthday = birth_date.replace(year=ref_date.year, day=28)
        
        if next_birthday < ref_date:
            try:
                next_birthday = next_birthday.replace(year=ref_date.year + 1)
            except ValueError:
                # Handle Feb 29 in non-leap years for next year
                next_birthday = birth_date.replace(year=ref_date.year + 1, day=28)
        
        # Check if birthday is within 3 months (90 days)
        days_to_birthday = (next_birthday - ref_date).days
        birthday_within_3_months = days_to_birthday <= 90
        
        # Calculate projected age
        projected_age = current_age + 1 if birthday_within_3_months else current_age
        
        return {
            'current_age': current_age,
            'projected_age': projected_age,
            'is_projected': birthday_within_3_months,
            'birthday_within_3_months': birthday_within_3_months,
            'next_birthday': next_birthday,
            'days_to_birthday': days_to_birthday
        }
    except ValueError:
        return None

def calculate_exact_crs_score(questionnaire_data):
    today = date.today()
    current_date = datetime.now()
    
    # If birth date is provided, calculate projected age
    birth_date = questionnaire_data.get('birth_date')
    age_info = None
    if birth_date:
        age_info = calculate_projected_age(birth_date, today)
        if age_info:
            # Use projected age for CRS calculations
            questionnaire_data['age'] = age_info['projected_age']
            questionnaire_data['current_age'] = age_info['current_age']
            questionnaire_data['is_age_projected'] = age_info['is_projected']
            questionnaire_data['birthday_within_3_months'] = age_info['birthday_within_3_months']
            questionnaire_data['days_to_birthday'] = age_info['days_to_birthday']
    
    # Add timestamp to the calculation
    questionnaire_data['calculation_date'] = current_date.strftime('%Y-%m-%d %H:%M:%S')
    
    # Rest of the CRS calculation logic...
    # [Previous implementation remains the same]

def calculate_crs_score(state):
    questionnaire = state["questionnaire"]
    current_date = datetime.now()
    
    # Update the extract data prompt to include birth date and projected age logic
    extract_data_prompt = ChatPromptTemplate.from_template("""
    Current Date: {current_date}
    
    Extract the following information from the questionnaire in a structured format:
    - Birth date (YYYY-MM-DD format)
    - Education level
    - Language test scores (CLB levels)
    - Years of work experience
    - Canadian work experience
    - Education in Canada (yes/no)
    - Arranged employment (yes/no)
    - Provincial nomination (yes/no)
    
    IMPORTANT: For age calculation, if the person's birthday falls within the next 3 months from {current_date}, 
    we will use their upcoming age (current age + 1) for CRS scoring purposes. This projected age approach 
    helps optimize CRS scores for immigration applications.
    
    Also calculate:
    - Current age as of {current_date}
    - Projected age (add 1 year if birthday is within 3 months)
    - Work experience duration up to {current_date}
    - Language test validity (tests should be less than 2 years old)
    
    Questionnaire: {questionnaire}
    """)
    
    chain = extract_data_prompt | llm_crs_score | StrOutputParser()
    structured_data = chain.invoke({
        "questionnaire": questionnaire,
        "current_date": current_date.strftime('%Y-%m-%d')
    })
    
    parsed_data = parse_llm_response(structured_data)
    exact_score = calculate_exact_crs_score(parsed_data)
    
    # Add calculation metadata including age projection info
    state["crs_score"] = str(exact_score)
    state["score_calculation_date"] = current_date.strftime('%Y-%m-%d %H:%M:%S')
    
    # Add age projection metadata to state for roadmap generation
    if parsed_data.get('is_age_projected'):
        state["age_projection_used"] = True
        state["current_age"] = parsed_data.get('current_age')
        state["projected_age"] = parsed_data.get('age')
        state["days_to_birthday"] = parsed_data.get('days_to_birthday')
    else:
        state["age_projection_used"] = False
    
    return state

def parse_llm_response(structured_data: str) -> dict:
    """
    Parse the LLM's response and include date-based validations
    """
    current_date = datetime.now()
    parsed_data = {
        'calculation_timestamp': current_date.strftime('%Y-%m-%d %H:%M:%S'),
        'age': 0,
        'birth_date': None,
        'education_level': 'less_than_secondary',
        'first_language_scores': {'reading': 0, 'writing': 0, 'speaking': 0, 'listening': 0},
        'second_language_scores': {'reading': 0, 'writing': 0, 'speaking': 0, 'listening': 0},
        'canadian_work_experience': 0,
        'foreign_work_experience': 0,
        'canadian_education': '',
        'provincial_nomination': False,
        'arranged_employment': '',
        'sibling_in_canada': False,
        'language_test_dates': {
            'first_language': None,
            'second_language': None
        },
        'spouse_factors': {
            'education_level': 'less_than_secondary',
            'language_scores': {'reading': 0, 'writing': 0, 'speaking': 0, 'listening': 0},
            'canadian_work_experience': 0
        }
    }
    
    try:
        # Add date validation for language tests
        def is_test_valid(test_date_str):
            if not test_date_str:
                return False
            test_date = datetime.strptime(test_date_str, '%Y-%m-%d').date()
            today = date.today()
            test_age = (today - test_date).days / 365
            return test_age <= 2  # Tests must be less than 2 years old
        
        # Parse the structured data with date validations
        # [Your existing parsing logic here]
        
        # Validate work experience dates
        def calculate_work_experience(start_date_str, end_date_str=None):
            if not start_date_str:
                return 0
            start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
            end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date() if end_date_str else date.today()
            experience_years = (end_date - start_date).days / 365
            return min(max(0, round(experience_years)), 5)  # Cap at 5 years
            
        # Additional date-based validations can be added here
        
    except Exception as e:
        print(f"Error parsing questionnaire data: {e}")
        
    return parsed_data
