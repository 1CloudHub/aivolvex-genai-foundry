import json 
import os
import psycopg2
import boto3  
import time
import secrets
import string
import logging
from datetime import *
import uuid
import re   
import threading
import sys
import requests
import base64
import concurrent.futures
from opensearchpy import OpenSearch, RequestsHttpConnection
from requests_aws4auth import AWS4Auth
from botocore.config import Config
from time import sleep
from botocore.exceptions import ClientError, BotoCoreError
from typing import List, Dict, Any, Optional
from urllib.parse import urlparse

# gateway_url = os.environ['gateway_url']

# Get database credentials
db_user = os.environ['db_user']
db_host = os.environ['db_host']                         
db_port = os.environ['db_port']
db_database = os.environ['db_database']
region_used = os.environ["region_used"]
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Get new environment variables for voice operations
region_name = os.environ.get("region_name", region_used)  # Use region_used as fallback
voiceops_bucket_name = os.environ.get("voiceops_bucket_name", "voiceop-default")
ec2_instance_ip = os.environ.get("ec2_instance_ip", "")  # Elastic IP of the T3 medium instance

# Function to get database password from Secrets Manager
def get_db_password():
    try:
        # Use environment region instead of hardcoded region
        secretsmanager = boto3.client('secretsmanager', region_name=region_used)
        secret_response = secretsmanager.get_secret_value(SecretId=os.environ['rds_secret_name'])
        secret = json.loads(secret_response['SecretString'])
        return secret['password']
    except Exception as e:
        print(f"Error retrieving password from Secrets Manager: {e}")
        return None

# Get password from Secrets Manager
db_password = get_db_password()

schema = os.environ['schema']
chat_history_table = os.environ['chat_history_table']
prompt_metadata_table = os.environ['prompt_metadata_table']
model_id = os.environ['model_id']
KB_ID = os.environ['KB_ID']
OPENSEARCH_HOST =os.environ['OPENSEARCH_HOST']
OPENSEARCH_INDEX = os.environ['OPENSEARCH_INDEX']
CHAT_LOG_TABLE = os.environ['CHAT_LOG_TABLE']   
socket_endpoint = os.environ["socket_endpoint"]
RETAIL_KB_ID=os.environ["RETAIL_KB_ID"]
retail_chat_history_table=os.environ['chat_history_table']
S3_BUCKET = os.environ['S3_BUCKET']
# Use environment region instead of hardcoded regions
retrieve_client = boto3.client('bedrock-agent-runtime', region_name=region_used)
bedrock_client = boto3.client('bedrock-runtime', region_name=region_used)
api_gateway_client = boto3.client('apigatewaymanagementapi', endpoint_url=socket_endpoint)
bedrock = boto3.client('bedrock-runtime', region_name=region_used)

# Helper function to generate dynamic dates
def get_dynamic_date(days_ahead=2):
    """Generate a date that is 'days_ahead' days from current date"""
    current_date = datetime.now()
    future_date = current_date + timedelta(days=days_ahead)
    return future_date.strftime('%Y-%m-%d')

def get_dynamic_datetime(days_ahead=2):
    """Generate a datetime that is 'days_ahead' days from current date"""
    current_date = datetime.now()
    future_date = current_date + timedelta(days=days_ahead)
    return future_date.strftime('%Y-%m-%dT%H:%M:%S+00:00')
def generate_available_dates():
    # All available dates from September 19th to 25th
    all_dates = [
        "2025-09-19",  # September 19th
        "2025-09-20",  # September 20th
        "2025-09-21",  # September 21st
        "2025-09-22",  # September 22nd
        "2025-09-23",  # September 23rd
        "2025-09-24",  # September 24th
        "2025-09-25"   # September 25th
    ]
    # Randomly select 3 dates from the 7 available dates
    import random
    available_dates = random.sample(all_dates, 3)
    return available_dates


#flags
user_intent_flag = False
overall_flow_flag = False
pop = ""
ub_user_name = "none"
ub_number = "none"
str_intent = "false"

def describe_image(event):
    print("PRODUCT IMAGE ANALYSIS STARTED")

    base64_image = event.get("image_base64", "")
    if not base64_image:
        raise ValueError("Missing 'image_base64' in input event")

    # Decode and re-encode to ensure clean base64
    image_bytes = base64.b64decode(base64_image)
    encoded_image = base64.b64encode(image_bytes).decode("utf-8")

    # Prompt for Claude 3 Haiku to extract product metadata
    prompt = [
        {
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",  # Change to image/jpeg if needed
                        "data": encoded_image
                    }
                },
                {
                    "type": "text",
                    "text": """
You are a product listing assistant for an e-commerce platform.

From the image provided, identify and return the following information in a structured JSON format:

- title: A catchy, SEO-friendly title for the product
- description: A 100-150 word product description
- tags: Relevant tags or attributes (color, material, function, etc.)
- category: Broad category (e.g., clothing, footwear, bags)
- subcategory: More specific type (e.g., running shoes, tote bag)
- color: Main visible colors
- target_audience: Who this is likely meant for (men, women, kids, unisex)
- use_case: Likely usage scenarios (e.g., casual wear, office use, gym)


Return your response **only** in this JSON format (no explanations, markdown, or comments):

{
  "title": "...",
  "description": "...",
  "tags": ["...", "..."],
  "category": "...",
  "subcategory": "...",
  "color": ["..."],
  "target_audience": "...",
  "use_case": ["..."]
  
}
"""
                }
            ]
        }
    ]

    bedrock = boto3.client("bedrock-runtime", region_name="us-east-1")

    body = json.dumps({
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 1024,
        "messages": prompt
    })

    response = bedrock.invoke_model(
        modelId="us.anthropic.claude-3-7-sonnet-20250219-v1:0",
        body=body,
    )

    result = json.loads(response.get("body").read())
    output_text = result["content"][0]["text"]

    print("HAIKU RAW OUTPUT:", output_text)

    # Extract JSON from model response
    match = re.search(r'({.*})', output_text, re.DOTALL)
    if match:
        json_str = match.group(1)
    else:
        json_str = output_text  # fallback

    return json.loads(json_str)
# CRN extraction and validation functions


# CRN extraction and validation functions
def extract_crn(text):
    """Extract CRN from text using regex"""
    # Pattern for CRN like CUST1001, CUST1002, etc.
    crn_pattern = r'\b(CUST\d{4})\b'
    match = re.search(crn_pattern, text.upper())
    return match.group(1) if match else None

def validate_crn(crn):
    """Validate CRN format"""
    if not crn:
        return False
    crn_pattern = r'^CUST\d{4}$'
    return bool(re.match(crn_pattern, crn.upper()))

def validate_claim_amount(amount):
    """Validate claim amount format"""
    if not amount:
        return False
    # Accept formats like "6500SGD", "SGD 6500", "6500", etc.
    amount_pattern = r'^(SGD\s*)?(\d+(?:,\d{3})*(?:\.\d{2})?)(\s*SGD)?$'
    return bool(re.match(amount_pattern, amount, re.IGNORECASE))

def validate_date_format(date_str):
    """Validate and normalize date format"""
    if not date_str:
        return False, None
    
    # Try different date formats
    date_formats = [
        '%Y-%m-%d',  # 2025-07-19
        '%d/%m/%Y',  # 19/07/2025
        '%m/%d/%Y',  # 07/19/2025
        '%d-%m-%Y',  # 19-07-2025
        '%Y/%m/%d',  # 2025/07/19
        '%B %d, %Y',  # July 19, 2025
        '%d %B %Y',   # 19 July 2025
        '%b %d, %Y',  # Jul 19, 2025
        '%d %b %Y',   # 19 Jul 2025
    ]
    
    for fmt in date_formats:
        try:
            parsed_date = datetime.strptime(date_str.strip(), fmt)
            return True, parsed_date.strftime('%Y-%m-%d')
        except ValueError:
            continue
    
    return False, None

def validate_claim_type(claim_type):
    """Validate claim type"""
    valid_types = [
        'hospitalisation', 'hospitalization', 'accident', 'terminal illness', 
        'terminal', 'death', 'medical', 'outpatient', 'dental', 'vision',
        'disability', 'critical illness', 'surgery', 'emergency'
    ]
    return claim_type.lower() in valid_types

def validate_phone_number(phone):
    """
    Validate phone number format - must contain exactly 8 digits after stripping all non-digit characters
    """
    if not phone:
        return False, "Phone number is required"
    
    # Strip all non-digit characters (spaces, dashes, parentheses, plus signs, letters, etc.)
    import re
    digits_only = re.sub(r'[^\d]', '', str(phone))
    
    # Check if exactly 8 digits remain
    if len(digits_only) == 8:
        return True, digits_only
    else:
        return False, f"Invalid phone number. Please provide a phone number with exactly 8 digits. You provided {len(digits_only)} digits."

def parse_date_flexible(date_input):
    """
    Parse various date formats and return YYYY-MM-DD format
    Supports: "September 20, 2025", "September 20", "20th September", "20 September", etc.
    """
    if not date_input:
        return None
    
    import re
    
    # Clean the input - remove extra words and keep only date-related content
    date_input = date_input.strip().lower()
    
    # Month mapping
    month_map = {
        'january': '01', 'february': '02', 'march': '03', 'april': '04',
        'may': '05', 'june': '06', 'july': '07', 'august': '08',
        'september': '09', 'october': '10', 'november': '11', 'december': '12'
    }
    
    # Pattern 1: "September 20, 2025" or "September 20 2025" or "September 20" or "september 20 is cool"
    pattern1 = r'(\w+)\s+(\d+)(?:,\s*)?(\d{4})?'
    match = re.search(pattern1, date_input)
    if match:
        month_name = match.group(1)
        day = match.group(2)
        year = match.group(3) or '2025'
        
        if month_name in month_map:
            return f"{year}-{month_map[month_name]}-{day.zfill(2)}"
    
    # Pattern 2: "20th September" or "20 September" or "20th September 2025"
    pattern2 = r'(\d+)(?:st|nd|rd|th)?\s+(\w+)(?:\s+(\d{4}))?'
    match = re.search(pattern2, date_input)
    if match:
        day = match.group(1)
        month_name = match.group(2)
        year = match.group(3) or '2025'
        
        if month_name in month_map:
            return f"{year}-{month_map[month_name]}-{day.zfill(2)}"
    
    # Pattern 3: "20/09/2025" or "20-09-2025"
    pattern3 = r'(\d{1,2})[/-](\d{1,2})[/-](\d{4})'
    match = re.search(pattern3, date_input)
    if match:
        day = match.group(1)
        month = match.group(2)
        year = match.group(3)
        return f"{year}-{month.zfill(2)}-{day.zfill(2)}"
    
    # Pattern 4: Just month and day with extra words - "september 20 is cool"
    pattern4 = r'(\w+)\s+(\d+)'
    match = re.search(pattern4, date_input)
    if match:
        month_name = match.group(1)
        day = match.group(2)
        
        if month_name in month_map:
            return f"2025-{month_map[month_name]}-{day.zfill(2)}"
    
    return None

def store_session_crn(session_id, crn):
    """Store CRN for a session"""
    try:
        # Create session_crn table if it doesn't exist
        create_table_query = f'''
            CREATE TABLE IF NOT EXISTS {schema}.session_crn (
                session_id VARCHAR(50) PRIMARY KEY,
                crn VARCHAR(20) NOT NULL,
                created_on TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_on TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        '''
        try:
            update_db(create_table_query)
        except Exception as e:
            print(f"Table creation error (may already exist): {e}")
        
        crn_query = f'''
            INSERT INTO {schema}.session_crn (session_id, crn, created_on)
            VALUES (%s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (session_id) DO UPDATE SET 
                crn = %s, 
                updated_on = CURRENT_TIMESTAMP
        '''
        insert_db(crn_query, (session_id, crn, crn))
        print(f"Stored CRN {crn} for session {session_id}")
        return True
    except Exception as e:
        print(f"Error storing CRN: {e}")
        return False

def get_session_crn(session_id):
    """Retrieve CRN for a session"""
    try:
        crn_query = f'''SELECT crn FROM {schema}.session_crn WHERE session_id = %s'''
        result = select_db(crn_query, (session_id,))
        if result and result[0][0]:
            print(f"Retrieved CRN {result[0][0]} for session {session_id}")
            return result[0][0]
        return None
    except Exception as e:
        print(f"Error retrieving CRN: {e}")
        return None


import requests
import time



def process_query(query, values):
    connection = None
    cur = None
    try:
        # Create connection when function is called
        connection = psycopg2.connect(
            user=os.environ['db_user'],
            password=db_password,
            host=os.environ['db_host'],
            port=os.environ['db_port'],
            database=os.environ['db_database']
        )
       
        cur = connection.cursor()
        cur.execute(query, values)
        
        # Check if it's a SELECT query (including those that start with WITH)
        query_lower = query.strip().lower()
        if query_lower.startswith("select") or query_lower.startswith("with"):
            result = cur.fetchall()
        else:
            connection.commit()
            result = 200
        return result

    except Exception as e:
        print(f"Error in process_query: {e}")  # Fixed the error message
        if connection:
            connection.rollback()
        return None  # Explicitly return None on error
        
    finally:
        if cur:
            cur.close()
        if connection:
            connection.close()

def select_db(query):
    connection = psycopg2.connect(  
        user=db_user,
        password=db_password,
        host=db_host,
        port=db_port,
        database=db_database
    )                      
    cursor = connection.cursor()
    cursor.execute(query)
    result = cursor.fetchall()
    connection.commit()
    cursor.close()
    connection.close()
    return result

def update_db(query):
    connection = psycopg2.connect(
        user=db_user,
        password=db_password,
        host=db_host,
        port=db_port,  
        database=db_database
    )                                  
    cursor = connection.cursor()
    cursor.execute(query)
    connection.commit()
    cursor.close()
    connection.close() 
    
def insert_db(query,values):
    connection = psycopg2.connect(
        user=db_user,
        password=db_password,
        host=db_host,
        port=db_port,  # Replace with the SSH tunnel local bind port
        database=db_database
    )                                                                           
    cursor = connection.cursor()
    cursor.execute(query,values)
    connection.commit()
    cursor.close()
    connection.close()

def send_keepalive(connection_id, duration=30):
    """Send periodic keepalive messages to prevent WebSocket timeout"""
    def keepalive():
        for _ in range(duration):
            try:
                heartbeat = {'type': 'keepalive', 'timestamp': time.time()}
                api_gateway_client.post_to_connection(ConnectionId=connection_id, Data=json.dumps(heartbeat))
                time.sleep(1)
            except:
                break
    
    thread = threading.Thread(target=keepalive)
    thread.daemon = True
    thread.start()
    return thread

#Insurance Sandbox code starts here


def generate_mediplus_assessment(event):
    applicant = event.get('applicant_data', {})
    print(applicant)
    
    # Extract form fields
    full_name = applicant.get('full_name', '')
    age = applicant.get('age', 0)
    gender = applicant.get('gender', '')
    occupation = applicant.get('occupation', '')
    annual_income = applicant.get('annual_income', 0)
    smoker_status = applicant.get('smoker_status', '')
    pre_existing_conditions = applicant.get('pre_existing_conditions', [])
    ongoing_medications = applicant.get('ongoing_medications', [])
    height_cm = applicant.get('height_cm', 0)
    weight_kg = applicant.get('weight_kg', 0)
    alcohol_consumption = applicant.get('alcohol_consumption', '')
    selected_plan = applicant.get('selected_plan', '')
    agent_comments = applicant.get('agent_comments', '')

    # BMI calculation
    height_m = height_cm / 100
    bmi = round(weight_kg / (height_m ** 2), 2) if height_m > 0 else None
    print (bmi)

    # Prepare prompt
    prompt = f"""
You are an intelligent underwriting and risk assessment assistant working for AnyCompany Insurance. 

 

Your task is to evaluate health insurance applicants applying for the **MediPlus Secure** plan. 

 

You will receive: 

1. Structured applicant information (from an insurance agent) 

2. Embedded underwriting rules for the below mentioned AnyCompany Innsurance plan 

 

Your job is to: 

- Determine if the applicant is eligible for the plan 

- Identify any applicable premium loadings, exclusions, or waiting periods 

- Assess their overall risk profile 

- Recommend whether to approve, decline, or conditionally approve the application 

- Provide guidance for the insurance agent, if necessary 

 

Please return only a structured JSON output in the following format. 

 

--- 

 

🧾 Output Format: 

```json 

{{ 

  "eligibility_status": "",                  // "Eligible" | "Declined" 

  "eligibility_summary": "",                // Short sentence explaining eligibility fit 

 

  "final_recommendation": "",               // "Standard Approval" | "Conditional Approval" | "Decline" 

  "recommendation_reasoning": "",           // Justification for the recommendation 

 

  "premium_loading_percent": null,          // Numeric loading %, or null if none 

  "exclusions": [],                         // List of applicable exclusions (e.g., ["Diabetes-related claims"]) 

 

  "waiting_periods": {{ 

    "general": "",                          // e.g., "30 days" 

    "condition_name": ""                    // e.g., "hypertension": "12 months" 

  }}, 

 

  "risk_score": null,                       // Numeric score (0–100) 

  "risk_tier": "",                          // "Low", "Medium", or "High" 

  "risk_summary": "",                       // One-liner summary of overall risk 

  "risk_reasoning": "",                     // Explanation for risk classification 

 

  "plan_fit_score": null,                   // How well the plan fits the applicant (0–100) 

  "plan_fit_reasoning": "",                 // Short explanation of plan suitability 

 

  "agent_assist_flags": [],                 // Actionable tips or reminders for the insurance agent 

 

  "rule_trace": [],                         // Bullet list of rules triggered, e.g., "✅ Age 42 eligible", "⚠️ Smoker - loading applied" 

 

  "underwriter_rationale": ""              // 2–3 sentence summary tying everything together 

}} 

 

Underwriting rules for Mediplus Secure Plan: 
 
Comprehensive Underwriting Rules – MediPlus Secure Plan (AnyCompany Insurance) 

Shape 

🔹 Eligibility Criteria 

  

  

Attribute 

Rule 

Age 

Eligible if between 18 and 65 (inclusive) at time of application. Outside this range → ❌ Decline. 

  

  

 

  

  

Shape 

🔹 Body Mass Index (BMI) 

BMI Range 

Decision Logic 

18.5 to 30 

✅ Acceptable, no loading 

>30 to 35 

⚠️ Acceptable with 10–25% loading depending on comorbidities 

>35 or <18.5 

❌ Flag for manual review or decline due to risk of complications 

  

Shape 

🔹 Smoker Status 

Status 

Decision 

Non-smoker 

✅ No impact 

Smoker 

⚠️ Apply +20% premium loading. If comorbid (e.g., smoker + hypertension) → +30–40% loading or manual review 

  

Shape 

🔹 Alcohol Consumption 

Frequency 

Decision Logic 

None / Occasional 

✅ Acceptable 

Moderate 

⚠️ Monitor — flag if paired with liver-related conditions 

Regular 

⚠️ Apply +10–25% loading, especially if liver enzymes flagged or alcohol-related conditions reported 

  

Shape 

🔹 Occupation Risk 

Job Category 

Decision 

Low Risk (e.g., admin, IT, teacher) 

✅ Accepted 

Medium Risk (e.g., delivery, construction under 10m height) 

⚠️ Review but generally acceptable 

High Risk (e.g., offshore rig worker, pilot, diver, construction >10m, firefighter) 

❌ Flag for manual review or exclusion 

  

Shape 

🔹 Pre-existing Conditions (Declared) 

Condition 

Decision 

Hypertension 

✅ Accepted with 12-month waiting period + 10–20% loading 

Type 2 Diabetes (oral meds only) 

✅ Accepted with loading + wait period 

Type 2 Diabetes (insulin) 

⚠️ Flag for manual review or decline 

Asthma (mild/stable) 

✅ Accepted, may incur +10% loading if medication needed 

Asthma (severe/uncontrolled) 

⚠️ Exclusion or manual review 

Heart Disease (any form) 

❌ Decline unless full cardiac clearance & 3+ years treatment-free 

Cancer (history) 

❌ Decline unless in remission >5 years and medically certified 

Mental Health (e.g., depression, anxiety) 

⚠️ Manual review, likely exclusion 

Autoimmune Disorders 

⚠️ Reviewed case-by-case → likely exclusion or decline 

Musculoskeletal/Joint Issues 

✅ Accepted with wait period or exclusion if surgery pending 

  

Shape 

🔹 Medications Declared 

Medication Type 

Decision Logic 

Standard (e.g., amlodipine, statins) 

✅ Accepted 

Chronic (e.g., metformin, beta blockers) 

⚠️ Monitor → triggers pre-existing wait rules 

Red Flag (e.g., insulin, immunosuppressants, psychiatric drugs) 

⚠️ Manual review or exclusion 

  

Shape 

🔹 Hospitalisation History 

History Type 

Impact 

>2 hospitalizations in past 12 months 

⚠️ Flag for review, potential loading 

Hospitalization due to chronic illness (e.g., COPD, cirrhosis) 

❌ Decline or heavy loading 

  

Shape 

🔹 Coverage Overview (for reference only) 

SGD 150,000/year annual inpatient + day surgery limit 

Fully covers private hospitals and A-class wards in restructured hospitals 

90 days pre- and 100 days post-hospitalisation covered 

Daily hospital cash up to SGD 500 

Optional rider: co-pay capped at 5% 

Emergency overseas medical (select countries only) 

Shape 

🔹 Waiting Periods 

Category 

Duration + Notes 

General Claims 

30 days for all first-time applicants 

Pre-existing Conditions 

12–24 months depending on condition (hypertension, diabetes, etc.) 

Specified Procedures 

12 months for: 

  

Cardiac surgery 

Organ transplants 

Joint replacements 

Spinal procedures                                          | 

Shape 

🔹 Permanent Exclusions 

Cosmetic or reconstructive surgery (unless post-accident) 

Fertility, IVF, or assisted reproductive treatments 

Experimental or unlicensed medical procedures 

Mental health treatments (unless specifically endorsed) 

First-year claims arising from declared pre-existing conditions 

Non-emergency treatments abroad 

Shape 

🔹 Risk Score Guidelines 

Tier 

Description 

Low (0–33) 

No major risks, no loadings, standard approval likely 

Medium (34–66) 

1–2 mild/moderate risks, conditional approval possible 

High (67–100) 

Significant health or lifestyle risks, likely decline 

  

Shape 

🔹 Decision Path 

If ineligible due to age/residency → Decline immediately 

If BMI >35 or <18.5 → Manual review or Decline 

If multiple high-risk conditions (e.g., diabetes + smoking) → Decline 

If declared conditions fit accepted list → Apply wait period + loading 

If medications are red-flag → Exclude or trigger review 

If everything acceptable → Approve or conditional approval 

 

 

Here is the Agent input about the customer: 
 
[get the input from the form here] 
 
inputs required through form: 
 
- Full Name: {full_name} 

- Age: {age} 

- Gender: {gender} 

- Occupation: {occupation} 

- Annual Income: SGD {annual_income} 

- Smoker: {smoker_status} 

- Pre-existing Conditions: {pre_existing_conditions} 

- Ongoing Medications: {ongoing_medications} 

- Height: {height_cm} cm 

- Weight: {weight_kg} kg 

- Alcohol Consumption: {alcohol_consumption} 

- Agent Comments: {agent_comments} 

"""
   


    # Prepare payload for Bedrock Claude
   #  bedrock = boto3.client("bedrock-runtime", region_name="us-east-1")

    body = json.dumps({
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 2048,
        "messages": [{"role": "user", "content": prompt}]
    })

    response = bedrock.invoke_model(
        modelId="us.anthropic.claude-3-7-sonnet-20250219-v1:0",
        body=body,
    )

    final_text = str(json.loads(response.get("body").read())["content"][0]["text"])
    print("LLM OUTPUT:", final_text)  # Debug

    # Try to extract JSON response
    match = re.search(r'({.*})', final_text, re.DOTALL)
    json_str = match.group(1) if match else final_text

    return json.loads(json_str)
def generate_lifesecure_assessment(event):
    applicant = event.get('applicant_data', {})
    print(applicant)

    # Extract form fields
    full_name = applicant.get('full_name', '')
    age = applicant.get('age', 0)
    gender = applicant.get('gender', '')
    occupation = applicant.get('occupation', '')
    annual_income = applicant.get('annual_income', 0)
    smoker_status = applicant.get('smoker_status', '')
    alcohol_consumption = applicant.get('alcohol_consumption', '')
    pre_existing_conditions = applicant.get('pre_existing_conditions', [])
    ongoing_medications = applicant.get('ongoing_medications', [])
    height_cm = applicant.get('height_cm', 0)
    weight_kg = applicant.get('weight_kg', 0)
    coverage_amount = applicant.get('coverage_amount', 0)
    term_duration = applicant.get('term_duration', 0)
    family_medical_history = applicant.get('family_medical_history', '')
    agent_comments = applicant.get('agent_comments', '')

    # Calculate BMI
    height_m = height_cm / 100
    bmi = round(weight_kg / (height_m ** 2), 2) if height_m > 0 else None
    projected_end_age = age + term_duration

    # Prompt
    prompt = f"""
 
You are an intelligent life insurance underwriting assistant working for AnyCompany Insurance. Your responsibility is to evaluate life insurance applicants specifically applying for the **LifeSecure Term Advantage** plan.  

 

Using the structured input from the insurance agent and the detailed underwriting rules for this plan, you must assess: 

- Whether the applicant is eligible 

- If any premium loading is applicable 

- If exclusions or waiting periods are necessary 

- Whether the plan fits the applicant's profile 

- A clear rationale that can be used in an underwriter dashboard 

 

Below is the applicant profile: 

 

<<START OF APPLICANT INPUT>> 

- Full Name: {full_name} 

- Age: {age}

- Gender: {gender} 

- Occupation: {occupation} 

- Annual Income: SGD {annual_income} 

- Smoker: {smoker_status}

- Alcohol Consumption: {alcohol_consumption} 

- Pre-existing Conditions: {pre_existing_conditions} 

- Ongoing Medications: {ongoing_medications} 

- Height: {height_cm} cm 

- Weight: {weight_kg} kg 

- Requested Coverage Amount: SGD {coverage_amount} 

- Term Duration: {term_duration} years 

- Family Medical History: {family_medical_history} 

- Agent Comments: {agent_comments} 

<<END OF APPLICANT INPUT>> 

 

Compare the profile against the **LifeSecure Term Advantage** plan's underwriting rules. 

 

Return your result in the following structured JSON format:	 

 

{{ 

  "eligibility_status": "",                      // "Eligible" or "Declined" 

  "eligibility_summary": "",                     // Key reasons for eligibility or decline 

 

  "final_recommendation": "",                    // "Standard Approval" | "Conditional Approval" | "Decline" 

  "final_recommendation_reasoning": "",          // Reasoning behind the decision 

 

  "premium_loading_percent": ,               // % loading due to risk factors, or null if none 

  "exclusions": [],                              // Specific conditions excluded from coverage (e.g., cancer history) 

 

  "risk_score": ,                            // Score 0–100 representing applicant's overall risk 

  "risk_tier": "",                               // "Low", "Medium", or "High" 

  "risk_summary": "",                            // Natural language explanation of risk classification 

 

  "plan_fit_score": ,                        // 0–100 score showing how well the applicant matches this plan 

  "plan_fit_reasoning": "",                      // Summary of why this plan is a good or poor fit 

 

  "agent_assist_flags": [                        // Tips for the agent to communicate with customer 

    "Consider recommending shorter term duration for age-fit", 

    "Highlight importance of disclosure for pre-existing conditions" 

  ], 

 

  "rule_trace": [                                // Rule-by-rule log for transparency 

    "✅ Age 42 within eligible range (21–60)", 

    "⚠️ Smoker status triggers +30% premium loading", 

    "✅ Term duration of 30 years within plan limits" 

  ], 

 

  "underwriter_rationale": ""                    // Final rationale, 2–3 sentence summary usable for dashboard/case notes 

   }} 
 
Underwriting rules: 
 
📘 Comprehensive Underwriting Rules – LifeSecure Term Advantage 

Shape 

🟦 Eligibility Criteria 

Age: 21 to 60 years (inclusive) at time of application. 

❌ Below 21 or above 60 → Declined. 

⚠️ Requested term duration + current age must not exceed age 75 (e.g., a 60-year-old cannot apply for a 20-year term). 

Residency: Must be one of: 

Singapore Citizen 🇸🇬 

Singapore PR 

Valid Work Pass holder 

❌ Long-term visit pass holders and tourists are ineligible. 

Gender: Used for actuarial pricing. 

Male applicants may have slightly higher base loadings due to statistical mortality risk. 

Annual Income: 

Used to assess affordability and coverage-to-income reasonability. 

⚠️ If requested sum assured > 20× annual income, flag for over-insurance review. 

Requested Coverage Amount: 

For applicants <45 years: ≤ SGD 1,000,000 auto-accepted. 

For applicants 45–60 years: ≤ SGD 500,000 auto-accepted. 

SGD 1M (any age) → Flag for manual underwriting. 

Requested Term Duration: 

Allowed terms: 10, 20, 30 years or up to age 75. 

Coverage expiry age must not exceed 75. 

Shape 

🟨 Lifestyle and Health Risk Evaluation 

🚬 Smoker Status 

Smoker → +25% to 40% premium loading depending on comorbidities. 

Non-Smoker → No loading. 

Ex-smoker (within past 12 months) → Treated as smoker. 

Smoker + comorbidities → triggers high composite risk classification. 

🍷 Alcohol Consumption 

None / Occasional (≤2/week) → No impact. 

Regular (≥3 drinks/week or binge drinking) → +10–15% loading. 

⚠️ Combined with liver-related conditions or medication → exclusion or review. 

🧬 BMI (Derived from Height & Weight) 

Acceptable BMI: 18.5 to 29.9 

30.0–35.0 → +10–20% loading 

35 or <18.5 → Manual review / likely decline 

Shape 

🩺 Medical Risk Assessment 

Pre-existing Conditions 

Condition 

Decision Logic 

Hypertension 

Accepted with +10–15% loading 

Type 2 Diabetes 

Oral meds → accepted with +15–20% loading 
Insulin → Manual review 

Type 1 Diabetes 

Declined 

Heart Disease 

Declined 

Stroke (history) 

Declined unless >5 years full recovery 

Asthma (stable) 

Accepted, no loading 

Cancer (remission 5+ yrs) 

Manual review; conditional approval 

Kidney Disease 

Manual review or decline 

Autoimmune Disorders 

Case-by-case; typically flagged for exclusions 

Mental Health Disorders 

May lead to exclusions or decline 

HIV / Terminal Illness 

Declined 

Recent Hospitalization (past 6 months) 

Manual review or postponement 

Obstructive Sleep Apnea 

Manual review; CPAP users may be accepted with loading 

Shape 

💊 Ongoing Medications 

Common maintenance meds (e.g., statins, beta blockers, metformin) → acceptable. 

Insulin → triggers diabetes risk flag. 

Immunosuppressants, opioids, psychiatric medications → flag for exclusion or decline. 

Polypharmacy (≥3 chronic meds) → moderate-to-high risk tiering. 

Shape 

👪 Family Medical History 

Major illness in first-degree relatives under age 60: 

Cardiovascular disease → +10% loading 

Cancer → +10–15% loading 

Stroke or neurological illness → +5–10% loading 

Multiple affected relatives or early onset (<50) → high risk flag 

Unknown history → treated neutrally 

Shape 

💼 Occupation Risk Classification 

Risk Tier 

Examples 

Decision 

Low 

Teacher, Office Admin, Retail, Nurse 

Accepted 

Medium 

Driver, Warehouse Worker, Waiter 

Accepted with possible minor loading 

High 

Offshore Rig Engineer, Pilot, Construction >10m, Diver, Military 

Flag for review / Decline 

Shape 

🟥 Exclusions (Permanent) 

Suicide (within first policy year) 

Death due to undisclosed pre-existing conditions 

Death during criminal activity or substance abuse 

War zone, terrorism, civil unrest (non-covered jurisdictions) 

High-risk occupations not disclosed at application 

-------

Return only the following JSON format (no markdown, no extra commentary):

{{
  "eligibility_status": "",                      
  "eligibility_summary": "",                     

  "final_recommendation": "",                    
  "final_recommendation_reasoning": "",          

  "premium_loading_percent": null,               
  "exclusions": [],                              

  "risk_score": null,                            
  "risk_tier": "",                               
  "risk_summary": "",                            
  "risk_reasoning": "",                           

  "plan_fit_score": null,                        
  "plan_fit_reasoning": "",                      

  "agent_assist_flags": [],                      
  "rule_trace": [],                               

  "underwriter_rationale": ""                    
}}

"""

    # Invoke Claude via Bedrock
    # bedrock = boto3.client("bedrock-runtime", region_name="us-east-1")

    body = json.dumps({
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 2048,
        "messages": [{"role": "user", "content": prompt}]
    })

    response = bedrock.invoke_model(
        modelId="us.anthropic.claude-3-7-sonnet-20250219-v1:0",
        body=body,
    )

    final_text = str(json.loads(response.get("body").read())["content"][0]["text"])
    print("LLM OUTPUT:", final_text)

    match = re.search(r'({.*})', final_text, re.DOTALL)
    json_str = match.group(1) if match else final_text

    return json.loads(json_str)



def agent_invoke_tool(chat_history, session_id, chat, connectionId):
    try:
        # Start keepalive thread
        keepalive_thread = send_keepalive(connectionId, 30)
        import uuid
        import random
        # Fetch base_prompt from the database as before
        select_query = f'''select base_prompt from {schema}.{prompt_metadata_table} where id =1;'''
        base_prompt = f'''You are a Virtual Insurance Assistant, a helpful and accurate chatbot for insurance customers. You help customers with their insurance policies, claims, and related services.

## CRITICAL INSTRUCTIONS:
- **NEVER** reply with any message that says you are checking, looking up, or finding information (such as "I'll check that for you", "Let me look that up", "One moment", "I'll find out", etc.).
- **NEVER** say "To answer your question about [topic], let me check our knowledge base" or similar phrases.
- After using a tool, IMMEDIATELY provide only the direct answer or summary to the user, with no filler, no explanations, and no mention of checking or looking up.
- If a user asks a question that requires a tool, use the tool and reply ONLY with the answer or summary, never with any statement about the process.
- For general insurance questions, IMMEDIATELY use the faq_tool_schema tool WITHOUT any preliminary message.

## CRN HANDLING RULES:
- **NEVER** ask for CRN if it has already been provided in the conversation history
- If CRN is available in the conversation, use it automatically for all tool calls
- If user says "I gave you before" or similar, acknowledge and proceed with the stored CRN
- Only ask for CRN if it's completely missing from the conversation history
- When CRN is provided, validate it matches the pattern CUST#### (e.g., CUST1001)

## MANDATORY QUESTION COLLECTION RULES:
- **ALWAYS** collect ALL required information for any tool before using it
- **NEVER** skip any required questions, even if the user provides some information
- **NEVER** assume or guess missing information
- **NEVER** proceed with incomplete information
- Ask questions ONE AT A TIME in this exact order:

### For get_user_policies tool:
1. CRN (Customer Reference Number) - if not already provided

### For track_claim_status tool:
1. CRN (Customer Reference Number) - if not already provided

### For file_claim tool (ask in this exact order):
1. CRN (Customer Reference Number) - if not already provided
2. Policy ID (from user's active policies)
3. Claim Type 
4. Date of Incident (accept any reasonable format)
5. Claim Amount (e.g., 6500SGD, SGD 6500, 6500)
6. Description (brief description of what happened)

### For schedule_agent_callback tool (ask in this exact order):
1. CRN (Customer Reference Number) - if not already provided
2. Reason for callback request
3. Preferred time slot (e.g., '13 July, 2-4pm')
4. Preferred contact method (phone or email)

## CALLBACK SCHEDULING RULES:
- When a user requests an agent callback, IMMEDIATELY start collecting required information
- Ask ONLY ONE question at a time in this exact order:
  1. Reason for callback request (e.g., "What would you like to discuss with our agent?")
  2. Preferred time slot (e.g., "When would you prefer the callback?")  
  3. Preferred contact method (e.g., "Would you prefer to be contacted by phone or email?")
- Do NOT assume or guess any of these values
- Do NOT use random dates, times, or contact methods
- Do NOT proceed until ALL information is collected
- **NEVER** automatically schedule with hardcoded values like "13 July, 2-4pm"
- **NEVER** assume the user wants a specific time or contact method

## INPUT VALIDATION RULES:
- **NEVER** ask for the same information twice in a session
- Accept any reasonable date format (July 19, 2025, 19/07/2025, 2025-07-19, etc.)
- Accept any reasonable claim amount format (6500SGD, SGD 6500, 6500, etc.)
- Accept any reasonable claim type
- **NEVER** ask for specific formats - accept what the user provides
- If validation fails, provide a clear, specific error message with examples

##NATURAL DATE INTERPRETATION RULE:
- When collecting a date or time-related input, accept natural expressions such as:
	
	“yesterday”, “today”, “tomorrow”, “last night”, etc.
	
- Convert these into actual calendar dates based on the current date.
	
- If a time of day is mentioned (e.g., “yesterday evening”), assign a random time in that time range:
	
	Morning: 8am–12pm
	
	Afternoon: 1pm–5pm
	
	Evening: 6pm–9pm
	
	Night: 9pm–11pm
	
- Examples:
	
	“yesterday” → 2025-07-30
	
	“today afternoon” → 2025-07-31, 2:34 PM (randomized)
	
	“tomorrow morning” → 2025-08-01, 9:12 AM (randomized)

## Tool Usage Rules:
- When a user asks about coverage, benefits, policy details, or general insurance questions, IMMEDIATELY use the faq_tool_schema tool
- Do NOT announce that you're using the tool or searching for information
- Simply use the tool and provide the direct answer from the knowledge base
- If the knowledge base doesn't have the information, say "I don't have specific information about that in our current knowledge base. Let me schedule a callback with one of our agents who can provide detailed information."

## Response Format:
- ALWAYS answer in the shortest, most direct way possible
- Do NOT add extra greetings, confirmations, or explanations
- Do NOT mention backend systems or tools
- Speak naturally as a helpful insurance representative who already knows the information
- After every completed tool call (such as filing a claim, tracking a claim, or scheduling a callback), always include a clear summary of all user-provided inputs involved in that flow, followed by the final result (e.g., Claim ID, callback confirmation, etc.).

	The summary must include:
	
	All collected fields in the order they were asked
	
	The tool output (e.g., Claim ID or confirmation)
	
	Example (for a filed claim):
	Your claim has been submitted.
	- CRN: CUST1001
	- Policy ID: POL12345
	- Claim Type: Accident
	- Date of Incident: July 19, 2025
	- Claim Amount: 6500SGD
	- Description: Got hit by train
	- Claim ID: CLM45829

Available Tools:
1. get_user_policies - Retrieve active insurance policies for a customer
2. track_claim_status - Check the status of insurance claims
3. file_claim - Submit a new insurance claim
4. schedule_agent_callback - Schedule a callback from a human agent
5. faq_tool_schema - Retrieve answers from the insurance knowledge base

## SYSTEMATIC QUESTION COLLECTION:
- When a user wants to file a claim OR schedule a callback, IMMEDIATELY start collecting required information
- Ask ONLY ONE question at a time
- After each user response, check what information is still missing
- Ask for the NEXT missing required field (in the exact order listed above)
- Do NOT ask multiple questions in one message
- Do NOT skip any required questions
- Do NOT proceed until ALL required information is collected

## EXAMPLES OF CORRECT BEHAVIOR:

### Filing a Claim:
User: "I want to file a claim"
Assistant: "I'll help you file a claim. What is your Customer Reference Number (CRN)?"

User: "CUST1001"
Assistant: "What type of claim is this?"

User: "Accident"
Assistant: "What was the date of the incident?"

User: "July 19, 2025"
Assistant: "What amount are you claiming?"

User: "6500SGD"
Assistant: "Please provide a brief description of what happened."

User: "Got hit by train"
Assistant: [Use file_claim tool with all collected information]

### Scheduling a Callback:
User: "I want to schedule an agent callback"
Assistant: "What would you like to discuss with our agent?"

User: "I need help with my policy coverage"
Assistant: "When would you prefer the callback?"

User: "Tomorrow morning"
Assistant: "Would you prefer to be contacted by phone or email?"

User: "Phone"
Assistant: [Use schedule_agent_callback tool with all collected information]

## EXAMPLES OF INCORRECT BEHAVIOR:
- ❌ "What's your CRN, policy ID, claim type, date, amount, and description?" (asking multiple questions)
- ❌ Skipping any required questions
- ❌ Proceeding with incomplete information
- ❌ Asking for the same information twice
- ❌ Using hardcoded values like "13 July, 2-4pm" without asking the user
- ❌ Assuming contact method or time preferences

## CRITICAL SESSION MEMORY RULES:
- When a user provides a CRN and asks to see their policies, check coverage, or similar, IMMEDIATELY use the get_user_policies tool with their CRN. Do NOT thank, confirm, or repeat the user's request—just use the tool and return the result.
- When a user asks about claim status, IMMEDIATELY use the track_claim_status tool with their CRN.
- When a user wants to file a new claim, IMMEDIATELY start collecting required information in the exact order specified above.
- When collecting information for a tool (such as filing a claim OR scheduling a callback), ALWAYS ask for only ONE missing required field at a time.
- NEVER ask for more than one piece of information in a single message.
- After the user answers, check which required field is still missing, and ask for only that field next.
- Do NOT list multiple questions or fields in a single message, even if several are missing.
- If the user provides more than one field in their answer, acknowledge all provided info, then ask for the next missing field (one at a time).
- If all required fields are provided, proceed to use the tool and summarize the result.

## FIELD COLLECTION PERSISTENCE:
- For each required field, use the user's first answer as the value for that field. Do NOT ask for the same field again, even if later user messages contain related or similar information.
- If the user provides additional information after a required field has already been answered, treat it as context or as the answer to the next required field, NOT as a replacement for a previous answer.
- Do NOT reinterpret or overwrite a previously collected answer for any required field.
- Only ask for a required field if it has not already been answered in this session.

## SESSION CONTINUITY:
- Once the user provides their CRN and policy, REMEMBER them for the entire session. Use the same CRN and policy for all subsequent tool calls and do NOT ask for them again, even if the user initiates multiple requests or cases in the same session, or if their answer is ambiguous or short.
- If the user's answer is unclear, make your best guess based on previous context, but do NOT re-ask for information you already have.
- If you are unsure, always proceed with the most recently provided value for each required field.
- Do NOT repeat questions that have already been answered. Only ask for information that is still missing.
- Stay focused and do not ask for the same information more than once per session.

## INPUT ACCEPTANCE RULES:
- Do NOT validate, reject, or question the user's input for required fields (such as dates, claim amounts, etc.). Accept any value the user provides and proceed to the next required field.
- Do NOT comment on whether a date is in the past or future. Simply record the value and continue.
- If the user provides a value that seems unusual, do NOT ask for clarification or corrections—just accept the input and move on.
- NEVER ask for a date in a specific format (such as 'DD_MM_YYYY?'). When you need a date, simply ask plainly for the date (e.g., 'When did the incident occur?'), without specifying a format in the question.

## RESPONSE GUIDELINES:
- For general insurance questions, IMMEDIATELY use the faq_tool_schema tool.
- ALWAYS answer in the shortest, most direct way possible. Do NOT add extra greetings, confirmations, or explanations.
- Do NOT mention backend systems or tools. Speak naturally as a helpful insurance representative.
- If a user provides a CRN and asks about their policies, use the get_user_policies tool immediately, without further confirmation.
- After using a tool, ALWAYS provide a short, direct summary of the result to the user. Do not leave the user without a response.
- NEVER reply with messages like "I'm thinking", "Let me check", "I'll look up your policies", "One moment", "Please wait", or any similar filler or placeholder text.
- ONLY reply with:
    - The next required question if you need more information from the user (ask one question at a time, never multiple).
    - The direct answer or result after using a tool.
    - A short, direct summary of the tool result.
- Do NOT add extra confirmations, explanations, or conversational filler.
- Do NOT mention backend systems, tools, or your own reasoning process.
- Speak naturally and concisely, as a helpful insurance representative.
- Handle greetings warmly and ask how you can help with their insurance needs today.
'''
        
        # Insurance tool schema, now including FAQ tool
        insurance_tools = [
            {
                "name": "get_user_policies",
                "description": "Retrieve active insurance policies for a customer based on their CRN",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "crn": {"type": "string", "description": "Customer Reference Number (e.g., CUST1001)"}
                    },
                    "required": ["crn"]
                }
            },
            {
                "name": "track_claim_status",
                "description": "Check the status of insurance claims for a customer",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "crn": {"type": "string", "description": "Customer Reference Number (e.g., CUST1001)"}
                    },
                    "required": ["crn"]
                }
            },
            {
                "name": "file_claim",
                "description": "Submit a new insurance claim",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "crn": {"type": "string", "description": "Customer Reference Number"},
                        "policy_id": {"type": "string", "description": "Policy ID from user's active policies"},
                        "claim_type": {"type": "string", "description": "Type of claim "},
                        "date_of_incident": {"type": "string", "description": "Date of the incident (YYYY-MM-DD format)"},
                        "claim_amount": {"type": "string", "description": "Claimed amount (e.g., SGD 12000)"},
                        "description": {"type": "string", "description": "Brief description of what happened"}
                    },
                    "required": ["crn", "policy_id", "claim_type", "date_of_incident", "claim_amount", "description"]
                }
            },
            {
                "name": "schedule_agent_callback",
                "description": "Schedule a callback from a human insurance agent",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "crn": {"type": "string", "description": "Customer Reference Number"},
                        "reason": {"type": "string", "description": "Reason for callback request"},
                        "preferred_timeslot": {"type": "string", "description": "Preferred time slot (e.g., '2-4pm')"},
                        "preferred_contact_method": {"type": "string", "description": "Preferred contact method (phone or email)"}
                    },
                    "required": ["crn", "reason", "preferred_timeslot", "preferred_contact_method"]
                }
            },
            {
                "name": "faq_tool_schema",
                "description": "Retrieve answers from the insurance knowledge base",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "knowledge_base_retrieval_question": {"type": "string", "description": "A question to retrieve from the insurance knowledge base."}
                    },
                    "required": ["knowledge_base_retrieval_question"]
                }
            }
        ]

        # --- Mock tool implementations ---
        def get_user_policies(crn):
            mock_policies = {
                "CUST1001": [
                    {"policy_id": "POL1001", "plan_name": "MediPlus Secure", "policy_type": "Health", "coverage_amount": "SGD 150,000/year", "start_date": "2022-04-15", "premium_amount": "SGD 120", "next_premium_due": get_dynamic_date(3), "status": "Active"},
                    {"policy_id": "POL1002", "plan_name": "LifeSecure Term Advantage", "policy_type": "Life", "coverage_amount": "SGD 500,000", "start_date": "2021-09-10", "premium_amount": "SGD 95", "next_premium_due": get_dynamic_date(3), "status": "Active"}
                ],
                "CUST1002": [
                    {"policy_id": "POL2001", "plan_name": "FamilyCare Protect", "policy_type": "Family", "coverage_amount": "SGD 250,000", "start_date": "2023-01-05", "premium_amount": "SGD 290", "next_premium_due": get_dynamic_date(3), "status": "Active"},
                    {"policy_id": "POL2002", "plan_name": "ActiveShield PA", "policy_type": "Accident", "coverage_amount": "SGD 100,000", "start_date": "2022-08-22", "premium_amount": "SGD 40", "next_premium_due": get_dynamic_date(3), "status": "Active"}
                ],
                "CUST1003": [
                    {"policy_id": "POL3001", "plan_name": "SilverShield Health", "policy_type": "Senior", "coverage_amount": "SGD 100,000/year", "start_date": "2021-11-30", "premium_amount": "SGD 320", "next_premium_due": get_dynamic_date(3), "status": "Active"}
                ],
                "CUST1004": [
                    {"policy_id": "POL4001", "plan_name": "MediPlus Secure", "policy_type": "Health", "coverage_amount": "SGD 150,000/year", "start_date": "2023-03-18", "premium_amount": "SGD 120", "next_premium_due": get_dynamic_date(3), "status": "Active"}
                ],
                "CUST1005": [
                    {"policy_id": "POL5001", "plan_name": "LifeSecure Term Advantage", "policy_type": "Life", "coverage_amount": "SGD 300,000", "start_date": "2020-06-25", "premium_amount": "SGD 85", "next_premium_due": get_dynamic_date(3), "status": "Active"}
                ]
            }
            return mock_policies.get(crn, [])

        def track_claim_status(crn):
            mock_claims = {
                "CUST1001": [
                    {"claim_id": "CLM1001", "policy_id": "POL1001", "claim_type": "Hospitalisation", "claim_status": "Under Review", "claim_amount": "SGD 12,000", "date_filed": get_dynamic_date(2), "last_updated": get_dynamic_date(3), "remarks": "Awaiting final approval from claims officer"},
                    {"claim_id": "CLM1002", "policy_id": "POL1002", "claim_type": "Terminal Illness", "claim_status": "Submitted", "claim_amount": "SGD 250,000", "date_filed": get_dynamic_date(2), "last_updated": get_dynamic_date(3), "remarks": "Doctor's certification under verification"}
                ],
                "CUST1002": [
                    {"claim_id": "CLM2001", "policy_id": "POL2002", "claim_type": "Accident Medical", "claim_status": "Approved", "claim_amount": "SGD 7,500", "date_filed": get_dynamic_date(2), "last_updated": get_dynamic_date(3), "remarks": "Payout issued to registered bank account"},
                    {"claim_id": "CLM2002", "policy_id": "POL2001", "claim_type": "Outpatient Family Cover", "claim_status": "Submitted", "claim_amount": "SGD 4,200", "date_filed": get_dynamic_date(2), "last_updated": get_dynamic_date(3), "remarks": "Pending document verification"}
                ],
                "CUST1003": [
                    {"claim_id": "CLM3001", "policy_id": "POL3001", "claim_type": "Hospitalisation", "claim_status": "Rejected", "claim_amount": "SGD 9,200", "date_filed": get_dynamic_date(2), "last_updated": get_dynamic_date(3), "remarks": "Missing discharge summary and itemised bill"}
                ],
                "CUST1004": [
                    {"claim_id": "CLM4001", "policy_id": "POL4001", "claim_type": "Hospitalisation", "claim_status": "Submitted", "claim_amount": "SGD 6,800", "date_filed": get_dynamic_date(2), "last_updated": get_dynamic_date(3), "remarks": "Documents received, pending review"}
                ],
                "CUST1005": [
                    {"claim_id": "CLM5001", "policy_id": "POL5001", "claim_type": "Death", "claim_status": "Under Review", "claim_amount": "SGD 300,000", "date_filed": get_dynamic_date(2), "last_updated": get_dynamic_date(3), "remarks": "Awaiting legal verification and supporting documents"}
                ]
            }
            return mock_claims.get(crn, [])

        def file_claim(crn, policy_id, claim_type, date_of_incident, claim_amount, description):
            claim_id = f"CLM{str(uuid.uuid4())[:4].upper()}"
            return {
                "claim_id": claim_id,
                "status": "Submitted",
                "remarks": "Your claim has been submitted. Our team will review it, and an agent will reach out to you shortly."
            }

        def schedule_agent_callback(crn, reason, preferred_timeslot, preferred_contact_method):
            return {
                "status": "Scheduled",
                "scheduled_for": preferred_timeslot,
                "remarks": f"An agent will reach out to you via {preferred_contact_method} during your selected time window."
            }

        input_tokens = 0
        output_tokens = 0
        print("In agent_invoke_tool (Insurance Bot)")
        
        # Extract CRN from chat history
        extracted_crn = None
        for message in chat_history:
            if message['role'] == 'user':
                content_text = message['content'][0]['text']
                crn_match = re.search(r'\b(CUST\d{4})\b', content_text.upper())
                if crn_match:
                    extracted_crn = crn_match.group(1)
                    print(f"Extracted CRN from chat history: {extracted_crn}")
                    break
        
        # Enhance system prompt with CRN context
        if extracted_crn:
            enhanced_prompt = base_prompt + f"\n\nIMPORTANT: The customer's CRN is {extracted_crn}. Use this CRN automatically for any tool calls that require it without asking again."
            print(f"Enhanced prompt with CRN: {extracted_crn}")
        else:
            enhanced_prompt = base_prompt
        
        # Use the enhanced_prompt instead of base_prompt
        prompt = enhanced_prompt
        
        # First API call to get initial response
        try:
            response = bedrock_client.invoke_model_with_response_stream(
                contentType='application/json',
                body=json.dumps({
                    "anthropic_version": "bedrock-2023-05-31",
                    "max_tokens": 4000,
                    "temperature": 0,
                    "top_p": 0.999,
                    "system": prompt,
                    "tools": insurance_tools,
                    "messages": chat_history
                }),
                modelId=model_id
            )
        except Exception as e:
            print("AN ERROR OCCURRED : ", e)
            response = "We are unable to assist right now please try again after few minutes"
            return {"answer": response, "question": chat, "session_id": session_id}

        streamed_content = ''
        content_block = None
        assistant_response = []
        for item in response['body']:
            content = json.loads(item['chunk']['bytes'].decode())
            if content['type'] == 'content_block_start':
                content_block = content['content_block']
            elif content['type'] == 'content_block_stop':
                print(f"Content block at stop: {content_block}")  # Add debug line
                try:
                    api_gateway_client.post_to_connection(ConnectionId=connectionId, Data=json.dumps(content))
                except api_gateway_client.exceptions.GoneException:
                    print(f"Connection {connectionId} is closed (GoneException) - stop message")
                except Exception as e:
                    print(f"WebSocket send error (stop): {e}")
                if content_block['type'] == 'text':
                    content_block['text'] = streamed_content
                    assistant_response.append(content_block)
                elif content_block['type'] == 'tool_use':
                    try:
                        content_block['input'] = json.loads(streamed_content)
                    except json.JSONDecodeError as e:
                        print(f"JSON decode error for tool input: {e}")
                        print(f"Streamed content: {streamed_content}")
                        content_block['input'] = {}
                    assistant_response.append(content_block)
                streamed_content = ''
            elif content['type'] == 'content_block_delta':
                try:
                    api_gateway_client.post_to_connection(ConnectionId=connectionId, Data=json.dumps(content))
                except api_gateway_client.exceptions.GoneException:
                    print(f"Connection {connectionId} is closed (GoneException) - delta message")
                except Exception as e:
                    print(f"WebSocket send error (delta): {e}")
                if 'delta' in content and isinstance(content['delta'], dict):
                    if content['delta']['type'] == 'text_delta':
                        streamed_content += content['delta']['text']
                    elif content['delta']['type'] == 'input_json_delta':
                        streamed_content += content['delta']['partial_json']
            elif content['type'] == 'message_delta':
                try:
                    if 'usage' in content and isinstance(content['usage'], dict):
                        tool_tokens = content['usage']['output_tokens']
                    else:
                        tool_tokens = 0
                except (KeyError, TypeError) as e:
                    print(f"Error accessing usage tokens: {e}")
                    tool_tokens = 0
            elif content['type'] == 'message_stop':
                input_tokens += content['amazon-bedrock-invocationMetrics']['inputTokenCount']
                output_tokens += content['amazon-bedrock-invocationMetrics']['outputTokenCount']
        chat_history.append({'role': 'assistant', 'content': assistant_response})
        
        # Check if any tools were called
        tools_used = []
        tool_results = []
        
        for action in assistant_response:
            if action['type'] == 'tool_use':
                tools_used.append(action['name'])
                tool_name = action['name']
                tool_input = action['input']
                tool_result = None
                
                # Send a heartbeat to keep WebSocket alive during tool execution
                try:
                    heartbeat = {'type': 'heartbeat'}
                    api_gateway_client.post_to_connection(ConnectionId=connectionId, Data=json.dumps(heartbeat))
                except Exception as e:
                    print(f"Heartbeat send error: {e}")
                
                # Execute the appropriate tool
                if tool_name == 'get_user_policies':
                    tool_result = get_user_policies(tool_input['crn'])
                elif tool_name == 'track_claim_status':
                    tool_result = track_claim_status(tool_input['crn'])
                elif tool_name == 'file_claim':
                    tool_result = file_claim(
                        tool_input['crn'],
                        tool_input['policy_id'],
                        tool_input['claim_type'],
                        tool_input['date_of_incident'],
                        tool_input['claim_amount'],
                        tool_input['description']
                    )
                elif tool_name == 'schedule_agent_callback':
                    tool_result = schedule_agent_callback(
                        tool_input['crn'],
                        tool_input['reason'],
                        tool_input['preferred_timeslot'],
                        tool_input['preferred_contact_method']
                    )
                elif tool_name == 'faq_tool_schema':
                    # Send another heartbeat before FAQ retrieval
                    try:
                        heartbeat = {'type': 'heartbeat'}
                        api_gateway_client.post_to_connection(ConnectionId=connectionId, Data=json.dumps(heartbeat))
                    except Exception as e:
                        print(f"FAQ heartbeat send error: {e}")
                    
                    tool_result = get_FAQ_chunks_tool(tool_input)
                    
                    # If FAQ tool returns empty or no results, provide fallback
                    if not tool_result or len(tool_result) == 0:
                        tool_result = ["I don't have specific information about that in our current knowledge base. Let me schedule a callback with one of our agents who can provide detailed information."]
                
                # Create tool result message with better error handling
                try:
                    if not isinstance(action, dict):
                        print(f"Action is not a dict: {type(action)}, value: {action}")
                        continue
                        
                    if 'id' not in action:
                        print(f"Action missing 'id' field: {action}")
                        continue
                    
                    print(f"Tool result type: {type(tool_result)}")
                    print(f"Tool result content: {tool_result}")
                    
                    if isinstance(tool_result, list) and tool_result:
                        content_text = "\n".join(tool_result)
                    elif isinstance(tool_result, list) and not tool_result:
                        content_text = "No information available"
                    else:
                        content_text = str(tool_result) if tool_result else "No information available"
                    
                    tool_response_dict = {
                        "type": "tool_result",
                        "tool_use_id": action['id'],
                        "content": [{"type": "text", "text": content_text}]
                    }
                    tool_results.append(tool_response_dict)
                    
                except Exception as e:
                    print(f"Error creating tool response: {e}")
                    print(f"Action type: {type(action)}")
                    print(f"Action content: {action}")
                    # Skip this tool result instead of crashing
                    continue
                tool_results.append(tool_response_dict)
        
        # If tools were used, add tool results to chat history and make second API call
        if tools_used:
            # Add tool results to chat history
            chat_history.append({'role': 'user', 'content': tool_results})
            
            # Make second API call with tool results
            try:
                response = bedrock_client.invoke_model_with_response_stream(
                    contentType='application/json',
                    body=json.dumps({
                        "anthropic_version": "bedrock-2023-05-31",
                        "max_tokens": 4000,
                        "temperature": 0,
                        "system": prompt,
                        "tools": insurance_tools,
                        "messages": chat_history
                    }),
                    modelId=model_id
                )
            except Exception as e:
                print("ERROR IN SECOND API CALL:", e)
                # Send error response via WebSocket
                error_response = "I apologize, but I'm having trouble accessing that information right now. Please try again in a moment."
                for word in error_response.split():
                    delta = {'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'text_delta', 'text': word + ' '}}
                    try:
                        api_gateway_client.post_to_connection(ConnectionId=connectionId, Data=json.dumps(delta))
                    except:
                        pass
                stop_answer = {'type': 'content_block_stop', 'index': 0}
                try:
                    api_gateway_client.post_to_connection(ConnectionId=connectionId, Data=json.dumps(stop_answer))
                except:
                    pass
                return {"answer": error_response, "question": chat, "session_id": session_id}

            # Process the streaming response
            streamed_content = ''
            content_block = None
            assistant_response = []
            
            for item in response['body']:
                content = json.loads(item['chunk']['bytes'].decode())
                if content['type'] == 'content_block_start':
                    content_block = content['content_block']
                elif content['type'] == 'content_block_stop':
                    try:
                        api_gateway_client.post_to_connection(ConnectionId=connectionId, Data=json.dumps(content))
                    except api_gateway_client.exceptions.GoneException:
                        print(f"Connection {connectionId} is closed (GoneException) - stop message (tool)")
                    except Exception as e:
                        print(f"WebSocket send error (stop, tool): {e}")
                    if content_block['type'] == 'text':
                        content_block['text'] = streamed_content
                        assistant_response.append(content_block)
                    elif content_block['type'] == 'tool_use':
                        try:
                            content_block['input'] = json.loads(streamed_content)
                        except json.JSONDecodeError as e:
                            print(f"JSON decode error for tool input: {e}")
                            print(f"Streamed content: {streamed_content}")
                            content_block['input'] = {}
                        assistant_response.append(content_block)
                    streamed_content = ''
                elif content['type'] == 'content_block_delta':
                    try:
                        api_gateway_client.post_to_connection(ConnectionId=connectionId, Data=json.dumps(content))
                    except api_gateway_client.exceptions.GoneException:
                        print(f"Connection {connectionId} is closed (GoneException) - delta message (tool)")
                    except Exception as e:
                        print(f"WebSocket send error (delta, tool): {e}")
                    if content['delta']['type'] == 'text_delta':
                        streamed_content += content['delta']['text']
                    elif content['delta']['type'] == 'input_json_delta':
                        streamed_content += content['delta']['partial_json']
                elif content['type'] == 'message_stop':
                    input_tokens += content['amazon-bedrock-invocationMetrics']['inputTokenCount']
                    output_tokens += content['amazon-bedrock-invocationMetrics']['outputTokenCount']
            
            # Extract final answer
            final_ans = ""
            for i in assistant_response:
                if i['type'] == 'text':
                    final_ans = i['text']
                    break
            
            # If no text response, provide fallback
            if not final_ans:
                final_ans = "I apologize, but I couldn't retrieve the information at this time. Please try again or contact our support team."
            
            return {"statusCode": "200", "answer": final_ans, "question": chat, "session_id": session_id, "input_tokens": str(input_tokens), "output_tokens": str(output_tokens)}

        else:
            # No tools called, handle normal response
            for action in assistant_response:
                if action['type'] == 'text':
                    ai_response = action['text']
                    return {"statusCode": "200", "answer": ai_response, "question": chat, "session_id": session_id, "input_tokens": str(input_tokens), "output_tokens": str(output_tokens)}
            
            # Fallback if no text response
            return {"statusCode": "200", "answer": "I'm here to help with your insurance needs. How can I assist you today?", "question": chat, "session_id": session_id, "input_tokens": str(input_tokens), "output_tokens": str(output_tokens)}
    except Exception as e:
        print(f"Unexpected error: {e}")
        response = "An Unknown error occurred. Please try again after some time."
        return {
            "statusCode": "500",
            "answer": response,
            "question": chat,
            "session_id": session_id,
            "input_tokens": "0",
            "output_tokens": "0"
        }

def get_FAQ_chunks_tool(query):
    try:
        print("IN FAQ: ", query)
        chat = query['knowledge_base_retrieval_question']
        chunks = []
        
        # Add timeout to prevent hanging
        response_chunks = retrieve_client.retrieve(
            retrievalQuery={                                                                                
                'text': chat   
            },
            knowledgeBaseId=KB_ID,
            retrievalConfiguration={
                'vectorSearchConfiguration': {                          
                    'numberOfResults': 10,                                                                                              
                    'overrideSearchType': 'HYBRID'
                }
            }
        )
       
        for item in response_chunks['retrievalResults']:
            if 'content' in item and 'text' in item['content']:
                chunks.append(item['content']['text'])
        
        print('CHUNKS: ', chunks)
        
        # Return meaningful chunks or fallback message
        if chunks:
            return chunks
        else:
            return ["I don't have specific information about that in our current knowledge base. Let me schedule a callback with one of our agents who can provide detailed information."]
            
    except Exception as e:
        print("An exception occurred while retrieving chunks:", e)
        return ["I'm having trouble accessing that information right now. Please try again in a moment, or I can schedule a callback with one of our agents."]

def get_hospital_faq_chunks(query):
    try:
        print("IN HOSPITAL FAQ: ", query)
        chunks = []
        response_chunks = retrieve_client.retrieve(
            retrievalQuery={                                                                                
                'text': query
            },
            knowledgeBaseId=health_kb_id,
            retrievalConfiguration={
                'vectorSearchConfiguration': {                          
                    'numberOfResults': 10,                                                                                              
                    'overrideSearchType': 'HYBRID'
                }
            }
        )
       
        for item in response_chunks['retrievalResults']:
            if 'content' in item and 'text' in item['content']:
                chunks.append(item['content']['text'])
        
        print('HOSPITAL FAQ CHUNKS: ', chunks)
        
        # Return meaningful chunks or fallback message
        if chunks:
            return chunks
        else:
            return ["I don't have specific information about that in our current hospital knowledge base. Please contact our hospital directly for detailed information."]
            
    except Exception as e:
        print("An exception occurred while retrieving hospital FAQ chunks:", e)
        return ["I'm having trouble accessing that information right now. Please try again in a moment, or contact our hospital directly."]


def extract_sections(llm_response):
    # Define the regular expression pattern for each section
    patterns = {
    "Topic": r'"Topic":\s*"([^"]+)"',  
    "Conversation Type": r'"Conversation Type":\s*"([^"]+)"',
    "Conversation Summary Explanation": r'"Conversation Summary Explanation":\s*"([^"]+)"',
    "Detailed Summary": r'"Detailed Summary":\s*"([^"]+)"',
    "Conversation Sentiment": r'"Conversation Sentiment":\s*"([^"]+)"',
    "Conversation Sentiment Generated Details" :r'"Conversation Sentiment Generated Details":\s*"([^"]+)"',
    "Lead Sentiment": r'"Lead Sentiment":\s*"([^"]+)"',
    "Leads Generated Details": r'"Leads Generated Details":\s*"([^"]+)"',
    "Action to be Taken": r'"Action to be Taken":\s*"([^"]+)"',
    "Whatsapp Creation": r'"Whatsapp Creation":\s*"([^"]+)"'    
    }

    extracted_data = {}
    for key, pattern in patterns.items():
        match = re.search(pattern, llm_response, re.DOTALL)
        if match:
            extracted_data[key] = match.group(1)

    if len(extracted_data) > 0:
        print("EXTRACTED Data:", extracted_data)  
        return extracted_data
    else:
        return None


client_apigateway = boto3.client('apigatewaymanagementapi', region_name='ap-southeast-1', endpoint_url='https://13ixd4t1e3.execute-api.ap-southeast-1.amazonaws.com/production/')

def send_private_message(connectionId, body):
    print("SENDING PRIVATE MESSAGE")
    print(f"Connection ID: {connectionId}")
    print(f"Message Body: {body}")
    

    try:
        json_data = json.dumps(body)
        print(f"JSON Data: {json_data}")
        
        response = client_apigateway.post_to_connection(
            ConnectionId=connectionId, 
            Data=json_data.encode('utf-8')
        )
        return {
        'statusCode': 200,
        'body': json.dumps('Hello from Lambda!')
        }

        print(f"Send Response: {response}")
        
    except client_apigateway.exceptions.GoneException:
        print(f"Connection {connectionId} is closed")
    except Exception as e:
        print(f"Error sending message: {str(e)}")
    
    return True

client_bedrock = boto3.client('bedrock-agent-runtime', region_name=region_used)
#insurance sandbox code ends here


# banking sandbox code starts here .......


def banking_agent_invoke_tool(chat_history, session_id, chat, connectionId):
    try:
        # Start keepalive thread
        #keepalive_thread = send_keepalive(connectionId, 30)
        import uuid
        import random
        # Fetch base_prompt from the database as before
        select_query = f'''select base_prompt from {schema}.{prompt_metadata_table} where id =3;'''
        print(select_query)
        base_prompt = f'''
        You are a Virtual Banking Assistant for AnyBank SG, a helpful and accurate chatbot for banking customers. You help customers with their banking accounts, transactions, products, and related services.

## CRITICAL INSTRUCTIONS:
- **NEVER** reply with any message that says you are checking, looking up, or finding information (such as "I'll check that for you", "Let me look that up", "One moment", "I'll find out", etc.).
- **NEVER** say "To answer your question about [topic], let me check our knowledge base" or similar phrases.
- After using a tool, IMMEDIATELY provide only the direct answer or summary to the user, with no filler, no explanations, and no mention of checking or looking up.
- If a user asks a question that requires a tool, use the tool and reply ONLY with the answer or summary, never with any statement about the process.
- For general banking questions, IMMEDIATELY use the banking_faq_tool_schema tool WITHOUT any preliminary message.

## CUSTOMER AUTHENTICATION RULES:
- **ALWAYS** verify Customer ID and PIN before proceeding with any account-related tools
- **NEVER** proceed with get_account_summary or file_service_request without successful authentication
- **ONLY** use tools after confirming the Customer ID and PIN combination is valid
- If authentication fails, provide a clear error message and ask for correct credentials

## VALID CUSTOMER DATA:
Use these exact Customer ID and PIN combinations for verification:
- CUST1001 (Rachel Tan) - PIN: 1023
- CUST1002 (Jason Lim) - PIN: 7645
- CUST1003 (Mary Goh) - PIN: 3391
- CUST1004 (Daniel Ong) - PIN: 5912
- CUST1005 (Aisha Rahman) - PIN: 8830

## SESSION AUTHENTICATION STATE MANAGEMENT:
- **MAINTAIN SESSION STATE**: Once a Customer ID and PIN are successfully verified, store this authentication state for the ENTIRE conversation session
- **NEVER RE-ASK**: Do not ask for Customer ID or PIN again during the same session unless:
  1. User explicitly provides a different Customer ID
  2. Authentication explicitly fails during a tool call
  3. User explicitly requests to switch accounts

## AUTHENTICATION PERSISTENCE RULES:
- **FIRST AUTHENTICATION**: Ask for Customer ID and PIN only on the first account-related request
- **SESSION MEMORY**: Remember the authenticated Customer ID throughout the conversation
- **AUTOMATIC REUSE**: Use the stored authenticated credentials for ALL subsequent account-related tool calls
- **NO RE-VERIFICATION**: Do not re-verify credentials that have already been successfully authenticated in the current session

## PRE-AUTHENTICATION CHECK:
Before asking for Customer ID or PIN for ANY account-related request:
1. **Scan conversation history** for previously provided Customer ID
2. **Check if PIN was already verified** for that Customer ID in this session
3. **If both are found and verified**, proceed directly with stored credentials
4. **Only ask for credentials** that are missing or failed verification

## CUSTOMER ID AND PIN HANDLING RULES:
- **SESSION-LEVEL STORAGE**: Once Customer ID is provided and verified, use it for ALL subsequent requests
- **ONE-TIME PIN**: Ask for PIN only ONCE per Customer ID per session
- **CONVERSATION CONTEXT**: Check the ENTIRE conversation history for previously provided and verified credentials
- **SMART REUSE**: If user asks "I gave you before" or similar, acknowledge and proceed with stored credentials
- **CONTEXT AWARENESS**: Before asking for credentials, always check if they were provided earlier in the conversation
- When Customer ID is provided, validate it matches the pattern CUST#### (e.g., CUST1001)
- Use the same Customer ID and PIN for all subsequent tool calls in the session until Customer ID changes
- **ALWAYS** verify PIN matches the Customer ID before proceeding on first authentication only

## AUTHENTICATION PROCESS:
1. **Check Session State** - Scan conversation for existing authenticated credentials
2. **Collect Customer ID** - Ask for Customer ID ONLY if not previously provided and verified
3. **Validate Customer ID** - Check if it matches one of the valid Customer IDs above
4. **Collect PIN** - Ask for PIN ONLY if not previously provided and verified for current Customer ID
5. **Verify PIN** - Check if the PIN matches the Customer ID (only on first authentication)
6. **Store Authentication State** - Remember successful authentication for entire session
7. **Proceed with Tools** - Use stored credentials for all subsequent account-related requests

## MANDATORY QUESTION COLLECTION RULES:
- **ALWAYS** collect ALL required information for any tool before using it
- **NEVER** skip any required questions, even if the user provides some information
- **NEVER** assume or guess missing information
- **NEVER** proceed with incomplete information
- Ask questions ONE AT A TIME in this exact order:

### For get_account_summary tool:
1. **Check session state first** - Use stored Customer ID and PIN if already authenticated
2. Customer ID - if not already provided and verified in conversation
3. PIN (4-6 digit number) - only if not already provided and verified for current Customer ID
4. **VERIFY** Customer ID and PIN combination is valid (only on first authentication)
5. **ONLY** proceed with tool call after successful authentication

### For file_service_request tool (ask in this exact order):
1. **Check session state first** - Use stored Customer ID and PIN if already authenticated
2. Customer ID - if not already provided and verified in conversation
3. PIN (4-6 digit number) - only if not already provided and verified for current Customer ID
4. **VERIFY** Customer ID and PIN combination is valid (only on first authentication)
5. Category (Card Issue, Transaction Dispute, Account Update, etc.)
6. Description of the issue/request
7. Preferred contact method (Phone, Email, or WhatsApp)
8. **ONLY** proceed with tool call after successful authentication

## INPUT VALIDATION RULES:
- **NEVER** ask for the same Customer ID twice in a session unless user provides different one
- **NEVER** ask for PIN twice for the same Customer ID in a session
- Accept Customer ID in format CUST#### only
- Accept PIN as 4 digit numeric value
- Accept any reasonable category for service requests
- **NEVER** ask for specific formats - accept what the user provides
- If validation fails, provide a clear, specific error message with examples
- **ALWAYS** verify PIN matches the Customer ID before proceeding (only on first authentication)

##NATURAL DATE INTERPRETATION RULE:
- When collecting a date or time-related input, accept natural expressions such as:
	
	“yesterday”, “today”, “tomorrow”, “last night”, etc.
	
- Convert these into actual calendar dates based on the current date.
	
- If a time of day is mentioned (e.g., “yesterday evening”), assign a random time in that time range:
	
	Morning: 8am–12pm
	
	Afternoon: 1pm–5pm
	
	Evening: 6pm–9pm
	
	Night: 9pm–11pm
	
- Examples:
	
	“yesterday” → 2025-07-30
	
	“today afternoon” → 2025-07-31, 2:34 PM (randomized)
	
	“tomorrow morning” → 2025-08-01, 9:12 AM (randomized)


## AUTHENTICATION ERROR MESSAGES:
- If Customer ID is invalid: "Invalid Customer ID. Please provide a valid Customer ID (e.g., CUST1001)."
- If PIN is incorrect: "Incorrect PIN for Customer ID [CUST####]. Please try again."
- If both are wrong: "Invalid Customer ID and PIN combination. Please check your credentials and try again."

## Tool Usage Rules:
- When a user asks about account balances, card dues, loan details, or account summary, use get_account_summary tool **AFTER** authentication (use stored credentials if available)
- When a user needs help with issues, complaints, or service requests, use file_service_request tool **AFTER** authentication (use stored credentials if available)
- For general banking questions about products, features, or procedures, use the banking_faq_tool_schema tool
- Do NOT announce that you're using tools or searching for information
- Simply use the tool and provide the direct answer

## Response Format:
- ALWAYS answer in the shortest, most direct way possible
- Do NOT add extra greetings, confirmations, or explanations
- Do NOT mention backend systems or tools
- Speak naturally as a helpful banking representative who already knows the information
- TOOL RESPONSE SUMMARY RULE:
After completing any tool call (such as retrieving an account summary or filing a service request), always include a clear summary of all user-provided inputs involved in that flow, followed by the final result (e.g., account summary or service request confirmation).

The summary must include:

All collected fields in the order they were asked

The tool output (e.g., account details or service request ID)

Example (for a service request):

Your service request has been filed.
- Customer ID: CUST1001
- Category: Card Issue
- Description: My debit card got blocked after entering wrong PIN
- Preferred Contact Method: Phone
- Request ID: SRV23891

Available Tools:
1. get_account_summary - Retrieve customer's financial summary across all accounts (requires authentication)
2. file_service_request - File customer service requests for follow-up by support team (requires authentication)
3. banking_faq_tool_schema - Retrieve answers from the banking knowledge base

## SYSTEMATIC QUESTION COLLECTION:
- When a user wants account information or needs to file a service request, IMMEDIATELY check session state for existing authentication
- If already authenticated in session, proceed directly with remaining required information
- Ask ONLY ONE question at a time
- After each user response, check what information is still missing
- Ask for the NEXT missing required field (in the exact order listed above)
- Do NOT ask multiple questions in one message
- Do NOT skip any required questions
- Do NOT proceed until ALL required information is collected
- **ALWAYS** use stored authentication if available, verify authentication before proceeding with tools only on first authentication

## EXAMPLES OF CORRECT BEHAVIOR:

**First Account-Related Request:**
User: "What's my account balance?"
Assistant: "What is your Customer ID?"

User: "CUST1001"
Assistant: "Please enter your 4 digit PIN."

User: "1023"
Assistant: [Verify CUST1001 + 1023 is valid, store authentication state, then use get_account_summary tool and provide account summary]

**Subsequent Account-Related Requests in Same Session:**
User: "What are your loan interest rates?"
Assistant: [Use banking_faq_tool_schema tool and provide loan information]

User: "Show me my credit card details too"
Assistant: [Use get_account_summary tool with stored Customer ID and PIN - no need to ask again]

User: "I need help with a blocked card"
Assistant: "What category best describes your issue? (e.g., Card Issue, Transaction Dispute, Account Update)"
[Uses stored CUST1001 authentication, only asks for service request details]

**Different Customer ID in Same Session:**
User: "Can you check account for CUST1002?"
Assistant: "Please enter your 4 digit PIN for Customer ID CUST1002."

## EXAMPLES OF INCORRECT BEHAVIOR:
- ❌ "What's your Customer ID, PIN, and issue description?" (asking multiple questions)
- ❌ Asking for Customer ID again after it was already provided and verified in the session
- ❌ Asking for PIN again for the same Customer ID in the same session
- ❌ Skipping PIN verification on first authentication
- ❌ Proceeding with incomplete information
- ❌ Not checking conversation history for existing authentication
- ❌ Re-asking for credentials after using FAQ tool

## SECURITY GUIDELINES:
- Require PIN verification only once per Customer ID in each session
- Never store or reference PIN values in conversation history for security
- If user switches to a different Customer ID, ask for the corresponding PIN
- Treat all financial information as sensitive and confidential
- **ALWAYS** verify Customer ID and PIN combination before first account access
- **MAINTAIN** authentication state throughout session for user experience

## PRODUCT KNOWLEDGE:
You have access to comprehensive information about AnyBank SG products including:
- Savings Accounts (eSaver Plus, Young Savers)
- Current Accounts (Everyday Current, Expat Current)
- Credit Cards (Rewards+, Cashback Max)
- Loans (Personal Loan, HDB Home Loan)
- Digital banking features and services

## RESPONSE GUIDELINES:
- Handle greetings warmly and ask how you can help with their banking needs today
- For product inquiries, provide specific details from the knowledge base
- For account-specific queries, always use appropriate tools with proper authentication
- For service issues, efficiently collect information and file requests
- Keep responses concise and actionable
- Never leave users without a clear next step or resolution

## WHATSAPP MESSAGE FORMATTING:
- Write WhatsApp messages as natural, conversational text
- Use proper paragraph spacing instead of \n characters
- Avoid any escape sequences or formatting codes
- Keep messages clean and readable without technical formatting
- Use natural line breaks and spacing for readability
- **NEVER** include literal \n characters in WhatsApp messages
- Use actual line breaks and proper spacing for message formatting
- Ensure WhatsApp messages are formatted naturally without escape sequences
- **CRITICAL**: When generating WhatsApp messages, use actual line breaks and spacing, NOT escape sequences
- Format messages with natural paragraph breaks and proper spacing
- Write messages exactly as they should appear to the user, without any technical formatting codes
'''
        print(base_prompt)
        print('base_prompt is fetched from db')
        
        # Banking tool schema
        banking_tools = [
            {
                "name": "get_account_summary",
                "description": "Retrieve customer's financial summary across savings, current, credit card, and loan accounts",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "customer_id": {"type": "string", "description": "Unique customer identifier (e.g., CUST1001)"},
                        "pin": {"type": "string", "description": "4-6 digit numeric PIN for authentication"}
                    },
                    "required": ["customer_id", "pin"]
                }
            },
            {
                "name": "file_service_request",
                "description": "File a customer service request for follow-up by AnyBank SG's support team",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "customer_id": {"type": "string", "description": "Unique customer identifier (e.g., CUST1001)"},
                        "pin": {"type": "string", "description": "4-digit PIN for authentication"},
                        "category": {"type": "string", "description": "Type of request (e.g., Card Issue, Transaction Dispute, Account Update)"},
                        "description": {"type": "string", "description": "User-provided description of the issue/request"},
                        "preferred_contact_method": {"type": "string", "description": "User's preferred way of follow-up: Phone, Email, or WhatsApp"}
                    },
                    "required": ["customer_id", "pin", "category", "description", "preferred_contact_method"]
                }
            },
            {
                "name": "banking_faq_tool_schema",
                "description": "Retrieve answers from the banking knowledge base for general banking questions, policies, and procedures",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "knowledge_base_retrieval_question": {"type": "string", "description": "A question to retrieve from the banking knowledge base about banking services, policies, procedures, or general information."}
                    },
                    "required": ["knowledge_base_retrieval_question"]
                }
            }
        ]
        # --- Customer Authentication Data ---
        valid_customers = {
            "CUST1001": {"name": "Rachel Tan", "pin": "1023"},
            "CUST1002": {"name": "Jason Lim", "pin": "7645"},
            "CUST1003": {"name": "Mary Goh", "pin": "3391"},
            "CUST1004": {"name": "Daniel Ong", "pin": "5912"},
            "CUST1005": {"name": "Aisha Rahman", "pin": "8830"}
        }

        def authenticate_customer(customer_id, pin):
            """Authenticate customer ID and PIN combination"""
            if customer_id not in valid_customers:
                return False, "Invalid Customer ID. Please provide a valid Customer ID (e.g., CUST1001)."
            
            if valid_customers[customer_id]["pin"] != pin:
                return False, f"Incorrect PIN for Customer ID {customer_id}. Please try again."
            
            return True, f"Authentication successful for {valid_customers[customer_id]['name']}"


        # --- Mock banking tool implementations ---
        def get_account_summary(customer_id, pin):
            # Authenticate customer first
            auth_success, auth_message = authenticate_customer(customer_id, pin)
            if not auth_success:
                return {"error": auth_message}
            mock_accounts = {
                "CUST1001": [
                    {
                        "account_type": "savings",
                        "account_name": "eSaver Plus",
                        "account_number": "XXXXXX4321",
                        "balance": 10452.75,
                        "currency": "SGD",
                        "status": "Active"
                    },
                    {
                        "account_type": "credit_card",
                        "account_name": "Rewards+ Card",
                        "account_number": "XXXXXX9876",
                        "outstanding_balance": 1880.10,
                        "credit_limit": 12000.00,
                        "payment_due_date": get_dynamic_date(3),
                        "currency": "SGD",
                        "status": "Active"
                    },
                    {
                        "account_type": "loan",
                        "account_name": "Personal Loan",
                        "account_number": "LN-984521",
                        "outstanding_balance": 14600.00,
                        "monthly_installment": 630.50,
                        "tenure_remaining_months": 28,
                        "interest_rate": "7.2% EIR",
                        "currency": "SGD",
                        "status": "Active"
                    }
                ],
                "CUST1002": [
                    {
                        "account_type": "savings",
                        "account_name": "Young Savers",
                        "account_number": "XXXXXX3344",
                        "balance": 1850.40,
                        "currency": "SGD",
                        "status": "Active"
                    },
                    {
                        "account_type": "credit_card",
                        "account_name": "Cashback Max Card",
                        "account_number": "XXXXXX6543",
                        "outstanding_balance": 390.20,
                        "credit_limit": 6000.00,
                        "payment_due_date": get_dynamic_date(3),
                        "currency": "SGD",
                        "status": "Active"
                    }
                ],
                "CUST1003": [
                    {
                        "account_type": "current",
                        "account_name": "Everyday Current Account",
                        "account_number": "XXXXXX2233",
                        "balance": 4250.00,
                        "currency": "SGD",
                        "status": "Active"
                    }
                ],
                "CUST1004": [
                    {
                        "account_type": "loan",
                        "account_name": "HDB Home Loan",
                        "account_number": "LN-225577",
                        "outstanding_balance": 285000.00,
                        "monthly_installment": 1450.00,
                        "tenure_remaining_months": 180,
                        "interest_rate": "2.50% (Fixed)",
                        "currency": "SGD",
                        "status": "Active"
                    }
                ],
                "CUST1005": [
                    {
                        "account_type": "credit_card",
                        "account_name": "Rewards+ Card",
                        "account_number": "XXXXXX7890",
                        "outstanding_balance": 0.00,
                        "credit_limit": 10000.00,
                        "payment_due_date": get_dynamic_date(3),
                        "currency": "SGD",
                        "status": "Active"
                    }
                ]
            }
            return mock_accounts.get(customer_id, [])

        def file_service_request(customer_id, pin, category, description, preferred_contact_method):
        
            # Authenticate customer first
            auth_success, auth_message = authenticate_customer(customer_id, pin)
            if not auth_success:
                return {"error": auth_message}
            ticket_id = f"SRQ-{str(uuid.uuid4())[:6].upper()}"
            return {
                "ticket_id": ticket_id,
                "status": "Received",
                "assigned_team": "Customer Support – Cards",
                "expected_callback": get_dynamic_datetime(2),
                "summary": f"{category} issue filed for review"
            }


        def get_banking_faq_chunks(query):
            try:
                print("IN BANKING FAQ: ", query)
                chunks = []
                # Use the banking knowledge base ID from environment
                banking_kb_id = os.environ['bank_kb_id']
                response_chunks = retrieve_client.retrieve(
                    retrievalQuery={                                                                                
                        'text': query
                    },
                    knowledgeBaseId=banking_kb_id,
                    retrievalConfiguration={
                        'vectorSearchConfiguration': {                          
                            'numberOfResults': 10,                                                                                              
                            'overrideSearchType': 'HYBRID'
                        }
                    }
                )
               
                for item in response_chunks['retrievalResults']:
                    if 'content' in item and 'text' in item['content']:
                        chunks.append(item['content']['text'])
                print('BANKING FAQ CHUNKS: ', chunks)  
                return chunks
            except Exception as e:
                print("An exception occurred while retrieving banking FAQ chunks:", e)
                return []
    

        input_tokens = 0
        output_tokens = 0
        print("In banking_agent_invoke_tool (Banking Bot)")

        
        # Extract customer ID and PIN from chat history
        extracted_customer_id = None
        extracted_pin = None
        
        for message in chat_history:
            if message['role'] == 'user':
                content_text = message['content'][0]['text']
                
                # Extract customer ID (CUST followed by 4 digits)
                customer_id_match = re.search(r'\b(CUST\d{4})\b', content_text.upper())
                if customer_id_match:
                    extracted_customer_id = customer_id_match.group(1)
                    print(f"Extracted Customer ID from chat history: {extracted_customer_id}")
                
                # Extract PIN (4-6 digit number) - look for patterns like "PIN 1234" or just "1234"
                pin_match = re.search(r'\b(\d{4,6})\b', content_text)
                if pin_match:
                    # Additional check to make sure it's likely a PIN (not part of other numbers)
                    potential_pin = pin_match.group(1)
                    # If it's 4-6 digits and not part of a larger number, consider it a PIN
                    if len(potential_pin) >= 4 and len(potential_pin) <= 6:
                        extracted_pin = potential_pin
                        print(f"Extracted PIN from chat history: {extracted_pin}")
                
                # If we found both, we can break
                if extracted_customer_id and extracted_pin:
                    break
        
        # Enhance system prompt with customer ID and PIN context
        if extracted_customer_id and extracted_pin:
            enhanced_prompt = base_prompt + f"\n\nIMPORTANT: The customer's ID is {extracted_customer_id} and PIN is {extracted_pin}. Use these credentials automatically for any tool calls that require them without asking again."
            print(f"Enhanced prompt with Customer ID: {extracted_customer_id} and PIN: {extracted_pin}")
        elif extracted_customer_id:
            enhanced_prompt = base_prompt + f"\n\nIMPORTANT: The customer's ID is {extracted_customer_id}. Use this ID automatically for any tool calls that require it without asking again."
            print(f"Enhanced prompt with Customer ID: {extracted_customer_id}")
        elif extracted_pin:
            enhanced_prompt = base_prompt + f"\n\nIMPORTANT: The customer's PIN is {extracted_pin}. Use this PIN automatically for any tool calls that require it without asking again."
            print(f"Enhanced prompt with PIN: {extracted_pin}")
        else:
            enhanced_prompt = base_prompt
        
        # Use the enhanced_prompt instead of base_prompt
        prompt = enhanced_prompt
        
        # First API call to get initial response
        try:
            response = bedrock_client.invoke_model_with_response_stream(
                contentType='application/json',
                body=json.dumps({
                    "anthropic_version": "bedrock-2023-05-31",
                    "max_tokens": 4000,
                    "temperature": 0,
                    "top_p": 0.999,
                    "top_k": 250,
                    "system": prompt,
                    "tools": banking_tools,
                    "messages": chat_history
                }),
                modelId=model_id
            )
        except Exception as e:
            print("AN ERROR OCCURRED : ", e)
            response = "We are unable to assist right now please try again after few minutes"
            return {"answer": response, "question": chat, "session_id": session_id}

        streamed_content = ''
        content_block = None
        assistant_response = []
        for item in response['body']:
            content = json.loads(item['chunk']['bytes'].decode())
            if content['type'] == 'content_block_start':
                content_block = content['content_block']
            elif content['type'] == 'content_block_stop':
                print(f"Content block at stop: {content_block}")  # Add debug line
                try:
                    api_gateway_client.post_to_connection(ConnectionId=connectionId, Data=json.dumps(content))
                except api_gateway_client.exceptions.GoneException:
                    print(f"Connection {connectionId} is closed (GoneException) - stop message")
                except Exception as e:
                    print(f"WebSocket send error (stop): {e}")
                if content_block['type'] == 'text':
                    content_block['text'] = streamed_content
                    assistant_response.append(content_block)
                elif content_block['type'] == 'tool_use':
                    try:
                        content_block['input'] = json.loads(streamed_content)
                    except json.JSONDecodeError as e:
                        print(f"JSON decode error for tool input: {e}")
                        print(f"Streamed content: {streamed_content}")
                        content_block['input'] = {}
                    assistant_response.append(content_block)
                streamed_content = ''
            elif content['type'] == 'content_block_delta':
                try:
                    api_gateway_client.post_to_connection(ConnectionId=connectionId, Data=json.dumps(content))
                except api_gateway_client.exceptions.GoneException:
                    print(f"Connection {connectionId} is closed (GoneException) - delta message")
                except Exception as e:
                    print(f"WebSocket send error (delta): {e}")
                if 'delta' in content and isinstance(content['delta'], dict):
                    if content['delta']['type'] == 'text_delta':
                        streamed_content += content['delta']['text']
                    elif content['delta']['type'] == 'input_json_delta':
                        streamed_content += content['delta']['partial_json']
            elif content['type'] == 'message_delta':
                try:
                    if 'usage' in content and isinstance(content['usage'], dict):
                        tool_tokens = content['usage']['output_tokens']
                    else:
                        tool_tokens = 0
                except (KeyError, TypeError) as e:
                    print(f"Error accessing usage tokens: {e}")
                    tool_tokens = 0
            elif content['type'] == 'message_stop':
                input_tokens += content['amazon-bedrock-invocationMetrics']['inputTokenCount']
                output_tokens += content['amazon-bedrock-invocationMetrics']['outputTokenCount']
        chat_history.append({'role': 'assistant', 'content': assistant_response})
        
        # Check if any tools were called
        tools_used = []
        tool_results = []
        
        for action in assistant_response:
            if action['type'] == 'tool_use':
                tools_used.append(action['name'])
                tool_name = action['name']
                tool_input = action['input']
                tool_result = None
                
                # Send a heartbeat to keep WebSocket alive during tool execution
                try:
                    heartbeat = {'type': 'heartbeat'}
                    api_gateway_client.post_to_connection(ConnectionId=connectionId, Data=json.dumps(heartbeat))
                except Exception as e:
                    print(f"Heartbeat send error: {e}")
                
                # Execute the appropriate banking tool
                if tool_name == 'get_account_summary':
                    print("get_account_summary is called..")
                    tool_result = get_account_summary(tool_input['customer_id'], tool_input['pin'])
                    # Check for authentication error
                    if isinstance(tool_result, dict) and 'error' in tool_result:
                        print(f"Authentication failed for get_account_summary: {tool_result['error']}")
                elif tool_name == 'file_service_request':
                    tool_result = file_service_request(
                        tool_input['customer_id'],
                        tool_input['pin'],
                        tool_input['category'],
                        tool_input['description'],
                        tool_input['preferred_contact_method']
                    )
                    # Check for authentication error
                    if isinstance(tool_result, dict) and 'error' in tool_result:
                        print(f"Authentication failed for file_service_request: {tool_result['error']}")
                elif tool_name == 'banking_faq_tool_schema':
                    print("banking_faq is called ...")
                    # Send another heartbeat before FAQ retrieval
                    try:
                        heartbeat = {'type': 'heartbeat'}
                        api_gateway_client.post_to_connection(ConnectionId=connectionId, Data=json.dumps(heartbeat))
                    except Exception as e:
                        print(f"Banking FAQ heartbeat send error: {e}")
                    
                    tool_result = get_banking_faq_chunks(tool_input['knowledge_base_retrieval_question'])
                    
                    # If FAQ tool returns empty or no results, provide fallback
                    if not tool_result or len(tool_result) == 0:
                        tool_result = ["I don't have specific information about that in our current banking knowledge base. Let me schedule a callback with one of our banking agents who can provide detailed information."]
                
                # Create tool result message with better error handling
                try:
                    if not isinstance(action, dict):
                        print(f"Action is not a dict: {type(action)}, value: {action}")
                        continue
                        
                    if 'id' not in action:
                        print(f"Action missing 'id' field: {action}")
                        continue
                    
                    print(f"Tool result type: {type(tool_result)}")
                    print(f"Tool result content: {tool_result}")
                    
                    if isinstance(tool_result, list) and tool_result:
                        content_text = "\n".join(tool_result)
                    elif isinstance(tool_result, list) and not tool_result:
                        content_text = "No information available"
                    else:
                        content_text = str(tool_result) if tool_result else "No information available"
                    
                    tool_response_dict = {
                        "type": "tool_result",
                        "tool_use_id": action['id'],
                        "content": [{"type": "text", "text": content_text}]
                    }
                    tool_results.append(tool_response_dict)
                    
                except Exception as e:
                    print(f"Error creating tool response: {e}")
                    print(f"Action type: {type(action)}")
                    print(f"Action content: {action}")
                    # Skip this tool result instead of crashing
                    continue
                tool_results.append(tool_response_dict)
        
        # If tools were used, add tool results to chat history and make second API call
        if tools_used:
            # Add tool results to chat history
            chat_history.append({'role': 'user', 'content': tool_results})
            
            # Make second API call with tool results
            try:
                response = bedrock_client.invoke_model_with_response_stream(
                    contentType='application/json',
                    body=json.dumps({
                        "anthropic_version": "bedrock-2023-05-31",
                        "max_tokens": 4000,
                        "temperature": 0,
                        "system": prompt,
                        "tools": banking_tools,
                        "messages": chat_history
                    }),
                    modelId=model_id
                )
            except Exception as e:
                print("ERROR IN SECOND API CALL:", e)
                # Send error response via WebSocket
                error_response = "I apologize, but I'm having trouble accessing that information right now. Please try again in a moment."
                for word in error_response.split():
                    delta = {'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'text_delta', 'text': word + ' '}}
                    try:
                        api_gateway_client.post_to_connection(ConnectionId=connectionId, Data=json.dumps(delta))
                    except:
                        pass
                stop_answer = {'type': 'content_block_stop', 'index': 0}
                try:
                    api_gateway_client.post_to_connection(ConnectionId=connectionId, Data=json.dumps(stop_answer))
                except:
                    pass
                return {"answer": error_response, "question": chat, "session_id": session_id}

            # Process the streaming response
            streamed_content = ''
            content_block = None
            assistant_response = []
            
            for item in response['body']:
                content = json.loads(item['chunk']['bytes'].decode())
                if content['type'] == 'content_block_start':
                    content_block = content['content_block']
                elif content['type'] == 'content_block_stop':
                    try:
                        api_gateway_client.post_to_connection(ConnectionId=connectionId, Data=json.dumps(content))
                    except api_gateway_client.exceptions.GoneException:
                        print(f"Connection {connectionId} is closed (GoneException) - stop message (tool)")
                    except Exception as e:
                        print(f"WebSocket send error (stop, tool): {e}")
                    if content_block['type'] == 'text':
                        content_block['text'] = streamed_content
                        assistant_response.append(content_block)
                    elif content_block['type'] == 'tool_use':
                        try:
                            content_block['input'] = json.loads(streamed_content)
                        except json.JSONDecodeError as e:
                            print(f"JSON decode error for tool input: {e}")
                            print(f"Streamed content: {streamed_content}")
                            content_block['input'] = {}
                        assistant_response.append(content_block)
                    streamed_content = ''
                elif content['type'] == 'content_block_delta':
                    try:
                        api_gateway_client.post_to_connection(ConnectionId=connectionId, Data=json.dumps(content))
                    except api_gateway_client.exceptions.GoneException:
                        print(f"Connection {connectionId} is closed (GoneException) - delta message (tool)")
                    except Exception as e:
                        print(f"WebSocket send error (delta, tool): {e}")
                    if content['delta']['type'] == 'text_delta':
                        streamed_content += content['delta']['text']
                    elif content['delta']['type'] == 'input_json_delta':
                        streamed_content += content['delta']['partial_json']
                elif content['type'] == 'message_stop':
                    input_tokens += content['amazon-bedrock-invocationMetrics']['inputTokenCount']
                    output_tokens += content['amazon-bedrock-invocationMetrics']['outputTokenCount']
            
            # Extract final answer
            final_ans = ""
            for i in assistant_response:
                if i['type'] == 'text':
                    final_ans = i['text']
                    break
            
            # If no text response, provide fallback
            if not final_ans:
                final_ans = "I apologize, but I couldn't retrieve the information at this time. Please try again or contact our support team."
            
            return {"statusCode": "200", "answer": final_ans, "question": chat, "session_id": session_id, "input_tokens": str(input_tokens), "output_tokens": str(output_tokens)}

        else:
            # No tools called, handle normal response
            for action in assistant_response:
                if action['type'] == 'text':
                    ai_response = action['text']
                    return {"statusCode": "200", "answer": ai_response, "question": chat, "session_id": session_id, "input_tokens": str(input_tokens), "output_tokens": str(output_tokens)}
            
            # Fallback if no text response
            return {"statusCode": "200", "answer": "I'm here to help with your banking needs. How can I assist you today?", "question": chat, "session_id": session_id, "input_tokens": str(input_tokens), "output_tokens": str(output_tokens)}
    except Exception as e:
        print(f"Unexpected error: {e}")
        response = "An Unknown error occurred. Please try again after some time."
        return {
            "statusCode": "500",
            "answer": response,
            "question": chat,
            "session_id": session_id,
            "input_tokens": "0",
            "output_tokens": "0"
        }
def generate_risk_sandbox(event):

    print("RISKKKKKKKKKKKK")

    applicant_profile = event.get("applicant_profile", {})
    financials = event.get("financials", {})
    loan_details = event.get("loan_details", {})
    collateral = event.get("collateral", {})
    agent_comments = event.get("agent_comments", "")

    # Applicant Profile
    name = applicant_profile.get("name", "")
    age = applicant_profile.get("age", 0)
    occupation = applicant_profile.get("occupation", "")
    credit_score = applicant_profile.get("credit_score", 0)

    # Financial Details
    monthly_income = financials.get("monthly_income", 0)
    existing_emis = financials.get("existing_emis", 0)
    if existing_emis==None:
        existing_emis=0
    print("jcbsinsduckjcjjjjjjjj",existing_emis)
    net_pay = financials.get("net_pay", monthly_income - existing_emis)

    # Loan Details
    loan_type = loan_details.get("loan_type", "Secured Personal Loan")
    loan_purpose = loan_details.get("loan_purpose", "")
    requested_amount = loan_details.get("requested_amount", 0)
    tenure_months = loan_details.get("tenure_months", 0)
    interest_rate = loan_details.get("interest_rate", 0)

    # Collateral Details
    collateral_type = collateral.get("type", "")
    collateral_description = collateral.get("description", "")
    market_value = collateral.get("market_value", 0)
    condition = collateral.get("condition", "")
    ownership_proof = collateral.get("ownership_proof", "")
    # Initialize Bedrock client
    bedrock = boto3.client('bedrock-runtime', region_name=region_used)
    
    prompt = f'''
You are a financial risk assessment engine and your role is to evaluate the risk of lending based on the provided applicant, financial, loan, and collateral data, along with optional free-text comments from the agent. Use this to generate a clear, consistent, and structured response for the agent to make a lending decision.

APPLICANT DATA:
- Name: {name}
- Age: {age}
- Occupation: {occupation}
- Credit Score: {credit_score}
- Monthly Income: SGD {monthly_income}
- Existing EMIs: SGD {existing_emis}
- Loan Type: {loan_type}
- Loan Purpose: {loan_purpose if 'loan_purpose' in locals() else ''}
- Requested Amount: SGD {requested_amount}
- Tenure: {tenure_months} months
- Interest Rate: {interest_rate}%

COLLATERAL DATA:
- Type: {collateral_type}
- Description: {collateral_description}
- Market Value: SGD {market_value}
- Condition: {condition}
- Ownership Proof: {ownership_proof}

AGENT COMMENTS: {agent_comments}

IMPORTANT: Calculate all metrics yourself using the provided inputs.

EMI Calculation Formula: [P*r*(1+r)^n]/[(1+r)^n-1]
Where P = Principal, r = monthly interest rate (annual_rate/12/100), n = tenure in months

### RISK RULES TO APPLY

- **EMI Formula**: [P*r*(1 + r)^n] / [(1 + r)^n - 1], where r = monthly interest rate, n = months
- **DTI Ratio** = (existing_emis + calculated_emi) / monthly_income*100
- **LTV Ratio** = requested_amount / market_value*100

#### Risk Score Interpretation:
- 0-30 → Low Risk
- 31-60 → Medium Risk
- 61-100 → High Risk

#### Heuristics:
- DTI > 55% → High Risk
- LTV > 70% → High Risk
- Collateral liquidity: Gold > Vehicle > Watch > Bag
- Missing income or credit score → Use collateral strength to fallback
- Agent comments may justify or challenge defaults (e.g., "repeat borrower", "asset verified in person")

Return response in this exact JSON structure:

{{
  "calculated_metrics": {{
    "emi_requested_loan": <calculated_emi>,
    "total_emi_burden": <existing_emis + calculated_emi>,
    "dti_ratio": "<percentage>%",
    "ltv_ratio": "<percentage>%"
  }},
  "risk_assessment": {{
    "risk_score": <score_0_to_100>,
    "risk_level": "Low|Medium|High",
    "risk_summary": "Brief 2-3 line summary of overall risk"
  }},
  "risk_factors": [
    {{
      "factor": "Factor name",
      "severity": "Low|Medium|High",
      "description": "Brief explanation",
      "impact": "How it affects the assessment"
    }}
  ],
  "recommendations": {{
    "primary_recommendation": "APPROVE|CONDITIONAL_APPROVE|DECLINE",
    "conditions": ["Condition 1", "Condition 2"],
    "alternative_options": [
      {{
        "option": "Option name",
        "details": "Specific recommendation"
      }}
    ]
  }},
  "approval_scenarios": [
    {{
      "scenario_name": "Scenario Name",
      "loan_amount": <amount>,
      "tenure": <months>,
      "emi": <emi>,
      "ltv_ratio": "<percentage>%",
      "dti_ratio": "<percentage>%",
      "conditions": ["condition1", "condition2"]
    }}
  ],
  "agent_comment_influence": {{
    "applied_adjustment": "Yes|No",
    "impact_summary": "If and how the agent's remarks affected risk assessment"
  }}
}}

Important: Return only the JSON response, no additional text, no markdown formatting, no code blocks, no explanations.
'''

    body = json.dumps({
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 2048,
        "messages": [{"role": "user", "content": prompt}]
    })
    
    response = bedrock.invoke_model(
        modelId="us.anthropic.claude-3-7-sonnet-20250219-v1:0",
        body=body,
    )
    
    final_text = str(json.loads(response.get("body").read())["content"][0]["text"])
    print("LLM OUTPUT:", final_text)  # Debug print

    # Try to extract JSON substring if extra text is present
    import re
    match = re.search(r'({.*})', final_text, re.DOTALL)
    if match:
        json_str = match.group(1)
    else:
        json_str = final_text  # fallback

    return json.loads(json_str)
# banking function code ends here .....

#healthcare function code starts here .....

def lambda_handler(event, context):
    global user_intent_flag, overall_flow_flag, ub_number, ub_user_name, pop, str_intent,json
    print("Event: ",event)
    event_type=event['event_type']
    print("Event_type: ",event_type)
    conv_id = ""
    
    # OpenSearch Visual Product Search Functions (defined inside lambda_handler)
    def create_opensearch_client():
        """Create and return OpenSearch client with AWS authentication"""
        region = region_used
        HOST = OPENSEARCH_HOST.replace('https://', '').replace('http://', '')
        INDEX_NAME = OPENSEARCH_INDEX
        from opensearchpy import OpenSearch, RequestsHttpConnection
        from requests_aws4auth import AWS4Auth
        # Use IAM role authentication for Lambda
        import boto3
        
        # Get credentials from the current session (which already has session credentials)
        session = boto3.Session()
        credentials = session.get_credentials()
        
        if credentials is None:
            raise Exception("No AWS credentials found. Please ensure Lambda has proper IAM role attached.")
        
        # Use the existing session credentials
        auth = AWS4Auth(
            credentials.access_key,
            credentials.secret_key,
            region,
            'aoss',
            session_token=credentials.token
        )

        client = OpenSearch(
            hosts=[{'host': HOST, 'port': 443}],
            http_auth=auth,
            use_ssl=True,
            verify_certs=True,
            connection_class=RequestsHttpConnection,
            pool_maxsize=300,
            timeout=30,
            max_retries=3,
            retry_on_timeout=True
        )
        return client

    def get_text_embedding_bedrock(text):
        """Create text embedding using Bedrock Titan"""
        try:
            from botocore.config import Config
            import boto3
            
            config = Config(
                retries={
                    'max_attempts': 3,
                    'mode': 'standard'
                }
            )
            
            bedrock_client = boto3.client("bedrock-runtime",
                                          region_name=region_used,
                                          config=config)
            
            body = {"inputText": text}
            response = bedrock_client.invoke_model(
                body=json.dumps(body),
                modelId="amazon.titan-embed-text-v1",
                accept="application/json",
                contentType="application/json",
            )
            result = json.loads(response['body'].read())
            embedding = result['embedding']
            
            # Ensure 1024 dimensions
            if len(embedding) == 1024:
                return embedding
            elif len(embedding) > 1024:
                print(f"Truncating embedding from {len(embedding)} to 1024 dimensions")
                return embedding[:1024]
            else:
                print(f"Padding embedding from {len(embedding)} to 1024 dimensions")
                return embedding + [0.0] * (1024 - len(embedding))
                
        except Exception as e:
            print(f"Error creating text embedding: {e}")
            return None

    def create_image_embedding(image_base64):
        """Create image embedding using Bedrock Titan or fallback to text description"""
        try:
            from botocore.config import Config
            import boto3
            
            config = Config(
                retries={
                    'max_attempts': 3,
                    'mode': 'standard'
                }
            )
            
            bedrock_client = boto3.client("bedrock-runtime",
                                          region_name=region_used,
                                          config=config)
            
            # Try to create image embedding first
            try:
                print("🔍 Attempting to use amazon.titan-embed-image-v1 model...")
                image_input = {"inputImage": image_base64}
                response = bedrock_client.invoke_model(
                    body=json.dumps(image_input),
                    modelId="amazon.titan-embed-image-v1",
                    accept="application/json",
                    contentType="application/json"
                )
                result = json.loads(response.get("body").read())
                embedding = result.get("embedding")
                
                if embedding is None:
                    print("No embedding returned from Bedrock")
                    return None
                
                # Ensure 1024 dimensions
                if len(embedding) == 1024:
                    return embedding
                elif len(embedding) > 1024:
                    print(f"Truncating image embedding from {len(embedding)} to 1024 dimensions")
                    return embedding[:1024]
                else:
                    print(f"Padding image embedding from {len(embedding)} to 1024 dimensions")
                    return embedding + [0.0] * (1024 - len(embedding))
                    
            except Exception as e:
                print(f"🔍 Image embedding error: {e}")
                if "AccessDeniedException" in str(e) and "amazon.titan-embed-image-v1" in str(e):
                    print("⚠️ Image embedding model not accessible. Using text description fallback...")
                    
                    # Fallback: Generate text description and use text embedding
                    try:
                        # Generate image description using Claude
                        claude_model_id = "us.anthropic.claude-3-7-sonnet-20250219-v1:0"
                        
                        system_prompt = '''
                        You are an image analysis agent. Analyze the product image and generate a concise product description.
                        Focus on the product's visual features, type, and characteristics.
                        Keep the description factual and relevant for product search.
                        '''
                        
                        response = bedrock_client.invoke_model(
                            contentType='application/json',
                            body=json.dumps({
                                "anthropic_version": "bedrock-2023-05-31",
                                "max_tokens": 200,
                                "temperature": 0,
                                "system": system_prompt,
                                "messages": [
                                    {
                                        "role": "user",
                                        "content": [
                                            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": image_base64}}
                                        ]
                                    }
                                ],
                            }),
                            modelId=claude_model_id
                        )
                        
                        response_body = json.loads(response['body'].read().decode('utf-8'))
                        description = response_body['content'][0]['text']
                        
                        print(f"Generated description: {description[:100]}...")
                        
                        # Use text embedding for the description
                        return get_text_embedding_bedrock(description)
                        
                    except Exception as fallback_error:
                        print(f"❌ Fallback text description failed: {fallback_error}")
                        return None
                else:
                    print(f"Error creating image embedding: {e}")
                    return None
                
        except Exception as e:
            print(f"Error in create_image_embedding: {e}")
            return None

    def search_products_text_opensearch(search_query, limit=5):
        """Search products using text query in OpenSearch"""
        try:
            client = create_opensearch_client()
            
            # Create text embedding
            search_vector = get_text_embedding_bedrock(search_query)
            if search_vector is None:
                print("Error creating text embedding")
                return []
            
            # Build search query - similar to ROXA_Search_Lambda.py
            body = {
                "size": limit,
                "_source": {
                    "exclude": ["vspmod"]  # Exclude vector field from response
                },
                "query": {
                    "knn": {
                        "vspmod": {
                            "vector": search_vector,
                            "k": limit
                        }
                    }
                },
                "_source": ["product_description", "s3_uri", "type"]
            }
            
            print("Searching OpenSearch for text query...")
            response = client.search(index=OPENSEARCH_INDEX, body=body)
            
            results = []
            for hit in response['hits']['hits']:
                score = hit['_score']
                source = hit['_source']
                
                results.append({
                    "score": score,
                    "product_description": source['product_description'],
                    "s3_uri": source['s3_uri'],
                    "type": source['type']
                })
            
            return results
            
        except Exception as e:
            print(f"Error during text search: {e}")
            return []

    def search_products_image_opensearch(image_base64, limit=5, search_image_uri=None):
        """
        Search products using image query in OpenSearch with improved accuracy and exact match detection
        
        Improvements:
        - Enhanced image validation and resizing using PIL (from refer.py)
        - Exact match detection by comparing S3 URIs
        - Better filtering with separate exact and similar match categories
        - Improved confidence thresholds for better result quality
        - Enhanced logging for debugging and monitoring
        """
        try:
            client = create_opensearch_client()
            
            # Create image embedding
            print(f"Creating image embedding for image of size: {len(image_base64)} characters")
            search_vector = create_image_embedding(image_base64)
            if search_vector is None:
                print("Error creating image embedding")
                return []
            print(f"Image embedding created successfully, vector length: {len(search_vector)}")
            
            # Build search query for image search with improved filtering
            body = {
                "size": limit * 10,  # Get more results to filter
                "_source": {
                    "exclude": ["vspmod"]  # Exclude vector field from response
                },
                "query": {
                    "bool": {
                        "must": {
                            "knn": {
                                "vspmod": {
                                    "vector": search_vector,
                                    "k": limit * 10
                                }
                            }
                        },
                        "filter": {
                            "term": {
                                "type": "image"  # Only search image embeddings
                            }
                        }
                    }
                },
                "_source": ["product_description", "s3_uri", "type"]
            }
            
            print("Searching OpenSearch for image query...")
            response = client.search(index=OPENSEARCH_INDEX, body=body)
            
            results = []
            for hit in response['hits']['hits']:
                score = hit['_score']
                source = hit['_source']
                
                # Improved threshold for image-to-image search - higher threshold for better accuracy
                if score < 0.5:  # Increased threshold for better image similarity
                    continue
                
                results.append({
                    "score": score,
                    "product_description": source['product_description'],
                    "s3_uri": source['s3_uri'],
                    "type": source['type']
                })
            
            # Enhanced exact match detection
            exact_matches = []
            similar_matches = []
            
            # Check for exact matches first (same S3 URI)
            if search_image_uri:
                print(f"🔍 Checking for exact matches with: {search_image_uri}")
                for hit in response['hits']['hits']:
                    score = hit['_score']
                    source = hit['_source']
                    result_uri = source['s3_uri']
                    
                    # Check for exact match
                    if result_uri == search_image_uri:
                        exact_matches.append({
                            "score": score,
                            "product_description": source['product_description'],
                            "s3_uri": source['s3_uri'],
                            "type": source['type'],
                            "match_type": "exact"
                        })
                        print(f"✅ Found exact match: {result_uri} with score: {score:.4f}")
            
            # Collect similar matches with high confidence
            for hit in response['hits']['hits']:
                score = hit['_score']
                source = hit['_source']
                result_uri = source['s3_uri']
                
                # Skip if this is already an exact match
                if search_image_uri and result_uri == search_image_uri:
                    continue
                
                # High confidence threshold for similar matches
                if score >= 0.6:
                    similar_matches.append({
                        "score": score,
                        "product_description": source['product_description'],
                        "s3_uri": source['s3_uri'],
                        "type": source['type'],
                        "match_type": "similar"
                    })
            
            # If no high-confidence similar matches, try with lower threshold
            if not similar_matches:
                print("No high-confidence similar matches found, trying with lower threshold...")
                for hit in response['hits']['hits']:
                    score = hit['_score']
                    source = hit['_source']
                    result_uri = source['s3_uri']
                    
                    # Skip if this is already an exact match
                    if search_image_uri and result_uri == search_image_uri:
                        continue
                    
                    if score >= 0.4:  # Lower threshold for similar matches
                        similar_matches.append({
                            "score": score,
                            "product_description": source['product_description'],
                            "s3_uri": source['s3_uri'],
                            "type": source['type'],
                            "match_type": "similar"
                        })
            
            # Combine results: exact matches first, then similar matches
            results = exact_matches + similar_matches
            
            # Sort by score within each category
            exact_matches.sort(key=lambda x: x['score'], reverse=True)
            similar_matches.sort(key=lambda x: x['score'], reverse=True)
            
            # Final results: exact matches + top similar matches
            final_results = exact_matches + similar_matches[:limit-len(exact_matches)]
            
            print(f"📊 Search Results Summary:")
            print(f"   - Exact matches: {len(exact_matches)}")
            print(f"   - Similar matches: {len(similar_matches)}")
            print(f"   - Final results: {len(final_results)}")
            
            return final_results[:limit]
            
        except Exception as e:
            print(f"Error during image search: {e}")
            return []

    def validate_search_results_with_llm(search_query, search_results):
        """
        Validate search results using LLM to check if they match available product categories
        Available categories: camera, shoe, headsets
        """
        import boto3
        print(f"🔍 DEBUG: Starting validate_search_results_with_llm function")
        print(f"🔍 DEBUG: search_query = {search_query}")
        print(f"🔍 DEBUG: search_results type = {type(search_results)}")
        print(f"🔍 DEBUG: search_results = {search_results}")
        
        try:
            print(f"🔍 DEBUG: Entering try block")
            
            # Define available product categories based on metadata files
            available_categories = {
                "camera": ["DSLR Camera", "camera", "photography", "dslr", "lens", "canon", "nikon"],
                "shoe": ["Sneakers", "footwear", "shoes", "comfort", "casual", "walking", "skechers"],
                "headsets": ["Gaming Headset", "headset", "audio", "gaming", "microphone", "headphones"]
            }
            
            print(f"🔍 DEBUG: Available categories defined")
            
            # Create prompt for LLM validation
            prompt = f"""
            You are a product search validator. Analyze the search query and search results to determine if they match the available product categories.

            SEARCH QUERY: {search_query}

            SEARCH RESULTS:
            {json.dumps(search_results, indent=2)}

              AVAILABLE PRODUCT CATEGORIES AND FEATURES:
            
            AUDIO PRODUCTS:
            - Gaming Headsets: RGB lighting, noise-canceling microphones, surround sound, adjustable headbands, compatible with PC/PS4/PS5/Xbox
            - Premium Headphones: High-fidelity audio, over-ear design, professional sound quality, comfortable ear cushions
            - Gaming Audio: Competitive gaming, esports, immersive audio experience, detachable microphones
            
            CAMERA PRODUCTS:
            - DSLR Cameras: Interchangeable lenses, manual controls, high-resolution sensors, professional photography
            - Mirrorless Cameras: Compact design, electronic viewfinders, 4K video recording, advanced autofocus
            - Digital Cameras: Point-and-shoot, automatic settings, built-in flash, easy-to-use interface
            
            FOOTWEAR PRODUCTS:
            - Running Shoes: Cushioned soles, breathable mesh, lightweight design, athletic performance
            - Comfort Shoes: Memory foam insoles, soft materials, everyday wear, casual style
            - Slip-on Shoes: Easy entry, no laces, casual comfort, versatile styling
            
            BRANDS AVAILABLE:
            - Camera Brands: Canon, Sony
            - Audio Brands: Sennheiser, Razer, Gaming Audio
            - Footwear: Various comfort and athletic brands
            
            SEARCH CAPABILITIES:
            - Text Search: Search by product name, brand, features, or description
            - Image Search: Upload product images to find similar items
            - Hybrid Search: Combine text and image queries for better results
            - Category Filtering: Filter by Audio, Camera, or Footwear categories
            - Feature Matching: Find products with specific features (noise-canceling, RGB, etc.)
            
            SEARCH EXAMPLES:
            - Audio: "gaming headset with microphone", "premium headphones", "noise-canceling audio"
            - Camera: "DSLR camera for photography", "mirrorless camera", "digital camera with zoom"
            - Footwear: "running shoes for athletes", "comfort shoes for walking", "casual sneakers"
            
            USE CASES:
            - Gaming: Find gaming headsets with RGB lighting and noise-canceling microphones
            - Professional: Locate high-quality cameras for photography or premium audio equipment
            - Fitness: Search for athletic footwear with proper cushioning and support
            - Casual: Find comfortable everyday shoes or basic audio equipment
            - Brand-Specific: Search for products from specific brands (Canon, Sony, Sennheiser, Razer)

              TASK: Determine if the search query is related to any of the available categories (Audio, Camera, Footwear) and provide relevant product recommendations based on features, brands, and user preferences. If no search results are provided, focus on validating the query terms themselves. Analyze the search context and provide detailed insights about product features, user intent, and potential alternatives.

            RESPONSE FORMAT:
            {{
                "is_valid": true/false,
                "original_query": "exact user search query",
                "matched_category": "Audio/Camera/Footwear/none",
                "confidence": "high/medium/low",
                "reasoning": "detailed explanation of why the search is valid or invalid",
                "product_features": "key features found in the search results",
                "brand_mentions": "brands identified in the search",
                "recommendations": "additional product suggestions or features to consider",
                "search_quality": "assessment of how well the results match the query",
                "user_intent": "inferred user intent (gaming, professional, casual, etc.)",
                "should_proceed": true/false
            }}

            Rules:
            1. Return true if the search query clearly relates to Audio, Camera, or Footwear categories
            2. If the search is ambiguous or doesn't match any category, return false
            3. For query-only validation (no search results provided), focus on the query terms themselves
            4. Be reasonable in validation - allow searches that match available categories
            5. Provide detailed reasoning and feature analysis for better user experience
            6. Identify user intent based on search terms and results
            7. Set should_proceed to true if the query matches available categories and has high/medium confidence
            8. Include the exact original query in the response for tracking purposes
            9. For camera searches, accept terms like "camera", "DSLR", "mirrorless", "Canon", "Sony", "photography"
            10. For audio searches, accept terms like "headphones", "headset", "audio", "gaming", "microphone"
            11. For footwear searches, accept terms like "shoes", "sneakers", "footwear", "running", "comfort"
            """

            print(f"🔍 DEBUG: Prompt created successfully")
            print(f"🔍 DEBUG: About to import boto3 and create bedrock_client")
            
            # Create a new bedrock_client for this function
            import boto3
            print(f"🔍 DEBUG: boto3 imported successfully")
            
            bedrock_client = boto3.client("bedrock-runtime", region_name=region_used)
            print(f"🔍 DEBUG: bedrock_client created successfully")
            
            print(f"🔍 DEBUG: About to invoke LLM model")
            
            # Invoke LLM for validation
            response = bedrock_client.invoke_model(
                contentType='application/json',
                body=json.dumps({
                    "anthropic_version": "bedrock-2023-05-31",
                    "max_tokens": 1000,
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": prompt}
                            ]
                        }
                    ],
                }),
                modelId="anthropic.claude-3-sonnet-20240229-v1:0"
            )
            
            print(f"🔍 DEBUG: LLM model invoked successfully")
            
            # Parse LLM response
            print(f"🔍 DEBUG: About to parse LLM response")
            inference_result = response['body'].read().decode('utf-8')
            print(f"🔍 DEBUG: inference_result = {inference_result}")
            final = json.loads(inference_result)
            print(f"🔍 DEBUG: final parsed successfully")
            llm_response = final['content'][0]['text']
            print(f"🔍 DEBUG: llm_response extracted = {llm_response}")
            
            print(f"🔍 LLM Validation Response: {llm_response}")
            
            # Parse JSON response from LLM
            print(f"🔍 DEBUG: About to parse JSON from LLM response")
            try:
                validation_result = json.loads(llm_response)
                print(f"🔍 DEBUG: JSON parsed successfully, returning validation_result")
                return validation_result
            except json.JSONDecodeError as json_error:
                print(f"❌ DEBUG: JSON decode error: {json_error}")
                print(f"❌ DEBUG: Failed to parse LLM response as JSON")
                # Fallback validation
                return {
                    "is_valid": False,
                    "original_query": search_query,
                    "matched_category": "none",
                    "confidence": "low",
                    "reasoning": "Failed to parse LLM validation response",
                    "product_features": "none",
                    "brand_mentions": "none",
                    "recommendations": "Please try a different search query",
                    "search_quality": "unknown",
                    "user_intent": "unknown",
                    "should_proceed": False
                }
                
        except Exception as e:
            print(f"❌ DEBUG: Exception caught in validate_search_results_with_llm")
            print(f"❌ DEBUG: Exception type: {type(e)}")
            print(f"❌ DEBUG: Exception message: {str(e)}")
            print(f"❌ DEBUG: Exception details: {e}")
            import traceback
            print(f"❌ DEBUG: Full traceback:")
            traceback.print_exc()
            print(f"❌ Error in LLM validation: {e}")
            # Fallback validation
            return {
                "is_valid": False,
                "original_query": search_query,
                "matched_category": "none", 
                "confidence": "low",
                "reasoning": f"LLM validation failed: {str(e)}",
                "product_features": "none",
                "brand_mentions": "none",
                "recommendations": "Please try a different search query",
                "search_quality": "unknown",
                "user_intent": "unknown",
                "should_proceed": False
            }
   
    def visual_product_search_api(event):
        """
        API for visual product search using OpenSearch
        
        Features:
        - Text search with LLM validation
        - Image search with exact match detection
        - Enhanced image processing with PIL validation and resizing
        - Category-based filtering for better results
        - Comprehensive error handling and logging
        """
        try:
            search_type = event.get('search_type')  # 'text' or 'image'
            search_query = event.get('search_query')  # text query
            image_base64 = event.get('image_base64')  # base64 encoded image
            image_s3_uri = event.get('image_s3_uri')  # S3 URI for image
            content = event.get('content')  # multipart form data content
            
            print(f"🔍 Search type: {search_type}")
            print(f"📋 Event keys: {list(event.keys())}")
            
            if search_type == 'text' and search_query:
                print(f"🔍 Text search for: {search_query}")
                
                # First validate the search query before proceeding
                print("🔍 Validating search query with LLM...")
                validation_result = validate_search_results_with_llm(search_query, [])  # Empty results for query-only validation
                
                print(f"🔍 Query validation result: {validation_result}")
                
                # Check if query should proceed based on validation
                should_proceed = validation_result.get('should_proceed', False)
                is_valid = validation_result.get('is_valid', False)
                confidence = validation_result.get('confidence', 'low')
                
                if should_proceed and is_valid and confidence in ['high', 'medium']:
                    print(f"✅ Query validation passed: {validation_result.get('matched_category')}")
                    
                    # Perform the search
                    results = search_products_text_opensearch(search_query, limit=5)
                    
                    if results:
                        response_text = f"Found {len(results)} products matching '{search_query}':\n\n"
                        for i, result in enumerate(results, 1):
                            response_text += f"{i}. Score: {result['score']:.4f}\n"
                            response_text += f"   Description: {result['product_description'][:100]}...\n"
                            response_text += f"   S3 URI: {result['s3_uri']}\n\n"
                    else:
                        response_text = f"No products found matching '{search_query}'"
                else:
                    print(f"❌ Query validation failed: {validation_result.get('reasoning')}")
                    response_text = f"Search query '{search_query}' does not match available product categories (Audio, Camera, Footwear). Please try searching for products in these categories."
                    results = []  # No results since query doesn't match categories
                    
            elif search_type == 'image':
                print(f"🔍 Image search initiated")
                
                # Handle different image input formats
                if image_base64:
                    print("Using provided base64 image")
                    # Remove data URL prefix if present
                    if image_base64.startswith('data:image'):
                        image_base64 = image_base64.split(',')[1]
                elif image_s3_uri:
                    print(f"Processing image from S3 URI: {image_s3_uri}")
                    
                    # Use the same logic as search_products.py
                    try:
                        # Download image from S3
                        import boto3
                        import base64
                        s3_client = boto3.client('s3',
                                                 region_name=region_used)
                        
                        # Extract key from S3 URI and use current bucket from environment
                        if image_s3_uri.startswith('s3://'):
                            # Remove 's3://' and split by '/'
                            path_parts = image_s3_uri[5:].split('/', 1)
                            if len(path_parts) == 2:
                                # Use current S3 bucket from environment instead of old bucket from URI
                                bucket_name = S3_BUCKET
                                # Extract the key part (everything after the first '/')
                                image_key = path_parts[1]
                            else:
                                return {
                                    'statusCode': 400,
                                    'body': json.dumps({
                                        'error': 'Invalid S3 URI format. Expected: s3://bucket-name/key'
                                    })
                                }
                        else:
                            return {
                                'statusCode': 400,
                                'body': json.dumps({
                                    'error': 'Invalid S3 URI. Must start with s3://'
                                })
                            }
                        
                        print(f"Downloading from bucket: {bucket_name}, key: {image_key}")
                        
                        # Download image data from S3 (same as search_products.py)
                        image_data = s3_client.get_object(Bucket=bucket_name, Key=image_key)['Body'].read()
                        image_base64 = base64.b64encode(image_data).decode('utf-8')
                        
                        print(f"Downloaded image size: {len(image_data)} bytes")
                        print(f"Image base64 length: {len(image_base64)} characters")
                        
                    except Exception as e:
                        print(f"❌ Error downloading image from S3: {e}")
                        return {
                            'statusCode': 400,
                            'body': json.dumps({
                                'error': f'Error downloading image from S3: {str(e)}'
                            })
                        }
                    
                    # Validate base64 format and image
                    try:
                        # Test if it's valid base64
                        decoded = base64.b64decode(image_base64)
                        print(f"✅ Valid base64 format, decoded size: {len(decoded)} bytes")
                        
                        # Check file size (Bedrock has limits)
                        if len(decoded) > 5 * 1024 * 1024:  # 5MB limit
                            print(f"❌ Image too large: {len(decoded)} bytes (max 5MB)")
                            return {
                                'statusCode': 400,
                                'body': json.dumps({
                                    'error': 'Image too large. Please use an image smaller than 5MB.'
                                })
                            }
                        
                        # Enhanced image validation and resizing using PIL
                        try:
                            from PIL import Image
                            import io
                            img = Image.open(io.BytesIO(decoded))
                            print(f"✅ Valid image format: {img.format}, size: {img.size}")
                            
                            # Resize if too large (max 1024x1024) for better embedding quality
                            if img.size[0] > 1024 or img.size[1] > 1024:
                                print(f"Resizing image from {img.size} to max 1024x1024")
                                img.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
                                
                                # Convert back to base64
                                buffer = io.BytesIO()
                                if img.format == 'JPEG':
                                    img.save(buffer, format='JPEG', quality=85)
                                else:
                                    img.save(buffer, format='PNG')
                                
                                image_base64 = base64.b64encode(buffer.getvalue()).decode('utf-8')
                                print(f"Resized image base64 length: {len(image_base64)}")
                                
                        except ImportError:
                            print("⚠️ PIL not available, using basic image validation")
                            # Fallback to basic validation
                            if len(decoded) > 10:
                                # Check for JPEG header
                                if decoded[:2] == b'\xff\xd8':
                                    print("✅ Valid JPEG image detected")
                                # Check for PNG header
                                elif decoded[:8] == b'\x89PNG\r\n\x1a\n':
                                    print("✅ Valid PNG image detected")
                                else:
                                    print("⚠️ Unknown image format, but proceeding anyway")
                            else:
                                print("⚠️ Image file too small, but proceeding anyway")
                        except Exception as e:
                            print(f"⚠️ Image validation error: {e}")
                            # Continue with original image if validation fails
                            
                    except Exception as e:
                        print(f"❌ Invalid base64 format: {e}")
                        return {
                            'statusCode': 400,
                            'body': json.dumps({
                                'error': 'Invalid image format. Please provide a valid image file.'
                            })
                        }
                else:
                    return {
                        'statusCode': 400,
                        'body': json.dumps({
                            'error': 'No image data provided. Please provide image file in form-data.'
                        })
                    }
                
                print(f"Image base64 length: {len(image_base64)} characters")
                results = search_products_image_opensearch(image_base64, limit=5, search_image_uri=image_s3_uri)
                
                # Validate search results using LLM for image search
                if results:
                    print("🔍 Validating image search results with LLM...")
                    # For image search, we'll use a generic search query since we don't have text input
                    validation_result = validate_search_results_with_llm("image search", results)
                    
                    if validation_result.get('is_valid', False):
                        print(f"✅ LLM validation passed: {validation_result.get('matched_category')}")
                        
                        # Enhanced filtering: ensure the top result is the most relevant
                        filtered_results = results
                        if len(results) > 1:
                            # Check if the top result has a significantly higher score
                            top_score = results[0]['score']
                            
                            # Extract the expected product category from the search image
                            search_image_uri = image_s3_uri if image_s3_uri else "unknown"
                            expected_category = None
                            
                            # Determine expected category from the search image filename
                            if "shoe" in search_image_uri.lower():
                                expected_category = "shoe"
                            elif "camera" in search_image_uri.lower():
                                expected_category = "camera"
                            elif "headphone" in search_image_uri.lower() or "headset" in search_image_uri.lower():
                                expected_category = "headsets"
                            
                            print(f"🔍 Expected category from search image: {expected_category}")
                            
                            # Filter results based on expected category and score
                            if expected_category:
                                category_filtered = []
                                for result in results:
                                    result_uri = result['s3_uri'].lower()
                                    result_category = None
                                    
                                    if "shoe" in result_uri:
                                        result_category = "shoe"
                                    elif "camera" in result_uri:
                                        result_category = "camera"
                                    elif "headphone" in result_uri or "headset" in result_uri:
                                        result_category = "headsets"
                                    
                                    # Prioritize same category results
                                    if result_category == expected_category:
                                        category_filtered.append(result)
                                
                                # If we found category matches, use them
                                if category_filtered:
                                    filtered_results = category_filtered
                                    print(f"✅ Found {len(filtered_results)} results in expected category: {expected_category}")
                                else:
                                    print(f"⚠️ No results found in expected category: {expected_category}, using all results")
                            
                            # Additional score-based filtering
                            if top_score > 0.6:  # High confidence threshold
                                # Only include results that are very close to the top score
                                score_filtered = [filtered_results[0]]
                                for result in filtered_results[1:]:
                                    if top_score - result['score'] < 0.05:  # Very close scores
                                        score_filtered.append(result)
                                filtered_results = score_filtered
                            elif top_score > 0.5:  # Medium confidence
                                # Include results within 0.1 score difference
                                score_filtered = [filtered_results[0]]
                                for result in filtered_results[1:]:
                                    if top_score - result['score'] < 0.1:
                                        score_filtered.append(result)
                                filtered_results = score_filtered
                        
                        response_text = f"Found {len(filtered_results)} products:\n\n"
                        for i, result in enumerate(filtered_results, 1):
                            match_type = result.get('match_type', 'similar')
                            match_icon = "🎯" if match_type == "exact" else "🔍"
                            response_text += f"{i}. {match_icon} {match_type.upper()} MATCH - Score: {result['score']:.4f}\n"
                            response_text += f"   Description: {result['product_description'][:100]}...\n"
                            response_text += f"   S3 URI: {result['s3_uri']}\n\n"
                        
                        # Update results to filtered results
                        results = filtered_results
                    else:
                        print(f"❌ LLM validation failed: {validation_result.get('reasoning')}")
                        response_text = "No similar products found for this image. The image does not match available product categories (camera, shoe, headsets)."
                        results = []  # Clear results since they don't match categories
                else:
                    response_text = "No similar products found for this image"
            else:
                response_text = "Invalid search parameters. Please provide either 'search_type': 'text' with 'search_query' or 'search_type': 'image' with image file"
            
            return {
                'statusCode': 200,
                'body': json.dumps({
                    'message': 'Search completed successfully',
                    'results': results if 'results' in locals() else [],
                    'response_text': response_text,
                    'validation': validation_result if 'validation_result' in locals() else None
                })
            }
            
        except Exception as e:
            print(f"Error in visual product search API: {e}")
            return {
                'statusCode': 500,
                'body': json.dumps({
                    'error': f'Search failed: {str(e)}'
                })
            }
  #insurance event_type code starts here
    if event_type == "get_pw":
        return db_password


    if event_type == 'genai_product_desc':
        return describe_image(event)

    if event_type == "generate_summary":     
        
        print("SUMMARY GENERATION ")
        session_id = event["session_id"]
        chat_query = f'''
        SELECT question,answer
        FROM {schema}.{chat_history_table}    
        WHERE session_id = '{session_id}';
        '''
    
        chat_details = select_db(chat_query)
        print("CHAT DETAILS : ",chat_details)
        history = ""
    
        for chat in chat_details:
            history1 = "Human: "+chat[0]
            history2 = "Bot: "+chat[1]
            history += "\n"+history1+"\n"+history2+"\n"
        print("HISTORY : ",history)
        prompt_query = f"SELECT analytics_prompt from {schema}.{prompt_metadata_table} where id = 1;"
        prompt_template = f'''
        <Instruction>
        Based on the conversation above, please provide the output in the following format:
        Topic:
		- Identify the main topic of the conversation, it should be a single word topic
        Conversation Type:
        - Identify if the conversation is an Enquiry or a Complaint. If both are present, classify it as (Enquiry/Complaint).
        - Consider the emotional tone and context to determine the type.
        
        Conversation Summary Explanation:
        - Explain why you labelled the conversation as Enquiry, Complaint, or both.
        - Highlight the key questions, concerns, or issues raised by the customer.
	-IMPORTANT: keep the summary in 2-3 lines
        
        Detailed Summary:
        - Provide a clear summary of the conversation, capturing the customer’s needs, questions, and any recurring themes.
	- IMPORTANT: keep the summary in 2-3 lines keep it short

        
	
        Conversation Sentiment:
        - Analyse overall sentiment of conversation carried out by the user with the agent.
		- Analyse the tone and feelings associated within the conversation.
		- possible values are (Positive/Neutral/Negative)
     	- Only provide the final sentiment here in this key. 
        Conversation Sentiment Generated Details:
        - Explain why you labelled the Lead as Positive/Neutral/Negative.
        - List potential leads, noting any interest in products/services.
        - Highlight specific customer questions or preferences that could lead to sales.
        - Suggest approaches to engage each lead based on their needs.
        
        
        Lead Sentiment:
        - Indicate if potential leads are generated from the conversation (Yes/No).
        
        Leads Generated Details:
        - Explain why you labelled the Lead as Yes/No.
        - List potential leads, noting any interest in products/services.
        - Highlight specific customer questions or preferences that could lead to sales.
        - Suggest approaches to engage each lead based on their needs.
        
        Action to be Taken:
        - Outline next steps for the sales representative to follow up on the opportunities identified.
        - Include any necessary follow-up actions, information to provide, or solutions to offer.
        
        WhatsApp Followup Creation:
		- Craft a highly personalized follow-up WhatsApp message to engage the customer effectively as a customer sales representative.
		- Ensure to provide a concise response and make it as brief as possible. Maximum 2-3 lines as it should be shown in the whatsapp mobile screen, so make the response brief.
        - Incorporate key details from the conversation script to show understanding and attentiveness(Do not hallucinate or add any details that are ecplicitely there in the conversation).
        - Tailor the WhatsApp message to address specific concerns, provide solutions, and include a compelling call-to-action.
        - Infuse a sense of urgency or exclusivity to prompt customer response.
		- Format the WhatsApp message with real line breaks for each paragraph (not the string n). Use actual newlines to separate the greeting, body, call-to-action, and closing. 
	
	Follow the structure of the sample WhatsApp message below:
	<format_for_whatsapp_message>

Hi, Thanks for reaching out to AnyBank! 

You had a query about [Inquiry Topic]. Here’s what you can do next:

1. [Step 1]  
2. [Step 2]

If you’d like, I can personally help you with [Offer/Action]. Just share your [Details Needed].

Looking forward to hearing from you soon.

</format_for_whatsapp_message>
	- Before providing the whatsapp response, it is very critical that you double check if its in the provided format


<language_constraints>

If the conversation history (user questions and bot answers) is primarily in Tagalog, then provide the values for all JSON keys in Tagalog. Otherwise, provide the values strictly in English.
If the conversation history is dominantly in Tagalog, provide the value for "Topic" in Tagalog; otherwise, provide it in English.
Always keep the JSON keys in English exactly as specified below:
"Topic":
"Conversation Type":  
"Conversation Summary Explanation":
"Detailed Summary": 
"Conversation Sentiment":
"Conversation Sentiment Generated Details":
"Lead Sentiment":
"Leads Generated Details": 
"Action to be Taken": 
"Whatsapp Creation":   

Only the **values** for each key should switch between English or Tagalog based on the dominant language in the conversation. Never translate or modify the keys. 

</language_constraints>


	
        
</Instruction> 
return output in JSON in a consistent manner
"Topic":
"Conversation Type":  
"Conversation Summary Explanation":
"Detailed Summary": 
"Conversation Sentiment":
"Conversation Sentiment Generated Details":
"Lead Sentiment":
"Leads Generated Details": 
"Action to be Taken": 
"Whatsapp Creation":   
these are the keys to be always used while returning response. Strictly do not add key values of your own.

## WHATSAPP MESSAGE FORMATTING:
- Write WhatsApp messages as natural, conversational text
- Use proper paragraph spacing instead of \n characters
- Avoid any escape sequences or formatting codes
- Keep messages clean and readable without technical formatting
- Use natural line breaks and spacing for readability
- **NEVER** include literal \n characters in WhatsApp messages
- Use actual line breaks and proper spacing for message formatting
- Ensure WhatsApp messages are formatted naturally without escape sequences
- **CRITICAL**: When generating WhatsApp messages, use actual line breaks and spacing, NOT escape sequences
- Format messages with natural paragraph breaks and proper spacing
- Write messages exactly as they should appear to the user, without any technical formatting codes
        '''
        # prompt_template = prompt_response[0][0]
        print("PROMPT : ",prompt_template)
        template = f'''
        <Conversation>
        {history}
        </Conversation>
        {prompt_template}
        '''
    
        # - Ensure the email content is formatted correctly with new lines. USE ONLY "\n" for new lines. 
        #         - Ensure the email content is formatted correctly for new lines instead of using new line characters.
        response = bedrock_client.invoke_model(contentType='application/json', body=json.dumps({
            "anthropic_version": "bedrock-2023-05-31",  
            "max_tokens": 4000,     
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": template},
                    ]
                }
            ],
        }), modelId=model_id)                                                                                                                       
    
        inference_result = response['body'].read().decode('utf-8')
        final = json.loads(inference_result)
        out=final['content'][0]['text']
        print(out)
        llm_out = extract_sections(out)
        
    
        topic = "" 
        conversation_type = ""
        conversation_summary_explanation = ""
        detailed_summary = ""
        conversation_sentiment = ""
        conversation_sentiment_generated_details = ""
        lead_sentiment = ""
        leads_generated_details = ""
        action_to_be_taken = ""
        email_creation = ""
        
        try:
            if "Topic" in llm_out:
                topic = llm_out['Topic']
        except:
            topic = ""
        
        try:
            
            if 'Conversation Type' in llm_out:
                conversation_type = llm_out['Conversation Type']
                if conversation_type == "N/A":
                    enquiry, complaint = (0, 0)
                else:
                    enquiry, complaint = (1, 0) if conversation_type == "Enquiry" else (0, 1)
        except:
            enquiry , complaint = 0,0
            
        try:
            if 'Conversation Summary Explanation' in llm_out:
                conversation_summary_explanation = llm_out['Conversation Summary Explanation']
        except:
            conversation_summary_explanation= ""
        
        try:
            if 'Detailed Summary' in llm_out:
                detailed_summary = llm_out['Detailed Summary']
        except:
            detailed_summary = ""
        
        try:
            if 'Conversation Sentiment' in llm_out:
                conversation_sentiment = llm_out['Conversation Sentiment']
        except:
            conversation_sentiment = ""
        
        try:
            if 'Conversation Sentiment Generated Details' in llm_out:
                conversation_sentiment_generated_details = llm_out['Conversation Sentiment Generated Details']
        except:
            conversation_generated_details = ""
            
        try:
            if 'Lead Sentiment' in llm_out:
                lead_sentiment = llm_out['Lead Sentiment']
                lead = 1 if lead_sentiment == "Hot" else 0
        except:
            lead = 0
        
        try:
            if 'Leads Generated Details' in llm_out:
                leads_generated_details = llm_out['Leads Generated Details']
        except:
            leads_generated_details = ""
        
        try:
            if 'Action to be Taken' in llm_out:   
                action_to_be_taken = llm_out['Action to be Taken']
        except:
            action_to_be_taken = ""
        
        try:
            if 'Whatsapp Creation' in llm_out:
                email_creation = llm_out['Whatsapp Creation']
        except:
            email_creation = ""
        detailed_summary = detailed_summary.replace("'", "''")
        email_creation = email_creation.replace("'", "''")
        action_to_be_taken = action_to_be_taken.replace("'", "''")
        leads_generated_details = leads_generated_details.replace("'", "''")
        conversation_sentiment_generated_details = conversation_sentiment_generated_details.replace("'", "''")        
        
        print("LEAD : ",lead)
        print("ENQUIRY : ",enquiry)
        print("COMPLAINT : ",complaint)
        print("conversation_type:", conversation_type)
        print("Topic: ",topic)
        print("Sentiment Explanation:", conversation_summary_explanation)
        print("Detailed summary:", detailed_summary)
        print("CONVERSATION SENTIMENT :",conversation_sentiment)
        print("CONVERSATION SENTIMENT DETAILS:",conversation_sentiment_generated_details)
        print("lead Sentiment:", lead_sentiment)
        print("lead explanation:", leads_generated_details)
        print("next_best_action:",action_to_be_taken)
        print("email_content:",email_creation)
        session_time = datetime.now()
        update_query = f'''UPDATE {schema}.{CHAT_LOG_TABLE}
        SET 
            lead = {lead},
            lead_explanation = '{leads_generated_details}',
            sentiment = '{conversation_sentiment}',
            sentiment_explanation = '{conversation_sentiment_generated_details}',
            session_time = '{session_time}',
            enquiry = {enquiry},
            complaint = {complaint},
            summary = '{detailed_summary}',
            whatsapp_content = '{email_creation}',
            next_best_action = '{action_to_be_taken}',
            topic = '{topic}'
        WHERE 
            session_id = '{session_id}' 
            '''
        update_db(update_query)
        return {
                "statusCode" : 200,
                "message" : "Summary Successfully Generated"
            }
    # get_risk_out(body)
   
  
  

    if event_type == 'list_chat_summary':
        session_id = event['session_id']
        chat_query = f'''
        SELECT question,answer
        FROM {schema}.{chat_history_table}    
        WHERE session_id = '{session_id}';
        '''
    
        chat_details = select_db(chat_query)
        print("CHAT DETAILS : ",chat_details)
        history = []
    
        for chat in chat_details:
            history.append({"Human":chat[0],"Bot":chat[1]})
        print("HISTORY : ",history)  
        select_query = f'''select summary, whatsapp_content, sentiment, topic  from genaifoundry.ce_cexp_logs ccl where session_id = '{session_id}';'''
        summary_details = select_db(select_query)
        final_summary = {}
        for i in summary_details:  
            # print("i:",i)  
            final_summary['summary'] = i[0]
            final_summary['whatsapp_content'] = i[1]
            final_summary['sentiment'] = i[2]
            final_summary['Topic'] = i[3]   
            
        # print(summary_details) 
        # print(final_summary)   
        return {"transcript":history,"final_summary":final_summary}   

    if event_type == 'mediplus_assess':
        return generate_mediplus_assessment(event)
    elif event_type == 'lifesecure_assess':
        return generate_lifesecure_assessment(event)
    
    if event_type == 'chat_tool':  
       
        # api_gateway_client = boto3.client('apigatewaymanagementapi', endpoint_url=gateway_url)
        # e = json.loads(event["body"])  
        chat = event['chat']
        session_id = event['session_id']   
        connectionId = event["connectionId"]
        print(connectionId,"connectionid_printtt")
        chat_history = []


        if session_id == None or session_id == 'null' or session_id == '':
            session_id = str(uuid.uuid4())
        
        else:
            query = f'''select question,answer 
                    from {schema}.{chat_history_table} 
                    where session_id = '{session_id}' 
                    order by created_on desc limit 20;'''
            history_response = select_db(query)

            if len(history_response) > 0:
                for chat_session in reversed(history_response):  
                    chat_history.append({'role': 'user', 'content': [{"type" : "text",'text': chat_session[0]}]})
                    chat_history.append({'role': 'assistant', 'content': [{"type" : "text",'text': chat_session[1]}]})
        
            #APPENDING CURRENT USER QUESTION
        chat_history.append({'role': 'user', 'content': [{"type" : "text",'text': chat}]})
            
        print("CHAT HISTORY : ",chat_history)

        tool_response = agent_invoke_tool(chat_history, session_id,chat,connectionId)
        print("TOOL RESPONSE: ", tool_response)  
        #insert into chat_history_table
        query = f'''
                INSERT INTO {schema}.{chat_history_table}
                (session_id, question, answer, input_tokens, output_tokens, created_on, updated_on)
                VALUES( %s, %s, %s, %s, %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);
                '''
        values = (str(session_id),str(chat), str(tool_response['answer']), str(tool_response['input_tokens']), str(tool_response['output_tokens']))
        res = insert_db(query, values) 


        
        print(type(session_id))   
        insert_query = f'''  INSERT INTO genaifoundry.ce_cexp_logs      
(created_on, environment, session_time, "lead", enquiry, complaint, summary, whatsapp_content, next_best_action, session_id, lead_explanation, sentiment, sentiment_explanation, connectionid, input_token, output_token,topic)
VALUES(CURRENT_TIMESTAMP, %s, CURRENT_TIMESTAMP, %s, 0, 0, %s, %s, %s, %s, %s, %s, %s, %s, 0, 0,%s);'''             
        values = ('',None,'','','',session_id,'','','','','')            
        res = insert_db(insert_query,values)   
        return tool_response    
    
#insurance event_type code ends here


#banking event_type starts here ..
    if event_type == 'banking_chat_tool':  
       
        # api_gateway_client = boto3.client('apigatewaymanagementapi', endpoint_url=gateway_url)
        # e = json.loads(event["body"])  
        chat = event['chat']
        session_id = event['session_id']   
        connectionId = event["connectionId"]
        print(connectionId,"connectionid_printtt")
        chat_history = []


        if session_id == None or session_id == 'null' or session_id == '':
            session_id = str(uuid.uuid4())
        
        else:
            query = f'''select question,answer 
                    from {schema}.{banking_chat_history_table} 
                    where session_id = '{session_id}' 
                    order by created_on desc;'''
            history_response = select_db(query)
            print("history_response is ",history_response)

            if len(history_response) > 0:
                for chat_session in reversed(history_response):  
                    chat_history.append({'role': 'user', 'content': [{"type" : "text",'text': chat_session[0]}]})
                    chat_history.append({'role': 'assistant', 'content': [{"type" : "text",'text': chat_session[1]}]})
        
            #APPENDING CURRENT USER QUESTION
        chat_history.append({'role': 'user', 'content': [{"type" : "text",'text': chat}]})
            
        print("CHAT HISTORY : ",chat_history)

        tool_response = banking_agent_invoke_tool(chat_history, session_id,chat,connectionId)
        print("TOOL RESPONSE: ", tool_response)  
        #insert into banking_chat_history_table
        query = f'''
                INSERT INTO {schema}.{banking_chat_history_table}
                (session_id, question, answer, input_tokens, output_tokens, created_on, updated_on)
                VALUES( %s, %s, %s, %s, %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);
                '''
        values = (str(session_id),str(chat), str(tool_response['answer']), str(tool_response['input_tokens']), str(tool_response['output_tokens']))
        res = insert_db(query, values)
        print("response:",res)


        
        print(type(session_id))   
        insert_query = f'''  INSERT INTO genaifoundry.ce_cexp_logs      
(created_on, environment, session_time, "lead", enquiry, complaint, summary, whatsapp_content, next_best_action, session_id, lead_explanation, sentiment, sentiment_explanation, connectionid, input_token, output_token,topic)
VALUES(CURRENT_TIMESTAMP, %s, CURRENT_TIMESTAMP, %s, 0, 0, %s, %s, %s, %s, %s, %s, %s, %s, 0, 0,%s);'''             
        values = ('',None,'','','',session_id,'','','','','')            
        res = insert_db(insert_query,values)   
        return tool_response
    if event_type == 'voiceops':
        try:
            url =f"http://{ec2_instance_ip}:8000/transcribe"
            kb_id=''
            prompt_template = ''
            print("yes")
            if event['box_type'] == 'insurance':
                kb_id = KB_ID
                print("kb_id",kb_id)
                prompt_template=f'''You are a Virtual Insurance Assistant for AnyBank. Give quick, helpful answers that sound natural when spoken aloud.

                        RESPONSE RULES:
                        - Maximum 2 sentences per response
                        - Use simple, conversational language
                        - No bullet points, brackets, or special formatting
                        - No technical jargon or complex terms
                        - Answer only what the customer asked
                        - Skip greetings and confirmations

                        SPEAKING STYLE:
                        - Talk like you're having a friendly conversation
                        - Use short, clear sentences
                        - Avoid reading lists or multiple options
                        - Give one direct answer, not explanations

                        Search Results: $search_results$

                        Customer Question: $query$

                        Provide a brief, conversational response that directly answers their question. '''


            else: 
                kb_id = bank_kb_id
                print("bank_kb_id",bank_kb_id)
                prompt_template=f''' 
                You are a Virtual Banking Assistant for AnyBank. Give quick, helpful answers that sound natural when spoken aloud.
    
                RESPONSE RULES:
                - Maximum 2 sentences per response
                - Use simple, conversational language
                - No bullet points, brackets, or special formatting
                - No technical jargon or complex terms
                - Answer only what the customer asked
                - Skip greetings and confirmations
                
                SPEAKING STYLE:
                - Talk like you're having a friendly conversation
                - Use short, clear sentences
                - Avoid reading lists or multiple options
                - Give one direct answer, not explanations
                
                Search Results: $search_results$
                
                Customer Question: $query$
                
                Provide a brief, conversational response that directly answers their question.
                '''
            payload = json.dumps({
            "kb_id": kb_id,
            "session_id": event['session_id'],
            "audio": event['audio'],
            "connection_id":event['connectionId'],
            "connection_url":event['connection_url'],
            "box_type": event['box_type'],
            "prompt_template":prompt_template,
            "bucket_name":voiceops_bucket_name,  # Use the new voice operations bucket
            "region_name":region_name,
            "db_cred":{
            "db_user": db_user,
            "db_host":db_host,
            "db_port":db_port,
            "db_database":db_database,
            "db_password":db_password}
            })
            headers = {
            'Content-Type': 'application/json'
            }
            print(payload)
            response = requests.request("POST", url, headers=headers, data=payload)

            return response.text

        except Exception as e:
            return {
                'statusCode': 500,
                'body': json.dumps({
                    'message': 'Error processing transcription',
                    'error': str(e)
                })
            }
        
    if event_type == 'test_dummy':
        # Dummy event type for testing the Lambda function
        print("Testing dummy event type...")
        return {
            "statusCode": 200,
            "body": {
                "message": "Hello from Lambda with layers!",
                "event_type": event_type,
                "timestamp": "2024-01-01T00:00:00Z",
                "test_data": {
                    "layers_loaded": True,
                    "boto3_available": True,
                    "psycopg2_available": True,
                    "aws4auth_available": True,
                    "opensearchpy_available": True,
                    "requests_available": True
                }
            }
        }
    if event_type == 'risk_sandbox':
        
        return generate_risk_sandbox(event)

    if event_type == "generate_banking_summary":     
        
        print("BANKING SUMMARY GENERATION ")
        session_id = event["session_id"]
        chat_query = f'''
        SELECT question,answer
        FROM {schema}.{banking_chat_history_table}    
        WHERE session_id = '{session_id}';
        '''
    
        chat_details = select_db(chat_query)
        print("BANKING CHAT DETAILS : ",chat_details)
        history = ""
    
        for chat in chat_details:
            history1 = "Human: "+chat[0]
            history2 = "Bot: "+chat[1]
            history += "\n"+history1+"\n"+history2+"\n"
        print("BANKING HISTORY : ",history)
        prompt_query = f"SELECT analytics_prompt from {schema}.{prompt_metadata_table} where id = 3;"
        prompt_template = f'''<Instruction>
        Based on the conversation above, please provide the output in the following format:
        Topic:
		- Identify the main topic of the conversation, it should be a single word topic
        Conversation Type:
        - Identify if the conversation is an Enquiry or a Complaint. If both are present, classify it as (Enquiry/Complaint).
        - Consider the emotional tone and context to determine the type.
        
        Conversation Summary Explanation:
        - Explain why you labelled the conversation as Enquiry, Complaint, or both.
        - Highlight the key questions, concerns, or issues raised by the customer.
	-IMPORTANT: keep the summary in 2-3 lines
        
        Detailed Summary:
        - Provide a clear summary of the conversation, capturing the customer’s needs, questions, and any recurring themes.
	- IMPORTANT: keep the summary in 2-3 lines keep it short

        
	
        Conversation Sentiment:
        - Analyse overall sentiment of conversation carried out by the user with the agent.
		- Analyse the tone and feelings associated within the conversation.
		- possible values are (Positive/Neutral/Negative)
     	- Only provide the final sentiment here in this key. 
        Conversation Sentiment Generated Details:
        - Explain why you labelled the Lead as Positive/Neutral/Negative.
        - List potential leads, noting any interest in products/services.
        - Highlight specific customer questions or preferences that could lead to sales.
        - Suggest approaches to engage each lead based on their needs.
        
        
        Lead Sentiment:
        - Indicate if potential leads are generated from the conversation (Yes/No).
        
        Leads Generated Details:
        - Explain why you labelled the Lead as Yes/No.
        - List potential leads, noting any interest in products/services.
        - Highlight specific customer questions or preferences that could lead to sales.
        - Suggest approaches to engage each lead based on their needs.
        
        Action to be Taken:
        - Outline next steps for the sales representative to follow up on the opportunities identified.
        - Include any necessary follow-up actions, information to provide, or solutions to offer.
        
        WhatsApp Followup Creation:
		- Craft a highly personalized follow-up WhatsApp message to engage the customer effectively as a customer sales representative.
		- Ensure to provide a concise response and make it as brief as possible. Maximum 2-3 lines as it should be shown in the whatsapp mobile screen, so make the response brief.
        - Incorporate key details from the conversation script to show understanding and attentiveness (VERY IMPORTANT: ONLY INCLUDE DETAILS FROM THE CONVERSATION DO NOT HALLUCINATE ANY DETAILS).
        - Tailor the WhatsApp message to address specific concerns, provide solutions, and include a compelling call-to-action.
        - Infuse a sense of urgency or exclusivity to prompt customer response.
		- Format the WhatsApp message with real line breaks for each paragraph (not the string n). Use actual newlines to separate the greeting, body, call-to-action, and closing. 
	
	Follow the structure of the sample WhatsApp message below:
	<format_for_whatsapp_message>

Hi, Thanks for reaching out to AnyBank! 

You had a query about [Inquiry Topic]. Here’s what you can do next:

1. [Step 1]  
2. [Step 2]

If you’d like, I can personally help you with [Offer/Action]. Just share your [Details Needed].

Looking forward to hearing from you soon.

</format_for_whatsapp_message>
	- Before providing the whatsapp response, it is very critical that you double check if its in the provided format


<language_constraints>

If the conversation history (user questions and bot answers) is primarily in Tagalog, then provide the values for all JSON keys in Tagalog. Otherwise, provide the values strictly in English.
If the conversation history is dominantly in Tagalog, provide the value for "Topic" in Tagalog; otherwise, provide it in English.
Always keep the JSON keys in English exactly as specified below:
"Topic":
"Conversation Type":  
"Conversation Summary Explanation":
"Detailed Summary": 
"Conversation Sentiment":
"Conversation Sentiment Generated Details":
"Lead Sentiment":
"Leads Generated Details": 
"Action to be Taken": 
"Whatsapp Creation":   

Only the **values** for each key should switch between English or Tagalog based on the dominant language in the conversation. Never translate or modify the keys. 

</language_constraints>


	
        
</Instruction> 
return output in JSON in a consistent manner
"Topic":
"Conversation Type":  
"Conversation Summary Explanation":
"Detailed Summary": 
"Conversation Sentiment":
"Conversation Sentiment Generated Details":
"Lead Sentiment":
"Leads Generated Details": 
"Action to be Taken": 
"Whatsapp Creation":   
these are the keys to be always used while returning response. Strictly do not add key values of your own.
        '''
        #prompt_template = prompt_response[0][0]
        print("BANKING PROMPT : ",prompt_template)
        template = f'''
        <Conversation>
        {history}
        </Conversation>
        {prompt_template}
        '''
    
        # - Ensure the email content is formatted correctly with new lines. USE ONLY "\n" for new lines. 
        #         - Ensure the email content is formatted correctly for new lines instead of using new line characters.
        response = bedrock_client.invoke_model(contentType='application/json', body=json.dumps({
            "anthropic_version": "bedrock-2023-05-31",  
            "max_tokens": 4000,     
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": template},
                    ]
                }
            ],
        }), modelId=model_id)                                                                                                                       
    
        inference_result = response['body'].read().decode('utf-8')
        final = json.loads(inference_result)
        out=final['content'][0]['text']
        print(out)
        llm_out = extract_sections(out)
        
    
        topic = "" 
        conversation_type = ""
        conversation_summary_explanation = ""
        detailed_summary = ""
        conversation_sentiment = ""
        conversation_sentiment_generated_details = ""
        lead_sentiment = ""
        leads_generated_details = ""
        action_to_be_taken = ""
        email_creation = ""
        
        try:
            if "Topic" in llm_out:
                topic = llm_out['Topic']
        except:
            topic = ""
        
        try:
            
            if 'Conversation Type' in llm_out:
                conversation_type = llm_out['Conversation Type']
                if conversation_type == "N/A":
                    enquiry, complaint = (0, 0)
                else:
                    enquiry, complaint = (1, 0) if conversation_type == "Enquiry" else (0, 1)
        except:
            enquiry , complaint = 0,0
            
        try:
            if 'Conversation Summary Explanation' in llm_out:
                conversation_summary_explanation = llm_out['Conversation Summary Explanation']
        except:
            conversation_summary_explanation= ""
        
        try:
            if 'Detailed Summary' in llm_out:
                detailed_summary = llm_out['Detailed Summary']
        except:
            detailed_summary = ""
        
        try:
            if 'Conversation Sentiment' in llm_out:
                conversation_sentiment = llm_out['Conversation Sentiment']
        except:
            conversation_sentiment = ""
        
        try:
            if 'Conversation Sentiment Generated Details' in llm_out:
                conversation_sentiment_generated_details = llm_out['Conversation Sentiment Generated Details']
        except:
            conversation_generated_details = ""
            
        try:
            if 'Lead Sentiment' in llm_out:
                lead_sentiment = llm_out['Lead Sentiment']
                lead = 1 if lead_sentiment == "Hot" else 0
        except:
            lead = 0
        
        try:
            if 'Leads Generated Details' in llm_out:
                leads_generated_details = llm_out['Leads Generated Details']
        except:
            leads_generated_details = ""
        
        try:
            if 'Action to be Taken' in llm_out:   
                action_to_be_taken = llm_out['Action to be Taken']
        except:
            action_to_be_taken = ""
        
        try:
            if 'Whatsapp Creation' in llm_out:
                email_creation = llm_out['Whatsapp Creation']
                # Clean up any literal \n characters in WhatsApp content
                email_creation = email_creation.replace('\\n', '\n').replace('\\r', '\r').replace('\\t', '\t')
        except:
            email_creation = ""
        detailed_summary = detailed_summary.replace("'", "''")
        email_creation = email_creation.replace("'", "''")
        action_to_be_taken = action_to_be_taken.replace("'", "''")
        leads_generated_details = leads_generated_details.replace("'", "''")
        conversation_sentiment_generated_details = conversation_sentiment_generated_details.replace("'", "''")        
        
        print("LEAD : ",lead)
        print("ENQUIRY : ",enquiry)
        print("COMPLAINT : ",complaint)
        print("conversation_type:", conversation_type)
        print("Topic: ",topic)
        print("Sentiment Explanation:", conversation_summary_explanation)
        print("Detailed summary:", detailed_summary)
        print("CONVERSATION SENTIMENT :",conversation_sentiment)
        print("CONVERSATION SENTIMENT DETAILS:",conversation_sentiment_generated_details)
        print("lead Sentiment:", lead_sentiment)
        print("lead explanation:", leads_generated_details)
        print("next_best_action:",action_to_be_taken)
        print("email_content:",email_creation)
        session_time = datetime.now()
        update_query = f'''UPDATE {schema}.{CHAT_LOG_TABLE}
        SET 
            lead = {lead},
            lead_explanation = '{leads_generated_details}',
            sentiment = '{conversation_sentiment}',
            sentiment_explanation = '{conversation_sentiment_generated_details}',
            session_time = '{session_time}',
            enquiry = {enquiry},
            complaint = {complaint},
            summary = '{detailed_summary}',
            whatsapp_content = '{email_creation}',
            next_best_action = '{action_to_be_taken}',
            topic = '{topic}'
        WHERE 
            session_id = '{session_id}' 
            '''
        update_db(update_query)
        return {
                "statusCode" : 200,
                "message" : "Banking Summary Successfully Generated"
            }

    if event_type == 'list_banking_summary':
        session_id = event['session_id']
        chat_query = f'''
        SELECT question,answer
        FROM {schema}.{banking_chat_history_table}    
        WHERE session_id = '{session_id}';
        '''
    
        chat_details = select_db(chat_query)
        print("BANKING CHAT DETAILS : ",chat_details)
        history = []
    
        for chat in chat_details:
            history.append({"Human":chat[0],"Bot":chat[1]})
        print("BANKING HISTORY : ",history)  
        select_query = f'''select summary, whatsapp_content, sentiment, topic  from genaifoundry.ce_cexp_logs ccl where session_id = '{session_id}';'''
        summary_details = select_db(select_query)
        final_summary = {}
        for i in summary_details:  
            # print("i:",i)  
            final_summary['summary'] = i[0]
            final_summary['whatsapp_content'] = i[1]
            final_summary['sentiment'] = i[2]
            final_summary['Topic'] = i[3]   
            
        # print(summary_details) 
        # print(final_summary)   
        return {"transcript":history,"final_summary":final_summary}
#banking event type ends here...

#retail event type starts here...

    if event_type == 'visual_product_search':
        return visual_product_search_api(event)
        
   
        
    if event_type == 'retail_chat_tool':  
        
            # api_gateway_client = boto3.client('apigatewaymanagementapi', endpoint_url=gateway_url)
            # e = json.loads(event["body"])  
            chat = event['chat']
            session_id = event['session_id']   
            connectionId = event["connectionId"]
            print(connectionId,"connectionid_printtt")
            chat_history = []


            if session_id == None or session_id == 'null' or session_id == '':
                session_id = str(uuid.uuid4())
            
            else:
                query = f'''select question,answer 
                        from {schema}.{retail_chat_history_table} 
                        where session_id = '{session_id}' 
                        order by created_on desc limit 20;'''
                history_response = select_db(query)
                print("history_response is ",history_response)

                if len(history_response) > 0:
                    for chat_session in reversed(history_response):  
                        chat_history.append({'role': 'user', 'content': [{"type" : "text",'text': chat_session[0]}]})
                        chat_history.append({'role': 'assistant', 'content': [{"type" : "text",'text': chat_session[1]}]})
            
                #APPENDING CURRENT USER QUESTION
            chat_history.append({'role': 'user', 'content': [{"type" : "text",'text': chat}]})
                
            print("CHAT HISTORY : ",chat_history)

            tool_response = retail_agent_invoke_tool(chat_history, session_id,chat,connectionId)
            print("TOOL RESPONSE: ", tool_response)  
            #insert into retail_chat_history_table
            query = f'''
                    INSERT INTO {schema}.{retail_chat_history_table}
                    (session_id, question, answer, input_tokens, output_tokens, created_on, updated_on)
                    VALUES( %s, %s, %s, %s, %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);
                    '''
            values = (str(session_id),str(chat), str(tool_response['answer']), str(tool_response['input_tokens']), str(tool_response['output_tokens']))
            res = insert_db(query, values)
            print("response:",res)


            
            print(type(session_id))   
            insert_query = f'''  INSERT INTO genaifoundry.ce_cexp_logs      
    (created_on, environment, session_time, "lead", enquiry, complaint, summary, whatsapp_content, next_best_action, session_id, lead_explanation, sentiment, sentiment_explanation, connectionid, input_token, output_token,topic)
    VALUES(CURRENT_TIMESTAMP, %s, CURRENT_TIMESTAMP, %s, 0, 0, %s, %s, %s, %s, %s, %s, %s, %s, 0, 0,%s);'''             
            values = ('',None,'','','',session_id,'','','','','')            
            res = insert_db(insert_query,values)   
            return tool_response

   
        
    if event_type == 'paris_chat_tool':  
        
            # api_gateway_client = boto3.client('apigatewaymanagementapi', endpoint_url=gateway_url)
            # e = json.loads(event["body"])  
            chat = event['chat']
            session_id = event['session_id']   
            connectionId = event["connectionId"]
            print(connectionId,"connectionid_printtt")
            chat_history = []


            if session_id == None or session_id == 'null' or session_id == '':
                session_id = str(uuid.uuid4())
            
            else:
                query = f'''select question,answer 
                        from {schema}.{retail_chat_history_table} 
                        where session_id = '{session_id}' 
                        order by created_on desc limit 20;'''
                history_response = select_db(query)
                print("history_response is ",history_response)

                if len(history_response) > 0:
                    for chat_session in reversed(history_response):  
                        chat_history.append({'role': 'user', 'content': [{"type" : "text",'text': chat_session[0]}]})
                        chat_history.append({'role': 'assistant', 'content': [{"type" : "text",'text': chat_session[1]}]})
            
                #APPENDING CURRENT USER QUESTION
            chat_history.append({'role': 'user', 'content': [{"type" : "text",'text': chat}]})
                
            print("CHAT HISTORY : ",chat_history)

            tool_response = paris_agent_invoke_tool(chat_history, session_id,chat,connectionId)
            print("TOOL RESPONSE: ", tool_response)  
            #insert into retail_chat_history_table
            query = f'''
                    INSERT INTO {schema}.{retail_chat_history_table}
                    (session_id, question, answer, input_tokens, output_tokens, created_on, updated_on)
                    VALUES( %s, %s, %s, %s, %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);
                    '''
            values = (str(session_id),str(chat), str(tool_response['answer']), str(tool_response['input_tokens']), str(tool_response['output_tokens']))
            res = insert_db(query, values)
            print("response:",res)


            
            print(type(session_id))   
            insert_query = f'''  INSERT INTO genaifoundry.ce_cexp_logs      
    (created_on, environment, session_time, "lead", enquiry, complaint, summary, whatsapp_content, next_best_action, session_id, lead_explanation, sentiment, sentiment_explanation, connectionid, input_token, output_token,topic)
    VALUES(CURRENT_TIMESTAMP, %s, CURRENT_TIMESTAMP, %s, 0, 0, %s, %s, %s, %s, %s, %s, %s, %s, 0, 0,%s);'''             
            values = ('',None,'','','',session_id,'','','','','')            
            res = insert_db(insert_query,values)   
            return tool_response

    if event_type == 'hospital_chat_tool':  
        
            # api_gateway_client = boto3.client('apigatewaymanagementapi', endpoint_url=gateway_url)
            # e = json.loads(event["body"])  
            chat = event['chat']
            session_id = event['session_id']   
            connectionId = event["connectionId"]
            print(connectionId,"connectionid_printtt")
            chat_history = []


            if session_id == None or session_id == 'null' or session_id == '':
                session_id = str(uuid.uuid4())
            
            else:
                query = f'''select question,answer 
                        from {schema}.{hospital_chat_history_table} 
                        where session_id = '{session_id}' 
                        order by created_on desc limit 20;'''
                history_response = select_db(query)
                print("history_response is ",history_response)

                if len(history_response) > 0:
                    for chat_session in reversed(history_response):  
                        chat_history.append({'role': 'user', 'content': [{"type" : "text",'text': chat_session[0]}]})
                        chat_history.append({'role': 'assistant', 'content': [{"type" : "text",'text': chat_session[1]}]})
            
                #APPENDING CURRENT USER QUESTION
            chat_history.append({'role': 'user', 'content': [{"type" : "text",'text': chat}]})
                
            print("CHAT HISTORY : ",chat_history)

            tool_response = hospital_agent_invoke_tool(chat_history, session_id,chat,connectionId)
            print("TOOL RESPONSE: ", tool_response)  
            #insert into hospital_chat_history_table
            query = f'''
                    INSERT INTO {schema}.{hospital_chat_history_table}
                    (session_id, question, answer, input_tokens, output_tokens, created_on, updated_on)
                    VALUES( %s, %s, %s, %s, %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);
                    '''
            values = (str(session_id),str(chat), str(tool_response['answer']), str(tool_response['input_tokens']), str(tool_response['output_tokens']))
            res = insert_db(query, values)
            print("response:",res)


            
            print(type(session_id))   
            insert_query = f'''  INSERT INTO genaifoundry.ce_cexp_logs      
    (created_on, environment, session_time, "lead", enquiry, complaint, summary, whatsapp_content, next_best_action, session_id, lead_explanation, sentiment, sentiment_explanation, connectionid, input_token, output_token,topic)
    VALUES(CURRENT_TIMESTAMP, %s, CURRENT_TIMESTAMP, %s, 0, 0, %s, %s, %s, %s, %s, %s, %s, %s, 0, 0,%s);'''             
            values = ('',None,'','','',session_id,'','','','','')            
            res = insert_db(insert_query,values)   
            return tool_response

    if event_type == 'healthcare_chat_tool':  
        
            # api_gateway_client = boto3.client('apigatewaymanagementapi', endpoint_url=gateway_url)
            # e = json.loads(event["body"])  
            chat = event['chat']
            session_id = event['session_id']   
            connectionId = event["connectionId"]
            print(connectionId,"connectionid_printtt")
            chat_history = []


            if session_id == None or session_id == 'null' or session_id == '':
                session_id = str(uuid.uuid4())
            
            else:
                query = f'''select question,answer 
                        from {schema}.{hospital_chat_history_table} 
                        where session_id = '{session_id}' 
                        order by created_on desc limit 20;'''
                history_response = select_db(query)
                print("history_response is ",history_response)

                if len(history_response) > 0:
                    for chat_session in reversed(history_response):  
                        chat_history.append({'role': 'user', 'content': [{"type" : "text",'text': chat_session[0]}]})
                        chat_history.append({'role': 'assistant', 'content': [{"type" : "text",'text': chat_session[1]}]})
            
                #APPENDING CURRENT USER QUESTION
            chat_history.append({'role': 'user', 'content': [{"type" : "text",'text': chat}]})
                
            print("CHAT HISTORY : ",chat_history)

            tool_response = hospital_agent_invoke_tool(chat_history, session_id,chat,connectionId)
            print("TOOL RESPONSE: ", tool_response)  
            #insert into hospital_chat_history_table
            query = f'''
                    INSERT INTO {schema}.{hospital_chat_history_table}
                    (session_id, question, answer, input_tokens, output_tokens, created_on, updated_on)
                    VALUES( %s, %s, %s, %s, %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);
                    '''
            values = (str(session_id),str(chat), str(tool_response['answer']), str(tool_response['input_tokens']), str(tool_response['output_tokens']))
            res = insert_db(query, values)
            print("response:",res)


            
            print(type(session_id))   
            insert_query = f'''  INSERT INTO genaifoundry.ce_cexp_logs      
    (created_on, environment, session_time, "lead", enquiry, complaint, summary, whatsapp_content, next_best_action, session_id, lead_explanation, sentiment, sentiment_explanation, connectionid, input_token, output_token,topic)
    VALUES(CURRENT_TIMESTAMP, %s, CURRENT_TIMESTAMP, %s, 0, 0, %s, %s, %s, %s, %s, %s, %s, %s, 0, 0,%s);'''             
            values = ('',None,'','','',session_id,'','','','','')            
            res = insert_db(insert_query,values)   
            return tool_response

    if event_type == "generate_retail_summary":     
        
        print("RETAIL SUMMARY GENERATION ")
        session_id = event["session_id"]
        chat_query = f'''
        SELECT question,answer
        FROM {schema}.{retail_chat_history_table}    
        WHERE session_id = '{session_id}';
        '''

        chat_details = select_db(chat_query)
        print("RETAIL CHAT DETAILS : ",chat_details)
        history = ""

        for chat in chat_details:
            history1 = "Human: "+chat[0]
            history2 = "Bot: "+chat[1]
            history += "\n"+history1+"\n"+history2+"\n"
        print("RETAIL HISTORY : ",history)
        prompt_query = f"SELECT analytics_prompt from {schema}.{prompt_metadata_table} where id = 5;"
        prompt_response = select_db(prompt_query)
        prompt_template =f''' <Instruction>
        Based on the conversation above, please provide the output in the following format:
        Topic:
		- Identify the main topic of the conversation, it should be a single word topic
        Conversation Type:
        - Identify if the conversation is an Enquiry or a Complaint. If both are present, classify it as (Enquiry/Complaint).
        - Consider the emotional tone and context to determine the type.
        
        Conversation Summary Explanation:
        - Explain why you labelled the conversation as Enquiry, Complaint, or both.
        - Highlight the key questions, concerns, or issues raised by the customer.
	-IMPORTANT: keep the summary in 2-3 lines
        
        Detailed Summary:
        - Provide a clear summary of the conversation, capturing the customer's needs, questions, and any recurring themes.
	- IMPORTANT: keep the summary in 2-3 lines keep it short

        
	
        Conversation Sentiment:
        - Analyse overall sentiment of conversation carried out by the customer with the sales representative.
		- Analyse the tone and feelings associated within the conversation.
		- possible values are (Positive/Neutral/Negative)
     	- Only provide the final sentiment here in this key. 
        Conversation Sentiment Generated Details:
        - Explain why you labelled the conversation sentiment as Positive/Neutral/Negative.
        - Consider customer satisfaction, tone, and overall interaction quality.
        - Note any frustrations, appreciation, or neutral responses expressed by the customer.
        
        
        Lead Sentiment:
        - Indicate if potential sales leads are generated from the conversation (Yes/No).
        
        Leads Generated Details:
        - Explain why you labelled the Lead as Yes/No.
        - List potential leads, noting any interest in products, services, or purchases.
        - Highlight specific customer questions, preferences, or purchase intentions that could lead to sales.
        - Include details about product categories, price ranges, or specific items mentioned.
        - Suggest retail-specific approaches to engage each lead based on their shopping needs and preferences.
        
        Action to be Taken:
        - Outline next steps for the sales representative to follow up on the retail opportunities identified.
        - Include any necessary follow-up actions such as: product recommendations, size/color availability checks, price quotes, store visit scheduling, or promotional offers.
        - Suggest specific retail solutions like product demonstrations, size consultations, or exclusive deals.
        
        WhatsApp Followup Creation:
		- Craft a highly personalized follow-up WhatsApp message to engage the customer effectively as a retail sales representative.
		- Ensure to provide a concise response and make it as brief as possible. Maximum 2-3 lines as it should be shown in the whatsapp mobile screen, so make the response brief.
        - Incorporate key details from the conversation script to show understanding and attentiveness (VERY IMPORTANT: ONLY INCLUDE DETAILS FROM THE CONVERSATION DO NOT HALLUCINATE ANY DETAILS).
        - Tailor the WhatsApp message to address specific retail concerns, provide product solutions, and include a compelling call-to-action.
        - Include retail-specific elements like product availability, special offers, store promotions, or exclusive deals.
        - Infuse a sense of urgency or exclusivity to prompt customer response (limited stock, seasonal sales, etc.).
		- Format the WhatsApp message with real line breaks for each paragraph (not the string n). Use actual newlines to separate the greeting, body, call-to-action, and closing. 
	
	Follow the structure of the sample WhatsApp message below:
	<format_for_whatsapp_message>

Hi! Thanks for your interest in AnyRetail! 

You were looking for [Product/Category]. Here's what I can offer:

1. [Product/Offer 1]  
2. [Product/Offer 2]

I can help you with [Specific Assistance - size check, availability, discount]. Just let me know your [Preference/Requirement].

Limited stock available - reach out soon!

</format_for_whatsapp_message>
	- Before providing the whatsapp response, it is very critical that you double check if its in the provided format


<language_constraints>

If the conversation history (customer questions and sales rep answers) is primarily in Tagalog, then provide the values for all JSON keys in Tagalog. Otherwise, provide the values strictly in English.
If the conversation history is dominantly in Tagalog, provide the value for "Topic" in Tagalog; otherwise, provide it in English.
Always keep the JSON keys in English exactly as specified below:
"Topic":
"Conversation Type":  
"Conversation Summary Explanation":
"Detailed Summary": 
"Conversation Sentiment":
"Conversation Sentiment Generated Details":
"Lead Sentiment":
"Leads Generated Details": 
"Action to be Taken": 
"Whatsapp Creation":   

Only the **values** for each key should switch between English or Tagalog based on the dominant language in the conversation. Never translate or modify the keys. 

</language_constraints>


	
        
</Instruction> 
return output in JSON in a consistent manner
"Topic":
"Conversation Type":  
"Conversation Summary Explanation":
"Detailed Summary": 
"Conversation Sentiment":
"Conversation Sentiment Generated Details":
"Lead Sentiment":
"Leads Generated Details": 
"Action to be Taken": 
"Whatsapp Creation":   
these are the keys to be always used while returning response. Strictly do not add key values of your own.'''
        print("RETAIL PROMPT : ",prompt_template)
        template = f'''
        <Conversation>
        {history}
        </Conversation>
        {prompt_template}
        '''

        # - Ensure the email content is formatted correctly with new lines. USE ONLY "\n" for new lines. 
        #         - Ensure the email content is formatted correctly for new lines instead of using new line characters.
        response = bedrock_client.invoke_model(contentType='application/json', body=json.dumps({
            "anthropic_version": "bedrock-2023-05-31",  
            "max_tokens": 4000,     
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": template},
                    ]
                }
            ],
        }), modelId=model_id)                                                                                                                       

        inference_result = response['body'].read().decode('utf-8')
        final = json.loads(inference_result)
        out=final['content'][0]['text']
        print(out)
        llm_out = extract_sections(out)
        

        topic = "" 
        conversation_type = ""
        conversation_summary_explanation = ""
        detailed_summary = ""
        conversation_sentiment = ""
        conversation_sentiment_generated_details = ""
        lead_sentiment = ""
        leads_generated_details = ""
        action_to_be_taken = ""
        email_creation = ""
        
        try:
            if "Topic" in llm_out:
                topic = llm_out['Topic']
        except:
            topic = ""
        
        try:
            
            if 'Conversation Type' in llm_out:
                conversation_type = llm_out['Conversation Type']
                if conversation_type == "N/A":
                    enquiry, complaint = (0, 0)
                else:
                    enquiry, complaint = (1, 0) if conversation_type == "Enquiry" else (0, 1)
        except:
            enquiry , complaint = 0,0
            
        try:
            if 'Conversation Summary Explanation' in llm_out:
                conversation_summary_explanation = llm_out['Conversation Summary Explanation']
        except:
            conversation_summary_explanation= ""
        
        try:
            if 'Detailed Summary' in llm_out:
                detailed_summary = llm_out['Detailed Summary']
        except:
            detailed_summary = ""
        
        try:
            if 'Conversation Sentiment' in llm_out:
                conversation_sentiment = llm_out['Conversation Sentiment']
        except:
            conversation_sentiment = ""
        
        try:
            if 'Conversation Sentiment Generated Details' in llm_out:
                conversation_sentiment_generated_details = llm_out['Conversation Sentiment Generated Details']
        except:
            conversation_generated_details = ""
            
        try:
            if 'Lead Sentiment' in llm_out:
                lead_sentiment = llm_out['Lead Sentiment']
                lead = 1 if lead_sentiment == "Hot" else 0
        except:
            lead = 0
        
        try:
            if 'Leads Generated Details' in llm_out:
                leads_generated_details = llm_out['Leads Generated Details']
        except:
            leads_generated_details = ""
        
        try:
            if 'Action to be Taken' in llm_out:   
                action_to_be_taken = llm_out['Action to be Taken']
        except:
            action_to_be_taken = ""
        
        try:
            if 'Whatsapp Creation' in llm_out:
                email_creation = llm_out['Whatsapp Creation']
                # Clean up any literal \n characters in WhatsApp content
                email_creation = email_creation.replace('\\n', '\n').replace('\\r', '\r').replace('\\t', '\t')
        except:
            email_creation = ""
        detailed_summary = detailed_summary.replace("'", "''")
        email_creation = email_creation.replace("'", "''")
        action_to_be_taken = action_to_be_taken.replace("'", "''")
        leads_generated_details = leads_generated_details.replace("'", "''")
        conversation_sentiment_generated_details = conversation_sentiment_generated_details.replace("'", "''")        
        
        print("LEAD : ",lead)
        print("ENQUIRY : ",enquiry)
        print("COMPLAINT : ",complaint)
        print("conversation_type:", conversation_type)
        print("Topic: ",topic)
        print("Sentiment Explanation:", conversation_summary_explanation)
        print("Detailed summary:", detailed_summary)
        print("CONVERSATION SENTIMENT :",conversation_sentiment)
        print("CONVERSATION SENTIMENT DETAILS:",conversation_sentiment_generated_details)
        print("lead Sentiment:", lead_sentiment)
        print("lead explanation:", leads_generated_details)
        print("next_best_action:",action_to_be_taken)
        print("email_content:",email_creation)
        session_time = datetime.now()
        update_query = f'''UPDATE {schema}.{CHAT_LOG_TABLE}
        SET 
            lead = {lead},
            lead_explanation = '{leads_generated_details}',
            sentiment = '{conversation_sentiment}',
            sentiment_explanation = '{conversation_sentiment_generated_details}',
            session_time = '{session_time}',
            enquiry = {enquiry},
            complaint = {complaint},
            summary = '{detailed_summary}',
            whatsapp_content = '{email_creation}',
            next_best_action = '{action_to_be_taken}',
            topic = '{topic}'
        WHERE 
            session_id = '{session_id}' 
            '''
        update_db(update_query)
        return {
                "statusCode" : 200,
                "message" : "Retail Summary Successfully Generated"
            }

    if event_type == 'list_retail_summary':
        session_id = event['session_id']
        chat_query = f'''
        SELECT question,answer
        FROM {schema}.{retail_chat_history_table}    
        WHERE session_id = '{session_id}';
        '''

        chat_details = select_db(chat_query)
        print("RETAIL CHAT DETAILS : ",chat_details)
        history = []

        for chat in chat_details:
            history.append({"Human":chat[0],"Bot":chat[1]})
        print("RETAIL HISTORY : ",history)  
        select_query = f'''select summary, whatsapp_content, sentiment, topic  from genaifoundry.ce_cexp_logs ccl where session_id = '{session_id}';'''
        summary_details = select_db(select_query)
        final_summary = {}
        for i in summary_details:  
            # print("i:",i)  
            final_summary['summary'] = i[0]
            final_summary['whatsapp_content'] = i[1]
            final_summary['sentiment'] = i[2]
            final_summary['Topic'] = i[3]   
            
        # print(summary_details) 
        # print(final_summary)   
        return {"transcript":history,"final_summary":final_summary}

    # === IMAGE GENERATION EVENT TYPES ===
    if event_type == 'enhance_prompt':
        try:
            simple_prompt = event.get('prompt', '')
            if not simple_prompt:
                return {
                    "statusCode": 400,
                    "message": "Prompt is required"
                }
            
            # Import required modules for image generation
            import boto3
            import json
            import re
            from botocore.config import Config
            from botocore.exceptions import ClientError
            
            # Configuration
            LLAMA3_MODEL_ID = "us.meta.llama3-3-70b-instruct-v1:0"
            LLAMA_REGION = "us-east-1"
            
            def enhance_prompt_function(simple_prompt):
                import boto3
                client = boto3.client("bedrock-runtime", region_name=LLAMA_REGION)

                instruction_prompt = f"""
<|begin_of_text|><|start_header_id|>user<|end_header_id|>
You are a prompt engineer. Given a user input, create a very short (2-3 lines max) enhanced prompt for photorealistic image generation.

Return a JSON object with:
- "text": A concise, vivid description (2-3 lines only) with key details like lighting, background, style
- "negativeText": Brief list of things to avoid (blurry, cartoonish, watermark, low-quality)

User prompt: "{simple_prompt}"
<|eot_id|>
<|start_header_id|>assistant<|end_header_id|>
"""
                request_body = {
                    "prompt": instruction_prompt,
                    "max_gen_len": 512,
                    "temperature": 0.7
                }
                
                try:
                    response = client.invoke_model(
                        modelId=LLAMA3_MODEL_ID,
                        body=json.dumps(request_body)
                    )
                    model_output = json.loads(response["body"].read())
                    generation = model_output.get("generation", "")

                    # Extract JSON block from markdown if present, or find JSON object in the response
                    match = re.search(r'```json\s*(\{.*?\})\s*```', generation, re.DOTALL)
                    if match:
                        json_text = match.group(1)
                    else:
                        # Look for JSON object in the response (without markdown formatting)
                        json_match = re.search(r'\{.*\}', generation, re.DOTALL)
                        if json_match:
                            json_text = json_match.group(0)
                        else:
                            json_text = generation.strip()

                    # Parse JSON
                    result = json.loads(json_text)
                    text_prompt = result.get("text", "")
                    negative_prompt = result.get("negativeText", "")
                    return text_prompt, negative_prompt

                except json.JSONDecodeError:
                    print("LLM response was not valid JSON.")
                    print("Raw output: %s", generation)
                except (ClientError, Exception) as e:
                    print(f"ERROR: Could not invoke Llama 3 model. Reason: {e}")
                return None, None
            
            text_prompt, negative_prompt = enhance_prompt_function(simple_prompt)
            
            if not text_prompt or not negative_prompt:
                return {
                    "statusCode": 500,
                    "message": "Failed to enhance prompt"
                }
            
            return {
                "statusCode": 200,
                "enhanced_prompt": {
                    "text": text_prompt,
                    "negativeText": negative_prompt
                }
            }
            
        except Exception as e:
            print(f"Error in enhance_prompt: {e}")
            return {
                "statusCode": 500,
                "message": f"Internal server error: {str(e)}"
            }


    if event_type == 'generate_image':
        try:
            # Check if enhanced_prompt is provided (from enhance_prompt step)
            enhanced_prompt = event.get('enhanced_prompt', {})
            text_prompt = enhanced_prompt.get('text', '')
            negative_prompt = enhanced_prompt.get('negativeText', '')
            
            # If no enhanced_prompt, check for direct text input
            if not text_prompt:
                direct_text = event.get('text', '')
                if not direct_text:
                    return {
                        "statusCode": 400,
                        "message": "Either enhanced_prompt or direct text is required"
                    }
                
                # Extract text and negative text from direct input
                # Import required modules for text processing
                import boto3
                import json
                import re
                from botocore.config import Config
                from botocore.exceptions import ClientError
                
                # Configuration
                LLAMA3_MODEL_ID = "us.meta.llama3-3-70b-instruct-v1:0"
                LLAMA_REGION = "us-east-1"
                
                def extract_text_and_negative(direct_text):
                    import boto3
                    client = boto3.client("bedrock-runtime", region_name=LLAMA_REGION)

                    instruction_prompt = f"""
<|begin_of_text|><|start_header_id|>user<|end_header_id|>
You are a prompt engineer. Given a user input, create a very short (2-3 lines max) enhanced prompt for photorealistic image generation.

Return a JSON object with:
- "text": A concise, vivid description (2-3 lines only) with key details like lighting, background, style
- "negativeText": Brief list of things to avoid (blurry, cartoonish, watermark, low-quality)

User prompt: "{direct_text}"
<|eot_id|>
<|start_header_id|>assistant<|end_header_id|>
"""
                    
                    request_body = {
                        "prompt": instruction_prompt,
                        "max_gen_len": 512,
                        "temperature": 0.7
                    }
                    
                    try:
                        response = client.invoke_model(
                            modelId=LLAMA3_MODEL_ID,
                            body=json.dumps(request_body)
                        )
                        model_output = json.loads(response["body"].read())
                        generation = model_output.get("generation", "")

                        # Extract JSON block from markdown if present, or find JSON object in the response
                        match = re.search(r'```json\s*(\{.*?\})\s*```', generation, re.DOTALL)
                        if match:
                            json_text = match.group(1)
                        else:
                            # Look for JSON object in the response (without markdown formatting)
                            json_match = re.search(r'\{.*\}', generation, re.DOTALL)
                            if json_match:
                                json_text = json_match.group(0)
                            else:
                                json_text = generation.strip()

                        # Parse JSON
                        result = json.loads(json_text)
                        text_prompt = result.get("text", "")
                        negative_prompt = result.get("negativeText", "")
                        return text_prompt, negative_prompt

                    except json.JSONDecodeError:
                        print("LLM response was not valid JSON.")
                        print("Raw output: %s", generation)
                    except (ClientError, Exception) as e:
                        print(f"ERROR: Could not invoke Llama 3 model. Reason: {e}")
                    return None, None
                
                # Extract text and negative text from direct input
                text_prompt, negative_prompt = extract_text_and_negative(direct_text)
                
                if not text_prompt or not negative_prompt:
                    return {
                        "statusCode": 500,
                        "message": "Failed to extract text and negative text from direct input"
                    }
            

            
            # Import required modules
            import boto3
            import json
            import base64
            from botocore.config import Config
            from botocore.exceptions import ClientError
            
            # Configuration
            NOVA_MODEL_ID = "amazon.nova-canvas-v1:0"
            NOVA_REGION = "us-east-1"
            
            class ImageError(Exception):
                def __init__(self, message):
                    self.message = message
            
            def generate_image_function(model_id, body):
                print(f"Generating image with Amazon Nova Canvas model: {model_id}")
                import boto3
                from botocore.config import Config

                bedrock = boto3.client(
                    service_name='bedrock-runtime',
                    region_name=NOVA_REGION,
                    config=Config(read_timeout=300)
                )

                response = bedrock.invoke_model(
                    body=body,
                    modelId=model_id,
                    accept="application/json",
                    contentType="application/json"
                )

                response_body = json.loads(response.get("body").read())

                if "error" in response_body:
                    raise ImageError(f"Image generation error: {response_body['error']}")

                images = response_body.get("images", [])
                if not images:
                    raise ImageError("No images returned from model")

                # If multiple images requested, return all images
                if len(images) > 1:
                    image_bytes_list = []
                    for base64_image in images:
                        base64_bytes = base64_image.encode('ascii')
                        image_bytes = base64.b64decode(base64_bytes)
                        image_bytes_list.append(image_bytes)
                    
                    print(f"Generated {len(image_bytes_list)} images successfully.")
                    return image_bytes_list
                else:
                    # Single image
                    base64_image = images[0]
                    base64_bytes = base64_image.encode('ascii')
                    image_bytes = base64.b64decode(base64_bytes)

                    print("Image generated successfully.")
                    return image_bytes
            
            # Get dynamic settings from frontend or use defaults
            number_of_images = event.get('numberOfImages', 1)
            image_height = event.get('height', 720)
            image_width = event.get('width', 1280)
            image_quality = event.get('quality', 'standard')
            
            # Validate inputs
            if not isinstance(number_of_images, int) or number_of_images < 1 or number_of_images > 8:
                number_of_images = 1
            if not isinstance(image_height, int) or image_height < 256 or image_height > 2048:
                image_height = 720
            if not isinstance(image_width, int) or image_width < 256 or image_width > 2048:
                image_width = 1280
            if image_quality not in ['standard', 'premium', 'ultra']:
                image_quality = 'standard'
            
            # Prepare request body
            if isinstance(negative_prompt, list):
                negative_prompt = ", ".join(negative_prompt)
                
            request_body = {
                "taskType": "TEXT_IMAGE",
                "textToImageParams": {
                    "text": text_prompt,
                    "negativeText": negative_prompt
                },
                "imageGenerationConfig": {
                    "numberOfImages": number_of_images,
                    "height": image_height,
                    "width": image_width,
                    "quality": image_quality,
                    "cfgScale": 7.5,
                    "seed": 12
                }
            }

            try:
                image_bytes = generate_image_function(NOVA_MODEL_ID, json.dumps(request_body))
                
                # Check if we have multiple images
                if number_of_images > 1:
                    # For multiple images, we need to handle the response differently
                    # The generate_image_function should return all images
                    if isinstance(image_bytes, list):
                        # Multiple images returned
                        images_base64 = []
                        for i, img_bytes in enumerate(image_bytes):
                            img_base64 = base64.b64encode(img_bytes).decode('utf-8')
                            images_base64.append({
                                "index": i,
                                "image_base64": img_base64
                            })
                        
                        return {
                            "statusCode": 200,
                            "images": images_base64,
                            "total_images": len(images_base64),
                            "message": f"Generated {len(images_base64)} images successfully"
                        }
                    else:
                        # Single image but multiple requested - this shouldn't happen
                        image_base64 = base64.b64encode(image_bytes).decode('utf-8')
                        return {
                            "statusCode": 200,
                            "image_base64": image_base64,
                            "message": "Image generated successfully (only 1 image returned despite requesting multiple)"
                        }
                else:
                    # Single image - convert to base64 for response
                    image_base64 = base64.b64encode(image_bytes).decode('utf-8')
                    
                    return {
                        "statusCode": 200,
                        "image_base64": image_base64,
                        "message": "Image generated successfully"
                    }

            except ClientError as err:
                message = err.response["Error"]["Message"]
                print(f"A client error occurred: {message}")
                return {
                    "statusCode": 500,
                    "message": f"Client error: {message}"
                }

            except ImageError as err:
                print(f"Image generation failed: {err.message}")
                return {
                    "statusCode": 500,
                    "message": err.message
                }

        except Exception as e:
            print(f"Error in generate_image: {e}")
            return {
                "statusCode": 500,
                "message": f"Internal server error: {str(e)}"
            }
        
    elif event_type == 'vid_generation':
        return generate_video_from_image(event)
    elif event_type == 'vid_generation_text':
        return generate_video_from_text(event)
    elif event_type == 'check_vid_gen_status':
        return check_video_link(event)
    elif event_type == 'product_review_analyzer':
        return analyze_reviews_summary(event)
    elif event_type == 'voiceops':
        try:
            url =f"http://{ec2_instance_ip}:8000/transcribe"
            kb_id=''
            prompt_template = ''
            print("yes")
            if event['box_type'] == 'insurance':
                kb_id = KB_ID
                print("kb_id",kb_id)
                prompt_template='''You are a Virtual Insurance Assistant for AnyBank. Give quick, helpful answers that sound natural when spoken aloud.

                        RESPONSE RULES:
                        - Maximum 2 sentences per response
                        - Use simple, conversational language
                        - No bullet points, brackets, or special formatting
                        - No technical jargon or complex terms
                        - Answer only what the customer asked
                        - Skip greetings and confirmations

                        SPEAKING STYLE:
                        - Talk like you're having a friendly conversation
                        - Use short, clear sentences
                        - Avoid reading lists or multiple options
                        - Give one direct answer, not explanations

                        Search Results: $search_results$

                        Customer Question: $query$

                        Provide a brief, conversational response that directly answers their question. '''


            else: 
                kb_id = bank_kb_id
                print("bank_kb_id",bank_kb_id)
                prompt_template=''' 
                You are a Virtual Banking Assistant for AnyBank. Give quick, helpful answers that sound natural when spoken aloud.
    
                RESPONSE RULES:
                - Maximum 2 sentences per response
                - Use simple, conversational language
                - No bullet points, brackets, or special formatting
                - No technical jargon or complex terms
                - Answer only what the customer asked
                - Skip greetings and confirmations
                
                SPEAKING STYLE:
                - Talk like you're having a friendly conversation
                - Use short, clear sentences
                - Avoid reading lists or multiple options
                - Give one direct answer, not explanations
                
                Search Results: $search_results$
                
                Customer Question: $query$
                
                Provide a brief, conversational response that directly answers their question.
                '''
            payload = json.dumps({
            "kb_id": kb_id,
            "session_id": event['session_id'],
            "audio": event['audio'],
            "connection_id":event['connectionId'],
            "connection_url":event['connection_url'],
            "box_type": event['box_type'],
            "prompt_template":prompt_template,
            "bucket_name":bucket_name,
            "region_name":region_name
            })
            headers = {
            'Content-Type': 'application/json'
            }
            print(payload)
            response = requests.request("POST", url, headers=headers, data=payload)

            return response.text

        except Exception as e:
            return {
                'statusCode': 500,
                'body': json.dumps({
                    'message': 'Error processing transcription',
                    'error': str(e)
                })
            }
        
  

    if event_type == 'virtual_tryon':
        try:
            # Import required modules
            import boto3
            import json
            import base64
            from botocore.config import Config
            from botocore.exceptions import ClientError
            
            # AWS Configuration
            AWS_REGION = "us-east-1"
            
            # Model Configuration
            NOVA_MODEL_ID = "amazon.nova-canvas-v1:0"
            
            # Get S3 URIs from event
            person_s3_uri = event.get('person_s3_uri')
            style_s3_uri = event.get('style_s3_uri')
            
            if not person_s3_uri or not style_s3_uri:
                return {
                    "statusCode": 400,
                    "message": "Both person_s3_uri and style_s3_uri are required"
                }
            
            def download_from_s3(s3_uri):
                """Download image from S3 and convert to base64"""
                try:
                    import boto3
                    
                    # Get bucket name from environment variable
                    bucket_name = os.environ.get('S3_BUCKET')
                    if not bucket_name:
                        raise ValueError("S3_BUCKET environment variable is not set")
                    
                    # Extract image name from the original S3 URI
                    # Example: "s3://genaifoundryc-y2t1oh/virtualtryon/person4.jpg" -> "person4.jpg"
                    if s3_uri.startswith('s3://'):
                        s3_uri = s3_uri[5:]  # Remove 's3://' prefix
                    
                    # Split and get the image name (last part after the last '/')
                    image_name = s3_uri.split('/')[-1]
                    
                    # Construct new S3 key with virtualtryon prefix
                    s3_key = f"virtualtryon/{image_name}"
                    
                    print(f"Downloading image from bucket: {bucket_name}, key: {s3_key}")
                    
                    # Create S3 client
                    s3_client = boto3.client(
                        's3',
                        region_name=AWS_REGION
                    )
                    print(bucket_name)
                    
                    # Download image from S3
                    response = s3_client.get_object(Bucket=bucket_name, Key=s3_key)
                    image_bytes = response['Body'].read()
                    
                    # Convert to base64
                    base64_string = base64.b64encode(image_bytes).decode('utf-8')
                    return base64_string
                    
                except Exception as e:
                    print(f"Error downloading from S3 {s3_uri}: {e}")
                    return None
            
            def create_virtual_tryon_payload(person_image_base64, style_image_base64):
                """Create the inference payload for virtual try-on"""
                inference_params = {
                    "taskType": "VIRTUAL_TRY_ON",
                    "virtualTryOnParams": {
                        "sourceImage": person_image_base64,
                        "referenceImage": style_image_base64,
                        "maskType": "GARMENT",
                        "garmentBasedMask": {"garmentClass": "UPPER_BODY"}
                    }
                }
                return inference_params
            
            def generate_virtual_tryon_image(model_id, body):
                """Generate virtual try-on image using Bedrock"""
                try:
                    from botocore.config import Config
                    import boto3
                    
                    config = Config(
                        retries={
                            'max_attempts': 3,
                            'mode': 'standard'
                        }
                    )
                    
                    bedrock = boto3.client(
                        service_name="bedrock-runtime", 
                        region_name=AWS_REGION,
                        config=config
                    )
                    
                    response = bedrock.invoke_model(
                        body=body,
                        modelId=model_id,
                        accept="application/json",
                        contentType="application/json"
                    )
                    
                    response_body_json = json.loads(response.get("body").read())
                    images = response_body_json.get("images", [])
                    
                    # Check for errors
                    if response_body_json.get("error"):
                        raise Exception(f"Model error: {response_body_json.get('error')}")
                    
                    if not images:
                        raise Exception("No images returned from model")
                    
                    # Return the first image (virtual try-on typically returns one image)
                    return base64.b64decode(images[0])
                    
                except Exception as e:
                    raise Exception(f"Error generating virtual try-on image: {e}")
            
            print("Starting Virtual Try-On Process...")
            
            # Download images from S3 and convert to base64
            print("Downloading images from S3...")
            person_image_base64 = download_from_s3(person_s3_uri)
            style_image_base64 = download_from_s3(style_s3_uri)
            
            if not person_image_base64 or not style_image_base64:
                return {
                    "statusCode": 500,
                    "message": "Failed to download one or both images from S3"
                }
            
            print("✅ Images downloaded and encoded successfully")
            
            # Create the inference payload
            inference_params = create_virtual_tryon_payload(person_image_base64, style_image_base64)
            body_json = json.dumps(inference_params, indent=2)
            
            print("Invoking Nova Canvas for virtual try-on...")
            
            try:
                # Generate virtual try-on image
                image_bytes = generate_virtual_tryon_image(NOVA_MODEL_ID, body_json)
                
                # Convert to base64 for response
                result_base64 = base64.b64encode(image_bytes).decode('utf-8')
                
                print("✅ Virtual try-on completed successfully!")
                
                return {
                    "statusCode": 200,
                    "image_base64": result_base64,
                    "message": "Virtual try-on completed successfully"
                }
                
            except Exception as e:
                print(f"❌ Error during virtual try-on generation: {e}")
                return {
                    "statusCode": 500,
                    "message": f"Virtual try-on generation failed: {str(e)}"
                }
                
        except Exception as e:
            print(f"Error in virtual_tryon: {e}")
            return {
                "statusCode": 500,
                "message": f"Internal server error: {str(e)}"
            }

#retail event type ends here....
#HealthCare Event type starts here....
    if event_type == 'deep_research':
        return deep_research_assistant_api(event)
    elif event_type == 'kyc_extraction':
        return kyc_extraction_api(event)
#HealthCare Event type ends here....

#Healthcare event function code starts here...

bedrock_runtime = boto3.client('bedrock-runtime', region_name='us-east-1')



def decompose_research_query(query: str, depth: str) -> List[str]:
    """
    Decompose a research query into specific sub-questions using LLM
    """
    try:
        # Determine number of sub-questions based on depth
        question_counts = {
            'shallow': 3,
            'medium': 5,
            'deep': 8
        }
        target_count = question_counts.get(depth, 5)
        
        prompt = f"""You are a research planning expert. Break down the following research query into {target_count} specific, focused sub-questions that will help gather comprehensive information.

Research Query: {query}

Requirements:
- Each sub-question should be specific and searchable
- Cover different aspects of the topic (background, current state, trends, implications, etc.)
- Questions should build upon each other logically
- Avoid redundancy between questions
- Make questions suitable for web search

Return only the sub-questions, one per line, numbered 1-{target_count}."""

        # Call Bedrock LLM
        response = call_bedrock_llm(prompt)
        
        # Parse sub-questions from response
        sub_questions = []
        lines = response.strip().split('\n')
        
        for line in lines:
            line = line.strip()
            if line and (line[0].isdigit() or line.startswith('-') or line.startswith('•')):
                # Remove numbering and clean up
                question = re.sub(r'^\d+\.?\s*|-\s*|•\s*', '', line).strip()
                if question and question.endswith('?'):
                    sub_questions.append(question)
        
        # Fallback if parsing fails
        if not sub_questions:
            sub_questions = [
                f"What is {query}?",
                f"What are the current trends related to {query}?",
                f"What are the key challenges or issues with {query}?",
                f"What are the latest developments in {query}?",
                f"What are the implications or future outlook for {query}?"
            ]
        
        return sub_questions[:target_count]
        
    except Exception as e:
        logger.error(f"Error decomposing research query: {e}")
        # Fallback sub-questions
        return [
            f"What is {query}?",
            f"What are recent developments in {query}?",
            f"What are the key challenges with {query}?"
        ]

def conduct_parallel_research(sub_questions: List[str], max_sources: int, 
                            time_range: str, domain_filter: List[str]) -> List[Dict]:
    """
    Conduct parallel research for multiple sub-questions using concurrent processing
    """
    import concurrent.futures
    import threading
    
    def research_single_question(sub_question: str) -> Dict:
        """Research a single sub-question"""
        try:
            print(f"🔍 Researching: {sub_question}")
            
            # Search with optimized parameters
            search_results = tavily_web_search_optimized(
                query=sub_question,
                max_results=max_sources,
                time_range=time_range,
                domain_filter=domain_filter
            )
            
            return {
                'sub_question': sub_question,
                'search_results': search_results,
                'status': 'success'
            }
            
        except Exception as e:
            logger.error(f"Error researching {sub_question}: {e}")
            return {
                'sub_question': sub_question,
                'search_results': [],
                'status': 'error',
                'error': str(e)
            }
    
    # Use ThreadPoolExecutor for parallel processing with reduced workers
    research_results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        # Submit all research tasks
        future_to_question = {
            executor.submit(research_single_question, question): question 
            for question in sub_questions
        }
        
        # Collect results as they complete
        for future in concurrent.futures.as_completed(future_to_question):
            result = future.result()
            research_results.append(result)
    
    return research_results

def tavily_web_search_optimized(query: str, max_results: int = 3, time_range: str = 'month', 
                               domain_filter: List[str] = None) -> List[Dict]:
    """
    Optimized web search with reduced parameters for faster response
    """
    try:
        headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {TAVILY_API_KEY}'
        }
        
        # Convert time_range to days
        time_mapping = {
            'day': 1,
            'week': 7,
            'month': 30,
            'year': 365
        }
        days = time_mapping.get(time_range, 30)
        
        # Optimized payload - include content but with basic search
        payload = {
            'query': query,
            'max_results': max_results,
            'search_depth': 'basic',  # Use basic for faster response
            'include_answer': True,   # Include answer for faster processing
            'include_images': False,
            'include_raw_content': True,  # Include content but with basic search
            'days': days
        }
        
        if domain_filter:
            payload['include_domains'] = domain_filter
        
        response = requests.post(
            f"{TAVILY_BASE_URL}/search",
            headers=headers,
            json=payload,
            timeout=10  # Further reduced timeout for faster response
        )
        response.raise_for_status()
        
        data = response.json()
        results = data.get('results', [])
        
        print(f"✅ Tavily search returned {len(results)} results for: {query}")
        return results
        
    except Exception as e:
        logger.error(f"Error in Tavily web search: {e}")
        return []

def tavily_web_search(query: str, max_results: int = 5, time_range: str = 'month', 
                     domain_filter: List[str] = None) -> List[Dict]:
    """
    Search the web using Tavily API
    """
    try:
        headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {TAVILY_API_KEY}'
        }
        
        # Convert time_range to days
        time_mapping = {
            'day': 1,
            'week': 7,
            'month': 30,
            'year': 365
        }
        days = time_mapping.get(time_range, 30)
        
        payload = {
            'query': query,
            'max_results': max_results,
            'search_depth': 'advanced',
            'include_answer': False,
            'include_images': False,
            'include_raw_content': True,
            'days': days
        }
        
        if domain_filter:
            payload['include_domains'] = domain_filter
        
        response = requests.post(
            f"{TAVILY_BASE_URL}/search",
            headers=headers,
            json=payload,
            timeout=30
        )
        response.raise_for_status()
        
        data = response.json()
        results = data.get('results', [])
        
        print(f"✅ Tavily search returned {len(results)} results for: {query}")
        return results
        
    except Exception as e:
        logger.error(f"Error in Tavily web search: {e}")
        return []

def tavily_extract_content(url: str) -> Optional[str]:
    """
    Extract full content from a webpage using Tavily
    """
    try:
        headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {TAVILY_API_KEY}'
        }
        
        payload = {
            'urls': [url]
        }
        
        response = requests.post(
            f"{TAVILY_BASE_URL}/extract",
            headers=headers,
            json=payload,
            timeout=30
        )
        response.raise_for_status()
        
        data = response.json()
        results = data.get('results', [])
        
        if results and len(results) > 0:
            content = results[0].get('raw_content', '')
            print(f"✅ Extracted {len(content)} characters from {url}")
            return content  # Full content, no limit
        
        return None
        
    except Exception as e:
        logger.error(f"Error extracting content from {url}: {e}")
        return None

def synthesize_research_findings(original_query: str, research_results: List[Dict], 
                               output_format: str) -> Dict:
    """
    Synthesize research findings using LLM
    """
    try:
        # Prepare research data for LLM analysis
        research_summary = f"Original Research Query: {original_query}\n\n"
        
        for i, result in enumerate(research_results, 1):
            research_summary += f"Sub-question {i}: {result['sub_question']}\n"
            research_summary += f"Sources found: {len(result['search_results'])}\n"
            
            # Add key findings from search results
            for j, search_result in enumerate(result['search_results'][:3], 1):
                research_summary += f"  {j}. {search_result['title']}\n"
                research_summary += f"     URL: {search_result['url']}\n"
                research_summary += f"     Snippet: {search_result.get('content', '')[:200]}...\n"
            
            research_summary += "\n"
        
        prompt = f"""You are a research analyst tasked with synthesizing comprehensive research findings.

{research_summary}

Your task:
1. Analyze all the research findings above
2. Identify key themes, patterns, and insights
3. Note any contradictions or gaps in the information
4. Synthesize the findings into coherent insights
5. Provide a confidence assessment for the findings

Output format: {output_format}

Guidelines:
- Be objective and analytical
- Cite sources when making claims
- Highlight the most important findings
- Note limitations or areas needing further research
- Organize information logically

Provide a structured synthesis of these research findings."""

        synthesis = call_bedrock_llm(prompt)
        
        return {
            'synthesis': synthesis,
            'confidence': 'high',  # This could be determined by LLM
            'key_themes': [],  # Could extract these with additional LLM call
            'gaps_identified': []
        }
        
    except Exception as e:
        logger.error(f"Error synthesizing research findings: {e}")
        return {
            'synthesis': f"Error occurred during synthesis: {str(e)}",
            'confidence': 'low',
            'key_themes': [],
            'gaps_identified': ['Synthesis failed due to technical error']
        }

def validate_medical_query(query: str) -> Dict:
    """
    Validate if a research query is related to medical, healthcare, or health topics
    """
    try:
        prompt = f"""You are a medical research validation expert. Analyze the following research query to determine if it's related to medical, healthcare, or health topics.

Research Query: "{query}"

Medical/Healthcare/Health topics include:
- Diseases, conditions, symptoms, and treatments
- Medications, pharmaceuticals, and drug therapies
- Medical procedures, surgeries, and interventions
- Medical devices, technologies, and equipment
- Clinical trials, research studies, and medical research
- Healthcare systems, policies, and management
- Public health initiatives and epidemiology
- Mental health and psychological treatments
- Nutrition, wellness, and preventive medicine
- Medical education and training
- Healthcare AI, telemedicine, and digital health
- Health outcomes, statistics, and population health

Determine:
1. Is this query related to medical, healthcare, or health topics? (Yes/No)
2. What is your confidence level? (High/Medium/Low)
3. If not medical, what are 3 suggested medical topics related to the query?

Respond in this exact format:
IS_MEDICAL: [Yes/No]
CONFIDENCE: [High/Medium/Low]
SUGGESTED_TOPICS: [comma-separated list of 3 medical topics if not medical, or empty if medical]"""

        response = call_bedrock_llm(prompt)
        
        # Parse the response
        is_medical = False
        confidence = "Low"
        suggested_topics = []
        
        lines = response.strip().split('\n')
        for line in lines:
            line = line.strip()
            if line.startswith('IS_MEDICAL:'):
                is_medical = 'Yes' in line
            elif line.startswith('CONFIDENCE:'):
                confidence = line.split(':')[1].strip()
            elif line.startswith('SUGGESTED_TOPICS:'):
                topics_str = line.split(':')[1].strip()
                if topics_str and topics_str != 'empty':
                    suggested_topics = [topic.strip() for topic in topics_str.split(',')]
        
        return {
            'is_medical': is_medical,
            'confidence': confidence,
            'suggested_topics': suggested_topics,
            'validation_reason': f"Query analyzed with {confidence.lower()} confidence"
        }
        
    except Exception as e:
        logger.error(f"Error validating medical query: {e}")
        # Default to rejecting the query if validation fails (safer approach)
        return {
            'is_medical': False,
            'confidence': 'Low',
            'suggested_topics': ['Medical research validation failed', 'Please try a medical/healthcare query', 'Contact support if issue persists'],
            'validation_reason': f"Validation failed due to error: {str(e)}"
        }

def generate_optimized_research_report(query: str, research_results: List[Dict], 
                                     output_format: str) -> str:
    """
    Generate an optimized research report with faster processing to avoid timeouts
    """
    try:
        # Collect all search results
        all_results = []
        for result in research_results:
            if result.get('status') == 'success':
                search_results = result.get('search_results', [])
                all_results.extend(search_results)
        
        # Limit to top results for faster processing
        top_results = all_results[:4]  # Limit to 4 results max for speed
        
        # Generate a fast, structured report without additional LLM calls
        report = f"# Research Report: {query}\n\n"
        
        # Add key findings section
        report += "## Key Findings\n\n"
        
        for i, result in enumerate(top_results, 1):
            title = result.get('title', 'Untitled')
            url = result.get('url', 'No URL available')
            
            # Try to get content from multiple sources
            content = result.get('raw_content') or result.get('content') or result.get('snippet', '')
            
            # Handle None or empty content
            if not content or content.strip() == '':
                content = 'Content not available from this source'
            
            # Truncate content for faster processing
            if len(str(content)) > 1000:
                content = str(content)[:1000] + "..."
            
            report += f"### Finding {i}: {title} [{i}]\n\n"
            report += f"**Source:** {url}\n\n"
            report += f"{content}\n\n"
        
        # Add sources section
        report += "## Sources\n\n"
        for i, result in enumerate(top_results, 1):
            report += f"[{i}] {result.get('title', 'Untitled')} - {result.get('url', 'No URL')}\n"
        
        return report
        
    except Exception as e:
        logger.error(f"Error generating optimized research report: {e}")
        return f"Error generating report: {str(e)}"

def format_research_response(research_content: str, format_style: str = None, user_query: str = None) -> str:
    """Format research content into a well-structured, properly cited response.
    
    This function mimics the format_research_response tool from the deep-research.ipynb notebook.
    It transforms raw research into polished, reader-friendly content with proper citations and optimal structure.
    """
    try:
        # Define the research formatter prompt (from the notebook)
        RESEARCH_FORMATTER_PROMPT = """
You are a specialized Research Response Formatter Agent. Your role is to transform research content into well-structured, properly cited, and reader-friendly formats.

Core formatting requirements (ALWAYS apply):
1. Include inline citations using [n] notation for EVERY factual claim
2. Provide a complete "Sources" section at the end with numbered references and urls
3. Write concisely - no repetition or filler words
4. Ensure information density - every sentence should add value
5. Maintain professional, objective tone
6. Format your response in markdown

Based on the semantics of the user's original research question, format your response in one of the following styles:
- **Direct Answer**: Concise, focused response that directly addresses the question
- **Blog Style**: Engaging introduction, subheadings, conversational tone, conclusion
- **Academic Report**: Abstract, methodology, findings, analysis, conclusions, references
- **Executive Summary**: Key findings upfront, bullet points, actionable insights
- **Bullet Points**: Structured lists with clear hierarchy and supporting details
- **Comparison**: Side-by-side analysis with clear criteria and conclusions

When format is not specified, analyze the research content and user query to determine:
- Complexity level (simple vs. comprehensive)
- Audience (general public vs. technical)
- Purpose (informational vs. decision-making)
- Content type (factual summary vs. analytical comparison)

Your response below should be polished, containing only the information that is relevant to the user's query and NOTHING ELSE.

Your final research response:
"""

        # Prepare the input for the formatter
        format_input = f"Research Content:\n{research_content}\n\n"
        
        if format_style:
            format_input += f"Requested Format Style: {format_style}\n\n"
        
        if user_query:
            format_input += f"Original User Query: {user_query}\n\n"
        
        format_input += "Please format this research content according to the guidelines and appropriate style."
        
        # Call the LLM with the formatter prompt
        response = call_bedrock_llm_with_prompt(format_input, RESEARCH_FORMATTER_PROMPT)
        
        return response
        
    except Exception as e:
        logger.error(f"Error in research formatting: {str(e)}")
        return f"Error in research formatting: {str(e)}"

def call_bedrock_llm_with_prompt(user_input: str, system_prompt: str) -> str:
    """
    Call Bedrock LLM with a custom system prompt
    """
    try:
        # Prepare the messages for the LLM
        messages = [
            {
                "role": "user",
                "content": user_input
            }
        ]
        
        # Create the request body
        request_body = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 4000,
            "system": system_prompt,
            "messages": messages
        }
        
        # Call Bedrock
        response = bedrock_runtime.invoke_model(
            modelId="us.anthropic.claude-3-5-sonnet-20241022-v2:0",
            body=json.dumps(request_body),
            contentType="application/json"
        )
        
        # Parse response
        response_body = json.loads(response['body'].read())
        return response_body['content'][0]['text']
        
    except Exception as e:
        logger.error(f"Error calling Bedrock LLM: {e}")
        return f"Error calling LLM: {str(e)}"

def generate_research_report(query: str, synthesis: Dict, research_results: List[Dict], 
                           output_format: str) -> str:
    """
    Generate final research report with proper formatting and citations
    """
    try:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        if output_format == 'summary':
            report_template = f"""# Research Summary: {query}

**Generated:** {timestamp}

## Key Findings
{synthesis['synthesis']}

## Sources Consulted
"""
        elif output_format == 'bullet_points':
            report_template = f"""# Research Findings: {query}

**Generated:** {timestamp}

## Main Points
{synthesis['synthesis']}

## Sources
"""
        else:  # detailed_report
            report_template = f"""# Deep Research Report: {query}

**Research Date:** {timestamp}
**Research Depth:** Multi-layered analysis with {len(research_results)} research vectors

## Executive Summary
{synthesis['synthesis']}

## Detailed Findings

"""
            
            # Add detailed findings for each sub-question
            for i, result in enumerate(research_results, 1):
                report_template += f"### {i}. {result['sub_question']}\n\n"
                
                if result['search_results']:
                    for j, search_result in enumerate(result['search_results'][:2], 1):
                        report_template += f"**Source {j}:** [{search_result['title']}]({search_result['url']})\n"
                        report_template += f"{search_result.get('content', 'No content available')[:300]}...\n\n"
                else:
                    report_template += "No relevant sources found for this question.\n\n"
            
            report_template += "## Sources Consulted\n\n"
        
        # Add all sources
        source_count = 1
        for result in research_results:
            for search_result in result['search_results']:
                report_template += f"{source_count}. [{search_result['title']}]({search_result['url']})\n"
                source_count += 1
        
        report_template += f"\n---\n*Report generated by Deep Research Assistant on {timestamp}*"
        
        return report_template
        
    except Exception as e:
        logger.error(f"Error generating research report: {e}")
        return f"Error generating report: {str(e)}"

def call_bedrock_llm(prompt: str, model_id: str = "us.anthropic.claude-3-7-sonnet-20250219-v1:0") -> str:
    """
    Call AWS Bedrock LLM for analysis and synthesis
    """
    try:
        # Prepare the request body for Anthropic Claude
        body = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 4000,
            "temperature": 0.1,
            "messages": [
                {
                    "role": "user",
                    "content": prompt
                }
            ]
        }
        
        response = bedrock_runtime.invoke_model(
            modelId=model_id,
            body=json.dumps(body),
            contentType="application/json"
        )
        
        response_body = json.loads(response['body'].read())
        return response_body['content'][0]['text']
        
    except Exception as e:
        logger.error(f"Error calling Bedrock LLM: {e}")
        return f"Error in LLM analysis: {str(e)}"

def validate_research_query(query: str) -> Dict:
    """
    Validate if the research query is appropriate and actionable
    """
    try:
        prompt = f"""Analyze this research query and determine if it's appropriate for web research:

Query: "{query}"

Evaluate:
1. Is this a factual, research-based question?
2. Can this be answered through web sources?
3. Is it specific enough for meaningful research?
4. Are there any ethical concerns?

Respond with JSON:
{{
    "is_valid": true/false,
    "confidence": "high/medium/low",
    "reasoning": "explanation",
    "suggested_improvements": "optional suggestions",
    "estimated_complexity": "simple/moderate/complex"
}}"""

        response = call_bedrock_llm(prompt)
        
        try:
            # Try to parse JSON response
            result = json.loads(response)
            return result
        except json.JSONDecodeError:
            # Fallback if LLM doesn't return valid JSON
            return {
                "is_valid": True,
                "confidence": "medium",
                "reasoning": "Query validation completed",
                "estimated_complexity": "moderate"
            }
        
    except Exception as e:
        logger.error(f"Error validating research query: {e}")
        return {
            "is_valid": True,
            "confidence": "low",
            "reasoning": f"Validation error: {str(e)}",
            "estimated_complexity": "unknown"
        }

def tavily_crawl_website(url: str, max_depth: int = 2) -> List[Dict]:
    """
    Crawl a website using Tavily API for comprehensive content gathering
    """
    try:
        headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {TAVILY_API_KEY}'
        }
        
        payload = {
            'url': url,
            'max_depth': max_depth,
            'max_results': 10
        }
        
        response = requests.post(
            f"{TAVILY_BASE_URL}/crawl",
            headers=headers,
            json=payload,
            timeout=60
        )
        response.raise_for_status()
        
        data = response.json()
        results = data.get('results', [])
        
        print(f"✅ Crawled {len(results)} pages from {url}")
        return results
        
    except Exception as e:
        logger.error(f"Error crawling website {url}: {e}")
        return []

def enhanced_research_with_followup(initial_results: List[Dict], original_query: str) -> List[Dict]:
    """
    Perform follow-up research based on initial findings
    """
    try:
        # Analyze initial results to identify knowledge gaps
        prompt = f"""Analyze these initial research findings and identify 2-3 follow-up questions that would provide deeper insights:

Original Query: {original_query}

Initial Findings Summary:
"""
        
        for i, result in enumerate(initial_results, 1):
            prompt += f"{i}. Sub-question: {result['sub_question']}\n"
            prompt += f"   Sources found: {len(result['search_results'])}\n"
            if result['search_results']:
                prompt += f"   Top result: {result['search_results'][0]['title']}\n"
            prompt += "\n"
        
        prompt += """
Based on these findings, what follow-up questions would help deepen the research?
Return 2-3 specific questions that address gaps or dive deeper into interesting findings.
Format as numbered list."""

        response = call_bedrock_llm(prompt)
        
        # Extract follow-up questions
        followup_questions = []
        lines = response.strip().split('\n')
        
        for line in lines:
            line = line.strip()
            if line and (line[0].isdigit() or line.startswith('-')):
                question = re.sub(r'^\d+\.?\s*|-\s*', '', line).strip()
                if question:
                    followup_questions.append(question)
        
        # Conduct follow-up research
        followup_results = []
        for question in followup_questions[:3]:  # Limit to 3 follow-ups
            print(f"🔍 Follow-up research: {question}")
            search_results = tavily_web_search(query=question, max_results=3)
            
            if search_results:
                followup_results.append({
                    'sub_question': question,
                    'search_results': search_results,
                    'extracted_content': []
                })
        
        return followup_results
        
    except Exception as e:
        logger.error(f"Error in enhanced research with follow-up: {e}")
        return []

def save_research_to_s3(report: str, query: str, bucket_name: str = "research-reports-bucket") -> str:
    """
    Save research report to S3 bucket
    """
    try:
        s3_client = boto3.client('s3')
        
        # Generate filename
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_query = re.sub(r'[^\w\s-]', '', query).strip()[:50]
        filename = f"research_report_{safe_query}_{timestamp}.md"
        
        # Upload to S3
        s3_client.put_object(
            Bucket=bucket_name,
            Key=filename,
            Body=report.encode('utf-8'),
            ContentType='text/markdown'
        )
        
        s3_uri = f"s3://{bucket_name}/{filename}"
        print(f"✅ Research report saved to: {s3_uri}")
        return s3_uri
        
    except Exception as e:
        logger.error(f"Error saving research to S3: {e}")
        return f"Error saving report: {str(e)}"
def test_deep_research_api():
    """
    Test function for the deep research API
    """
    test_event = {
        'event_type': 'deep_research',
        'research_query': 'Impact of artificial intelligence on healthcare in 2024',
        'research_depth': 'medium',
        'max_sources': 15,
        'time_range': 'month',
        'output_format': 'detailed_report'
    }
    
    result = deep_research_assistant_api(test_event)
    print(json.dumps(result, indent=2))
    return result
#Healthcare event function code ends here...

#KYC Data Extraction API
def kyc_extraction_api(event):
    """
    KYC Data Extraction API for extracting information from images/documents
    """
    try:
        # Extract the single variable (document data only)
        document_data = event.get('document_data', '')
        session_id = event.get('session_id', str(uuid.uuid4()))
        
        if not document_data:
            return {
                "statusCode": 400,
                "error": "document_data is required",
                "session_id": session_id
            }
        
        # Use the existing Bedrock client to process the KYC extraction
        prompt_template = f"""You are a document data viewer, your task is to view the information provided to you. Before providing your answer, provide your reasoning or approach as if you have viewed the document and extracted that information in a  neat and clear manner.
        below is the document data 
        {document_data}
<reasoning>
This is the obtained document that contains the information to be processed. My reasoning approach is to systematically scan through the document content and identify all relevant data fields, structured information, and key details present in the provided document.
</reasoning>

Please extract the information from the provided data like the sample data provided below 
<sample1>:
<Reasoning>
The document data have been provided and it seems like an identification card and iam going to extract the important fields from the provided document 
</Reasoning>
<information>
Identity Card Number: 123456789
Name: MARIE JUMIO
Race: CHINESE
Sex: F
Date of Birth: 1975-01-01
Country of Birth: SINGAPORE
</information>
</sample1>
<sample2>:
<Reasoning>
The provided document seems like to be set of three documents and there are discrepencies and matches present and they are 
</Reasoning>
<information>

## Matching Items
- **Document Numbers**: PO-2025072401, INV-2025072401, and GRN-2025072401 match across all three documents
- **Vendor**: FastSupply Co. is consistent across all documents
- **Wireless Speaker Quantity**: 10 units consistently across all documents
- **Wireless Speaker Price**: S$1,250.00 consistently across all documents

## Discrepancies Found
1. **USB Cable Quantity**:
   - Purchase Order: 50 units
   - Sales Invoice: 50 units
   - Delivery Receipt: 48 units (2 units short)

2. **USB Cable Price**:
   - Purchase Order: S$45.00 per unit
   - Sales Invoice: S$47.00 per unit (S$2.00 higher)
   - Delivery Receipt: S$45.00 per unit

3. **USB Cable Total**:
   - Purchase Order: S$2,250.00
   - Sales Invoice: S$2,350.00
   - Delivery Receipt: S$2,160.00

4. **Grand Total**:
   - Purchase Order: S$14,750.00
   - Sales Invoice: S$14,850.00
   - Delivery Receipt: S$14,660.00

</information>
<sample2>
<Note>
1.Make sure to keep it soft and crisp and act like you are thinking and providing the information in  a neat and clean manner.
2.Do not provide tags while responding and provide the response in mark down format 
3.Never show the information in table instead show it as list
"""
        
        try:
            # Prepare the request body for Bedrock
            body = json.dumps({
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": 4000,
                "messages": [
                    {
                        "role": "user",
                        "content": prompt_template
                    }
                ]
            })
            
            # Call Bedrock using the same pattern as other functions
            response = bedrock_client.invoke_model(
                contentType='application/json',
                body=body,
                modelId="us.anthropic.claude-3-7-sonnet-20250219-v1:0"
            )
            
            response_body = json.loads(response['body'].read())
            extracted_data = response_body.get('content', [{}])[0].get('text', '')
            
            # Create response data
            response_data = {
                "extracted_kyc_data": extracted_data,
                "timestamp": datetime.now().isoformat(),
                "session_id": session_id
            }
            
            # Log the KYC extraction
            try:
                log_query = """
                    INSERT INTO {}.{} (session_id, query, response, timestamp, api_type)
                    VALUES (%s, %s, %s, %s, %s)
                """.format(schema, CHAT_LOG_TABLE)
                
                log_values = (
                    session_id,
                    json.dumps({"document_data": bool(document_data)}),
                    json.dumps(response_data),
                    datetime.now(),
                    'kyc_extraction'
                )
                
                insert_db(log_query, log_values)
            except Exception as e:
                print(f"Error logging KYC extraction: {e}")
            
            return {
                "statusCode": 200,
                "session_id": session_id,
                "response_data": response_data
            }
            
        except Exception as e:
            print(f"Error calling Bedrock for KYC extraction: {e}")
            return {
                "statusCode": 500,
                "error": "Error processing KYC extraction",
                "session_id": session_id
            }
        
    except Exception as e:
        print(f"Error in KYC extraction API: {e}")
        return {
            "statusCode": 500,
            "error": "Internal server error during KYC extraction",
            "session_id": session_id if 'session_id' in locals() else str(uuid.uuid4())
        }

# retail function code starts here...


def generate_video_from_image(event):
    """
    Generate video and store link in database
    """
    try:
        image_b64 = event["image_base64"]
        prompt = event["prompt"]
        session_id = event["session_id"]

        region = region_used
        s3_region = region_used
        model_id = "amazon.nova-reel-v1:1"
        bucket = S3_BUCKET
        prefix = f"videos/{session_id}"
        s3_uri = f"s3://{bucket}/{prefix}/"
        s3_key = f"{prefix}/output.mp4"

        print(f"Generating video for session {session_id}")
        print(bucket, prefix, s3_uri, s3_key)

        # Construct model input
        model_input = {
            "taskType": "TEXT_VIDEO",
            "textToVideoParams": {
                "text": prompt,
                "images": [
                    {
                        "format": "jpeg",
                        "source": {
                            "bytes": image_b64
                        }
                    }
                ]
            },
            "videoGenerationConfig": {
                "durationSeconds": 6,
                "fps": 24,
                "dimension": "1280x720",
                "seed": 5234255
            }
        }

        # Call Bedrock async invoke
        bedrock_runtime = boto3.client("bedrock-runtime", region_name=region)
        invocation = bedrock_runtime.start_async_invoke(
            modelId=model_id,
            modelInput=model_input,
            outputDataConfig={
                "s3OutputDataConfig": {
                    "s3Uri": s3_uri
                }
            },
        )

        # Wait for video in S3
        s3 = boto3.client("s3", region_name=s3_region)
        waited = 0
        
        while waited < 300:  # 5 minutes timeout
            try:
                # List objects in the prefix to find video file
                list_response = s3.list_objects_v2(
                    Bucket=bucket,
                    Prefix=prefix
                )
                
                if 'Contents' in list_response:
                    # Look for video files
                    video_files = [obj['Key'] for obj in list_response['Contents'] 
                                 if obj['Key'].endswith(('.mp4', '.mov', '.avi'))]
                    
                    if video_files:
                        s3_key = video_files[0]  # Use the first video file found
                        break
                        
                sleep(10)
                waited += 10
            except Exception as e:
                sleep(10)
                waited += 10
        else:
            return {
                "status": "timeout",
                "message": "Video generation timed out"
            }

        # Generate presigned URL
        presigned_url = s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": s3_key},
            ExpiresIn=86400
        )

        # Store in database
        insert_query = "INSERT INTO genaifoundry.vid_gen_link (session_id, s3_link) VALUES (%s, %s)"
        process_query(insert_query, (session_id, presigned_url))

        return {
            "status": "success",
            "session_id": session_id,
            "video_url": presigned_url
        }

    except Exception as e:
        return {
            "status": "error",
            "message": f"Exception occurred: {str(e)}"
        }

def generate_video_from_text(event):
    """
    Generate video using only text and store link in database
    """
    try:
        prompt = event["prompt"]
        session_id = event["session_id"]

        region = region_used
        model_id = "amazon.nova-reel-v1:1"
        bucket = S3_BUCKET
        prefix = f"videos/{session_id}"
        s3_uri = f"s3://{bucket}/{prefix}/"
        s3_key = f"{prefix}/output.mp4"

        # Construct model input for text-only
        model_input = {
            "taskType": "TEXT_VIDEO",
            "textToVideoParams": {
                "text": prompt
            },
            "videoGenerationConfig": {
                "durationSeconds": 6,
                "fps": 24,
                "dimension": "1280x720",
                "seed": 5234255
            }
        }

        # Call Bedrock async invoke
        bedrock_runtime = boto3.client("bedrock-runtime", region_name=region)
        invocation = bedrock_runtime.start_async_invoke(
            modelId=model_id,
            modelInput=model_input,
            outputDataConfig={
                "s3OutputDataConfig": {
                    "s3Uri": s3_uri
                }
            },
        )

        # Wait for video in S3
        s3 = boto3.client("s3", region_name=region)
        waited = 0

        while waited < 300:  # 5 minutes timeout
            try:
                list_response = s3.list_objects_v2(
                    Bucket=bucket,
                    Prefix=prefix
                )

                if 'Contents' in list_response:
                    video_files = [obj['Key'] for obj in list_response['Contents']
                                   if obj['Key'].endswith(('.mp4', '.mov', '.avi'))]

                    if video_files:
                        s3_key = video_files[0]
                        break

                sleep(10)
                waited += 10
            except Exception:
                sleep(10)
                waited += 10
        else:
            return {
                "status": "timeout",
                "message": "Video generation timed out"
            }

        # Generate presigned URL
        presigned_url = s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": s3_key},
            ExpiresIn=86400
        )

        # Store in database
        insert_query = "INSERT INTO genaifoundry.vid_gen_link (session_id, s3_link) VALUES (%s, %s)"
        process_query(insert_query, (session_id, presigned_url))

        return {
            "status": "success",
            "session_id": session_id,
            "video_url": presigned_url
        }

    except Exception as e:
        return {
            "status": "error",
            "message": f"Exception occurred: {str(e)}"
        }


def check_video_link(event):
    """
    Check if video link exists in database
    """
    try:
        session_id = event["session_id"]

        # Query database for the link
        select_query = "SELECT session_id, s3_link FROM genaifoundry.vid_gen_link WHERE session_id = %s"
        result = process_query(select_query, (session_id,))

        if result and result[0][1]:  # If link exists
            return {
                "status": "found",
                "session_id": session_id,
                "video_url": result[0][1]
            }
        else:
            return {
                "status": "not_found",
                "session_id": session_id,
                "message": "Video link not found"
            }

    except Exception as e:
        return {
            "status": "error",
            "message": f"Exception occurred: {str(e)}"
        }
def analyze_reviews_summary(event):
    print("REVIEW SUMMARY ANALYSIS STARTED")

    spreadsheet_data = event.get("spreadsheet_json", [])
    if not spreadsheet_data or not isinstance(spreadsheet_data, list):
        raise ValueError("Input must include 'spreadsheet_json' as a list of rows")

    # Construct tabular input string for Claude
    table = "Review\tRating\n"
    for row in spreadsheet_data:
        review = str(row.get("review", "")).replace("\n", " ").strip()
        rating = str(row.get("rating", "")).strip()
        table += f"{review}\t{rating}\n"

    # Prompt for Claude to analyze sentiment
    prompt = f"""
You are a customer sentiment analysis assistant.

Given a dataset of customer product reviews with ratings (out of 5), analyze the feedback and return a comprehensive summary in the following JSON format:

{{
  "sentiment_distribution": {{
    "positive": "<%>",
    "neutral": "<%>",
    "negative": "<%>"
  }},
  "top_pros": ["...", "..."],
  "top_cons": ["...", "..."],
  "feature_requests": [
    "Feature 1 with % of users",
    "Feature 2 with % of users"
  ],
  "customer_insights": {{
    "average_rating": "<x.y>/5",
    "total_reviews": <int>,
    "recommendation_rate": "<%>"
  }}
}}

Only use the reviews and ratings to compute everything. Classify:
- Ratings >= 4 as Positive
- Ratings = 3 as Neutral
- Ratings <= 2 as Negative

For "average_rating", compute it **strictly as the sum of all ratings divided by the total number of reviews**, rounded to one decimal place. Do not estimate or guess based on sentiment — use actual numerical ratings only.If the Average is 2.99 round it off to 3.0 only not 2.9

Highlight useful patterns. Show percentages only if statistically meaningful.



Below is the tabular data:

{table}

Only return the JSON. No markdown, no explanations, no code blocks.
"""

    bedrock = boto3.client("bedrock-runtime", region_name="us-east-1")
    body = json.dumps({
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 2048,
        "messages": [{"role": "user", "content": prompt}]
    })

    response = bedrock.invoke_model(
        modelId="us.anthropic.claude-3-7-sonnet-20250219-v1:0",
        body=body,
    )

    result = json.loads(response.get("body").read())
    output_text = result["content"][0]["text"]
    print("LLM OUTPUT:", output_text)

    import re
    match = re.search(r'({.*})', output_text, re.DOTALL)
    if match:
        json_str = match.group(1)
    else:
        json_str = output_text

    return json.loads(json_str)



def retail_agent_invoke_tool(chat_history, session_id, chat, connectionId):
    try:
        # Start keepalive thread
        #keepalive_thread = send_keepalive(connectionId, 30)
        import uuid
        import random
        # # Fetch base_prompt from the database as before
        # select_query = f'''select base_prompt from {schema}.{prompt_metadata_table} where id =5;'''
        # print(select_query)
        base_prompt =f'''

You are a Virtual Shopping Assistant for AnyRetail, a helpful and accurate chatbot for retail customers. You help customers with their orders, returns, product inquiries, account management, and shopping services.

CRITICAL INSTRUCTIONS:
NEVER reply with any message that says you are checking, looking up, or finding information (such as "I'll check that for you", "Let me look that up", "One moment", "I'll find out", etc.).
NEVER say "To answer your question about [topic], let me check our system" or similar phrases.
After using a tool, IMMEDIATELY provide only the direct answer or summary to the user, with no filler, no explanations, and no mention of checking or looking up.
If a user asks a question that requires a tool, use the tool and reply ONLY with the answer or summary, never with any statement about the process.
For general retail questions, IMMEDIATELY use the retail_faq_tool_schema tool WITHOUT any preliminary message.

ACCOUNT AUTHENTICATION RULES:
ALWAYS verify Account ID and Email before proceeding with any order-related tools
NEVER proceed with get_order_status, initiate_return_request, place_order, or cancel_order without successful authentication
ONLY use tools after confirming the Account ID and Email combination is valid
If authentication fails, provide a clear error message and ask for correct credentials

VALID ACCOUNT DATA:
Use these exact Account ID and Email combinations for verification:
ACC1001 (Rachel Tan) - Email: rachel.tan@email.com  
ACC1002 (Jason Lim) - Email: jason.lim@email.com  
ACC1003 (Mary Goh) - Email: mary.goh@email.com  
ACC1004 (Daniel Ong) - Email: daniel.ong@email.com  
ACC1005 (Aisha Rahman) - Email: aisha.rahman@email.com

SESSION AUTHENTICATION STATE MANAGEMENT:
MAINTAIN SESSION STATE: Once an Account ID and Email are successfully verified, store this authentication state for the ENTIRE conversation session
NEVER RE-ASK: Do not ask for Account ID or Email again during the same session unless:
1. User explicitly provides a different Account ID
2. Authentication explicitly fails during a tool call
3. User explicitly requests to switch accounts

AUTHENTICATION PERSISTENCE RULES:
FIRST AUTHENTICATION: Ask for Account ID and Email only on the first order-related request
SESSION MEMORY: Remember the authenticated Account ID throughout the conversation
AUTOMATIC REUSE: Use the stored authenticated credentials for ALL subsequent order-related tool calls
NO RE-VERIFICATION: Do not re-verify credentials that have already been successfully authenticated in the current session

PRE-AUTHENTICATION CHECK:
Before asking for Account ID or Email for ANY order-related request:
Scan conversation history for previously provided Account ID
Check if Email was already verified for that Account ID in this session
If both are found and verified, proceed directly with stored credentials
Only ask for credentials that are missing or failed verification

ACCOUNT ID AND EMAIL HANDLING RULES:
SESSION-LEVEL STORAGE: Once Account ID is provided and verified, use it for ALL subsequent requests
ONE-TIME EMAIL: Ask for Email only ONCE per Account ID per session
CONVERSATION CONTEXT: Check the ENTIRE conversation history for previously provided and verified credentials
SMART REUSE: If user asks "I gave you before" or similar, acknowledge and proceed with stored credentials
CONTEXT AWARENESS: Before asking for credentials, always check if they were provided earlier in the conversation
When Account ID is provided, validate it matches the pattern ACC#### (e.g., ACC1001)
Use the same Account ID and Email for all subsequent tool calls in the session until Account ID changes
ALWAYS verify Email matches the Account ID before proceeding on first authentication only

AUTHENTICATION PROCESS:
Check Session State - Scan conversation for existing authenticated credentials
Collect Account ID - Ask for Account ID ONLY if not previously provided and verified
Validate Account ID - Check if it matches one of the valid Account IDs above
Collect Email - Ask for Email ONLY if not previously provided and verified for current Account ID
Verify Email - Check if the Email matches the Account ID (only on first authentication)
Store Authentication State - Remember successful authentication for entire session
Proceed with Tools - Use stored credentials for all subsequent order-related requests

MANDATORY QUESTION COLLECTION RULES:
ALWAYS collect ALL required information for any tool before using it
NEVER skip any required questions, even if the user provides some information
NEVER assume or guess missing information
NEVER proceed with incomplete information
Ask questions ONE AT A TIME in this exact order:

For get_order_status tool:
1. Check session state first - Use stored Account ID and Email if already authenticated
2. Account ID - if not already provided and verified in conversation
3. Email - only if not already provided and verified for current Account ID
4. VERIFY Account ID and Email combination is valid (only on first authentication)
5. Order Number (e.g., ORD789012)
6. ONLY proceed with tool call after successful authentication

For initiate_return_request tool (ask in this exact order):
1. Check session state first - Use stored Account ID and Email if already authenticated
2. Account ID - if not already provided and verified in conversation
3. Email - only if not already provided and verified for current Account ID
4. VERIFY Account ID and Email combination is valid (only on first authentication)
5. Order Number
6. Item ID (specific item to return)
7. Return Reason (Defective, Wrong Size, Not as Described, Changed Mind)
8. Description of the issue
9. Preferred refund method (Original Payment, Store Credit, Exchange)
10. ONLY proceed with tool call after successful authentication

For place_order tool (ask in this exact order):
1. Check session state first - Use stored Account ID and Email if already authenticated
2. Account ID - if not already provided and verified in conversation
3. Email - only if not already provided and verified for current Account ID
4. VERIFY Account ID and Email combination is valid (only on first authentication)
5. Items to purchase (with SKUs and quantities)
6. Shipping address
7. Shipping method (Standard, Express, Same-Day)
8. Payment method (Credit Card, PayPal, Store Credit, Gift Card)
9. Payment details
10. ONLY proceed with tool call after successful authentication

For cancel_order tool (ask in this exact order):
1. Check session state first - Use stored Account ID and Email if already authenticated
2. Account ID - if not already provided and verified in conversation
3. Email - only if not already provided and verified for current Account ID
4. VERIFY Account ID and Email combination is valid (only on first authentication)
5. Order Number to cancel
6. Cancellation reason
7. Preferred refund method
8. ONLY proceed with tool call after successful authentication

## PRODUCT INQUIRIES HANDLING

For product-related questions, use the retail_faq_tool_schema to provide general information about products, services, and policies. This tool can answer questions about:
- Product categories and general information
- Pricing policies and payment options
- Return and warranty policies
- Store services and features
- Account types and benefits

Always provide helpful, accurate information from the knowledge base without making specific product availability claims.

INPUT VALIDATION RULES:
NEVER ask for the same Account ID twice in a session unless user provides different one
NEVER ask for Email twice for the same Account ID in a session
Accept Account ID in format ACC#### only
Accept Email in standard email format
Accept any reasonable order numbers, SKUs, or product descriptions
NEVER ask for specific formats - accept what the user provides
If validation fails, provide a clear, specific error message with examples
ALWAYS verify Email matches the Account ID before proceeding (only on first authentication)

AUTHENTICATION ERROR MESSAGES:
If Account ID is invalid: "Invalid Account ID. Please provide a valid Account ID (e.g., ACC0001)."
If Email is incorrect: "Email address doesn't match Account ID [ACC####]. Please provide the correct email address."
If both are wrong: "Invalid Account ID and Email combination. Please check your credentials and try again."

Tool Usage Rules:
When a user asks about order status, tracking, or delivery updates, use get_order_status tool AFTER authentication (use stored credentials if available)
When a user wants to return items or needs return authorization, use initiate_return_request tool AFTER authentication (use stored credentials if available)
When a user wants to place a new order or purchase items, use place_order tool AFTER authentication (use stored credentials if available)
When a user wants to cancel an existing order, use cancel_order tool AFTER authentication (use stored credentials if available)
When a user asks about product availability, pricing, or stock levels, use the retail_faq_tool_schema tool to provide general information about products and services
For general shopping questions about accounts, services, or policies, use the retail_faq_tool_schema tool
Do NOT announce that you're using tools or searching for information
Simply use the tool and provide the direct answer

Response Format:
ALWAYS answer in the shortest, most direct way possible
Do NOT add extra greetings, confirmations, or explanations
Do NOT mention backend systems or tools
Speak naturally as a helpful retail representative who already knows the information

Available Tools:
get_order_status - Retrieve customer's order information and tracking details (requires authentication)
initiate_return_request - Process return requests and generate return authorization (requires authentication)
place_order - Process new customer orders with payment and shipping (requires authentication)
cancel_order - Cancel existing orders and process refunds (requires authentication)
retail_faq_tool_schema - Retrieve answers from the retail knowledge base for general questions, policies, and product information

SYSTEMATIC QUESTION COLLECTION:
When a user wants order information, returns, new orders, or cancellations, IMMEDIATELY check session state for existing authentication
If already authenticated in session, proceed directly with remaining required information
Ask ONLY ONE question at a time
After each user response, check what information is still missing
Ask for the NEXT missing required field (in the exact order listed above)
Do NOT ask multiple questions in one message
Do NOT skip any required questions
Do NOT proceed until ALL required information is collected
ALWAYS use stored authentication if available, verify authentication before proceeding with tools only on first authentication

EXAMPLES OF CORRECT BEHAVIOR:
First Order-Related Request:
User: "Where is my order?"
Assistant: "What is your Account ID?"
User: "ACC1001"
Assistant: "Please provide your email address for verification."
User: "rachel.tan@email.com"
Assistant: "What is your order number?"
User: "ORD789012"
Assistant: [Verify ACC1001 + rachel.tan@email.com is valid, store authentication state, then use get_order_status tool and provide order details]

Subsequent Order-Related Requests in Same Session:
User: "What are your return policies?"
Assistant: [Use retail_faq_tool_schema tool and provide return policy information]
User: "I want to return the headphones from that order"
Assistant: "Which specific item would you like to return? Please provide the item ID."
[Uses stored ACC0001 authentication, only asks for return-specific details]
User: "Can I place another order?"
Assistant: "What items would you like to purchase?"
[Uses stored ACC0001 authentication, only asks for order details]

Different Account ID in Same Session:
User: "Can you check order for ACC0002?"
Assistant: "Please provide your email address for Account ID ACC0002 verification."

EXAMPLES OF INCORRECT BEHAVIOR:
❌ "What's your Account ID, email, and order number?" (asking multiple questions)
❌ Asking for Account ID again after it was already provided and verified in the session
❌ Asking for Email again for the same Account ID in the same session
❌ Skipping Email verification on first authentication
❌ Proceeding with incomplete information
❌ Not checking conversation history for existing authentication
❌ Re-asking for credentials after using FAQ tool

SECURITY GUIDELINES:
Require Email verification only once per Account ID in each session
Never store or reference Email values in conversation history for security
If user switches to a different Account ID, ask for the corresponding Email
Treat all order and account information as sensitive and confidential
ALWAYS verify Account ID and Email combination before first account access
MAINTAIN authentication state throughout session for user experience

PRODUCT KNOWLEDGE:
You have access to comprehensive information about AnyRetail products and services including:
Account Types (Rewards Account, Student Account)
Shopping Services (Express Shopping, Corporate Account)
Store Credit Cards (Rewards+ Card, Cashback Max Card)
Financing Options (Personal Shopping Credit, Buy Now Pay Later)
Mobile app features and digital shopping capabilities

RESPONSE GUIDELINES:
Handle greetings warmly and ask how you can help with their shopping needs today
For product inquiries, provide specific details from the knowledge base
For order-specific queries, always use appropriate tools with proper authentication
For service issues, efficiently collect information and process requests
Keep responses concise and actionable
Never leave users without a clear next step or resolution

CUSTOMER SERVICE EXCELLENCE:
Be proactive in offering related services (e.g., suggest express shipping for urgent orders)
Acknowledge customer concerns and provide reassurance
Offer alternatives when primary requests cannot be fulfilled
Follow up on complex issues with clear next steps
Maintain a friendly, professional tone throughout all interactions
 '''
        print(base_prompt)
        print('base_prompt is fetched from db')
        
        # Retail tool schema based on retail_sandbox_tools
        retail_tools = [
            {
                "name": "get_order_status",
                "description": "Retrieve customer's order information and tracking details",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "account_id": {"type": "string", "description": "Account ID in format ACC#### (e.g., ACC1002)"},
                        "email": {"type": "string", "description": "Email address for account verification"},
                        "order_id": {"type": "string", "description": "Order reference number (e.g., ORD789012)"}
                    },
                    "required": ["account_id", "email", "order_id"]
                }
            },
            {
                "name": "initiate_return_request",
                "description": "Process return requests and generate return authorization",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "account_id": {"type": "string", "description": "Account ID in format ACC#### (e.g., ACC1002)"},
                        "email": {"type": "string", "description": "Email address for account verification"},
                        "order_id": {"type": "string", "description": "Original order reference number"},
                        "item_id": {"type": "string", "description": "Specific item identifier within the order"},
                        "return_reason": {"type": "string", "description": "Reason code: Defective, Wrong Size, Not as Described, Changed Mind"}
                    },
                    "required": ["account_id", "email", "order_id", "item_id", "return_reason"]
                }
            },
            {
                "name": "cancel_order",
                "description": "Cancel existing orders and process refunds",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "order_id": {"type": "string", "description": "Order to be cancelled (e.g., ORD123456)"},
                        "account_id": {"type": "string", "description": "Account ID in format ACC#### (e.g., ACC1003)"},
                        "email": {"type": "string", "description": "Email address for account verification"},
                        "cancellation_reason": {"type": "string", "description": "Reason for cancellation (e.g., Changed Mind, Found Better Price, No Longer Needed)"},
                        "refund_method": {"type": "string", "description": "Preferred refund method (Original Payment, Store Credit, Exchange)"}
                    },
                    "required": ["order_id", "account_id", "email", "cancellation_reason", "refund_method"]
                }
            },
            {
                "name": "retail_faq_tool_schema",
                "description": "Retrieve answers from the retail knowledge base for general retail questions, policies, and procedures",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "knowledge_base_retrieval_question": {"type": "string", "description": "A question to retrieve from the retail knowledge base about retail services, policies, procedures, or general information."}
                    },
                    "required": ["knowledge_base_retrieval_question"]
                }
            }
        ]

        # --- Customer Database for AnyRetail ---
        valid_customers = {
            "ACC1001": {"name": "Rachel Tan", "email": "rachel.tan@email.com", "phone": "+1-555-0123"},
            "ACC1002": {"name": "Jason Lim", "email": "jason.lim@email.com", "phone": "+1-555-0456"},
            "ACC1003": {"name": "Mary Goh", "email": "mary.goh@email.com", "phone": "+1-555-0789"},
            "ACC1004": {"name": "Daniel Ong", "email": "daniel.ong@email.com", "phone": "+1-555-0321"},
            "ACC1005": {"name": "Aisha Rahman", "email": "aisha.rahman@email.com", "phone": "+1-555-0654"}
        }

        # --- Structured Account-Order-Item Relationships ---
        account_order_relationships = {
            "ACC1001": ["ORD789012", "ORD890123"],
            "ACC1002": ["ORD567890", "ORD123456"],
            "ACC1003": ["ORD901234", "ORD234567"],
            "ACC1004": ["ORD345678", "ORD345679"],
            "ACC1005": ["ORD456789", "ORD456789"]
        }

        order_item_relationships = {
            "ORD789012": {
                "ITM001": {"account_id": "ACC1001", "name": "Wireless Bluetooth Headphones - Black", "quantity": 1, "price": 89.99},
                "ITM002": {"account_id": "ACC1001", "name": "Wireless Bluetooth Headphones - Silver", "quantity": 1, "price": 79.99},
                "ITM003": {"account_id": "ACC1001", "name": "SoundMax Elite - In-ear", "quantity": 1, "price": 199.99}
            },
            "ORD890123": {
                "ITM004": {"account_id": "ACC1001", "name": "ZX900 Pro Headphones - Black", "quantity": 1, "price": 299.99}
            },
            "ORD567890": {
                "ITM005": {"account_id": "ACC1002", "name": "Gaming Console - Black", "quantity": 1, "price": 299.99}
            },
            "ORD123456": {
                "ITM006": {"account_id": "ACC1002", "name": "Smart TV 55-inch - VisionX", "quantity": 1, "price": 899.99}
            },
            "ORD901234": {
                "ITM007": {"account_id": "ACC1003", "name": "Wireless Bluetooth Headphones - White", "quantity": 1, "price": 89.99}
            },
            "ORD234567": {
                "ITM008": {"account_id": "ACC1003", "name": "Smart TV 65-inch - Alpha7", "quantity": 1, "price": 1899.99}
            },
            "ORD345678": {
                "ITM009": {"account_id": "ACC1004", "name": "Gaming Console - White", "quantity": 1, "price": 299.99}
            },
            "ORD345679": {
                "ITM010": {"account_id": "ACC1004", "name": "ZX900 Pro Headphones - Black", "quantity": 1, "price": 299.99}
            },
            "ORD456789": {
                "ITM011": {"account_id": "ACC1005", "name": "Smart TV 55-inch - VisionX", "quantity": 1, "price": 899.99}
            },
            "ORD456789": {
                "ITM012": {"account_id": "ACC1005", "name": "SoundMax Elite - Silver", "quantity": 1, "price": 199.99}
            }
        }

        def validate_account_order_relationship(account_id, order_id):
            """Validate that the account ID owns the order ID"""
            if account_id not in account_order_relationships:
                return False, f"Invalid Account ID: {account_id}"
            
            if order_id not in account_order_relationships[account_id]:
                return False, f"Order {order_id} does not belong to Account ID {account_id}. Please provide the correct Account ID for this order."
            
            return True, "Valid relationship"

        def validate_order_item_relationship(order_id, item_id):
            """Validate that the order ID contains the item ID"""
            if order_id not in order_item_relationships:
                return False, f"Order {order_id} not found"
            
            if item_id not in order_item_relationships[order_id]:
                return False, f"Item {item_id} is not found in order {order_id}. Please provide a valid item ID for this order."
            
            return True, "Valid relationship"

        def find_item_by_product_name(order_id, product_name):
            """Find item ID by product name in a specific order"""
            if order_id not in order_item_relationships:
                return None, f"Order {order_id} not found"
            
            order_items = order_item_relationships[order_id]
            for item_id, item_data in order_items.items():
                if product_name.lower() in item_data["name"].lower():
                    return item_id, item_data["name"]
            
            return None, f"Product '{product_name}' not found in order {order_id}"

        def validate_order_product_relationship(account_id, order_id, product_name):
            """Validate that the account owns the order and the order contains the product"""
            # First validate account-order relationship
            account_order_valid, account_order_msg = validate_account_order_relationship(account_id, order_id)
            if not account_order_valid:
                return False, account_order_msg
            
            # Then find item by product name
            item_id, item_name = find_item_by_product_name(order_id, product_name)
            if item_id is None:
                return False, item_name  # item_name contains the error message
            
            # Finally validate that the item belongs to the account
            item_data = order_item_relationships[order_id][item_id]
            if item_data["account_id"] != account_id:
                return False, f"Product '{product_name}' in order {order_id} does not belong to Account ID {account_id}"
            
            return True, item_id  # Return the found item_id for further processing

        def validate_account_order_item_relationship(account_id, order_id, item_id):
            """Validate the complete relationship chain: Account ID → Order ID → Item ID"""
            # First validate account-order relationship
            account_order_valid, account_order_msg = validate_account_order_relationship(account_id, order_id)
            if not account_order_valid:
                return False, account_order_msg
            
            # Then validate order-item relationship
            order_item_valid, order_item_msg = validate_order_item_relationship(order_id, item_id)
            if not order_item_valid:
                return False, order_item_msg
            
            # Finally validate that the item belongs to the account
            item_data = order_item_relationships[order_id][item_id]
            if item_data["account_id"] != account_id:
                return False, f"Item {item_id} in order {order_id} does not belong to Account ID {account_id}"
            
            return True, "Valid relationship"

        def authenticate_customer(account_id, email=None):
            """Authenticate Account ID and optionally verify email"""
            if account_id not in valid_customers:
                return False, "Invalid Account ID. Please provide a valid Account ID (e.g., ACC1001)."
            
            # If email is provided, verify it matches the account
            if email:
                expected_email = valid_customers[account_id]['email']
                if email.lower() != expected_email.lower():
                    return False, f"I'm unable to verify your account. The email address doesn't match Account ID {account_id}. Please provide the correct email address."
            
            return True, f"Authentication successful for {valid_customers[account_id]['name']}"

        # --- Mock retail tool implementations ---
        def get_order_status(account_id, email, order_id):
            # Authenticate customer first
            auth_success, auth_message = authenticate_customer(account_id, email)
            if not auth_success:
                return {"error": auth_message}

            # Validate that the account ID owns the order ID
            relationship_valid, relationship_msg = validate_account_order_relationship(account_id, order_id)
            if not relationship_valid:
                return {"error": relationship_msg}

            if order_id not in order_item_relationships:
                return {"error": "Order not found"}

            # Get all items in the order
            order_items = order_item_relationships[order_id]
            items_list = []
            total_price = 0

            for item_id, item_data in order_items.items():
                items_list.append({
                    "item_id": item_id,
                    "item_name": item_data["name"],
                    "quantity": item_data["quantity"],
                    "price": item_data["price"]
                })
                total_price += item_data["price"] * item_data["quantity"]

            # Determine order status based on order ID
            order_status_map = {
                "ORD789012": "Shipped",
                "ORD890123": "Processing",
                "ORD567890": "Delivered",
                "ORD123456": "Processing",
                "ORD901234": "Shipped",
                "ORD234567": "Processing",
                "ORD345678": "Delivered",
                "ORD345679": "Processing",
                "ORD456789": "Shipped",
                "ORD456789": "Processing"
            }

            # Delivery information - estimated for pending orders, actual for delivered orders
            delivery_info = {
                "ORD789012": {"type": "estimated", "date": get_dynamic_date(3)},
                "ORD890123": {"type": "estimated", "date": get_dynamic_date(3)},
                "ORD567890": {"type": "delivered", "date": get_dynamic_date(2)},
                "ORD123456": {"type": "estimated", "date": get_dynamic_date(3)},
                "ORD901234": {"type": "estimated", "date": get_dynamic_date(3)},
                "ORD234567": {"type": "estimated", "date": get_dynamic_date(3)},
                "ORD345678": {"type": "delivered", "date": get_dynamic_date(2)},
                "ORD345679": {"type": "estimated", "date": get_dynamic_date(3)},
                "ORD456789": {"type": "estimated", "date": get_dynamic_date(3)},
                "ORD456789": {"type": "estimated", "date": get_dynamic_date(3)}
            }

            # Get delivery information
            delivery_data = delivery_info.get(order_id, {"type": "unknown", "date": "TBD"})

            # Format delivery information based on status
            if delivery_data["type"] == "delivered":
                delivery_text = f"Delivered on {delivery_data['date']}"
            elif delivery_data["type"] == "estimated":
                delivery_text = f"Estimated delivery: {delivery_data['date']}"
            else:
                delivery_text = "Delivery information unavailable"

            return {
                "order_id": order_id,
                "items": items_list,
                "total_price": total_price,
                "currency": "SGD",
                "status": order_status_map.get(order_id, "Unknown"),
                "delivery_info": delivery_text,
                "last_updated": get_dynamic_datetime(2)
            }


        def get_order_items_for_return(order_id):
            """Helper function to get items in an order for return request"""
            if order_id not in order_item_relationships:
                return "Order not found"
            
            items = order_item_relationships[order_id]
            if not items:
                return "No items found in this order"
            
            items_list = []
            for item_id, item_data in items.items():
                items_list.append(f"• {item_data['name']} (Item ID: {item_id})")
            
            return "\n\n".join(items_list)

        def initiate_return_request(account_id, email, order_id, item_id_or_product_name, return_reason):
            # Authenticate customer first
            auth_success, auth_message = authenticate_customer(account_id, email)
            if not auth_success:
                return {"error": auth_message}

            if not item_id_or_product_name or item_id_or_product_name.lower() in ["", "none", "null"]:
                order_items = get_order_items_for_return(order_id)
                if order_items.startswith("Order not found") or order_items.startswith("No items found"):
                    return {"error": order_items}
                
                return {
                    "message": f"Please specify which item you'd like to return from order {order_id}. Here are the items in your order:\n\n{order_items}\n\nPlease provide the item name (e.g., 'Gaming Console - White') or item ID you wish to return along with your return reason."
                }
            
            # Check if item_id_or_product_name is an order number (starts with ORD)
            if item_id_or_product_name.startswith("ORD"):
                # User provided an order number instead of item ID
                return {
                    "error": f"You provided order number '{item_id_or_product_name}', but I need the specific item you want to return from order {order_id}. Please provide the item name (e.g., 'Gaming Console - White') or item ID."
                }
            
            # Check if item_id_or_product_name is an item ID (starts with ITM) or a product name
            if item_id_or_product_name.startswith("ITM"):
                # It's an item ID, use the original validation
                relationship_valid, relationship_msg = validate_account_order_item_relationship(account_id, order_id, item_id_or_product_name)
                if not relationship_valid:
                    return {"error": relationship_msg}
                item_id = item_id_or_product_name
                item_name = order_item_relationships[order_id][item_id]["name"]
            else:
                # It's a product name, use product name validation
                relationship_valid, relationship_result = validate_order_product_relationship(account_id, order_id, item_id_or_product_name)
                if not relationship_valid:
                    return {"error": relationship_result}
                item_id = relationship_result  # relationship_result contains the found item_id
                item_name = order_item_relationships[order_id][item_id]["name"]
            
            return_request_id = f"RA-{str(uuid.uuid4())[:6].upper()}"
            return {
                "return_request_id": return_request_id,
                "status": "Approved",
                "assigned_team": "Returns Processing",
                "expected_pickup": get_dynamic_date(3),
                "summary": f"Return request approved for {item_name} from order {order_id}. Reason: {return_reason}",
                "refund_method": "We’ll process your refund using the same payment method you used at checkout."
            }
        



        def check_product_availability(product_name, model=None, color=None, type=None, size_in_inches=None, display_type=None):
            # Only support headphones and TV as per base prompt
            if product_name not in ["headphones", "TV"]:
                return {"error": "Sorry, I can assist only with headphones and TVs at the moment."}
            
            mock_products = {
                "headphones": {
                    "ZX900 Pro": {
                        "product_name": "headphones",
                        "model": "ZX900 Pro",
                        "color": "Black",
                        "type": "over-ear",
                        "price": 299.99,
                        "availability": "In Stock",
                        "currency": "SGD",
                        "status": "Active",
                        "last_updated": get_dynamic_datetime(2)
                    },
                    "SoundMax Elite": {
                        "product_name": "headphones",
                        "model": "SoundMax Elite",
                        "color": "Silver",
                        "type": "in-ear",
                        "price": 199.99,
                        "availability": "In Stock",
                        "currency": "SGD",
                        "status": "Active",
                        "last_updated": get_dynamic_datetime(2)
                    }
                },
                "TV": {
                    "VisionX": {
                        "product_name": "TV",
                        "size_in_inches": 55,
                        "display_type": "OLED",
                        "model": "VisionX",
                        "price": 1299.99,
                        "availability": "In Stock",
                        "currency": "SGD",
                        "status": "Active",
                        "last_updated": get_dynamic_datetime(2)
                    },
                    "Alpha7": {
                        "product_name": "TV",
                        "size_in_inches": 65,
                        "display_type": "QLED",
                        "model": "Alpha7",
                        "price": 1899.99,
                        "availability": "In Stock",
                        "currency": "SGD",
                        "status": "Active",
                        "last_updated": get_dynamic_datetime(2)
                    }
                }
            }
            
            category_products = mock_products.get(product_name, {})
            
            # For TVs, match by size and display type if provided
            if product_name == "TV":
                for product in category_products.values():
                    # If size and display type are provided, check for exact match
                    if size_in_inches is not None and display_type is not None:
                        if (product.get("size_in_inches") == size_in_inches and 
                            product.get("display_type") == display_type):
                            return product
                    
                    # If only size is provided, check for size match
                    elif size_in_inches is not None:
                        if product.get("size_in_inches") == size_in_inches:
                            return product
                    
                    # If only display type is provided, check for display type match
                    elif display_type is not None:
                        if product.get("display_type") == display_type:
                            return product
                
                # If no exact match found, provide information about available options
                if size_in_inches is not None or display_type is not None:
                    available_options = []
                    for product in category_products.values():
                        available_options.append(f"{product['model']} {product['size_in_inches']}-inch {product['display_type']}")
                    
                    if size_in_inches is not None and display_type is not None:
                        return {
                            "error": f"Sorry, we don't have a {size_in_inches}-inch {display_type} TV in stock. Available options: {', '.join(available_options)}"
                        }
                    elif size_in_inches is not None:
                        return {
                            "error": f"Sorry, we don't have a {size_in_inches}-inch TV in stock. Available options: {', '.join(available_options)}"
                        }
                    elif display_type is not None:
                        return {
                            "error": f"Sorry, we don't have a {display_type} TV in stock. Available options: {', '.join(available_options)}"
                        }
            
            # For headphones, match by model, color, and type if provided
            elif product_name == "headphones":
                for product in category_products.values():
                    # If model, color, and type are provided, check for exact match
                    if model is not None and color is not None and type is not None:
                        if (product.get("model") == model and 
                            product.get("color") == color and 
                            product.get("type") == type):
                            return product
                    
                    # If only model is provided, check for model match
                    elif model is not None:
                        if product.get("model") == model:
                            return product
                
                # If no exact match found for headphones, provide information about available options
                if model is not None or color is not None or type is not None:
                    available_options = []
                    for product in category_products.values():
                        available_options.append(f"{product['model']} {product['color']} {product['type']}")
                    
                    return {
                        "error": f"Sorry, the requested headphones are not available. Available options: {', '.join(available_options)}"
                    }
            
            # If no specific parameters provided, return first available product
            if category_products:
                return list(category_products.values())[0]
            
            return {"error": "Product not found"}

        def cancel_order(order_id, account_id, email, cancellation_reason, refund_method):
            # Authenticate customer first
            auth_success, auth_message = authenticate_customer(account_id, email)
            if not auth_success:
                return {"error": auth_message}
            
            # Validate that the account ID owns the order ID
            relationship_valid, relationship_msg = validate_account_order_relationship(account_id, order_id)
            if not relationship_valid:
                return {"error": relationship_msg}
            
            cancellation_id = f"CAN-{str(uuid.uuid4())[:6].upper()}"
            return {
                "cancellation_id": cancellation_id,
                "status": "Approved",
                "assigned_team": "Order Management",
                "expected_callback": get_dynamic_date(2),
                "summary": f"Order {order_id} has been successfully cancelled. Reason: {cancellation_reason}. Refund will be processed via {refund_method} within 3-5 business days."
            }

        def get_retail_faq_chunks(query):
            try:
                print("IN RETAIL FAQ: ", query)
                chunks = []
                # Use the same KB_ID for now, but you might want to create a separate retail KB
                response_chunks = retrieve_client.retrieve(
                    retrievalQuery={                                                                                
                        'text': query
                    },
                    knowledgeBaseId=RETAIL_KB_ID,
                    retrievalConfiguration={
                        'vectorSearchConfiguration': {                          
                            'numberOfResults': 10,                                                                                              
                            'overrideSearchType': 'HYBRID'
                        }
                    }
                )
               
                for item in response_chunks['retrievalResults']:
                    if 'content' in item and 'text' in item['content']:
                        chunks.append(item['content']['text'])
                print('RETAIL FAQ CHUNKS: ', chunks)  
                return chunks
            except Exception as e:
                print("An exception occurred while retrieving retail FAQ chunks:", e)
                return []

        input_tokens = 0
        output_tokens = 0
        print("In retail_agent_invoke_tool (Retail Bot)")

        # Extract Account ID, Order ID, and Email from chat history
        extracted_account_id = None
        extracted_order_id = None
        extracted_email = None
        
        for message in chat_history:
            if message['role'] == 'user':
                content_text = message['content'][0]['text']
                
                # Extract Account ID (ACC followed by 4 digits)
                account_id_match = re.search(r'\b(ACC\d{4})\b', content_text.upper())
                if account_id_match:
                    extracted_account_id = account_id_match.group(1)
                    print(f"Extracted Account ID from chat history: {extracted_account_id}")
                    
                # Extract Order ID (ORD followed by 6 digits)
                order_id_match = re.search(r'\b(ORD\d{6})\b', content_text.upper())
                if order_id_match:
                    extracted_order_id = order_id_match.group(1)
                    print(f"Extracted Order ID from chat history: {extracted_order_id}")
                
                # Extract Email address
                email_match = re.search(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b', content_text)
                if email_match:
                    extracted_email = email_match.group(0)
                    print(f"Extracted Email from chat history: {extracted_email}")
        
        # Enhance system prompt with Account ID, Order ID, and Email context
        enhanced_context = []
        
        # Enhance system prompt with Account ID context
        if extracted_account_id:
            enhanced_context.append(f"The customer's Account ID is {extracted_account_id}. Use this Account ID automatically for any tool calls that require it without asking again.")
        
        if extracted_order_id:
            enhanced_context.append(f"The customer's Order ID is {extracted_order_id}. Use this Order ID automatically for any tool calls that require it without asking again.")
        
        if extracted_email:
            enhanced_context.append(f"The customer's Email is {extracted_email}. Use this Email automatically for any tool calls that require it without asking again.")
        
        if enhanced_context:
            enhanced_prompt = base_prompt + f"\n\nIMPORTANT: {' '.join(enhanced_context)}"
            print(f"Enhanced prompt with context: {enhanced_context}")
        else:
            enhanced_prompt = base_prompt
        
        # Use the enhanced_prompt instead of base_prompt
        prompt = enhanced_prompt
        
        # First API call to get initial response
        try:
            response = bedrock_client.invoke_model_with_response_stream(
                contentType='application/json',
                body=json.dumps({
                    "anthropic_version": "bedrock-2023-05-31",
                    "max_tokens": 4000,
                    "temperature": 0,
                    "top_p": 0.999,
                    "top_k": 250,
                    "system": prompt,
                    "tools": retail_tools,
                    "messages": chat_history
                }),
                modelId=model_id
            )
        except Exception as e:
            print("AN ERROR OCCURRED : ", e)
            response = "We are unable to assist right now please try again after few minutes"
            return {"answer": response, "question": chat, "session_id": session_id}

        streamed_content = ''
        content_block = None
        assistant_response = []
        for item in response['body']:
            content = json.loads(item['chunk']['bytes'].decode())
            if content['type'] == 'content_block_start':
                content_block = content['content_block']
            elif content['type'] == 'content_block_stop':
                try:
                    api_gateway_client.post_to_connection(ConnectionId=connectionId, Data=json.dumps(content))
                except api_gateway_client.exceptions.GoneException:
                    print(f"Connection {connectionId} is closed (GoneException) - stop message")
                except Exception as e:
                    print(f"WebSocket send error (stop): {e}")
                if content_block['type'] == 'text':
                    content_block['text'] = streamed_content
                    assistant_response.append(content_block)
                elif content_block['type'] == 'tool_use':
                    content_block['input'] = json.loads(streamed_content)
                    assistant_response.append(content_block)
                streamed_content = ''
            elif content['type'] == 'content_block_delta':
                try:
                    api_gateway_client.post_to_connection(ConnectionId=connectionId, Data=json.dumps(content))
                except api_gateway_client.exceptions.GoneException:
                    print(f"Connection {connectionId} is closed (GoneException) - delta message")
                except Exception as e:
                    print(f"WebSocket send error (delta): {e}")
                if content['delta']['type'] == 'text_delta':
                    streamed_content += content['delta']['text']
                elif content['delta']['type'] == 'input_json_delta':
                    streamed_content += content['delta']['partial_json']
            elif content['type'] == 'message_delta':
                tool_tokens = content['usage']['output_tokens']
            elif content['type'] == 'message_stop':
                input_tokens += content['amazon-bedrock-invocationMetrics']['inputTokenCount']
                output_tokens += content['amazon-bedrock-invocationMetrics']['outputTokenCount']
        chat_history.append({'role': 'assistant', 'content': assistant_response})
        
        # Check if any tools were called
        tools_used = []
        tool_results = []
        
        for action in assistant_response:
            if action['type'] == 'tool_use':
                tools_used.append(action['name'])
                tool_name = action['name']
                tool_input = action['input']
                tool_result = None

                # Send a heartbeat to keep WebSocket alive during tool execution
                try:
                    heartbeat = {'type': 'heartbeat'}
                    api_gateway_client.post_to_connection(ConnectionId=connectionId, Data=json.dumps(heartbeat))
                except Exception as e:
                    print(f"Heartbeat send error: {e}")

                # Execute the appropriate retail tool
                if tool_name == 'get_order_status':
                    print("get_order_status is called..")
                    tool_result = get_order_status(
                        tool_input['account_id'],
                        tool_input['email'],
                        tool_input['order_id']
                    )
                    # Check for authentication error
                    if isinstance(tool_result, dict) and 'error' in tool_result:
                        print(f"Authentication failed for get_order_status: {tool_result['error']}")
                elif tool_name == 'initiate_return_request':
                    tool_result = initiate_return_request(
                        tool_input['account_id'],
                        tool_input['email'],
                        tool_input['order_id'],
                        tool_input['item_id'],
                        tool_input['return_reason']
                    )
                    # Check for authentication error
                    if isinstance(tool_result, dict) and 'error' in tool_result:
                        print(f"Authentication failed for initiate_return_request: {tool_result['error']}")
                elif tool_name == 'check_product_availability':
                    tool_result = check_product_availability(
                        tool_input['product_name'],
                        tool_input.get('model'),
                        tool_input.get('color'),
                        tool_input.get('type'),
                        tool_input.get('size_in_inches'),
                        tool_input.get('display_type')
                    )
                elif tool_name == 'cancel_order':
                    tool_result = cancel_order(
                        tool_input['order_id'],
                        tool_input['account_id'],
                        tool_input['email'],
                        tool_input['cancellation_reason'],
                        tool_input['refund_method']
                    )
                    # Check for authentication error
                    if isinstance(tool_result, dict) and 'error' in tool_result:
                        print(f"Authentication failed for cancel_order: {tool_result['error']}")
                elif tool_name == 'retail_faq_tool_schema':
                    print("retail_faq is called ...")
                    # Send another heartbeat before FAQ retrieval
                    try:
                        heartbeat = {'type': 'heartbeat'}
                        api_gateway_client.post_to_connection(ConnectionId=connectionId, Data=json.dumps(heartbeat))
                    except Exception as e:
                        print(f"Retail FAQ heartbeat send error: {e}")
                    
                    tool_result = get_retail_faq_chunks(tool_input['knowledge_base_retrieval_question'])
                    
                    # If FAQ tool returns empty or no results, provide fallback
                    if not tool_result or len(tool_result) == 0:
                        tool_result = ["I don't have specific information about that in our current retail knowledge base. Let me schedule a callback with one of our retail agents who can provide detailed information."]
                
                # Create tool result message
                tool_response_dict = {
                    "type": "tool_result",
                    "tool_use_id": action['id'],
                    "content": [{"type": "text", "text": json.dumps(tool_result)}]
                }
                tool_results.append(tool_response_dict)
        
        # If tools were used, add tool results to chat history and make second API call
        if tools_used:
            # Add tool results to chat history
            chat_history.append({'role': 'user', 'content': tool_results})
            
            # Make second API call with tool results
            try:
                response = bedrock_client.invoke_model_with_response_stream(
                    contentType='application/json',
                    body=json.dumps({
                        "anthropic_version": "bedrock-2023-05-31",
                        "max_tokens": 4000,
                        "temperature": 0,
                        "system": prompt,
                        "tools": retail_tools,
                        "messages": chat_history
                    }),
                    modelId=model_id
                )
            except Exception as e:
                print("ERROR IN SECOND API CALL:", e)
                # Send error response via WebSocket
                error_response = "I apologize, but I'm having trouble accessing that information right now. Please try again in a moment."
                for word in error_response.split():
                    delta = {'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'text_delta', 'text': word + ' '}}
                    try:
                        api_gateway_client.post_to_connection(ConnectionId=connectionId, Data=json.dumps(delta))
                    except:
                        pass
                stop_answer = {'type': 'content_block_stop', 'index': 0}
                try:
                    api_gateway_client.post_to_connection(ConnectionId=connectionId, Data=json.dumps(stop_answer))
                except:
                    pass
                return {"answer": error_response, "question": chat, "session_id": session_id}

            # Process the streaming response
            streamed_content = ''
            content_block = None
            assistant_response = []
            
            for item in response['body']:
                content = json.loads(item['chunk']['bytes'].decode())
                if content['type'] == 'content_block_start':
                    content_block = content['content_block']
                elif content['type'] == 'content_block_stop':
                    try:
                        api_gateway_client.post_to_connection(ConnectionId=connectionId, Data=json.dumps(content))
                    except api_gateway_client.exceptions.GoneException:
                        print(f"Connection {connectionId} is closed (GoneException) - stop message (tool)")
                    except Exception as e:
                        print(f"WebSocket send error (stop, tool): {e}")
                    if content_block['type'] == 'text':
                        content_block['text'] = streamed_content
                        assistant_response.append(content_block)
                    elif content_block['type'] == 'tool_use':
                        content_block['input'] = json.loads(streamed_content)
                        assistant_response.append(content_block)
                    streamed_content = ''
                elif content['type'] == 'content_block_delta':
                    try:
                        api_gateway_client.post_to_connection(ConnectionId=connectionId, Data=json.dumps(content))
                    except api_gateway_client.exceptions.GoneException:
                        print(f"Connection {connectionId} is closed (GoneException) - delta message (tool)")
                    except Exception as e:
                        print(f"WebSocket send error (delta, tool): {e}")
                    if content['delta']['type'] == 'text_delta':
                        streamed_content += content['delta']['text']
                    elif content['delta']['type'] == 'input_json_delta':
                        streamed_content += content['delta']['partial_json']
                elif content['type'] == 'message_stop':
                    input_tokens += content['amazon-bedrock-invocationMetrics']['inputTokenCount']
                    output_tokens += content['amazon-bedrock-invocationMetrics']['outputTokenCount']
            
            # Extract final answer
            final_ans = ""
            for i in assistant_response:
                if i['type'] == 'text':
                    final_ans = i['text']
                    break
            
            # If no text response, provide fallback
            if not final_ans:
                final_ans = "I apologize, but I couldn't retrieve the information at this time. Please try again or contact our support team."
            
            return {"statusCode": "200", "answer": final_ans, "question": chat, "session_id": session_id, "input_tokens": str(input_tokens), "output_tokens": str(output_tokens)}

        else:
            # No tools called, handle normal response
            for action in assistant_response:
                if action['type'] == 'text':
                    ai_response = action['text']
                    return {"statusCode": "200", "answer": ai_response, "question": chat, "session_id": session_id, "input_tokens": str(input_tokens), "output_tokens": str(output_tokens)}
            
            # Fallback if no text response
            return {"statusCode": "200", "answer": "I'm here to help with your retail shopping needs. How can I assist you today?", "question": chat, "session_id": session_id, "input_tokens": str(input_tokens), "output_tokens": str(output_tokens)}
    except Exception as e:
        print(f"Unexpected error: {e}")
        response = "An Unknown error occurred. Please try again after some time."
        return {
            "statusCode": "500",
            "answer": response,
            "question": chat,
            "session_id": session_id,
            "input_tokens": "0",
            "output_tokens": "0"
        }

#retail functions end here















def paris_agent_invoke_tool(chat_history, session_id, chat, connectionId):
    try:
        # Start keepalive thread
        #keepalive_thread = send_keepalive(connectionId, 30)
        import uuid
        import random
        # # Fetch base_prompt from the database as before
        # select_query = f'''select base_prompt from {schema}.{prompt_metadata_table} where id =5;'''
        # print(select_query)
        base_prompt =f'''

You are a Virtual Bakery Assistant for Paris Baguettes, a helpful and friendly chatbot for bakery customers. You help customers with their orders, reorders, location inquiries, menu browsing, and payment processing.

CRITICAL INSTRUCTIONS:
NEVER reply with any message that says you are checking, looking up, or finding information (such as "I'll check that for you", "Let me look that up", "One moment", "I'll find out", etc.).
NEVER say "To answer your question about [topic], let me check our system" or similar phrases.
After using a tool, IMMEDIATELY provide only the direct answer or summary to the user, with no filler, no explanations, and no mention of checking or looking up.
If a user asks a question that requires a tool, use the tool and reply ONLY with the answer or summary, never with any statement about the process.
For general bakery questions, IMMEDIATELY use the bakery_faq_tool_schema tool WITHOUT any preliminary message.

PAYMENT REQUIREMENTS:
ALL orders (new orders and reorders) require payment confirmation before processing.
NEVER process any order without payment.
ALWAYS provide payment links for order confirmation.
Payment is mandatory for order fulfillment.

CRITICAL INSTRUCTIONS:
NEVER reply with any message that says you are checking, looking up, or finding information (such as "I'll check that for you", "Let me look that up", "One moment", "I'll find out", etc.).
NEVER say "To answer your question about [topic], let me check our system" or similar phrases.
After using a tool, IMMEDIATELY provide only the direct answer or summary to the user, with no filler, no explanations, and no mention of checking or looking up.
If a user asks a question that requires a tool, use the tool and reply ONLY with the answer or summary, never with any statement about the process.
For general retail questions, IMMEDIATELY use the retail_faq_tool_schema tool WITHOUT any preliminary message.

CUSTOMER AUTHENTICATION RULES:
ALWAYS verify Customer ID and Email before proceeding with any order-related tools
NEVER proceed with get_previous_orders, place_order, or reorder_with_payment without successful authentication
ONLY use tools after confirming the Customer ID and Email combination is valid
If authentication fails, provide a clear error message and ask for correct credentials

VALID CUSTOMER DATA:
Use these exact Customer ID and Email combinations for verification:
CUST1001 (Rachel Tan) - Email: rachel.tan@email.com  
CUST1002 (Jason Lim) - Email: jason.lim@email.com  
CUST1003 (Mary Goh) - Email: mary.goh@email.com  
CUST1004 (Daniel Ong) - Email: daniel.ong@email.com  
CUST1005 (Aisha Rahman) - Email: aisha.rahman@email.com

SESSION AUTHENTICATION STATE MANAGEMENT:
MAINTAIN SESSION STATE: Once an Account ID and Email are successfully verified, store this authentication state for the ENTIRE conversation session
NEVER RE-ASK: Do not ask for Account ID or Email again during the same session unless:
1. User explicitly provides a different Account ID
2. Authentication explicitly fails during a tool call
3. User explicitly requests to switch accounts

AUTHENTICATION PERSISTENCE RULES:
FIRST AUTHENTICATION: Ask for Account ID and Email only on the first order-related request
SESSION MEMORY: Remember the authenticated Account ID throughout the conversation
AUTOMATIC REUSE: Use the stored authenticated credentials for ALL subsequent order-related tool calls
NO RE-VERIFICATION: Do not re-verify credentials that have already been successfully authenticated in the current session

PRE-AUTHENTICATION CHECK:
Before asking for Account ID or Email for ANY order-related request:
Scan conversation history for previously provided Account ID
Check if Email was already verified for that Account ID in this session
If both are found and verified, proceed directly with stored credentials
Only ask for credentials that are missing or failed verification

ACCOUNT ID AND EMAIL HANDLING RULES:
SESSION-LEVEL STORAGE: Once Account ID is provided and verified, use it for ALL subsequent requests
ONE-TIME EMAIL: Ask for Email only ONCE per Account ID per session
CONVERSATION CONTEXT: Check the ENTIRE conversation history for previously provided and verified credentials
SMART REUSE: If user asks "I gave you before" or similar, acknowledge and proceed with stored credentials
CONTEXT AWARENESS: Before asking for credentials, always check if they were provided earlier in the conversation
When Account ID is provided, validate it matches the pattern ACC#### (e.g., ACC1001)
Use the same Account ID and Email for all subsequent tool calls in the session until Account ID changes
ALWAYS verify Email matches the Account ID before proceeding on first authentication only

AUTHENTICATION PROCESS:
Check Session State - Scan conversation for existing authenticated credentials
Collect Account ID - Ask for Account ID ONLY if not previously provided and verified
Validate Account ID - Check if it matches one of the valid Account IDs above
Collect Email - Ask for Email ONLY if not previously provided and verified for current Account ID
Verify Email - Check if the Email matches the Account ID (only on first authentication)
Store Authentication State - Remember successful authentication for entire session
Proceed with Tools - Use stored credentials for all subsequent order-related requests

MANDATORY QUESTION COLLECTION RULES:
ALWAYS collect ALL required information for any tool before using it
NEVER skip any required questions, even if the user provides some information
NEVER assume or guess missing information
NEVER proceed with incomplete information
Ask questions ONE AT A TIME in this exact order:

For get_previous_orders tool:
1. Check session state first - Use stored Customer ID and Email if already authenticated
2. Customer ID - if not already provided and verified in conversation
3. Email - only if not already provided and verified for current Customer ID
4. VERIFY Customer ID and Email combination is valid (only on first authentication)
5. ONLY proceed with tool call after successful authentication

For place_order tool (ask in this exact order):
1. Check session state first - Use stored Customer ID and Email if already authenticated
2. Customer ID - if not already provided and verified in conversation
3. Email - only if not already provided and verified for current Customer ID
4. VERIFY Customer ID and Email combination is valid (only on first authentication)
5. Location/Shop selection
6. Items to purchase (with quantities)
7. Pickup time preference
8. ONLY proceed with tool call after successful authentication (payment link will be automatically generated)

For reorder_with_payment tool (ask in this exact order):
1. Check session state first - Use stored Customer ID and Email if already authenticated
2. Customer ID - if not already provided and verified in conversation
3. Email - only if not already provided and verified for current Customer ID
4. VERIFY Customer ID and Email combination is valid (only on first authentication)
5. Previous order number to reorder
6. Confirm reorder details
7. ONLY proceed with tool call after successful authentication (payment link will be automatically generated)

## PRODUCT INQUIRIES HANDLING

For product-related questions, use the retail_faq_tool_schema to provide general information about products, services, and policies. This tool can answer questions about:
- Product categories and general information
- Pricing policies and payment options
- Return and warranty policies
- Store services and features
- Account types and benefits

Always provide helpful, accurate information from the knowledge base without making specific product availability claims.

INPUT VALIDATION RULES:
NEVER ask for the same Account ID twice in a session unless user provides different one
NEVER ask for Email twice for the same Account ID in a session
Accept Account ID in format ACC#### only
Accept Email in standard email format
Accept any reasonable order numbers, SKUs, or product descriptions
NEVER ask for specific formats - accept what the user provides
If validation fails, provide a clear, specific error message with examples
ALWAYS verify Email matches the Account ID before proceeding (only on first authentication)

AUTHENTICATION ERROR MESSAGES:
If Account ID is invalid: "Invalid Account ID. Please provide a valid Account ID (e.g., ACC0001)."
If Email is incorrect: "Email address doesn't match Account ID [ACC####]. Please provide the correct email address."
If both are wrong: "Invalid Account ID and Email combination. Please check your credentials and try again."

Tool Usage Rules:
When a user asks about their previous orders, use get_previous_orders tool AFTER authentication (use stored credentials if available)
When a user wants to place a new order, use place_order tool AFTER authentication (use stored credentials if available)
When a user wants to reorder from a previous order with payment, use reorder_with_payment tool AFTER authentication (use stored credentials if available)
When a user asks about shop locations, use get_shop_locations tool to provide location information
When a user asks about menu for a specific location, use get_location_menu tool to provide menu information
When a user asks about general bakery information, policies, or services, use the bakery_faq_tool_schema tool
Do NOT announce that you're using tools or searching for information
Simply use the tool and provide the direct answer

Response Format:
ALWAYS answer in the shortest, most direct way possible
Do NOT add extra greetings, confirmations, or explanations
Do NOT mention backend systems or tools
Speak naturally as a helpful retail representative who already knows the information

Available Tools:
get_previous_orders - Retrieve customer's previous order history and details (requires authentication)
place_order - Process new customer orders with payment and pickup details (requires authentication)
reorder_with_payment - Reorder from previous orders with payment processing (requires authentication)
get_shop_locations - Retrieve available bakery shop locations and addresses
get_location_menu - Retrieve menu items available at specific shop locations
bakery_faq_tool_schema - Retrieve answers from the bakery knowledge base for general questions, policies, and product information

SYSTEMATIC QUESTION COLLECTION:
When a user wants order information, returns, new orders, or cancellations, IMMEDIATELY check session state for existing authentication
If already authenticated in session, proceed directly with remaining required information
Ask ONLY ONE question at a time
After each user response, check what information is still missing
Ask for the NEXT missing required field (in the exact order listed above)
Do NOT ask multiple questions in one message
Do NOT skip any required questions
Do NOT proceed until ALL required information is collected
ALWAYS use stored authentication if available, verify authentication before proceeding with tools only on first authentication

EXAMPLES OF CORRECT BEHAVIOR:
First Order-Related Request:
User: "Where is my order?"
Assistant: "What is your Account ID?"
User: "ACC1001"
Assistant: "Please provide your email address for verification."
User: "rachel.tan@email.com"
Assistant: "What is your order number?"
User: "ORD789012"
Assistant: [Verify ACC1001 + rachel.tan@email.com is valid, store authentication state, then use get_order_status tool and provide order details]

Subsequent Order-Related Requests in Same Session:
User: "What are your return policies?"
Assistant: [Use retail_faq_tool_schema tool and provide return policy information]
User: "I want to return the headphones from that order"
Assistant: "Which specific item would you like to return? Please provide the item ID."
[Uses stored ACC0001 authentication, only asks for return-specific details]
User: "Can I place another order?"
Assistant: "What items would you like to purchase?"
[Uses stored ACC0001 authentication, only asks for order details]

Different Account ID in Same Session:
User: "Can you check order for ACC0002?"
Assistant: "Please provide your email address for Account ID ACC0002 verification."

EXAMPLES OF INCORRECT BEHAVIOR:
❌ "What's your Account ID, email, and order number?" (asking multiple questions)
❌ Asking for Account ID again after it was already provided and verified in the session
❌ Asking for Email again for the same Account ID in the same session
❌ Skipping Email verification on first authentication
❌ Proceeding with incomplete information
❌ Not checking conversation history for existing authentication
❌ Re-asking for credentials after using FAQ tool

SECURITY GUIDELINES:
Require Email verification only once per Account ID in each session
Never store or reference Email values in conversation history for security
If user switches to a different Account ID, ask for the corresponding Email
Treat all order and account information as sensitive and confidential
ALWAYS verify Account ID and Email combination before first account access
MAINTAIN authentication state throughout session for user experience

PRODUCT KNOWLEDGE:
You have access to comprehensive information about AnyRetail products and services including:
Account Types (Rewards Account, Student Account)
Shopping Services (Express Shopping, Corporate Account)
Store Credit Cards (Rewards+ Card, Cashback Max Card)
Financing Options (Personal Shopping Credit, Buy Now Pay Later)
Mobile app features and digital shopping capabilities

RESPONSE GUIDELINES:
Handle greetings warmly and ask how you can help with their shopping needs today
For product inquiries, provide specific details from the knowledge base
For order-specific queries, always use appropriate tools with proper authentication
For service issues, efficiently collect information and process requests
Keep responses concise and actionable
Never leave users without a clear next step or resolution

CUSTOMER SERVICE EXCELLENCE:
Be proactive in offering related services (e.g., suggest express shipping for urgent orders)
Acknowledge customer concerns and provide reassurance
Offer alternatives when primary requests cannot be fulfilled
Follow up on complex issues with clear next steps
Maintain a friendly, professional tone throughout all interactions
 '''

        
        # Bakery tool schema for Paris Baguettes
        bakery_tools = [
            {
                "name": "get_previous_orders",
                "description": "Retrieve customer's previous order history and details",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "customer_id": {"type": "string", "description": "Customer ID in format CUST#### (e.g., CUST1001)"},
                        "email": {"type": "string", "description": "Email address for customer verification"}
                    },
                    "required": ["customer_id", "email"]
                }
            },
            {
                "name": "place_order",
                "description": "Process new customer orders with payment and pickup details",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "customer_id": {"type": "string", "description": "Customer ID in format CUST#### (e.g., CUST1001)"},
                        "email": {"type": "string", "description": "Email address for customer verification"},
                        "location": {"type": "string", "description": "Shop location (e.g., Downtown, Mall, Airport)"},
                        "items": {"type": "array", "description": "Array of items with quantities", "items": {"type": "object"}},
                        "pickup_time": {"type": "string", "description": "Preferred pickup time"}
                    },
                    "required": ["customer_id", "email", "location", "items", "pickup_time"]
                }
            },
            {
                "name": "reorder_with_payment",
                "description": "Reorder from previous orders with payment processing",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "customer_id": {"type": "string", "description": "Customer ID in format CUST#### (e.g., CUST1001)"},
                        "email": {"type": "string", "description": "Email address for customer verification"},
                        "previous_order_id": {"type": "string", "description": "Previous order ID to reorder from"}
                    },
                    "required": ["customer_id", "email", "previous_order_id"]
                }
            },
            {
                "name": "get_shop_locations",
                "description": "Retrieve available bakery shop locations and addresses",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "location_query": {"type": "string", "description": "Optional location query or area preference"}
                    },
                    "required": []
                }
            },
            {
                "name": "get_location_menu",
                "description": "Retrieve menu items available at specific shop locations",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "location": {"type": "string", "description": "Shop location (e.g., Downtown, Mall, Airport)"}
                    },
                    "required": ["location"]
                }
            },
            {
                "name": "bakery_faq_tool_schema",
                "description": "Retrieve answers from the bakery knowledge base for general bakery questions, policies, and product information",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "knowledge_base_retrieval_question": {"type": "string", "description": "A question to retrieve from the bakery knowledge base about bakery services, policies, procedures, or general information."}
                    },
                    "required": ["knowledge_base_retrieval_question"]
                }
            }
        ]
      # --- Customer Database for Paris Baguettes ---
        valid_customers = {
            "CUST1001": {"name": "Rachel Tan", "email": "rachel.tan@email.com", "phone": "+1-555-0123"},
            "CUST1002": {"name": "Jason Lim", "email": "jason.lim@email.com", "phone": "+1-555-0456"},
            "CUST1003": {"name": "Mary Goh", "email": "mary.goh@email.com", "phone": "+1-555-0789"},
            "CUST1004": {"name": "Daniel Ong", "email": "daniel.ong@email.com", "phone": "+1-555-0321"},
            "CUST1005": {"name": "Aisha Rahman", "email": "aisha.rahman@email.com", "phone": "+1-555-0654"}
        }

        # --- Bakery Shop Locations ---
        shop_locations = {
            "Downtown": {
                "address": "123 Main Street, Downtown",
                "phone": "+1-555-0100",
                "hours": "7:00 AM - 8:00 PM",
                "available_products": ["cappuccino cake", "chocolate cake", "mocha cake", "pumpkin scone", "croissant", "sweet potato pastry"]
            },
            "Mall": {
                "address": "456 Shopping Center, Mall District",
                "phone": "+1-555-0200",
                "hours": "8:00 AM - 9:00 PM",
                "available_products": ["cappuccino cake", "chocolate cake", "mocha cake", "croissant", "sweet potato pastry"]
            },
            "Airport": {
                "address": "789 Terminal 1, Airport Plaza",
                "phone": "+1-555-0300",
                "hours": "6:00 AM - 10:00 PM",
                "available_products": ["cappuccino cake", "chocolate cake", "croissant", "sweet potato pastry"]
            }
        }

        # --- Product Database ---
        bakery_products = {
            "cappuccino cake": {"name": "Cappuccino Cake", "price": 12.99, "description": "Rich coffee-flavored cake with cream"},
            "chocolate cake": {"name": "Chocolate Cake", "price": 14.99, "description": "Decadent chocolate cake with ganache"},
            "mocha cake": {"name": "Mocha Cake", "price": 13.99, "description": "Coffee and chocolate combination cake"},
            "pumpkin scone": {"name": "Pumpkin Scone", "price": 4.99, "description": "Spiced pumpkin scone with glaze"},
            "croissant": {"name": "Croissant", "price": 3.99, "description": "Buttery French croissant"},
            "sweet potato pastry": {"name": "Sweet Potato Pastry", "price": 5.99, "description": "Sweet potato filled pastry"}
        }

        # --- Customer Order History ---
        customer_order_relationships = {
            "CUST1001": ["BAK001", "BAK002"],
            "CUST1002": ["BAK003", "BAK004"],
            "CUST1003": ["BAK005", "BAK006"],
            "CUST1004": ["BAK007", "BAK008"],
            "CUST1005": ["BAK009", "BAK010"]
        }

        order_details = {
            "BAK001": {
                "customer_id": "CUST1001",
                "location": "Downtown",
                "items": [
                    {"name": "Cappuccino Cake", "quantity": 1, "price": 12.99},
                    {"name": "Croissant", "quantity": 2, "price": 3.99}
                ],
                "total": 20.97,
                "pickup_time": "2024-01-15 10:00 AM",
                "status": "Completed"
            },
            "BAK002": {
                "customer_id": "CUST1001",
                "location": "Mall",
                "items": [
                    {"name": "Chocolate Cake", "quantity": 1, "price": 14.99}
                ],
                "total": 14.99,
                "pickup_time": "2024-01-20 2:00 PM",
                "status": "Completed"
            },
            "BAK003": {
                "customer_id": "CUST1002",
                "location": "Airport",
                "items": [
                    {"name": "Mocha Cake", "quantity": 1, "price": 13.99},
                    {"name": "Sweet Potato Pastry", "quantity": 3, "price": 5.99}
                ],
                "total": 31.96,
                "pickup_time": "2024-01-18 8:00 AM",
                "status": "Completed"
            }
        }
        def validate_customer_order_relationship(customer_id, order_id):
            """Validate that the customer ID owns the order ID"""
            if customer_id not in customer_order_relationships:
                return False, f"Invalid Customer ID: {customer_id}"
            
            if order_id not in customer_order_relationships[customer_id]:
                return False, f"Order {order_id} does not belong to Customer ID {customer_id}. Please provide the correct Customer ID for this order."
            
            return True, "Valid relationship"

        def authenticate_customer(customer_id, email=None):
            """Authenticate Customer ID and optionally verify email"""
            if customer_id not in valid_customers:
                return False, "Invalid Customer ID. Please provide a valid Customer ID (e.g., CUST1001)."
            
            # If email is provided, verify it matches the account
            if email:
                expected_email = valid_customers[customer_id]['email']
                if email.lower() != expected_email.lower():
                    return False, f"I'm unable to verify your account. The email address doesn't match Customer ID {customer_id}. Please provide the correct email address."
            
            return True, f"Authentication successful for {valid_customers[customer_id]['name']}"

        # --- Mock bakery tool implementations ---
        def get_previous_orders(customer_id, email):
            # Authenticate customer first
            auth_success, auth_message = authenticate_customer(customer_id, email)
            if not auth_success:
                return {"error": auth_message}
            
            if customer_id not in customer_order_relationships:
                return {"error": "No orders found for this customer"}
            
            orders = customer_order_relationships[customer_id]
            order_list = []
            
            for order_id in orders:
                if order_id in order_details:
                    order_info = order_details[order_id]
                    order_list.append({
                        "order_id": order_id,
                        "location": order_info["location"],
                        "items": order_info["items"],
                        "total": order_info["total"],
                        "pickup_time": order_info["pickup_time"],
                        "status": order_info["status"]
                    })
            
            return {
                "customer_id": customer_id,
                "customer_name": valid_customers[customer_id]["name"],
                "orders": order_list,
                "total_orders": len(order_list)
            }

        def place_order(customer_id, email, location, items, pickup_time):
            # Set default payment method
            payment_method = "Credit Card"
            # Authenticate customer first
            auth_success, auth_message = authenticate_customer(customer_id, email)
            if not auth_success:
                return {"error": auth_message}
            
            # Validate location
            if location not in shop_locations:
                return {"error": f"Location '{location}' not found. Available locations: {', '.join(shop_locations.keys())}"}
            
            # Calculate total and validate items
            total = 0
            order_items = []
            
            for item in items:
                item_name = item.get("name", "").lower()
                quantity = item.get("quantity", 1)
                
                if item_name not in bakery_products:
                    return {"error": f"Product '{item_name}' not found in our menu"}
                
                if item_name not in shop_locations[location]["available_products"]:
                    return {"error": f"Product '{item_name}' is not available at {location} location"}
                
                product_info = bakery_products[item_name]
                item_total = product_info["price"] * quantity
                total += item_total
                
                order_items.append({
                    "name": product_info["name"],
                    "quantity": quantity,
                    "price": product_info["price"],
                    "total": item_total
                })
            
            # Generate order ID
            order_id = f"BAK{str(uuid.uuid4())[:6].upper()}"
            
            # Create payment link for ALL orders (required for confirmation)
            payment_link = f"https://payment.parisbaguettes.com/pay/{order_id}"
            
            # Generate unique payment reference
            payment_reference = f"PB-{order_id}-{int(total * 100)}"
            
            return {
                "order_id": order_id,
                "customer_id": customer_id,
                "customer_name": valid_customers[customer_id]["name"],
                "location": location,
                "items": order_items,
                "total": round(total, 2),
                "currency": "USD",
                "pickup_time": pickup_time,
                "payment_method": payment_method,
                "payment_link": payment_link,
                "payment_reference": payment_reference,
                "status": "Pending Payment",
                "payment_required": True,
                "message": f"Order placed successfully! Payment is required to confirm your order. Please complete payment using the provided link: {payment_link}"
            }

        def reorder_with_payment(customer_id, email, previous_order_id):
            # Set default payment method
            payment_method = "Credit Card"
            # Authenticate customer first
            auth_success, auth_message = authenticate_customer(customer_id, email)
            if not auth_success:
                return {"error": auth_message}
            
            # Validate that the customer owns the previous order
            relationship_valid, relationship_msg = validate_customer_order_relationship(customer_id, previous_order_id)
            if not relationship_valid:
                return {"error": relationship_msg}
            
            if previous_order_id not in order_details:
                return {"error": "Previous order not found"}
            
            # Get previous order details
            previous_order = order_details[previous_order_id]
            
            # Generate new order ID
            new_order_id = f"BAK{str(uuid.uuid4())[:6].upper()}"
            
            # Create payment link for ALL reorders (required for confirmation)
            payment_link = f"https://payment.parisbaguettes.com/pay/{new_order_id}"
            
            # Generate unique payment reference
            payment_reference = f"PB-{new_order_id}-{int(previous_order['total'] * 100)}"
            
            return {
                "new_order_id": new_order_id,
                "previous_order_id": previous_order_id,
                "customer_id": customer_id,
                "customer_name": valid_customers[customer_id]["name"],
                "location": previous_order["location"],
                "items": previous_order["items"],
                "total": previous_order["total"],
                "currency": "USD",
                "payment_method": payment_method,
                "payment_link": payment_link,
                "payment_reference": payment_reference,
                "status": "Pending Payment",
                "payment_required": True,
                "message": f"Reorder created successfully! Payment is required to confirm your reorder. Please complete payment using the provided link: {payment_link}"
            }

        def get_shop_locations(location_query=None):
            """Get available shop locations"""
            if location_query:
                # Filter locations based on query
                filtered_locations = {}
                for location_name, location_data in shop_locations.items():
                    if location_query.lower() in location_name.lower() or location_query.lower() in location_data["address"].lower():
                        filtered_locations[location_name] = location_data
                return {"locations": filtered_locations}
            
            return {"locations": shop_locations}

        def get_location_menu(location):
            """Get menu for specific location"""
            if location not in shop_locations:
                return {"error": f"Location '{location}' not found. Available locations: {', '.join(shop_locations.keys())}"}
            
            location_data = shop_locations[location]
            menu_items = []
            
            for product_key in location_data["available_products"]:
                if product_key in bakery_products:
                    product_info = bakery_products[product_key]
                    menu_items.append({
                        "name": product_info["name"],
                        "price": product_info["price"],
                        "description": product_info["description"]
                    })
            
            return {
                "location": location,
                "address": location_data["address"],
                "phone": location_data["phone"],
                "hours": location_data["hours"],
                "menu": menu_items
            }

        def get_dynamic_date(days_offset):
            """Helper function to get dynamic date"""
            from datetime import datetime, timedelta
            return (datetime.now() + timedelta(days=days_offset)).strftime("%Y-%m-%d")

        def get_dynamic_datetime(hours_offset):
            """Helper function to get dynamic datetime"""
            from datetime import datetime, timedelta
            return (datetime.now() + timedelta(hours=hours_offset)).strftime("%Y-%m-%d %H:%M:%S")

        def initiate_return_request(account_id, email, order_id, item_id_or_product_name, return_reason):
            # Authenticate customer first
            auth_success, auth_message = authenticate_customer(account_id, email)
            if not auth_success:
                return {"error": auth_message}

            if not item_id_or_product_name or item_id_or_product_name.lower() in ["", "none", "null"]:
                order_items = get_order_items_for_return(order_id)
                if order_items.startswith("Order not found") or order_items.startswith("No items found"):
                    return {"error": order_items}
                
                return {
                    "message": f"Please specify which item you'd like to return from order {order_id}. Here are the items in your order:\n\n{order_items}\n\nPlease provide the item name (e.g., 'Gaming Console - White') or item ID you wish to return along with your return reason."
                }
            
            # Check if item_id_or_product_name is an order number (starts with ORD)
            if item_id_or_product_name.startswith("ORD"):
                # User provided an order number instead of item ID
                return {
                    "error": f"You provided order number '{item_id_or_product_name}', but I need the specific item you want to return from order {order_id}. Please provide the item name (e.g., 'Gaming Console - White') or item ID."
                }
            
            # Check if item_id_or_product_name is an item ID (starts with ITM) or a product name
            if item_id_or_product_name.startswith("ITM"):
                # It's an item ID, use the original validation
                relationship_valid, relationship_msg = validate_account_order_item_relationship(account_id, order_id, item_id_or_product_name)
                if not relationship_valid:
                    return {"error": relationship_msg}
                item_id = item_id_or_product_name
                item_name = order_item_relationships[order_id][item_id]["name"]
            else:
                # It's a product name, use product name validation
                relationship_valid, relationship_result = validate_order_product_relationship(account_id, order_id, item_id_or_product_name)
                if not relationship_valid:
                    return {"error": relationship_result}
                item_id = relationship_result  # relationship_result contains the found item_id
                item_name = order_item_relationships[order_id][item_id]["name"]
            
            return_request_id = f"RA-{str(uuid.uuid4())[:6].upper()}"
            return {
                "return_request_id": return_request_id,
                "status": "Approved",
                "assigned_team": "Returns Processing",
                "expected_pickup": get_dynamic_date(3),
                "summary": f"Return request approved for {item_name} from order {order_id}. Reason: {return_reason}",
                "refund_method": "We’ll process your refund using the same payment method you used at checkout."
            }
        



        def check_product_availability(product_name, model=None, color=None, type=None, size_in_inches=None, display_type=None):
            # Only support headphones and TV as per base prompt
            if product_name not in ["headphones", "TV"]:
                return {"error": "Sorry, I can assist only with headphones and TVs at the moment."}
            
            mock_products = {
                "headphones": {
                    "ZX900 Pro": {
                        "product_name": "headphones",
                        "model": "ZX900 Pro",
                        "color": "Black",
                        "type": "over-ear",
                        "price": 299.99,
                        "availability": "In Stock",
                        "currency": "SGD",
                        "status": "Active",
                        "last_updated": get_dynamic_datetime(2)
                    },
                    "SoundMax Elite": {
                        "product_name": "headphones",
                        "model": "SoundMax Elite",
                        "color": "Silver",
                        "type": "in-ear",
                        "price": 199.99,
                        "availability": "In Stock",
                        "currency": "SGD",
                        "status": "Active",
                        "last_updated": get_dynamic_datetime(2)
                    }
                },
                "TV": {
                    "VisionX": {
                        "product_name": "TV",
                        "size_in_inches": 55,
                        "display_type": "OLED",
                        "model": "VisionX",
                        "price": 1299.99,
                        "availability": "In Stock",
                        "currency": "SGD",
                        "status": "Active",
                        "last_updated": get_dynamic_datetime(2)
                    },
                    "Alpha7": {
                        "product_name": "TV",
                        "size_in_inches": 65,
                        "display_type": "QLED",
                        "model": "Alpha7",
                        "price": 1899.99,
                        "availability": "In Stock",
                        "currency": "SGD",
                        "status": "Active",
                        "last_updated": get_dynamic_datetime(2)
                    }
                }
            }
            
            category_products = mock_products.get(product_name, {})
            
            # For TVs, match by size and display type if provided
            if product_name == "TV":
                for product in category_products.values():
                    # If size and display type are provided, check for exact match
                    if size_in_inches is not None and display_type is not None:
                        if (product.get("size_in_inches") == size_in_inches and 
                            product.get("display_type") == display_type):
                            return product
                    
                    # If only size is provided, check for size match
                    elif size_in_inches is not None:
                        if product.get("size_in_inches") == size_in_inches:
                            return product
                    
                    # If only display type is provided, check for display type match
                    elif display_type is not None:
                        if product.get("display_type") == display_type:
                            return product
                
                # If no exact match found, provide information about available options
                if size_in_inches is not None or display_type is not None:
                    available_options = []
                    for product in category_products.values():
                        available_options.append(f"{product['model']} {product['size_in_inches']}-inch {product['display_type']}")
                    
                    if size_in_inches is not None and display_type is not None:
                        return {
                            "error": f"Sorry, we don't have a {size_in_inches}-inch {display_type} TV in stock. Available options: {', '.join(available_options)}"
                        }
                    elif size_in_inches is not None:
                        return {
                            "error": f"Sorry, we don't have a {size_in_inches}-inch TV in stock. Available options: {', '.join(available_options)}"
                        }
                    elif display_type is not None:
                        return {
                            "error": f"Sorry, we don't have a {display_type} TV in stock. Available options: {', '.join(available_options)}"
                        }
            
            # For headphones, match by model, color, and type if provided
            elif product_name == "headphones":
                for product in category_products.values():
                    # If model, color, and type are provided, check for exact match
                    if model is not None and color is not None and type is not None:
                        if (product.get("model") == model and 
                            product.get("color") == color and 
                            product.get("type") == type):
                            return product
                    
                    # If only model is provided, check for model match
                    elif model is not None:
                        if product.get("model") == model:
                            return product
                
                # If no exact match found for headphones, provide information about available options
                if model is not None or color is not None or type is not None:
                    available_options = []
                    for product in category_products.values():
                        available_options.append(f"{product['model']} {product['color']} {product['type']}")
                    
                    return {
                        "error": f"Sorry, the requested headphones are not available. Available options: {', '.join(available_options)}"
                    }
            
            # If no specific parameters provided, return first available product
            if category_products:
                return list(category_products.values())[0]
            
            return {"error": "Product not found"}

        def cancel_order(order_id, account_id, email, cancellation_reason, refund_method):
            # Authenticate customer first
            auth_success, auth_message = authenticate_customer(account_id, email)
            if not auth_success:
                return {"error": auth_message}
            
            # Validate that the account ID owns the order ID
            relationship_valid, relationship_msg = validate_account_order_relationship(account_id, order_id)
            if not relationship_valid:
                return {"error": relationship_msg}
            
            cancellation_id = f"CAN-{str(uuid.uuid4())[:6].upper()}"
            return {
                "cancellation_id": cancellation_id,
                "status": "Approved",
                "assigned_team": "Order Management",
                "expected_callback": get_dynamic_date(2),
                "summary": f"Order {order_id} has been successfully cancelled. Reason: {cancellation_reason}. Refund will be processed via {refund_method} within 3-5 business days."
            }

        def get_bakery_faq_chunks(query):
            try:
                print("IN BAKERY FAQ: ", query)
                chunks = []
                # Use the same KB_ID for now, but you might want to create a separate bakery KB
                response_chunks = retrieve_client.retrieve(
                    retrievalQuery={                                                                                
                        'text': query
                    },
                    knowledgeBaseId=RETAIL_KB_ID,
                    retrievalConfiguration={
                        'vectorSearchConfiguration': {                          
                            'numberOfResults': 10,                                                                                              
                            'overrideSearchType': 'HYBRID'
                        }
                    }
                )
               
                for item in response_chunks['retrievalResults']:
                    if 'content' in item and 'text' in item['content']:
                        chunks.append(item['content']['text'])
                print('BAKERY FAQ CHUNKS: ', chunks)  
                return chunks
            except Exception as e:
                print("An exception occurred while retrieving bakery FAQ chunks:", e)
                return []

        input_tokens = 0
        output_tokens = 0
        print("In bakery_agent_invoke_tool (Bakery Bot)")

        # Extract Customer ID, Order ID, and Email from chat history
        extracted_customer_id = None
        extracted_order_id = None
        extracted_email = None
        
        for message in chat_history:
            if message['role'] == 'user':
                content_text = message['content'][0]['text']
                
                # Extract Customer ID (CUST followed by 4 digits)
                customer_id_match = re.search(r'\b(CUST\d{4})\b', content_text.upper())
                if customer_id_match:
                    extracted_customer_id = customer_id_match.group(1)
                    print(f"Extracted Customer ID from chat history: {extracted_customer_id}")
                    
                # Extract Order ID (BAK followed by 6 digits)
                order_id_match = re.search(r'\b(BAK\d{6})\b', content_text.upper())
                if order_id_match:
                    extracted_order_id = order_id_match.group(1)
                    print(f"Extracted Order ID from chat history: {extracted_order_id}")
                
                # Extract Email address
                email_match = re.search(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b', content_text)
                if email_match:
                    extracted_email = email_match.group(0)
                    print(f"Extracted Email from chat history: {extracted_email}")
        
        # Enhance system prompt with Customer ID, Order ID, and Email context
        enhanced_context = []
        
        # Enhance system prompt with Customer ID context
        if extracted_customer_id:
            enhanced_context.append(f"The customer's Customer ID is {extracted_customer_id}. Use this Customer ID automatically for any tool calls that require it without asking again.")
        
        if extracted_order_id:
            enhanced_context.append(f"The customer's Order ID is {extracted_order_id}. Use this Order ID automatically for any tool calls that require it without asking again.")
        
        if extracted_email:
            enhanced_context.append(f"The customer's Email is {extracted_email}. Use this Email automatically for any tool calls that require it without asking again.")
        
        if enhanced_context:
            enhanced_prompt = base_prompt + f"\n\nIMPORTANT: {' '.join(enhanced_context)}"
            print(f"Enhanced prompt with context: {enhanced_context}")
        else:
            enhanced_prompt = base_prompt
        
        # Use the enhanced_prompt instead of base_prompt
        prompt = enhanced_prompt
        
        # First API call to get initial response
        try:
            response = bedrock_client.invoke_model_with_response_stream(
                contentType='application/json',
                body=json.dumps({
                    "anthropic_version": "bedrock-2023-05-31",
                    "max_tokens": 4000,
                    "temperature": 0,
                    "top_p": 0.999,
                    "top_k": 250,
                    "system": prompt,
                    "tools": bakery_tools,
                    "messages": chat_history
                }),
                modelId=model_id
            )
        except Exception as e:
            print("AN ERROR OCCURRED : ", e)
            response = "We are unable to assist right now please try again after few minutes"
            return {"answer": response, "question": chat, "session_id": session_id}

        streamed_content = ''
        content_block = None
        assistant_response = []
        for item in response['body']:
            content = json.loads(item['chunk']['bytes'].decode())
            if content['type'] == 'content_block_start':
                content_block = content['content_block']
            elif content['type'] == 'content_block_stop':
                print(f"Content block at stop: {content_block}")  # Add debug line
                try:
                    api_gateway_client.post_to_connection(ConnectionId=connectionId, Data=json.dumps(content))
                except api_gateway_client.exceptions.GoneException:
                    print(f"Connection {connectionId} is closed (GoneException) - stop message")
                except Exception as e:
                    print(f"WebSocket send error (stop): {e}")
                if content_block['type'] == 'text':
                    content_block['text'] = streamed_content
                    assistant_response.append(content_block)
                elif content_block['type'] == 'tool_use':
                    try:
                        content_block['input'] = json.loads(streamed_content)
                    except json.JSONDecodeError as e:
                        print(f"JSON decode error for tool input: {e}")
                        print(f"Streamed content: {streamed_content}")
                        content_block['input'] = {}
                    assistant_response.append(content_block)
                streamed_content = ''
            elif content['type'] == 'content_block_delta':
                try:
                    api_gateway_client.post_to_connection(ConnectionId=connectionId, Data=json.dumps(content))
                except api_gateway_client.exceptions.GoneException:
                    print(f"Connection {connectionId} is closed (GoneException) - delta message")
                except Exception as e:
                    print(f"WebSocket send error (delta): {e}")
                if 'delta' in content and isinstance(content['delta'], dict):
                    if content['delta']['type'] == 'text_delta':
                        streamed_content += content['delta']['text']
                    elif content['delta']['type'] == 'input_json_delta':
                        streamed_content += content['delta']['partial_json']
            elif content['type'] == 'message_delta':
                try:
                    if 'usage' in content and isinstance(content['usage'], dict):
                        tool_tokens = content['usage']['output_tokens']
                    else:
                        tool_tokens = 0
                except (KeyError, TypeError) as e:
                    print(f"Error accessing usage tokens: {e}")
                    tool_tokens = 0
            elif content['type'] == 'message_stop':
                input_tokens += content['amazon-bedrock-invocationMetrics']['inputTokenCount']
                output_tokens += content['amazon-bedrock-invocationMetrics']['outputTokenCount']
        chat_history.append({'role': 'assistant', 'content': assistant_response})
        
        # Check if any tools were called
        tools_used = []
        tool_results = []
        
        for action in assistant_response:
            if action['type'] == 'tool_use':
                tools_used.append(action['name'])
                tool_name = action['name']
                tool_input = action['input']
                tool_result = None
                
                # Send a heartbeat to keep WebSocket alive during tool execution
                try:
                    heartbeat = {'type': 'heartbeat'}
                    api_gateway_client.post_to_connection(ConnectionId=connectionId, Data=json.dumps(heartbeat))
                except Exception as e:
                    print(f"Heartbeat send error: {e}")
                
                # Execute the appropriate bakery tool
                if tool_name == 'get_previous_orders':
                    print("get_previous_orders is called..")
                    tool_result = get_previous_orders(
                        tool_input['customer_id'],
                        tool_input['email']
                    )
                    # Check for authentication error
                    if isinstance(tool_result, dict) and 'error' in tool_result:
                        print(f"Authentication failed for get_previous_orders: {tool_result['error']}")
                elif tool_name == 'place_order':
                    print("place_order is called..")
                    tool_result = place_order(
                        tool_input['customer_id'],
                        tool_input['email'],
                        tool_input['location'],
                        tool_input['items'],
                        tool_input['pickup_time']
                    )
                    # Check for authentication error
                    if isinstance(tool_result, dict) and 'error' in tool_result:
                        print(f"Authentication failed for place_order: {tool_result['error']}")
                elif tool_name == 'reorder_with_payment':
                    print("reorder_with_payment is called..")
                    tool_result = reorder_with_payment(
                        tool_input['customer_id'],
                        tool_input['email'],
                        tool_input['previous_order_id']
                    )
                    # Check for authentication error
                    if isinstance(tool_result, dict) and 'error' in tool_result:
                        print(f"Authentication failed for reorder_with_payment: {tool_result['error']}")
                elif tool_name == 'get_shop_locations':
                    print("get_shop_locations is called..")
                    tool_result = get_shop_locations(tool_input.get('location_query'))
                elif tool_name == 'get_location_menu':
                    print("get_location_menu is called..")
                    tool_result = get_location_menu(tool_input['location'])
                elif tool_name == 'bakery_faq_tool_schema':
                    print("bakery_faq is called ...")
                    # Send another heartbeat before FAQ retrieval
                    try:
                        heartbeat = {'type': 'heartbeat'}
                        api_gateway_client.post_to_connection(ConnectionId=connectionId, Data=json.dumps(heartbeat))
                    except Exception as e:
                        print(f"Bakery FAQ heartbeat send error: {e}")
                    
                    tool_result = get_bakery_faq_chunks(tool_input['knowledge_base_retrieval_question'])
                    
                    # If FAQ tool returns empty or no results, provide fallback
                    if not tool_result or len(tool_result) == 0:
                        tool_result = ["I don't have specific information about that in our current bakery knowledge base. Let me schedule a callback with one of our bakery agents who can provide detailed information."]
                
                # Create tool result message with better error handling
                try:
                    if not isinstance(action, dict):
                        print(f"Action is not a dict: {type(action)}, value: {action}")
                        continue
                        
                    if 'id' not in action:
                        print(f"Action missing 'id' field: {action}")
                        continue
                    
                    print(f"Tool result type: {type(tool_result)}")
                    print(f"Tool result content: {tool_result}")
                    
                    if isinstance(tool_result, list) and tool_result:
                        content_text = "\n".join(tool_result)
                    elif isinstance(tool_result, list) and not tool_result:
                        content_text = "No information available"
                    else:
                        content_text = str(tool_result) if tool_result else "No information available"
                    
                    tool_response_dict = {
                        "type": "tool_result",
                        "tool_use_id": action['id'],
                        "content": [{"type": "text", "text": content_text}]
                    }
                    tool_results.append(tool_response_dict)
                    
                except Exception as e:
                    print(f"Error creating tool response: {e}")
                    print(f"Action type: {type(action)}")
                    print(f"Action content: {action}")
                    # Skip this tool result instead of crashing
                    continue
                tool_results.append(tool_response_dict)
        
        # If tools were used, add tool results to chat history and make second API call
        if tools_used:
            # Add tool results to chat history
            chat_history.append({'role': 'user', 'content': tool_results})
            
            # Make second API call with tool results
            try:
                response = bedrock_client.invoke_model_with_response_stream(
                    contentType='application/json',
                    body=json.dumps({
                        "anthropic_version": "bedrock-2023-05-31",
                        "max_tokens": 4000,
                        "temperature": 0,
                        "system": prompt,
                        "tools": bakery_tools,
                        "messages": chat_history
                    }),
                    modelId=model_id
                )
            except Exception as e:
                print("ERROR IN SECOND API CALL:", e)
                # Send error response via WebSocket
                error_response = "I apologize, but I'm having trouble accessing that information right now. Please try again in a moment."
                for word in error_response.split():
                    delta = {'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'text_delta', 'text': word + ' '}}
                    try:
                        api_gateway_client.post_to_connection(ConnectionId=connectionId, Data=json.dumps(delta))
                    except:
                        pass
                stop_answer = {'type': 'content_block_stop', 'index': 0}
                try:
                    api_gateway_client.post_to_connection(ConnectionId=connectionId, Data=json.dumps(stop_answer))
                except:
                    pass
                return {"answer": error_response, "question": chat, "session_id": session_id}

            # Process the streaming response
            streamed_content = ''
            content_block = None
            assistant_response = []
            
            for item in response['body']:
                content = json.loads(item['chunk']['bytes'].decode())
                if content['type'] == 'content_block_start':
                    content_block = content['content_block']
                elif content['type'] == 'content_block_stop':
                    try:
                        api_gateway_client.post_to_connection(ConnectionId=connectionId, Data=json.dumps(content))
                    except api_gateway_client.exceptions.GoneException:
                        print(f"Connection {connectionId} is closed (GoneException) - stop message (tool)")
                    except Exception as e:
                        print(f"WebSocket send error (stop, tool): {e}")
                    if content_block['type'] == 'text':
                        content_block['text'] = streamed_content
                        assistant_response.append(content_block)
                    elif content_block['type'] == 'tool_use':
                        try:
                            content_block['input'] = json.loads(streamed_content)
                        except json.JSONDecodeError as e:
                            print(f"JSON decode error for tool input: {e}")
                            print(f"Streamed content: {streamed_content}")
                            content_block['input'] = {}
                        assistant_response.append(content_block)
                    streamed_content = ''
                elif content['type'] == 'content_block_delta':
                    try:
                        api_gateway_client.post_to_connection(ConnectionId=connectionId, Data=json.dumps(content))
                    except api_gateway_client.exceptions.GoneException:
                        print(f"Connection {connectionId} is closed (GoneException) - delta message (tool)")
                    except Exception as e:
                        print(f"WebSocket send error (delta, tool): {e}")
                    if content['delta']['type'] == 'text_delta':
                        streamed_content += content['delta']['text']
                    elif content['delta']['type'] == 'input_json_delta':
                        streamed_content += content['delta']['partial_json']
                elif content['type'] == 'message_stop':
                    input_tokens += content['amazon-bedrock-invocationMetrics']['inputTokenCount']
                    output_tokens += content['amazon-bedrock-invocationMetrics']['outputTokenCount']
            
            # Extract final answer
            final_ans = ""
            for i in assistant_response:
                if i['type'] == 'text':
                    final_ans = i['text']
                    break
            
            # If no text response, provide fallback
            if not final_ans:
                final_ans = "I apologize, but I couldn't retrieve the information at this time. Please try again or contact our support team."
            
            return {"statusCode": "200", "answer": final_ans, "question": chat, "session_id": session_id, "input_tokens": str(input_tokens), "output_tokens": str(output_tokens)}

        else:
            # No tools called, handle normal response
            for action in assistant_response:
                if action['type'] == 'text':
                    ai_response = action['text']
                    return {"statusCode": "200", "answer": ai_response, "question": chat, "session_id": session_id, "input_tokens": str(input_tokens), "output_tokens": str(output_tokens)}
            
            # Fallback if no text response
            return {"statusCode": "200", "answer": "I'm here to help with your bakery needs. How can I assist you today?", "question": chat, "session_id": session_id, "input_tokens": str(input_tokens), "output_tokens": str(output_tokens)}
    except Exception as e:
        print(f"Unexpected error: {e}")
        response = "An Unknown error occurred. Please try again after some time."
        return {
            "statusCode": "500",
            "answer": response,
            "question": chat,
            "session_id": session_id,
            "input_tokens": "0",
            "output_tokens": "0"
        }

def hospital_agent_invoke_tool(chat_history, session_id, chat, connectionId):
    try:
        # Hardcoded patient data
        patients = {
            "PAT1001": {
                "dob": "1985-03-15",
                "name": "John Smith",
                "email": "john.smith@email.com",
                "phone": "91234567"
            },
            "PAT1002": {
                "dob": "1990-07-22",
                "name": "Sarah Johnson",
                "email": "sarah.johnson@email.com",
                "phone": "98765432"
            },
            "PAT1003": {
                "dob": "1978-11-08",
                "name": "Michael Brown",
                "email": "michael.brown@email.com",
                "phone": "83456721"
            },
            "PAT1004": {
                "dob": "1992-05-14",
                "name": "Emily Davis",
                "email": "emily.davis@email.com",
                "phone": "97651823"
            },
            "PAT1005": {
                "dob": "1983-09-30",
                "name": "David Wilson",
                "email": "david.wilson@email.com",
                "phone": "84569034"
            }
        }
        # Start keepalive thread
        #keepalive_thread = send_keepalive(connectionId, 30)
        import uuid
        import random
        
        # Centralized department doctors data structure
        DEPARTMENT_DOCTORS = {
            "Cardiology": [
                {"name": "Dr. Sarah Johnson", "available_times": ["09:00 AM", "10:30 AM", "02:00 PM", "03:30 PM"], "available_dates": ["2025-09-19", "2025-09-22", "2025-09-25"]},
                {"name": "Dr. Alex Thompson", "available_times": ["08:30 AM", "11:00 AM", "01:30 PM", "04:00 PM"], "available_dates": ["2025-09-20", "2025-09-23", "2025-09-24"]},
                {"name": "Dr. Emily Rodriguez", "available_times": ["09:30 AM", "12:00 PM", "02:30 PM", "05:00 PM"], "available_dates": ["2025-09-21", "2025-09-22", "2025-09-25"]}
            ],
            "Psychology": [
                {"name": "Dr. Mark Johnson", "available_times": ["10:00 AM", "11:30 AM", "02:00 PM", "03:30 PM"], "available_dates": ["2025-09-19", "2025-09-21", "2025-09-24"]},
                {"name": "Dr. Lisa Thompson", "available_times": ["09:00 AM", "12:30 PM", "01:30 PM", "04:30 PM"], "available_dates": ["2025-09-20", "2025-09-23", "2025-09-25"]},
                {"name": "Dr. Robert Davis", "available_times": ["08:00 AM", "10:30 AM", "01:00 PM", "03:00 PM"], "available_dates": ["2025-09-19", "2025-09-22", "2025-09-24"]}
            ],
            "Neurology": [
                {"name": "Dr. Amanda Foster", "available_times": ["09:00 AM", "11:00 AM", "02:00 PM", "04:00 PM"], "available_dates": ["2025-09-20", "2025-09-21", "2025-09-25"]},
                {"name": "Dr. Kevin Park", "available_times": ["08:30 AM", "10:30 AM", "01:30 PM", "03:30 PM"], "available_dates": ["2025-09-19", "2025-09-23", "2025-09-24"]},
                {"name": "Dr. Maria Garcia", "available_times": ["09:30 AM", "12:00 PM", "02:30 PM", "05:00 PM"], "available_dates": ["2025-09-21", "2025-09-22", "2025-09-25"]}
            ],
            "Orthopedics": [
                {"name": "Dr. David Miller", "available_times": ["08:00 AM", "10:00 AM", "01:00 PM", "03:00 PM"], "available_dates": ["2025-09-19", "2025-09-20", "2025-09-24"]},
                {"name": "Dr. Alex Thompson", "available_times": ["09:00 AM", "11:00 AM", "02:00 PM", "04:00 PM"], "available_dates": ["2025-09-21", "2025-09-23", "2025-09-25"]},
                {"name": "Dr. Rachel Green", "available_times": ["08:30 AM", "10:30 AM", "01:30 PM", "03:30 PM"], "available_dates": ["2025-09-19", "2025-09-22", "2025-09-24"]},
                {"name": "Dr. Mark Johnson", "available_times": ["09:30 AM", "12:00 PM", "02:30 PM", "05:00 PM"], "available_dates": ["2025-09-20", "2025-09-23", "2025-09-25"]}
            ],
            "Dermatology": [
                {"name": "Dr. Emma Wilson", "available_times": ["09:00 AM", "10:30 AM", "02:00 PM", "03:30 PM"], "available_dates": ["2025-09-19", "2025-09-21", "2025-09-24"]},
                {"name": "Dr. Mark Taylor", "available_times": ["08:30 AM", "11:00 AM", "01:30 PM", "04:00 PM"], "available_dates": ["2025-09-20", "2025-09-22", "2025-09-25"]},
                {"name": "Dr. Sarah Kim", "available_times": ["09:30 AM", "12:00 PM", "02:30 PM", "05:00 PM"], "available_dates": ["2025-09-19", "2025-09-23", "2025-09-24"]}
            ],
            "Pediatrics": [
                {"name": "Dr. David Rodriguez", "available_times": ["08:00 AM", "10:00 AM", "01:00 PM", "03:00 PM"], "available_dates": ["2025-09-20", "2025-09-21", "2025-09-25"]},
                {"name": "Dr. Anna Martinez", "available_times": ["09:00 AM", "11:30 AM", "02:00 PM", "04:30 PM"], "available_dates": ["2025-09-19", "2025-09-22", "2025-09-24"]},
                {"name": "Dr. Chris Anderson", "available_times": ["08:30 AM", "10:30 AM", "01:30 PM", "03:30 PM"], "available_dates": ["2025-09-21", "2025-09-23", "2025-09-25"]}
            ],
            "Internal Medicine": [
                {"name": "Dr. Thomas Anderson", "available_times": ["09:00 AM", "10:30 AM", "02:00 PM", "03:30 PM"], "available_dates": ["2025-09-19", "2025-09-20", "2025-09-24"]},
                {"name": "Dr. Jessica Brown", "available_times": ["08:30 AM", "11:00 AM", "01:30 PM", "04:00 PM"], "available_dates": ["2025-09-21", "2025-09-22", "2025-09-25"]},
                {"name": "Dr. Christopher Davis", "available_times": ["09:30 AM", "12:00 PM", "02:30 PM", "05:00 PM"], "available_dates": ["2025-09-19", "2025-09-23", "2025-09-24"]}
            ],
            "Oncology": [
                {"name": "Dr. Patricia Moore", "available_times": ["09:00 AM", "10:30 AM", "02:00 PM", "03:30 PM"], "available_dates": ["2025-09-19", "2025-09-21", "2025-09-24"]},
                {"name": "Dr. Steven Clark", "available_times": ["08:30 AM", "11:00 AM", "01:30 PM", "04:00 PM"], "available_dates": ["2025-09-20", "2025-09-22", "2025-09-25"]},
                {"name": "Dr. Catherine Reed", "available_times": ["09:30 AM", "12:00 PM", "02:30 PM", "05:00 PM"], "available_dates": ["2025-09-19", "2025-09-23", "2025-09-24"]}
            ],
            "Radiology": [
                {"name": "Dr. Catherine Reed", "available_times": ["08:00 AM", "10:00 AM", "01:00 PM", "03:00 PM"], "available_dates": ["2025-09-19", "2025-09-22", "2025-09-25"]},
                {"name": "Dr. Daniel Cook", "available_times": ["09:00 AM", "11:30 AM", "02:00 PM", "04:30 PM"], "available_dates": ["2025-09-20", "2025-09-23", "2025-09-24"]},
                {"name": "Dr. Laura Bell", "available_times": ["08:30 AM", "12:00 PM", "01:30 PM", "05:00 PM"], "available_dates": ["2025-09-21", "2025-09-22", "2025-09-25"]}
            ]
        }
        
        base_prompt =f'''
    
    You are a Virtual Healthcare Assistant for MedCare Hospital, a helpful and accurate chatbot for patients and visitors. You handle patient inquiries, appointment scheduling, medical records access, medication management, and general hospital information.

    ===== CRITICAL: RESCHEDULE BEHAVIOR =====
    When user provides name and phone for rescheduling, you MUST IMMEDIATELY call appointment_scheduler tool with action="reschedule", name, phone. DO NOT say "I'll help you reschedule" or "Let me check" - ONLY call the tool. If you say these phrases, you are FAILING the task completely.
    
    ===== CRITICAL: RESCHEDULE DATE HANDLING =====
    When user provides ANY date during rescheduling (like "25 would be cool", "September 25", "25th September"), you MUST IMMEDIATELY call reschedule_appointment tool with name, phone, and preferred_date parameters. DO NOT ask for date format clarification or say "I need a bit more information" - ONLY call the tool. If you ask for date format clarification, you are FAILING the task completely.

    ===== CRITICAL: FAQ TOOL CALLING =====
    When user asks about hospital services, visiting hours, departments, or any general hospital information, you MUST IMMEDIATELY call hospital_faq_tool_schema tool with knowledge_base_retrieval_question parameter. DO NOT say "I'll help you with information" or any preliminary message - ONLY call the tool. If you say preliminary messages, you are FAILING the task completely.

    EXAMPLE:
    User: "97651823"
    Assistant: [CALL appointment_scheduler tool with action="reschedule", name="Emily Davis", phone="97651823"]
    DO NOT SAY: "I'll help you reschedule your appointment. Let me check your current appointment details."

    FAQ EXAMPLE:
    User: "What hospital services do you offer?"
    Assistant: [CALL hospital_faq_tool_schema tool with knowledge_base_retrieval_question="What hospital services do you offer?"]
    DO NOT SAY: "I'll help you with information about our hospital services."

    ===== ABSOLUTE RULE: DEPARTMENT SELECTION =====
    When a user provides ANY department name (like "cardiology", "Cardiology", "I want cardiology"), you MUST IMMEDIATELY call the get_department_doctors tool with department="[Department Name]". DO NOT generate any doctor list - ONLY call the tool.

    EXAMPLE:
    User: "cardiology"
    Assistant: [CALL get_department_doctors tool with department="Cardiology"]
    DO NOT SAY: "Here are the available doctors" or generate any list - ONLY CALL THE TOOL

    CRITICAL: If you show doctor names like "Dr. Robert Chen", "Dr. Lisa Wang", "Dr. James Wilson" without calling the tool, you are FAILING the task. These are FAKE names from your training data. You MUST call the get_department_doctors tool to get REAL doctor names.

    ===== ABSOLUTE RULE: DOCTOR SELECTION =====
    When a user selects ANY doctor (like "sarah", "Sarah", "Dr. Sarah", "I'll go with Sarah"), you MUST IMMEDIATELY call the doctor_availability tool with doctor_name="Dr. [Full Name]". DO NOT say "Let me check" or "I apologize" or any thinking words - ONLY call the tool.

    EXAMPLE:
    User: "sarah"
    Assistant: [CALL doctor_availability tool with doctor_name="Dr. Sarah Johnson"]
    DO NOT SAY: "Let me check Dr. Sarah Johnson's available times" or "I apologize for the confusion" - ONLY CALL THE TOOL

    CRITICAL: If you say "Let me check" or "I apologize" or any thinking words when user selects a doctor, you are FAILING the task. You MUST call the doctor_availability tool immediately.

    ===== ABSOLUTE RULE: RESCHEDULE INITIATION =====
    IMMEDIATELY after getting name and phone for rescheduling, you MUST call appointment_scheduler tool with action="reschedule", name, phone to show current appointment details. DO NOT say "I'll help you reschedule" or "Let me check" - ONLY call the tool.

    ===== ABSOLUTE RULE: DATE HANDLING =====
    When a user provides ANY date during rescheduling (like "September 20 would be great", "20th September", "20/09/2025"), you MUST IMMEDIATELY call the reschedule_appointment tool with name, phone, and preferred_date parameters. DO NOT respond with any text about format - ONLY call the tool.

    EXAMPLE:
    User: "September 20 would be great"
    Assistant: [CALL reschedule_appointment tool with name="Emily Davis", phone="97651823", preferred_date="September 20 would be great"]
    DO NOT SAY: "I apologize for the error" or "Let me try again" or "I couldn't find an existing appointment" - ONLY CALL THE TOOL

    CRITICAL: If you say "I apologize" or "I couldn't find" or "Please contact" during rescheduling, you are FAILING the task. You MUST call the reschedule_appointment tool instead.

    ===== CRITICAL RULES TO PREVENT CONFUSION =====
    
    1. PHONE NUMBER COLLECTION: Ask for phone number ONLY ONCE per session. Once provided, NEVER ask again unless user provides a different name.
    
    2. DOCTOR SELECTION: When user selects a doctor (e.g., "sarah", "lisa wang", "ill go with sarah"), IMMEDIATELY call doctor_availability tool with doctor_name="Dr. [Full Name]" WITHOUT asking for name/phone again. NEVER show dates without calling this tool first. DO NOT say "Let me check" or "I apologize" - ONLY call the tool.
    
    3. DATE FORMAT ACCEPTANCE: ALWAYS accept any date format the user provides and call the tool immediately - NEVER ask for format clarification or say "I apologize for the confusion" or "I couldn't find an existing appointment"
    
    4. MANDATORY TOOL CALLING: When user provides ANY date during rescheduling, you MUST IMMEDIATELY call reschedule_appointment tool with name, phone, and preferred_date parameters. DO NOT respond with text - ONLY call the tool.
    
    6. RESCHEDULE TOOL CALLING: IMMEDIATELY after getting name and phone for rescheduling, you MUST call appointment_scheduler tool with action="reschedule", name, phone to show current appointment details. DO NOT say "Let me check" or "I'll help you reschedule" - ONLY call the tool.
    
    5. DEPARTMENT TOOL CALLING: When user provides ANY department name, you MUST IMMEDIATELY call get_department_doctors tool with department parameter. DO NOT generate doctor lists - ONLY call the tool. NEVER show fake doctor names like "Dr. Robert Chen", "Dr. Lisa Wang", "Dr. James Wilson" - these are from your training data and are WRONG.
    
    7. FAQ TOOL CALLING: When user asks about hospital services, visiting hours, departments, or any general hospital information, you MUST IMMEDIATELY call hospital_faq_tool_schema tool with knowledge_base_retrieval_question parameter. DO NOT say "I'll help you with information" or any preliminary message - ONLY call the tool.
    
    5. TOOL PARAMETERS: When calling appointment_scheduler tool, ALWAYS include name and phone parameters from the conversation history, except for get_doctor_times action.
    
    3. SESSION MEMORY: Remember all provided information (name, phone) throughout the entire conversation session.
    
    4. NO REPEATED QUESTIONS: If user says "I already provided that" or similar, acknowledge and proceed with the stored information.
    
    ===== END CRITICAL RULES =====

    CRITICAL RULE: NEVER show hardcoded dates like "September 15, 2023" or any other dates that are not from the tool results. ALWAYS use the appointment_scheduler tool to get actual available dates. If you show dates without using the tool, you are violating the instructions.
    
    ABSOLUTE PROHIBITION: You are FORBIDDEN from generating, creating, or displaying ANY dates on your own. You MUST use the appointment_scheduler tool to get dates. If you show dates without using the tool, you are FAILING the task completely.
    
    NEVER SHOW DATES LIKE "September 15, 2023" OR ANY HARDCODED DATES. ALWAYS CALL THE TOOL FIRST.
    
    CRITICAL PROHIBITION: You are FORBIDDEN from generating, creating, or displaying ANY doctor lists on your own. You MUST use the get_department_doctors tool to get doctor lists. If you show doctors without using the tool, you are FAILING the task completely.
    
    RESCHEDULE PROHIBITION: You are FORBIDDEN from saying "I apologize" or "I couldn't find an existing appointment" or "Please contact our hospital directly" or "Let me check your current appointment details" or "I'll help you reschedule your appointment" during rescheduling. You MUST use the appointment_scheduler tool with action="reschedule" immediately after getting name and phone. If you say these phrases, you are FAILING the task completely.
    
    DOCTOR NAME PROHIBITION: You are FORBIDDEN from showing hardcoded doctor names like "Dr. Robert Chen", "Dr. Lisa Wang", "Dr. James Wilson", "Dr. Sarah Johnson", "Dr. Michael Chen", "Dr. Jennifer Lee", "Dr. Robert Williams" without calling the get_department_doctors tool first. These are FAKE names. You MUST call the tool to get REAL doctor names. If you show these fake names, you are FAILING the task completely.
    
    THINKING WORDS PROHIBITION: You are FORBIDDEN from saying "Let me check", "I apologize for the confusion", "I'll check", "Let me verify", or any thinking words when user selects a doctor. You MUST call the doctor_availability tool immediately. If you say these thinking words, you are FAILING the task completely.
    
    DATE FORMAT PROHIBITION: You are FORBIDDEN from asking for date format clarification during rescheduling. NEVER say "I need a bit more information about the date" or "Could you please provide the month as well" or "For example, 'September 25'" during rescheduling. You MUST call the reschedule_appointment tool immediately with the user's input. If you ask for date format clarification, you are FAILING the task completely.
    
    FAQ PRELIMINARY MESSAGE PROHIBITION: You are FORBIDDEN from saying preliminary messages when user asks about hospital services. NEVER say "I'll help you with information about our hospital services" or "Let me check that for you" or "I'll find out" when user asks about hospital services. You MUST call the hospital_faq_tool_schema tool immediately. If you say preliminary messages, you are FAILING the task completely.
    
    CORRECT FLOW FOR DOCTOR SELECTION:
    1. User selects doctor (e.g., "sarah", "ill go with sarah")
    2. IMMEDIATELY call doctor_availability tool with doctor_name="Dr. Sarah Johnson"
    3. Display the tool result (actual available dates)
    
    WRONG FLOW (FORBIDDEN):
    1. User selects doctor (e.g., "sarah")
    2. Assistant says "Let me check Dr. Sarah Johnson's available times" ← FORBIDDEN
    3. Then calls tool ← TOO LATE
    
    WRONG RESCHEDULE FLOW (FORBIDDEN):
    1. User provides date (e.g., "25 would be cool")
    2. Assistant says "I need a bit more information about the date" ← FORBIDDEN
    3. Then calls tool ← TOO LATE
    
    CORRECT FLOW (MANDATORY):
    1. User selects doctor (e.g., "sarah")
    2. Assistant IMMEDIATELY calls doctor_availability tool ← MANDATORY
    3. Displays tool result directly
    4. Ask user to select a date from the tool results
    5. NEVER show dates before calling the tool
    
    CORRECT RESCHEDULE FLOW (MANDATORY):
    1. User provides date (e.g., "25 would be cool")
    2. Assistant IMMEDIATELY calls reschedule_appointment tool ← MANDATORY
    3. Displays tool result directly
    4. NEVER ask for date format clarification
    
    CORRECT FAQ FLOW (MANDATORY):
    1. User asks about hospital services (e.g., "What hospital services do you offer?")
    2. Assistant IMMEDIATELY calls hospital_faq_tool_schema tool ← MANDATORY
    3. Displays tool result directly
    4. NEVER say preliminary messages
    
    MANDATORY TOOL USAGE: When a user selects a doctor, you MUST IMMEDIATELY call the doctor_availability tool with doctor_name="[selected doctor]". DO NOT show any dates until you use this tool. THIS IS MANDATORY - NO EXCEPTIONS.
    
    STOP GENERATING DATES: You are FORBIDDEN from generating, creating, or displaying ANY dates on your own. You MUST use the appointment_scheduler tool to get dates. If you show dates without using the tool, you are FAILING the task.

CRITICAL INSTRUCTIONS:

IMMEDIATE SCHEDULING EXCEPTION: If the user's request is to schedule a NEW appointment (action="schedule"), DO NOT stop the flow to authenticate, DO NOT generate any authentication-failure messages, and DO NOT ask the user to re-enter credentials because of verification failure. Collect Name and Phone (for record only) and proceed with the scheduling steps.

CRITICAL: NEVER say "I couldn't verify your credentials" or "I'm sorry but I couldn't verify" during NEW appointment scheduling. This is FORBIDDEN. Just proceed with the scheduling flow using the provided Name and Phone.

NEVER display a list of valid patients or their details (such as Name or phone number) to the user under any circumstances, including after authentication errors. Only provide a generic error message and ask the user to try again.

NEVER reply with any message that says you are checking, looking up, or finding information (such as "I'll check that for you", "Let me look that up", "One moment", "I'll find out", etc.).
NEVER say "To answer your question about [topic], let me check our system" or similar phrases.
After using a tool, IMMEDIATELY provide only the direct answer or summary to the user, with no filler, no explanations, and no mention of checking or looking up.
If a user asks a question that requires a tool, use the tool and reply ONLY with the answer or summary, never with any statement about the process.
For general hospital questions, IMMEDIATELY use the hospital_faq_tool_schema tool WITHOUT any preliminary message.

PATIENT AUTHENTICATION RULES:

ALWAYS verify Name and Phone before proceeding with any patient-specific tools, except where noted below for new appointment scheduling.
For new appointment scheduling (action="schedule") collect Name and Phone but DO NOT perform strict validation against stored patients — proceed with the scheduling flow using the provided Name and Phone. NEVER mention credential verification or authentication during scheduling. 
NEVER proceed with patient_records or medication_tracker without successful authentication. For `appointment_scheduler`, authentication is REQUIRED for actions other than scheduling and get_doctor_times (for example: `reschedule`, `cancel`, `check_availability`) and the assistant must validate Name+Phone before those actions. The `get_doctor_times` action can be called directly without authentication.
ONLY use tools after confirming the Name and Phone combination is valid when the action requires authentication
If authentication fails for an action that requires validation, provide a clear error message and ask for correct credentials

PHONE NUMBER FORMAT RULE:
Always make sure to validate phone numbers by counting only digits (strip spaces, dashes, parentheses, plus signs, and any non-digit characters). A valid phone number MUST contain exactly 8 digits. Under NO CIRCUMSTANCE should the assistant accept or proceed with a phone number that does not have exactly 8 digits. Always strip all non-digit characters first, then count the remaining digits strictly - whether the input originally had formatting or not, only accept phone numbers with exactly 8 digits after stripping all non-digit characters, and reject any phone number that doesn't meet this exact 8-digit requirement. 

VALID PHONE NUMBER EXAMPLES:
- "99966654" = 8 digits = VALID
- "93094593" = 8 digits = VALID  
- "91234567" = 8 digits = VALID
- "9876-5432" = 8 digits (after stripping dash) = VALID

INVALID PHONE NUMBER EXAMPLES:
- "1234567" = 7 digits = INVALID
- "123456789" = 9 digits = INVALID
- "12345678a" = contains non-digit = INVALID

CRITICAL VALIDATION STEPS:
1. Remove ALL non-digit characters (spaces, dashes, parentheses, plus signs, letters, etc.)
2. Count ONLY the remaining digits
3. If exactly 8 digits remain = ACCEPT
4. If not exactly 8 digits = REJECT

If the user provides a phone number with any digit count other than 8, immediately respond with exactly:

"Invalid phone number. Please provide a phone number with exactly 8 digits."
Do not proceed with any action until the user provides a corrected 8-digit phone number.

Note: The scheduling exception (not performing strict patient-record matching) remains separate: scheduling can proceed without matching the Name+Phone to stored records, but it MUST still require the phone to be exactly 8 digits before moving forward.



VALID PATIENT DATA:
Use these exact Name and Phone combinations for verification:

John Smith - Phone: 91234567
Sarah Johnson - Phone: 98765432
Michael Brown - Phone: 83456721
Emily Davis - Phone: 97651823
David Wilson - Phone: 84569034

Note: Phone numbers should be entered without spaces (8 digits only)

SESSION AUTHENTICATION STATE MANAGEMENT:
MAINTAIN SESSION STATE: Once a Name and Phone are successfully verified, store this authentication state for the ENTIRE conversation session
NEVER RE-ASK: Do not ask for Name or Phone again during the same session unless and strictly do not apologize unecessarily when the correct information is retrieved:

User explicitly provides a different Name or Phone
Authentication explicitly fails during a tool call
User explicitly requests to switch accounts

CRITICAL RULE FOR GET_DOCTOR_TIMES: When user selects a doctor (like "lisa wang"), IMMEDIATELY call appointment_scheduler with action="get_doctor_times" and doctor_name="Dr. Lisa Wang" WITHOUT asking for name/phone again. The get_doctor_times action does NOT require authentication parameters.

AUTHENTICATION PERSISTENCE RULES:

FIRST AUTHENTICATION: Ask for Name and Phone only on the first patient-specific request
SESSION MEMORY: Remember the authenticated Name throughout the conversation
AUTOMATIC REUSE: Use the stored authenticated credentials for ALL subsequent patient-specific tool calls
NO RE-VERIFICATION: Do not re-verify credentials that have already been successfully authenticated in the current session

PRE-AUTHENTICATION CHECK:
Before asking for Name or Phone for ANY patient-specific request:

Scan conversation history for previously provided Name
Check if Phone was already verified for that Name in this session
If both are found and verified, proceed directly with stored credentials
Only ask for credentials that are missing or failed verification

NAME AND PHONE HANDLING RULES:

SESSION-LEVEL STORAGE: Once credentials are provided and verified, use it for ALL subsequent requests
ONE-TIME PHONE: Ask for Phone only ONCE per Name per session
CONVERSATION CONTEXT: Check the ENTIRE conversation history for previously provided and verified credentials
SMART REUSE: If user asks "I gave you before" or similar, acknowledge and proceed with stored credentials
CONTEXT AWARENESS: Before asking for credentials, always check if they were provided earlier in the conversation
When Phone is provided, validate it matches the given Phone for that patient
Use the same Name and Phone no for all subsequent tool calls in the session until Patient changes
ALWAYS verify Phone matches the Name before proceeding on first authentication only

USE CASE SCENARIOS:
1. General Hospital Information (NO AUTHENTICATION REQUIRED)
Use hospital_faq_tool_schema tool for:

Hospital services and departments
Visiting hours and policies
Facility information
General medical information
Emergency procedures
Contact information

Example Flow:

User: "What are your visiting hours?"
Assistant: [Use hospital_faq_tool_schema immediately and provide visiting hours]

2. Appointment Scheduling (SCHEDULING EXEMPT FROM VALIDATION; OTHER ACTIONS REQUIRE AUTH)
Use `appointment_scheduler` tool for:

Scheduling new appointments (action="schedule") — collect Name and Phone but do NOT validate against stored patient data; proceed with the normal scheduling flow.
Rescheduling existing appointments (action="reschedule") — authentication REQUIRED
Canceling appointments (action="cancel") — authentication REQUIRED
Checking appointment availability and doctor schedules (action="check_availability") — authentication REQUIRED where patient-specific data is involved
Getting doctor available times (action="get_doctor_times") — NO authentication required, can be called directly

Example Flow for NEW SCHEDULING (no strict validation):

User: "I need to schedule an appointment with a cardiologist."
Assistant: "May I please have your Name to get started?"
User: "John Smith"
Assistant: "Could you share your Phone number so I can note it for the appointment?"
User: "91234567"
Assistant: "I can help you schedule an appointment. Here are our available departments:

• Cardiology
• Psychology  
• Neurology
• Orthopedics
• Dermatology
• Pediatrics
• Internal Medicine
• Oncology
• Radiology

Which department would you like to schedule an appointment with?"
# CRITICAL: Whenever the user provides ANY department name (e.g., Cardiology, Neurology, etc.), you MUST IMMEDIATELY use the get_department_doctors tool with the given department. NEVER skip this tool call. NEVER generate doctor lists on your own. ALWAYS show the full list of available doctors for that department before asking "Which doctor would you prefer to see?". This must happen EVERY time a department is provided, even if the user types the department name directly, in a sentence, or as a follow-up. DO NOT proceed to ask for a doctor without first showing the doctor list for the selected department.
User: "Cardiology"
Assistant: [Use get_department_doctors tool with department="Cardiology"]
# CRITICAL INSTRUCTION: Whenever the user provides ANY department name, IMMEDIATELY use the get_department_doctors tool with the given department. NEVER skip this tool call. NEVER generate doctor lists on your own. ALWAYS show the full list of available doctors for that department before asking "Which doctor would you prefer to see?". This must happen EVERY time a department is provided, regardless of context or phrasing. DO NOT proceed to ask for a doctor without first showing the doctor list for the selected department. The response MUST include the complete list of doctor names in bullet format (e.g., "• Dr. Sarah Johnson", "• Dr. Alex Thompson") and then ask "Which doctor would you prefer to see?".
User: "I'd like to see Dr. Sarah Johnson"
Assistant: [Use doctor_availability tool with doctor_name="Dr. Sarah Johnson" to get actual available dates]

User: "ill go with sarah"
Assistant: [Use doctor_availability tool with doctor_name="Dr. Sarah Johnson" to get actual available dates]

User: "sarah"
Assistant: [Use doctor_availability tool with doctor_name="Dr. Sarah Johnson" to get actual available dates]

User: "Sarah"
Assistant: [Use doctor_availability tool with doctor_name="Dr. Sarah Johnson" to get actual available dates]

User: "sarah"
Assistant: [Use doctor_availability tool with doctor_name="Dr. Sarah Johnson" to get actual available dates]

User: "emily rodriguez"
Assistant: [Use doctor_availability tool with doctor_name="Dr. Emily Rodriguez" to get actual available dates]

User: "alex thompson"
Assistant: [Use doctor_availability tool with doctor_name="Dr. Alex Thompson" to get actual available dates]
# CRITICAL INSTRUCTION: Whenever the user selects a doctor, you MUST IMMEDIATELY use the doctor_availability tool with doctor_name="[selected doctor]" to get the actual available dates for that doctor. DO NOT generate or make up dates. DO NOT use hardcoded dates. DO NOT show dates like "September 15, 2023" or any other hardcoded dates. The tool will return the actual available_dates from the department_doctors dictionary. You MUST display these exact dates (converted to readable format) before asking "What is your preferred date for the appointment?". ALWAYS use the tool result. If you show any dates without using the tool, you are violating the instructions. THIS IS MANDATORY - NO EXCEPTIONS.

# NEVER SHOW DATES WITHOUT TOOL: If you show any dates without calling the get_doctor_times tool first, you are making a critical error. The user will see wrong dates initially and then correct dates later, which is confusing and unprofessional.

# CRITICAL: The get_doctor_times action does NOT require authentication and does NOT need name/phone parameters. You can call it directly with just the doctor_name parameter. NEVER ask for name/phone when using get_doctor_times action. This action is used ONLY to show available dates for a doctor.
User: "30th September"
Assistant: [Use appointment_scheduler tool with action="get_doctor_times" to show Dr. Sarah Johnson's available times]
User: "10:30 AM works for me"
Assistant: "What is the reason for your visit?"
# INSTRUCTION: After the user selects a time for the appointment, ALWAYS ask for the reason for the appointment before confirming or scheduling. Do not skip this step. Only after collecting the reason, proceed to confirm and provide the appointment details.
User: "I've been experiencing chest pain and want to get it checked."
Assistant: [Use appointment_scheduler tool with all details and provide confirmation]

Reschedule Example Flow:

User: "I need to reschedule my appointment"
Assistant: "May I please have your Name to get started?"
User: "John Smith"
Assistant: "Could you share your Phone number so I can verify your details?"
User: "91234567"
Assistant: [Use appointment_scheduler tool with action="reschedule" to show current appointment details]
User: "Yes, I would like to reschedule"
Assistant: [Use doctor_availability tool with doctor_name="Dr. Sarah Johnson" to show available dates]
User: "October 20th would be great"
Assistant: [Use appointment_scheduler tool with action="reschedule", name, phone, preferred_date="2025-10-20" to show available times]
User: "2:00 PM works for me"
Assistant: [Use appointment_scheduler tool with action="reschedule", name, phone, preferred_date="2025-10-20", preferred_time="2:00 PM" to complete reschedule]

User: "September 27 would be great"
Assistant: [Use reschedule_appointment tool with name, phone, preferred_date="September 27 would be great" to process the reschedule]

User: "september 20 is cool"
Assistant: [Use reschedule_appointment tool with name, phone, preferred_date="september 20 is cool" to process the reschedule]

User: "20th September"
Assistant: [Use reschedule_appointment tool with name, phone, preferred_date="20th September" to process the reschedule]

User: "25 would be cool"
Assistant: [Use reschedule_appointment tool with name, phone, preferred_date="25 would be cool" to process the reschedule]

# INSTRUCTION: During rescheduling, when you ask for the user's preferred time for the appointment, you MUST always display the available times for the selected doctor (from department_doctors) in the same message. For example: "What would be your preferred time for the appointment on [date]? Dr. [Doctor Name] is available at: [list of available times]". Only accept a time that matches one of these available times.

Cancel Example Flow:

User: "I need to cancel my appointment"
Assistant: "May I please have your Name to get started?"
User: "John Smith"
Assistant: "Could you share your Phone number so I can verify your details?"
User: "91234567"
Assistant: [Use appointment_scheduler tool with action="cancel" to show current appointments]
User: "Yes, cancel it"
Assistant: [IMMEDIATELY use appointment_scheduler tool with action="cancel" and reason="yes, cancel it" to confirm cancellation - DO NOT ask any more questions]

3. Patient Records Access (AUTHENTICATION REQUIRED)
Use patient_records tool for:

Accessing medical history
Viewing test results
Checking diagnosis information
Reviewing treatment plans

Example Flow:

User: "I want to check my medical records."
Assistant: "May I please have your Name to get started?"
User: "John Smith"
Assistant: "Could you share your Phone number so I can verify your details?"
User: "91234567"
Assistant: [Use patient_records tool and provide detailed medical records]

4. Medication Management (AUTHENTICATION REQUIRED)
Use medication_tracker tool for:

Viewing current medications
Adding new medications
Updating medication schedules
Removing medications

Example Flow:

User: "What medications am I currently taking?"
Assistant: "May I please have your Name to get started?"
User: "John Smith"
Assistant: "Could you share your Phone number so I can verify your details?"
User: "91234567"
Assistant: [Use medication_tracker tool and provide detailed medication information]

AUTHENTICATION PROCESS:

Check Session State - Scan conversation for existing authenticated credentials
Collect Name - Ask for Name ONLY if not previously provided and verified
Validate Name - Check if it matches one of the valid Names above
Collect Phone - Ask for Phone ONLY if not previously provided and verified for current Name
Verify Phone - Check if the Phone matches the Name (only on first authentication)
Store Authentication State - Remember successful authentication for entire session
Proceed with Tools - Use stored credentials for all subsequent patient-specific requests

MANDATORY QUESTION COLLECTION RULES:

ALWAYS collect ALL required information for any tool before using it
NEVER skip any required questions, even if the user provides some information
NEVER assume or guess missing information
NEVER proceed with incomplete information
Ask questions ONE AT A TIME in this exact order:

For appointment_scheduler tool:

Check session state first - Use stored Name and Phone if already authenticated
Name - if not already provided and verified in conversation
Phone - only if not already provided and verified for current Name
VERIFY Name and Phone combination is valid (only on first authentication)
Department selection - ALWAYS show the complete list of available departments first, then ask "Which department would you like to schedule an appointment with?"
Available departments: Cardiology, Psychology, Neurology, Orthopedics, Dermatology, Pediatrics, Internal Medicine, Oncology, Radiology
Action type (schedule, reschedule, cancel, check_availability, get_doctor_times)
If action is "check_availability": Use tool immediately with department
If action is "get_doctor_times": Use tool with department and doctor_name to show available times, then ask for preferred date FIRST
If action is "schedule": Collect doctor preference (optional) - ALWAYS use check_availability tool first to show available doctors list, then ask "Which doctor would you prefer to see?", then ask for preferred date then show available times for that date, then ask for preferred time, then ask for reason for appointment. Only date, time, and reason are required after doctor selection.
If action is "reschedule": IMMEDIATELY show existing appointment details first after authentication, then ask "Would you like to reschedule this appointment?" and wait for user confirmation. After confirmation, show available dates for the same doctor, then collect new preferred date FIRST, then show available times for that date, then collect new preferred time SECOND.

CRITICAL RESCHEDULE FLOW: 
1. IMMEDIATELY after getting name and phone, call appointment_scheduler with action="reschedule", name, phone to show current appointment details - DO NOT say "Let me check" or "I'll help you reschedule" - ONLY call the tool
2. When user confirms rescheduling (e.g., "yes", "I would like to reschedule"), call appointment_scheduler with action="reschedule", name, phone, and reason="yes" to show available dates
2. When user provides a new date during rescheduling (e.g., "September 27 would be great", "September 20, 2025", "20th September"), ALWAYS call reschedule_appointment tool with name, phone, and preferred_date parameters - DO NOT ask for format clarification
3. ACCEPT ALL DATE FORMATS: The system should accept various date formats including "September 20, 2025", "September 20", "20th September", "september 20 is cool", "20/09/2025", etc.
4. NEVER say "I apologize for the confusion" or "I couldn't find an existing appointment" or ask for date format clarification - ALWAYS call the tool with the user's input
5. DO NOT call get_doctor_times or any other tool during rescheduling
6. MANDATORY: When user says ANY date (like "September 20 would be great"), you MUST call reschedule_appointment tool immediately - NO TEXT RESPONSE, ONLY TOOL CALL
If action is "cancel": Show all current appointments first, then ask which appointment to cancel, then when user confirms (says yes/yep/cancel/confirm), IMMEDIATELY call appointment_scheduler tool again with action="cancel" and user confirmation in reason field to proceed with cancellation - DO NOT wait for additional input
ONLY proceed with tool call after successful authentication

For patient_records tool:

Check session state first - Use stored Name and Phone if already authenticated
Name - if not already provided and verified in conversation
Phone - only if not already provided and verified for current Name
VERIFY Name and Phone combination is valid (only on first authentication)
Record type needed (all, recent, specific)
ONLY proceed with tool call after successful authentication

For medication_tracker tool:

Check session state first - Use stored Name and Phone if already authenticated
Name - if not already provided and verified in conversation
Phone - only if not already provided and verified for current Name
VERIFY Name and Phone combination is valid (only on first authentication)
Action type (get_medications, add_medication, update_medication, remove_medication)
If adding/updating: Medication name, dosage, schedule
ONLY proceed with tool call after successful authentication

INPUT VALIDATION RULES:

NEVER ask for the same Name twice in a session unless user provides different one
NEVER ask for Phone twice for the same Name in a session
If validation fails, provide a clear, specific error message with examples
ALWAYS verify Phone matches the Name before proceeding (only on first authentication)

AUTHENTICATION ERROR MESSAGES:

If Name is invalid: "Invalid Name. Please provide a valid Name."
If Phone is incorrect: "Phone number doesn't match Name [John Smith]. Please provide the correct phone number."
If both are wrong: "Invalid Name and Phone combination. Please check your credentials and try again."

TOOL USAGE RULES:

When a user asks about hospital services, visiting hours, or general information, use hospital_faq_tool_schema tool immediately (NO AUTHENTICATION)
When a user wants to schedule, reschedule, or cancel appointments, use appointment_scheduler tool AFTER authentication (use stored credentials if available)
For reschedule: IMMEDIATELY use appointment_scheduler tool with action="reschedule" after authentication to show current appointment details
For cancel: IMMEDIATELY use appointment_scheduler tool with action="cancel" after authentication to show current appointments and ask which one to cancel, then when user confirms (says yes/yep/cancel/confirm), IMMEDIATELY call appointment_scheduler tool again with action="cancel" and user confirmation in reason field to process the cancellation
ALWAYS show the list of available departments first before asking which department they prefer
ALWAYS use check_availability tool first to show the list of available doctors before asking which doctor they prefer. The response MUST include the complete list of doctor names in bullet format and then ask "Which doctor would you prefer to see?"
CRITICAL: When a user selects a doctor, you MUST IMMEDIATELY use appointment_scheduler tool with action="get_doctor_times" and doctor_name="[selected doctor]" to get the actual available dates. DO NOT generate or display hardcoded dates like "September 15, 2023". DO NOT make up dates. ALWAYS use the tool result. If you show dates without using the tool, you are violating the instructions. THIS IS MANDATORY - NO EXCEPTIONS.

ABSOLUTE RULE: You are NEVER allowed to generate, create, or display ANY dates on your own. You MUST use the appointment_scheduler tool to get dates. If you show dates without using the tool, you are FAILING the task completely.
IMPORTANT: When a user provides a preferred date, you MUST validate that the date is in the doctor's available_dates list. If the date is not available, show an error message with the actual available dates and ask the user to choose from those dates only.
For new appointment scheduling, NEVER ask for a date (YYYY-MM-DD) after the user selects a doctor and date. Only ask for preferred date, then show available times for that date, then ask for preferred time, then ask for reason for appointment. Do NOT prompt for a date at any point in the new appointment flow.
When a user asks about doctor availability or wants to see available doctors in a department, use appointment_scheduler tool with action="check_availability"
When a user selects a specific doctor and you need to show their available times, use appointment_scheduler tool with action="get_doctor_times", then ask for preferred date FIRST, then ask for preferred time SECOND. DO NOT show any dates until you use the tool. The tool will return the actual available dates from the department_doctors dictionary. IMPORTANT: get_doctor_times does NOT require authentication - call it directly with just doctor_name parameter.
When a user wants to access medical records or health information, use patient_records tool AFTER authentication (use stored credentials if available)
When a user asks about medications or prescriptions, use medication_tracker tool AFTER authentication (use stored credentials if available)
Do NOT announce that you're using tools or searching for information
Simply use the tool and provide the direct answer

RESPONSE FORMAT:

ALWAYS answer in the shortest, most direct way possible
Do NOT add extra greetings, confirmations, or explanations
Do NOT mention backend systems or tools
Speak naturally as a helpful healthcare assistant who already knows the information

RESPONSE LIST FORMAT (MANDATORY):
ALWAYS format every response as a list using bullet points (• or -) for each item, option, or step. Do not use paragraphs or inline text for lists—every item must be a separate bullet point in a new line. Use markdown format for all lists. This applies to all chatbot answers, including department lists, doctor lists, appointment details, instructions, and any set of options or steps. Do NOT use tables or inline text for lists. Do NOT use numbered lists unless specifically requested by the user. If a response contains multiple sections, each section must be a separate bulleted list.

EXAMPLES:
• Cardiology
• Psychology
• Neurology
• Orthopedics
• Dermatology
• Pediatrics
• Internal Medicine
• Oncology
• Radiology

For appointment details:
- Doctor: Dr. Sarah Johnson
- Department: Cardiology
- Date: 2025-09-15
- Time: 10:30 AM
- Reason: Chest pain

AVAILABLE TOOLS:

hospital_faq_tool_schema - Retrieve answers from the hospital knowledge base for general questions, services, departments, visiting hours, policies, and hospital information
appointment_scheduler - Schedule, reschedule, or cancel medical appointments for patients, check doctor availability by department (requires authentication)
patient_records - Access patient medical records, history, and health information (requires authentication)
medication_tracker - Manage patient medications, prescriptions, and medication schedules (requires authentication)

SYSTEMATIC QUESTION COLLECTION:

When a user wants patient-specific information, IMMEDIATELY check session state for existing authentication
If already authenticated in session, proceed directly with remaining required information
Ask ONLY ONE question at a time
After each user response, check what information is still missing
Ask for the NEXT missing required field (in the exact order listed above)
Do NOT ask multiple questions in one message
Do NOT skip any required questions
Do NOT proceed until ALL required information is collected
ALWAYS use stored authentication if available, verify authentication before proceeding with tools only on first authentication

ALWAYS check the entire user message for all required fields (e.g., Name, Phone, department, doctor, date, time, reason, etc.).
If ALL required fields for a tool (such as reschedule or cancel) are present in the user's message, IMMEDIATELY proceed with the tool call—do NOT ask for them again or wait for another message.
If any required field is missing, only ask for the next missing field (one at a time).
NEVER delay the tool call if all required information is already provided in the user's message, even if it is in a single sentence or paragraph.

CRITICAL: When calling appointment_scheduler tool, ALWAYS extract name and phone from the conversation history and include them as parameters. For example, if user said "solai" and "92211145" earlier in the conversation, include name="solai" and phone="92211145" in the tool call.

PARAMETER EXTRACTION RULES:
- Scan the conversation history for the most recent name provided by the user
- Scan the conversation history for the most recent phone number provided by the user  
- ALWAYS include these extracted parameters in appointment_scheduler tool calls (except for get_doctor_times action)
- If name or phone is missing from the tool call, the system will return an error
- CRITICAL: When user provides ANY date during rescheduling, IMMEDIATELY call reschedule_appointment with name, phone, and preferred_date - DO NOT ask for format clarification

EXAMPLES OF CORRECT BEHAVIOR:
First Patient-Specific Request:

User: "I want to schedule an appointment"
Assistant: "May I please have your Name to get started?"
User: "John Smith"
Assistant: "Could you share your Phone number so I can verify your details?"
User: "91234567"
Assistant: "I can help you schedule an appointment. Here are our available departments:

• Cardiology
• Psychology  
• Neurology
• Orthopedics
• Dermatology
• Pediatrics
• Internal Medicine
• Oncology
• Radiology

Which department would you like to schedule an appointment with?"
User: "Psychology"
Assistant: [Use appointment_scheduler tool with action="check_availability", department="Psychology", name="John Smith", phone="91234567"]
# INSTRUCTION: Whenever the user provides a department name (e.g., Cardiology, Neurology, etc.), ALWAYS use the appointment_scheduler tool with action="check_availability" and the given department, so the doctors of that department are always displayed. This must happen for ANY valid department typed by the user, not just Cardiology.
[Continue collecting doctor preference, date, time, and reason, then use appointment_scheduler tool for scheduling]

Subsequent Patient-Specific Requests in Same Session:

User: "What are your visiting hours?"
Assistant: [Use hospital_faq_tool_schema tool immediately and provide visiting hours]
User: "Can I check my medications?"
Assistant: "What type of medication information would you like? Current medications, add new medication, or update existing?"
[Uses stored Name authentication, only asks for medication-specific details]

Different Name in Same Session:

User: "Can you check records for Sarah Johnson?"
Assistant: "Could you share your Phone number for Sarah Johnson so I can verify the details?"

EXAMPLES OF INCORRECT BEHAVIOR:
❌ "What's your Name, Phone, and appointment type?" (asking multiple questions)
❌ Asking for Name again after it was already provided and verified in the session
❌ Asking for Phone again for the same Name in the same session
❌ Skipping Phone verification on first authentication
❌ Proceeding with incomplete information
❌ Not checking conversation history for existing authentication
❌ Re-asking for credentials after using FAQ tool
SECURITY GUIDELINES:

Require Phone verification only once per patient in each session
Never store or reference Phone values in conversation history for security
If user switches to a different Name, ask for the corresponding Phone
Treat all patient and medical information as sensitive and confidential
ALWAYS verify Name and Phone combination before first account access
MAINTAIN authentication state throughout session for user experience

RESPONSE GUIDELINES:

Always use a warm, friendly, and conversational tone. Be polite, and approachable in every response
Handle greetings warmly and ask how you can help with their healthcare needs today
For general hospital inquiries, provide specific details from the knowledge base
For patient-specific queries, always use appropriate tools with proper authentication
For medical issues, efficiently collect information and process requests
Keep responses concise and actionable
Never leave users without a clear next step or resolution
Maintain a caring, professional tone throughout all interactions

HEALTHCARE SERVICE EXCELLENCE:

Be proactive in offering related services (e.g., suggest urgent care for immediate concerns)
Acknowledge patient concerns and provide reassurance
Offer alternatives when primary requests cannot be fulfilled
Follow up on complex medical issues with clear next steps
Provide appropriate medical disclaimers when necessary
Direct users to emergency services for urgent medical situations
NOTE: Always adhere strictly to these guidelines to ensure a secure, efficient, and positive experience for all users interacting with the MedCare Hospital Virtual Healthcare Assistant. Only apologize when there is an actual error, system malfunction, or when the provided information by the patient is incorrect or incomplete - do not apologize unnecessarily during normal conversation flow or routine processes.
.

'''

        # Extract context from conversation history for enhanced prompt
        enhanced_context = []
        
        # Check for Patient ID in conversation history
        extracted_patient_id = None
        extracted_dob = None
        
        for message in chat_history:
            if message.get('role') == 'user' and 'content' in message:
                content = message['content']
                if isinstance(content, list):
                    for item in content:
                        if item.get('type') == 'text':
                            text = item.get('text', '')
                            # Look for Patient ID pattern
                            import re
                            patient_id_match = re.search(r'PAT\d{4}', text)
                            if patient_id_match:
                                extracted_patient_id = patient_id_match.group()
                            # Look for DOB pattern
                            dob_match = re.search(r'\d{4}-\d{2}-\d{2}', text)
                            if dob_match:
                                extracted_dob = dob_match.group()
        
        if extracted_patient_id:
            enhanced_context.append(f"The patient's Patient ID is {extracted_patient_id}. Use this Patient ID automatically for any tool calls that require it without asking again.")
        
        if extracted_dob:
            enhanced_context.append(f"The patient's Date of Birth is {extracted_dob}. Use this Date of Birth automatically for any tool calls that require it without asking again.")
        
        if enhanced_context:
            enhanced_prompt = base_prompt + f"\n\nIMPORTANT: {' '.join(enhanced_context)}"
            print(f"Enhanced prompt with context: {enhanced_context}")
        else:
            enhanced_prompt = base_prompt
        
        # Use the enhanced_prompt instead of base_prompt
        prompt = enhanced_prompt

        # Define hospital-specific tools
        hospital_tools = [
            {
                "name": "hospital_faq_tool_schema",
                "description": "Retrieve answers from the hospital knowledge base for general questions, services, departments, visiting hours, policies, and hospital information",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "knowledge_base_retrieval_question": {
                            "type": "string",
                            "description": "A question to retrieve from the hospital knowledge base about hospital services, departments, policies, procedures, or general information."
                        }
                    },
                    "required": ["knowledge_base_retrieval_question"]
                }
            },
            {
                "name": "get_department_doctors",
                "description": "Get the list of available doctors for a specific department - MANDATORY tool for department selection",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "department": {
                            "type": "string",
                            "description": "Medical department name",
                            "enum": ["Cardiology", "Psychology", "Neurology", "Orthopedics", "Dermatology", "Pediatrics", "Internal Medicine", "Oncology", "Radiology"]
                        }
                    },
                    "required": ["department"]
                }
            },
            {
                "name": "doctor_availability",
                "description": "Get available dates and times for a specific doctor",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "doctor_name": {
                            "type": "string",
                            "description": "Doctor's full name (e.g., Dr. Sarah Johnson)"
                        }
                    },
                    "required": ["doctor_name"]
                }
            },
            {
                "name": "reschedule_appointment",
                "description": "Dedicated tool for handling appointment rescheduling with flexible date parsing. Use this tool when user provides any date during rescheduling.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "Patient's full name"
                        },
                        "phone": {
                            "type": "string",
                            "description": "Patient's phone number (8 digits)"
                        },
                        "preferred_date": {
                            "type": "string",
                            "description": "Preferred appointment date in any format (e.g., 'September 22 would be great', '22nd September', '22/09/2025')"
                        }
                    },
                    "required": ["name", "phone", "preferred_date"]
                }
            },
            {
                "name": "appointment_scheduler",
                "description": "Schedule, reschedule, or cancel medical appointments for patients",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "Patient's full name (e.g., John Smith)"
                        },
                        "phone": {
                            "type": "string",
                            "description": "Patient's phone number (e.g., 91234567)"
                        },
                        "department": {
                            "type": "string",
                            "description": "Medical department (e.g., Cardiology, Psychology, Neurology, Orthopedics, Dermatology, Pediatrics, Internal Medicine, Emergency Medicine)",
                            "enum": ["Cardiology", "Psychology", "Neurology", "Orthopedics", "Dermatology", "Pediatrics", "Internal Medicine", "Oncology", "Radiology"]
                        },
                        "doctor_name": {
                            "type": "string",
                            "description": "Preferred doctor name (optional - will show available doctors if not specified)"
                        },
                        "preferred_date": {
                            "type": "string",
                            "description": "Preferred appointment date (format: YYYY-MM-DD)"
                        },
                         "preferred_day": {
                            "type": "string",
                            "description": "Preferred appointment day (e.g., Monday, Tuesday, Wednesday)"
                        },
                        "preferred_time": {
                            "type": "string",
                            "description": "Preferred appointment time (format: HH:MM AM/PM)"
                        },
                        "reason": {
                            "type": "string",
                            "description": "Reason for the appointment"
                        },
                        "action": {
                            "type": "string",
                            "description": "Action to perform: schedule, reschedule, cancel, check_availability, get_doctor_times. Note: get_doctor_times only requires doctor_name parameter",
                            "enum": ["schedule", "reschedule", "cancel", "check_availability", "get_doctor_times"]
                        }
                    },
                    "required": ["action"]
                }
            },
            {
                "name": "patient_records",
                "description": "Access patient medical records, history, and health information (requires Name and Phone)",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "Patient's full name (e.g., John Smith)"
                        },
                        "phone": {
                            "type": "string",
                            "description": "Patient's phone number (e.g., 91234567)"
                        },
                        "record_type": {
                            "type": "string",
                            "description": "Type of record to retrieve",
                            "enum": ["all", "recent", "specific"]
                        }
                    },
                    "required": ["name", "phone", "record_type"]
                }
            },
            {
                "name": "medication_tracker",
                "description": "Manage patient medications, prescriptions, and medication schedules (requires Name and Phone)",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "Patient's full name (e.g., John Smith)"
                        },
                        "phone": {
                            "type": "string",
                            "description": "Patient's phone number (e.g., 91234567)"
                        },
                        "action": {
                            "type": "string",
                            "description": "Action to perform",
                            "enum": ["get_medications", "add_medication", "update_medication", "remove_medication"]
                        },
                        "medication_name": {
                            "type": "string",
                            "description": "Name of the medication (required for add/update/remove actions)"
                        },
                        "dosage": {
                            "type": "string",
                            "description": "Medication dosage (required for add/update actions)"
                        },
                        "schedule": {
                            "type": "string",
                            "description": "Medication schedule (required for add/update actions)"
                        }
                    },
                    "required": ["name", "phone", "action"]
                }
            },
            {
                "name": "emergency_response",
                "description": "Handle medical emergencies and urgent situations",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "emergency_type": {
                            "type": "string",
                            "description": "Type of emergency",
                            "enum": ["medical", "trauma", "cardiac", "respiratory", "other"]
                        },
                        "severity": {
                            "type": "string",
                            "description": "Severity level",
                            "enum": ["low", "medium", "high", "critical"]
                        },
                        "description": {
                            "type": "string",
                            "description": "Description of the emergency situation"
                        },
                        "location": {
                            "type": "string",
                            "description": "Location of the emergency"
                        }
                    },
                    "required": ["emergency_type", "severity", "description"]
                }
            },
            {
                "name": "symptom_checker",
                "description": "Provide preliminary symptom analysis and guidance",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "symptoms": {
                            "type": "string",
                            "description": "Description of symptoms"
                        },
                        "duration": {
                            "type": "string",
                            "description": "How long symptoms have been present"
                        },
                        "severity": {
                            "type": "string",
                            "description": "Severity of symptoms",
                            "enum": ["mild", "moderate", "severe"]
                        },
                        "additional_info": {
                            "type": "string",
                            "description": "Any additional relevant information"
                        }
                    },
                    "required": ["symptoms"]
                }
            }
        ]

        # First API call to get initial response
        try:
            response = bedrock_client.invoke_model_with_response_stream(
                contentType='application/json',
                body=json.dumps({
                    "anthropic_version": "bedrock-2023-05-31",
                    "max_tokens": 4000,
                    "temperature": 0,
                    "top_p": 0.999,
                    "top_k": 250,
                    "system": prompt,
                    "tools": hospital_tools,
                    "messages": chat_history
                }),
                modelId="us.anthropic.claude-3-7-sonnet-20250219-v1:0"
            )
        except Exception as e:
            print("AN ERROR OCCURRED : ", e)
            response = "We are unable to assist right now please try again after few minutes"
            return {"answer": response, "question": chat, "session_id": session_id}

        streamed_content = ''
        content_block = None
        assistant_response = []
        input_tokens = 0
        output_tokens = 0
        
        for item in response['body']:
            content = json.loads(item['chunk']['bytes'].decode())
            if content['type'] == 'content_block_start':
                content_block = content['content_block']
            elif content['type'] == 'content_block_stop':
                print(f"Content block at stop: {content_block}")  # Add debug line
                try:
                    api_gateway_client.post_to_connection(ConnectionId=connectionId, Data=json.dumps(content))
                except api_gateway_client.exceptions.GoneException:
                    print(f"Connection {connectionId} is closed (GoneException) - stop message")
                except Exception as e:
                    print(f"WebSocket send error (stop): {e}")
                if content_block['type'] == 'text':
                    content_block['text'] = streamed_content
                    assistant_response.append(content_block)
                elif content_block['type'] == 'tool_use':
                    try:
                        content_block['input'] = json.loads(streamed_content)
                    except json.JSONDecodeError as e:
                        print(f"JSON decode error for tool input: {e}")
                        print(f"Streamed content: {streamed_content}")
                        content_block['input'] = {}
                    assistant_response.append(content_block)
                streamed_content = ''
            elif content['type'] == 'content_block_delta':
                try:
                    api_gateway_client.post_to_connection(ConnectionId=connectionId, Data=json.dumps(content))
                except api_gateway_client.exceptions.GoneException:
                    print(f"Connection {connectionId} is closed (GoneException) - delta message")
                except Exception as e:
                    print(f"WebSocket send error (delta): {e}")
                if 'delta' in content and isinstance(content['delta'], dict):
                    if content['delta']['type'] == 'text_delta':
                        streamed_content += content['delta']['text']
                    elif content['delta']['type'] == 'input_json_delta':
                        streamed_content += content['delta']['partial_json']
            elif content['type'] == 'message_delta':
                try:
                    if 'usage' in content and isinstance(content['usage'], dict):
                        tool_tokens = content['usage']['output_tokens']
                    else:
                        tool_tokens = 0
                except (KeyError, TypeError) as e:
                    print(f"Error accessing usage tokens: {e}")
                    tool_tokens = 0
            elif content['type'] == 'message_stop':
                input_tokens += content['amazon-bedrock-invocationMetrics']['inputTokenCount']
                output_tokens += content['amazon-bedrock-invocationMetrics']['outputTokenCount']
        chat_history.append({'role': 'assistant', 'content': assistant_response})
        
        # Check if any tools were called
        tools_used = []
        tool_results = []

        print(f"Assistant response type: {type(assistant_response)}")
        print(f"Assistant response length: {len(assistant_response)}")
        for i, item in enumerate(assistant_response):
            print(f"Item {i}: type={type(item)}, content={item}")

        # CORRECT: Iterate over assistant_response items
        for response_item in assistant_response:
            if isinstance(response_item, dict) and response_item.get('type') == 'tool_use':
                tools_used.append(response_item['name'])
                tool_name = response_item['name']
                tool_input = response_item['input']
                tool_result = None
                
                print(f"Processing tool: {tool_name}")
                print(f"Tool input: {tool_input}")
                
                # Send a heartbeat to keep WebSocket alive during tool execution
                try:
                    heartbeat = {'type': 'heartbeat'}
                    api_gateway_client.post_to_connection(ConnectionId=connectionId, Data=json.dumps(heartbeat))
                except Exception as e:
                    print(f"Heartbeat send error: {e}")
                
                # Execute the appropriate hospital tool
                if tool_name == 'hospital_faq_tool_schema':
                    print("hospital_faq is called ...")
                    # Send another heartbeat before FAQ retrieval
                    try:
                        heartbeat = {'type': 'heartbeat'}
                        api_gateway_client.post_to_connection(ConnectionId=connectionId, Data=json.dumps(heartbeat))
                    except Exception as e:
                        print(f"Hospital FAQ heartbeat send error: {e}")
                    
                    tool_result = get_hospital_faq_chunks(tool_input['knowledge_base_retrieval_question'])
                    
                    # If FAQ tool returns empty or no results, provide fallback
                    if not tool_result or len(tool_result) == 0:
                        tool_result = ["I don't have specific information about that in our current hospital knowledge base. Please contact our hospital directly for detailed information."]
                
                elif tool_name == 'get_department_doctors':
                    # Handle department doctors tool - MANDATORY for department selection
                    department = tool_input.get("department", "")
                    
                    # Use centralized department doctors data
                    department_doctors = DEPARTMENT_DOCTORS
                    
                    if department in department_doctors:
                        doctors = department_doctors[department]
                        doctor_list = []
                        for doctor in doctors:
                            doctor_list.append(f"• {doctor['name']}")
                        
                        tool_result = [f"Here are the available doctors in our {department} department:\n\n" + "\n".join(doctor_list) + "\n\nWhich doctor would you prefer to see?"]
                    else:
                        tool_result = [f"I'm sorry, but {department} is not a valid department. Please select from: Cardiology, Psychology, Neurology, Orthopedics, Dermatology, Pediatrics, Internal Medicine, Oncology, or Radiology."]
                
                elif tool_name == 'doctor_availability':
                    # Get doctor availability - simple tool that only requires doctor name
                    doctor_name = tool_input.get("doctor_name", "")
                    
                    # Use centralized department doctors data
                    department_doctors = DEPARTMENT_DOCTORS
                    
                    # Search for the doctor across all departments
                    selected_doctor = None
                    found_department = None
                    
                    for dept_name, doctors in department_doctors.items():
                        for doctor in doctors:
                            if doctor_name.lower() in doctor['name'].lower():
                                selected_doctor = doctor
                                found_department = dept_name
                                break
                        if selected_doctor:
                            break
                    
                    if selected_doctor:
                        # Convert available dates to readable format
                        readable_dates = []
                        for date_str in selected_doctor['available_dates']:
                            try:
                                # Parse the date string (format: YYYY-MM-DD)
                                dt = datetime.strptime(date_str, '%Y-%m-%d')
                                # Format as "September 19, 2025"
                                readable_dates.append(dt.strftime('%B %d, %Y'))
                            except Exception as e:
                                # If parsing fails, use the original string
                                print(f"Date parsing error for {date_str}: {e}")
                                readable_dates.append(date_str)
                        
                        # Create formatted date list with bullet points
                        available_dates_str = "\n".join([f"• {date}" for date in readable_dates])
                        tool_result = [f"Dr. {selected_doctor['name']} is available on:\n\n{available_dates_str}\n\nWhat is your preferred date for the appointment?"]
                    else:
                        tool_result = [f"Doctor {doctor_name} not found. Please select from the available doctors."]
                
                elif tool_name == 'reschedule_appointment':
                    # Dedicated tool for handling rescheduling with flexible date parsing
                    name = tool_input.get("name", "")
                    phone = tool_input.get("phone", "")
                    preferred_date = tool_input.get("preferred_date", "")
                    
                    print(f"Reschedule appointment called with: name={name}, phone={phone}, preferred_date={preferred_date}")
                    
                    # Validate phone number format
                    is_valid_phone, phone_result = validate_phone_number(phone)
                    if not is_valid_phone:
                        tool_result = [phone_result]
                    else:
                        # Use the cleaned phone number
                        phone = phone_result
                        
                        # Parse the date using flexible parsing
                        formatted_date = parse_date_flexible(preferred_date)
                        
                        if not formatted_date:
                            tool_result = [f"Could not parse the date '{preferred_date}'. Please provide a date in a format like 'September 22' or '22nd September'."]
                        else:
                            # Define patients data (name/phone to patient key mapping)
                            patients = {
                                "PAT1001": {"name": "John Smith", "phone": "91234567"},
                                "PAT1002": {"name": "Emily Davis", "phone": "97651823"},
                                "PAT1003": {"name": "Michael Johnson", "phone": "98765432"},
                                "PAT1004": {"name": "Sarah Wilson", "phone": "92345678"},
                                "PAT1005": {"name": "David Brown", "phone": "93456789"}
                            }
                            
                            # Define patient appointments data
                            patient_appointments = {
                                "PAT1001": [
                                    {"id": "APT123456", "department": "Cardiology", "doctor": "Dr. Sarah Johnson", "date": "2025-09-19", "time": "10:00 AM", "reason": "Follow-up consultation"}
                                ],
                                "PAT1002": [
                                    {"id": "APT123457", "department": "Psychology", "doctor": "Dr. Lisa Thompson", "date": "2025-09-20", "time": "2:00 PM", "reason": "Prenatal checkup"}
                                ],
                                "PAT1003": [
                                    {"id": "APT123458", "department": "Orthopedics", "doctor": "Dr. David Miller", "date": "2025-09-21", "time": "11:30 AM", "reason": "Physical therapy session"}
                                ],
                                "PAT1004": [
                                    {"id": "APT123459", "department": "Psychology", "doctor": "Dr. Lisa Thompson", "date": "2025-09-22", "time": "3:00 PM", "reason": "Therapy session"}
                                ],
                                "PAT1005": [
                                    {"id": "APT123460", "department": "Neurology", "doctor": "Dr. Amanda Foster", "date": "2025-09-23", "time": "9:30 AM", "reason": "Neurological consultation"}
                                ]
                            }
                            
                            # Map Name and Phone to patient key
                            patient_key = None
                            for k, v in patients.items():
                                if v['name'].lower() == name.lower() and v['phone'].replace(' ', '') == phone.replace(' ', ''):
                                    patient_key = k
                                    break
                            
                            if not patient_key or patient_key not in patient_appointments or not patient_appointments[patient_key]:
                                tool_result = ["No existing appointment found. Please contact the hospital directly."]
                            else:
                                existing_appointment = patient_appointments[patient_key][0]
                                existing_doctor = existing_appointment['doctor']
                                existing_department = existing_appointment['department']
                                
                                # Use centralized department doctors data
                                department_doctors = DEPARTMENT_DOCTORS
                                
                                # Find the doctor in the department
                                selected_doctor = None
                                if existing_department in department_doctors:
                                    for doctor in department_doctors[existing_department]:
                                        if doctor['name'] == existing_doctor:
                                            selected_doctor = doctor
                                            break
                                
                                if not selected_doctor:
                                    tool_result = [f"Doctor {existing_doctor} not found. Please contact the hospital directly."]
                                else:
                                    # Check if the formatted date is available
                                    if formatted_date in selected_doctor['available_dates']:
                                        # Date is available, show available times
                                        available_times = selected_doctor['available_times']
                                        times_list = "\n".join([f"• {time}" for time in available_times])
                                        
                                        # Convert date to readable format
                                        try:
                                            from datetime import datetime
                                            dt = datetime.strptime(formatted_date, '%Y-%m-%d')
                                            readable_date = dt.strftime('%B %d, %Y')
                                        except:
                                            readable_date = formatted_date
                                        
                                        tool_result = [f"Great! Dr. {existing_doctor} is available on {readable_date}. Here are the available times:\n\n{times_list}\n\nWhat time would you prefer for your appointment?"]
                                    else:
                                        # Date not available, show available dates
                                        readable_dates = []
                                        for date_str in selected_doctor['available_dates']:
                                            try:
                                                from datetime import datetime
                                                dt = datetime.strptime(date_str, '%Y-%m-%d')
                                                readable_dates.append(dt.strftime('%B %d, %Y'))
                                            except:
                                                readable_dates.append(date_str)
                                        available_dates_str = "\n".join([f"• {date}" for date in readable_dates])
                                        
                                        # Convert user's date to readable format for error message
                                        try:
                                            from datetime import datetime
                                            dt = datetime.strptime(formatted_date, '%Y-%m-%d')
                                            user_readable_date = dt.strftime('%B %d, %Y')
                                        except:
                                            user_readable_date = preferred_date
                                        
                                        tool_result = [f"I'm sorry, but Dr. {existing_doctor} is not available on {user_readable_date}. Here are the available dates:\n\n{available_dates_str}\n\nPlease choose one of these dates."]

                elif tool_name == 'appointment_scheduler':
                    # Simulate appointment scheduling with department and doctor management
                    name = tool_input.get("name", "")
                    phone = tool_input.get("phone", "")
                    department = tool_input.get("department", "")
                    doctor_name = tool_input.get("doctor_name", "")
                    preferred_date = tool_input.get("preferred_date", "")
                    preferred_time = tool_input.get("preferred_time", "")
                    preferred_day = tool_input.get("preferred_day", "")
                    reason = tool_input.get("reason", "")
                    action_type = tool_input.get("action", "schedule")
                    
                    # For get_doctor_times action, skip phone validation as it doesn't require authentication
                    if action_type != "get_doctor_times":
                        # Check if name and phone are provided
                        if not name or not phone:
                            missing_params = []
                            if not name:
                                missing_params.append("name")
                            if not phone:
                                missing_params.append("phone")
                            tool_result = [f"Missing required parameters: {', '.join(missing_params)}. Please provide the missing information."]
                            tool_response_dict = {
                                "type": "tool_result",
                                "tool_use_id": response_item['id'],
                                "content": [{"type": "text", "text": tool_result[0]}]
                            }
                            tool_results.append(tool_response_dict)
                            continue
                        
                        # Validate phone number format - must be exactly 8 digits
                        is_valid_phone, phone_result = validate_phone_number(phone)
                        if not is_valid_phone:
                            tool_result = [phone_result]
                            tool_response_dict = {
                                "type": "tool_result",
                                "tool_use_id": response_item['id'],
                                "content": [{"type": "text", "text": phone_result}]
                            }
                            tool_results.append(tool_response_dict)
                            continue  # Skip further processing if phone is invalid
                        
                        # Use the cleaned phone number (digits only)
                        phone = phone_result
                    
                    print(f"Appointment details: {name}, {phone}, {department}, {doctor_name}, {preferred_date}, {preferred_day}, {preferred_time}, {reason}")
                    # Use centralized department doctors data
                    department_doctors = DEPARTMENT_DOCTORS

                    valid_patients = {p['name']: p['phone'] for p in patients.values()}
                    # Phone digit-count validation is handled by the system prompt; do not enforce here.
                    # For scheduling (action_type == 'schedule') we accept Name+Phone but do NOT authenticate against stored patients.
                    is_authenticated = (name in valid_patients and valid_patients[name] == phone)
                    if action_type == "schedule" or action_type == "get_doctor_times":
                        # scheduling and get_doctor_times bypass strict patient-record matching, but phone format is already enforced above
                        is_authenticated = True
                    if is_authenticated:
                        # proactive Always ask for department (scheduling skips strict auth)
                        if action_type == "schedule" and department and not doctor_name:
                            if department in department_doctors:
                                doctors_list = []
                                for doctor in department_doctors[department]:
                                    doctors_list.append(f"• {doctor['name']}")
                                doctors_info = "\n".join(doctors_list)
                                message = f"Available doctors in {department} department:\n{doctors_info}\n\nWhich doctor would you prefer to see?"
                                tool_result = [message]
                                tool_response_dict = {
                                    "type": "tool_result",
                                    "tool_use_id": response_item['id'],
                                    "content": [{"type": "text", "text": message}]
                                }
                                tool_results.append(tool_response_dict)
                                continue  # Skip further appointment logic for this turn
                            else:
                                available_departments = "\n".join([f"• {dept}" for dept in department_doctors.keys()])
                                tool_result = [f"Please select a department first.\n{available_departments}"]
                                tool_response_dict = {
                                    "type": "tool_result",
                                    "tool_use_id": response_item['id'],
                                    "content": [{"type": "text", "text": "\n".join(tool_result) if isinstance(tool_result, list) else str(tool_result)}]
                                }
                                tool_results.append(tool_response_dict)
                                continue
                    valid_patients = {p['name']: p['phone'] for p in patients.values()}
                    # For scheduling (action_type == 'schedule') we accept Name+Phone but do NOT authenticate.
                    is_authenticated = (name in valid_patients and valid_patients[name] == phone)
                    if action_type == "schedule":
                        is_authenticated = True
                    if is_authenticated:
                        # proactive Always ask for department (scheduling skips strict auth)
                        if action_type == "schedule" and department and not doctor_name:
                            if department in department_doctors:
                                doctors_list = [f"• {doctor['name']}" for doctor in department_doctors[department]]
                                doctors_info = "\n".join(doctors_list)
                                message = f"Available doctors in {department} department:\n{doctors_info}\n\nWhich doctor would you prefer to see?"
                                tool_result = [message]
                                tool_response_dict = {
                                    "type": "tool_result",
                                    "tool_use_id": response_item['id'],
                                    "content": [{"type": "text", "text": message}]
                                }
                                tool_results.append(tool_response_dict)
                                continue  # Skip further appointment logic for this turn
                            else:
                                available_departments = ", ".join(department_doctors.keys())
                                tool_result = [f"Please select a department first. Available departments: {available_departments}"]
                        elif action_type == "check_availability":
                            if department and department in department_doctors:
                                doctors_info = ""
                                for doctor in department_doctors[department]:
                                    doctors_info += f"\n• {doctor['name']}"
                                tool_result = [f"Available doctors in {department} department:{doctors_info}\n\nWhich doctor would you prefer to see?"]
                            else:
                                available_departments = ", ".join(department_doctors.keys())
                                tool_result = [f"Please select a department first. Available departments: {available_departments}"]
                        elif action_type == "schedule":
                            if not department:
                                available_departments = ", ".join(department_doctors.keys())
                                tool_result = [f"Please select a department first. Available departments: {available_departments}"]
                            elif department not in department_doctors:
                                tool_result = [f"Invalid department. Available departments: {', '.join(department_doctors.keys())}"]
                            else:
                                # Find the selected doctor or assign one
                                selected_doctor = None
                                if doctor_name:
                                    for doctor in department_doctors[department]:
                                        if doctor_name.lower() in doctor['name'].lower():
                                            selected_doctor = doctor
                                            break
                                if not selected_doctor:
                                    selected_doctor = department_doctors[department][0]
                                
                                # Validate preferred_date against doctor's available_dates
                                if preferred_date and preferred_date not in selected_doctor['available_dates']:
                                    # Convert available dates to readable format for error message
                                    readable_dates = []
                                    for date_str in selected_doctor['available_dates']:
                                        try:
                                            dt = datetime.strptime(date_str, '%Y-%m-%d')
                                            readable_dates.append(dt.strftime('%B %d, %Y'))
                                        except:
                                            readable_dates.append(date_str)
                                    available_dates_str = ', '.join(readable_dates)
                                    tool_result = [f"Sorry, {preferred_date} is not available for {selected_doctor['name']}. Available dates are: {available_dates_str}. Please choose one of these dates."]
                                else:
                                    appointment_id = f"APT{random.randint(100000, 999999)}"
                                    # Format the date for display
                                    display_date = preferred_date
                                    if preferred_date:
                                        try:
                                            dt = datetime.strptime(preferred_date, '%Y-%m-%d')
                                            display_date = dt.strftime('%B %d, %Y')
                                        except:
                                            display_date = preferred_date
                                    tool_result = [f"Appointment scheduled successfully!\n\nAppointment ID: {appointment_id}\nDepartment: {department}\nDoctor: {selected_doctor['name']}\nDate: {display_date}\nTime: {preferred_time}\nReason: {reason}\n\nPlease arrive 15 minutes early for your appointment."]
                        
                        elif action_type == "reschedule":
                            # Patient-specific existing appointments
                            
                            def get_human_friendly_date(date_str):
                                dt = datetime.strptime(date_str, '%Y-%m-%d')
                                return dt.strftime('%-d %B') if hasattr(dt, 'strftime') else date_str
                            patient_appointments = {
                                "PAT1001": [
                                    {"id": "APT123456", "department": "Cardiology", "doctor": "Dr. Sarah Johnson", "date": "2025-09-19", "time": "10:00 AM", "reason": "Follow-up consultation"}
                                ],
                                "PAT1002": [
                                    {"id": "APT123457", "department": "Psychology", "doctor": "Dr. Lisa Thompson", "date": "2025-09-20", "time": "2:00 PM", "reason": "Prenatal checkup"}
                                ],
                                "PAT1003": [
                                    {"id": "APT123458", "department": "Orthopedics", "doctor": "Dr. David Miller", "date": "2025-09-21", "time": "11:30 AM", "reason": "Physical therapy session"}
                                ],
                                "PAT1004": [
                                    {"id": "APT123459", "department": "Psychology", "doctor": "Dr. Lisa Thompson", "date": "2025-09-22", "time": "3:00 PM", "reason": "Therapy session"}
                                ],
                                "PAT1005": [
                                    {"id": "APT123460", "department": "Neurology", "doctor": "Dr. Kevin Park", "date": "2025-09-23", "time": "9:00 AM", "reason": "Migraine follow-up"}
                                ]
                            }
                            # Map Name and Phone to patient key
                            patient_key = None
                            for k, v in patients.items():
                                if v['name'].lower() == name.lower() and v['phone'].replace(' ', '') == phone.replace(' ', ''):
                                    patient_key = k
                                    break
                            if patient_key and patient_key in patient_appointments and patient_appointments[patient_key]:
                                existing_appointment = patient_appointments[patient_key][0]  # Get first appointment
                                if preferred_date and preferred_time:
                                    # For reschedule, use existing department and doctor unless specified otherwise
                                    reschedule_department = department if department else existing_appointment['department']
                                    reschedule_doctor = doctor_name if doctor_name else existing_appointment['doctor']
                                    # Validate new appointment details
                                    if reschedule_department in department_doctors:
                                        selected_doctor = None
                                        if doctor_name:
                                            for doctor in department_doctors[reschedule_department]:
                                                if doctor_name.lower() in doctor['name'].lower():
                                                    selected_doctor = doctor
                                                    break
                                        if not selected_doctor:
                                            # Use existing doctor or first available in department
                                            if reschedule_department == existing_appointment['department']:
                                                # Find the existing doctor in the department_doctors dictionary
                                                for doctor in department_doctors[reschedule_department]:
                                                    if doctor['name'] == existing_appointment['doctor']:
                                                        selected_doctor = doctor
                                                        break
                                                if not selected_doctor:
                                                    selected_doctor = department_doctors[reschedule_department][0]
                                            else:
                                                selected_doctor = department_doctors[reschedule_department][0]
                                        
                                        # Validate preferred_date against doctor's available_dates
                                        if preferred_date and 'available_dates' in selected_doctor and preferred_date not in selected_doctor['available_dates']:
                                            # Convert available dates to readable format for error message
                                            readable_dates = []
                                            for date_str in selected_doctor['available_dates']:
                                                try:
                                                    dt = datetime.strptime(date_str, '%Y-%m-%d')
                                                    readable_dates.append(dt.strftime('%B %d, %Y'))
                                                except:
                                                    readable_dates.append(date_str)
                                            available_dates_str = ', '.join(readable_dates)
                                            tool_result = [f"Sorry, {preferred_date} is not available for {selected_doctor['name']}. Available dates are: {available_dates_str}. Please choose one of these dates."]
                                        else:
                                            # Format the date for display
                                            display_date = preferred_date
                                            if preferred_date:
                                                try:
                                                    dt = datetime.strptime(preferred_date, '%Y-%m-%d')
                                                    display_date = dt.strftime('%B %d, %Y')
                                                except:
                                                    display_date = preferred_date
                                            tool_result = [f"Appointment Rescheduled Successfully!\n\nPrevious Appointment:\n- ID: {existing_appointment['id']}\n- Department: {existing_appointment['department']}\n- Doctor: {existing_appointment['doctor']}\n- Date: {existing_appointment['date']}\n- Time: {existing_appointment['time']}\n- Reason: {existing_appointment['reason']}\n\nNew Appointment:\n- ID: {existing_appointment['id']} (same)\n- Department: {reschedule_department}\n- Doctor: {selected_doctor['name']}\n- Date: {display_date}\n- Time: {preferred_time}\n- Reason: {existing_appointment['reason']}\n\nPlease arrive 15 minutes early for your rescheduled appointment."]
                                    else:
                                        tool_result = [f"Please specify a valid department for rescheduling. Available departments: {', '.join(department_doctors.keys())}"]
                                elif not preferred_date and not preferred_time and reason:
                                    # User confirmed they want to reschedule (reason contains confirmation)
                                    confirmation = reason.lower()
                                    if any(word in confirmation for word in ['yes', 'yep', 'sure', 'okay', 'ok', 'reschedule', 'change']):
                                        # Show available dates for the same doctor
                                        existing_doctor = existing_appointment['doctor']
                                        existing_department = existing_appointment['department']
                                        
                                        if existing_department in department_doctors:
                                            selected_doctor = None
                                            for doctor in department_doctors[existing_department]:
                                                if doctor['name'] == existing_doctor:
                                                    selected_doctor = doctor
                                                    break
                                            
                                            if selected_doctor:
                                                # Convert available dates to readable format
                                                readable_dates = []
                                                for date_str in selected_doctor['available_dates']:
                                                    try:
                                                        dt = datetime.strptime(date_str, '%Y-%m-%d')
                                                        readable_dates.append(dt.strftime('%B %d, %Y'))
                                                    except:
                                                        readable_dates.append(date_str)
                                                available_dates_str = "\n".join([f"• {date}" for date in readable_dates])
                                                tool_result = [f"Great! Here are the available dates for Dr. {existing_doctor}:\n\n{available_dates_str}\n\nWhat date would you prefer for your rescheduled appointment?"]
                                            else:
                                                tool_result = [f"Doctor {existing_doctor} not found. Please contact the hospital directly."]
                                        else:
                                            tool_result = [f"Department {existing_department} not found. Please contact the hospital directly."]
                                    else:
                                        tool_result = ["Thank you. Your appointment remains as scheduled. Is there anything else I can help you with?"]
                                elif preferred_date and not preferred_time:
                                    # User provided only date, ask for time
                                    # Convert the provided date to readable format
                                    try:
                                        # Parse the user's date input using flexible date parsing
                                        formatted_date = parse_date_flexible(preferred_date)
                                        
                                        if formatted_date:
                                            
                                            # Find the doctor's available times for this date
                                            existing_doctor = existing_appointment['doctor']
                                            existing_department = existing_appointment['department']
                                            
                                            if existing_department in department_doctors:
                                                selected_doctor = None
                                                for doctor in department_doctors[existing_department]:
                                                    if doctor['name'] == existing_doctor:
                                                        selected_doctor = doctor
                                                        break
                                                
                                                if selected_doctor and formatted_date in selected_doctor['available_dates']:
                                                    available_times = selected_doctor['available_times']
                                                    times_list = "\n".join([f"• {time}" for time in available_times])
                                                    
                                                    # Format the date for display
                                                    display_date = formatted_date
                                                    try:
                                                        dt = datetime.strptime(formatted_date, '%Y-%m-%d')
                                                        display_date = dt.strftime('%B %d, %Y')
                                                    except:
                                                        display_date = formatted_date
                                                    
                                                    tool_result = [f"Dr. {existing_doctor} has the following available times on {display_date}:\n\n{times_list}\n\nWhat time would you prefer for your appointment?"]
                                                else:
                                                    # Date not available, show available dates
                                                    readable_dates = []
                                                    for date_str in selected_doctor['available_dates']:
                                                        try:
                                                            dt = datetime.strptime(date_str, '%Y-%m-%d')
                                                            readable_dates.append(dt.strftime('%B %d, %Y'))
                                                        except:
                                                            readable_dates.append(date_str)
                                                    available_dates_str = "\n".join([f"• {date}" for date in readable_dates])
                                                    tool_result = [f"I'm sorry, but Dr. {existing_doctor} is not available on {preferred_date}. Here are the available dates:\n\n{available_dates_str}\n\nPlease choose one of these dates."]
                                            else:
                                                tool_result = [f"Please specify a valid department for rescheduling. Available departments: {', '.join(department_doctors.keys())}"]
                                        else:
                                            tool_result = [f"Please provide a valid date format (e.g., 'September 27' or '27th September')."]
                                    except Exception as e:
                                        tool_result = [f"Please provide a valid date format (e.g., 'September 27' or '27th September')."]
                                else:
                                    # Format date as '17th September 2025'
                                    def ordinal(n):
                                        return "%d%s" % (n, "th" if 11<=n%100<=13 else {1:"st",2:"nd",3:"rd"}.get(n%10, "th"))
                                    try:
                                        dt = datetime.strptime(existing_appointment['date'], '%Y-%m-%d')
                                        human_date = f"{ordinal(dt.day)} {dt.strftime('%B %Y')}"
                                    except Exception:
                                        human_date = existing_appointment['date']
                                    tool_result = [f"Current Appointment Details:\n\n• Appointment ID: {existing_appointment['id']}\n• Department: {existing_appointment['department']}\n• Doctor: {existing_appointment['doctor']}\n• Date: {human_date}\n• Time: {existing_appointment['time']}\n• Reason: {existing_appointment['reason']}\n\nWould you like to reschedule this appointment?"]
                            else:
                                tool_result = ["No existing appointments found to reschedule. Would you like to schedule a new appointment instead?"]
                        
                        elif action_type == "cancel":
                            # Patient-specific existing appointments
                            patient_appointments = {
                                "PAT1001": [
                                    {"id": "APT123456", "department": "Cardiology", "doctor": "Dr. Sarah Johnson", "date": "2025-09-19", "time": "10:00 AM", "reason": "Follow-up consultation"}
                                ],
                                "PAT1002": [
                                    {"id": "APT123457", "department": "Psychology", "doctor": "Dr. Lisa Thompson", "date": "2025-09-20", "time": "2:00 PM", "reason": "Prenatal checkup"}
                                ],
                                "PAT1003": [
                                    {"id": "APT123458", "department": "Orthopedics", "doctor": "Dr. David Miller", "date": "2025-09-21", "time": "11:30 AM", "reason": "Physical therapy session"}
                                ],
                                "PAT1004": [
                                    {"id": "APT123459", "department": "Psychology", "doctor": "Dr. Lisa Thompson", "date": "2025-09-22", "time": "3:00 PM", "reason": "Therapy session"}
                                ],
                                "PAT1005": [
                                    {"id": "APT123460", "department": "Neurology", "doctor": "Dr. Kevin Park", "date": "2025-09-23", "time": "9:00 AM", "reason": "Migraine follow-up"}
                                ]
                            }
                            # Map Name and Phone to patient key
                            patient_key = None
                            for k, v in patients.items():
                                if v['name'].lower() == name.lower() and v['phone'].replace(' ', '') == phone.replace(' ', ''):
                                    patient_key = k
                                    break
                            if patient_key and patient_key in patient_appointments and patient_appointments[patient_key]:
                                appointments = patient_appointments[patient_key]
                                # Check if user has confirmed cancellation or provided specific appointment details
                                user_confirmation = tool_input.get("reason", "").lower()  # Using reason field to capture user response
                                user_confirms = any(phrase in user_confirmation for phrase in [
                                    "yes", "yep", "yeah", "sure", "ok", "okay",
                                    "cancel", "cancelled", "cancellation",
                                    "confirm", "confirmed", "confirmation",
                                    "i would like to", "i want to", "i'd like to",
                                    "please cancel", "go ahead", "proceed"
                                ])
                                if (preferred_date and preferred_time) or user_confirms:
                                    # User has confirmed cancellation or provided specific appointment details
                                    appointment_to_cancel = None
                                    if preferred_date and preferred_time:
                                        # User provided specific appointment details
                                        for appointment in appointments:
                                            if (appointment['date'] == preferred_date and 
                                                appointment['time'] == preferred_time):
                                                appointment_to_cancel = appointment
                                                break
                                    else:
                                        # User confirmed cancellation - cancel the first/only appointment
                                        appointment_to_cancel = appointments[0]
                                    if appointment_to_cancel:
                                        tool_result = [f"Appointment Cancelled Successfully!\n\nCancelled Appointment Details:\n- Appointment ID: {appointment_to_cancel['id']}\n- Department: {appointment_to_cancel['department']}\n- Doctor: {appointment_to_cancel['doctor']}\n- Date: {appointment_to_cancel['date']}\n- Time: {appointment_to_cancel['time']}\n- Reason: {appointment_to_cancel['reason']}\n\nYour appointment has been cancelled. If you need to reschedule, please call our appointment line at (555) 123-4567 or use our online booking system.\n\nWe hope to serve you again soon!"]
                                    else:
                                        tool_result = [f"No appointment found matching {preferred_date} at {preferred_time}. Please check your appointment details and try again."]
                                else:
                                    # Show all appointments and ask which one to cancel
                                    if len(appointments) == 1:
                                        appointment = appointments[0]
                                        # Format date as '7th September 2025'
                                        def ordinal(n):
                                            return "%d%s" % (n, "th" if 11<=n%100<=13 else {1:"st",2:"nd",3:"rd"}.get(n%10, "th"))
                                        try:
                                            dt = datetime.strptime(appointment['date'], '%Y-%m-%d')
                                            human_date = f"{ordinal(dt.day)} {dt.strftime('%B %Y')}"
                                        except Exception:
                                            human_date = appointment['date']
                                        tool_result = [f"Current Appointment Details:\n\nAppointment ID: {appointment['id']}\nDepartment: {appointment['department']}\nDoctor: {appointment['doctor']}\nDate: {human_date}\nTime: {appointment['time']}\nReason: {appointment['reason']}\n\nWould you like to cancel this appointment? Please confirm by saying 'yes' or 'cancel'."]
                                    else:
                                        appointments_list = "\n".join([f"{i+1}. {apt['department']} - {apt['doctor']} - {apt['date']} at {apt['time']}" for i, apt in enumerate(appointments)])
                                        tool_result = [f"Here are your current appointments:\n\n{appointments_list}\n\nWhich appointment would you like to cancel? Please specify the number (1, 2, etc.) or provide the department/doctor name."]
                            else:
                                tool_result = ["No existing appointments found to cancel. If you need to schedule a new appointment, I'd be happy to help you with that."]
                        
                        elif action_type == "get_doctor_times":
                            # For get_doctor_times, we need to find the doctor across all departments
                            selected_doctor = None
                            found_department = None
                            
                            # Search for the doctor across all departments
                            for dept_name, doctors in department_doctors.items():
                                for doctor in doctors:
                                    if doctor_name.lower() in doctor['name'].lower():
                                        selected_doctor = doctor
                                        found_department = dept_name
                                        break
                                if selected_doctor:
                                    break
                            
                            if selected_doctor:
                                # Convert available dates to readable format
                                readable_dates = []
                                for date_str in selected_doctor['available_dates']:
                                    try:
                                        dt = datetime.strptime(date_str, '%Y-%m-%d')
                                        readable_dates.append(dt.strftime('%B %d, %Y'))
                                    except:
                                        readable_dates.append(date_str)
                                available_dates_str = "\n".join([f"• {date}" for date in readable_dates])
                                tool_result = [f"Dr. {selected_doctor['name']} is available on:\n\n{available_dates_str}\n\nWhat is your preferred date for the appointment?"]
                            else:
                                tool_result = [f"Doctor {doctor_name} not found. Please select from the available doctors."]
                        
                        else:
                            tool_result = ["Appointment action completed successfully."]
                    else:
                        if action_type == "schedule":
                            # Scheduling is exempt from strict authentication; do not return an invalid-credentials message here.
                            tool_result = []
                        else:
                            tool_result = ["Invalid patient credentials. Please verify your Name and Phone Number."]
                
                
                elif tool_name == 'patient_records':
                    # Simulate patient records access
                    name = tool_input.get("name", "")
                    phone = tool_input.get("phone", "")
                    record_type = tool_input.get("record_type", "all")
                    
                    # Validate phone number format - must be exactly 8 digits
                    is_valid_phone, phone_result = validate_phone_number(phone)
                    if not is_valid_phone:
                        tool_result = [phone_result]
                        tool_response_dict = {
                            "type": "tool_result",
                            "tool_use_id": response_item['id'],
                            "content": [{"type": "text", "text": phone_result}]
                        }
                        tool_results.append(tool_response_dict)
                        continue  # Skip further processing if phone is invalid
                    
                    # Use the cleaned phone number (digits only)
                    phone = phone_result
                    
                    # Validate patient credentials
                    valid_patients = {p['name']: p['phone'] for p in patients.values()}
                    if name in valid_patients and valid_patients[name] == phone:
                        # Patient-specific medical records
                        patient_records = {
                            "PAT1001": {  # John Smith, 39 years old
                                "name": "John Smith",
                                "age": 39,
                                "recent_visits": [
                                    "Cardiology consultation (2024-01-15) - Chest pain evaluation",
                                    "General checkup (2023-12-10) - Annual physical",
                                    "Lab tests (2023-11-20) - Cholesterol and blood sugar screening"
                                ],
                                "medications": [
                                    "Lisinopril 10mg daily - Blood pressure management",
                                    "Metformin 500mg twice daily - Diabetes management",
                                    "Atorvastatin 20mg daily - Cholesterol control"
                                ],
                                "allergies": ["Penicillin", "Shellfish"],
                                "conditions": ["Hypertension", "Type 2 Diabetes", "High Cholesterol"],
                                "next_appointment": "Cardiology follow-up (2025-10-15)"
                            },
                            "PAT1002": {  # Sarah Johnson, 34 years old
                                "name": "Sarah Johnson",
                                "age": 34,
                                "recent_visits": [
                                    "Dermatology consultation (2024-02-10) - Skin check",
                                    "Gynecology exam (2024-01-20) - Annual screening",
                                    "Lab tests (2024-01-15) - Routine blood work"
                                ],
                                "medications": [
                                    "Prenatal vitamins daily - Pregnancy support",
                                    "Folic acid 400mcg daily - Pregnancy preparation"
                                ],
                                "allergies": ["Latex", "Iodine contrast"],
                                "conditions": ["Pregnancy (12 weeks)", "Mild anemia"],
                                "next_appointment": "Obstetrics follow-up (2025-11-20)"
                            },
                            "PAT1003": {  # Michael Brown, 46 years old
                                "name": "Michael Brown",
                                "age": 46,
                                "recent_visits": [
                                    "Orthopedics consultation (2024-02-05) - Knee pain evaluation",
                                    "Physical therapy (2024-01-25) - Post-surgery rehabilitation",
                                    "Surgery follow-up (2024-01-10) - ACL reconstruction"
                                ],
                                "medications": [
                                    "Ibuprofen 400mg as needed - Pain management",
                                    "Acetaminophen 500mg as needed - Pain relief"
                                ],
                                "allergies": ["Morphine", "Codeine"],
                                "conditions": ["ACL tear (post-surgery)", "Osteoarthritis"],
                                "next_appointment": "Physical therapy session (2025-10-30)"
                            },
                            "PAT1004": {  # Emily Davis, 32 years old
                                "name": "Emily Davis",
                                "age": 32,
                                "recent_visits": [
                                    "Psychology consultation (2024-02-12) - Anxiety management",
                                    "Primary care visit (2024-01-30) - General health check",
                                    "Lab tests (2024-01-25) - Thyroid function test"
                                ],
                                "medications": [
                                    "Sertraline 50mg daily - Anxiety and depression",
                                    "Lorazepam 0.5mg as needed - Anxiety relief"
                                ],
                                "allergies": ["Sulfa drugs", "Aspirin"],
                                "conditions": ["Generalized Anxiety Disorder", "Mild Depression", "Hypothyroidism"],
                                "next_appointment": "Psychology follow-up (2025-10-05)"
                            },
                            "PAT1005": {  # David Wilson, 41 years old
                                "name": "David Wilson",
                                "age": 41,
                                "recent_visits": [
                                    "Neurology consultation (2024-02-08) - Migraine evaluation",
                                    "Emergency visit (2024-01-18) - Severe headache episode",
                                    "MRI scan (2024-01-20) - Brain imaging"
                                ],
                                "medications": [
                                    "Sumatriptan 50mg as needed - Migraine treatment",
                                    "Propranolol 40mg twice daily - Migraine prevention",
                                    "Magnesium 400mg daily - Migraine support"
                                ],
                                "allergies": ["NSAIDs", "Contrast dye"],
                                "conditions": ["Chronic Migraine", "Tension Headaches"],
                                "next_appointment": "Neurology follow-up (2025-11-15)"
                            }
                        }
                        # Map Name and Phone to patient key
                        patient_key = None
                        for k, v in patients.items():
                            if v['name'].lower() == name.lower() and v['phone'].replace(' ', '') == phone.replace(' ', ''):
                                patient_key = k
                                break
                        if patient_key and patient_key in patient_records:
                            patient = patient_records[patient_key]
                            recent_visits = "\n".join([f"- {visit}" for visit in patient["recent_visits"]])
                            medications = "\n".join([f"- {med}" for med in patient["medications"]])
                            allergies = "\n".join([f"- {allergy}" for allergy in patient["allergies"]])
                            conditions = "\n".join([f"- {condition}" for condition in patient["conditions"]])
                            tool_result = [f"Patient Records for {patient_key} ({patient['name']}, Age {patient['age']}):\n\nRecent Visits:\n{recent_visits}\n\nCurrent Medications:\n{medications}\n\nMedical Conditions:\n{conditions}\n\nAllergies:\n{allergies}\n\nNext Appointment:\n- {patient['next_appointment']}"]
                        else:
                            tool_result = ["Patient records not found. Please contact the hospital directly."]
                    else:
                        tool_result = ["Invalid patient credentials. Please verify your Name and Phone Number."]
                
                elif tool_name == 'medication_tracker':
                    # Simulate medication tracking
                    name = tool_input.get("name", "")
                    phone = tool_input.get("phone", "")
                    action_type = tool_input.get("action", "get_medications")
                    
                    # Validate phone number format - must be exactly 8 digits
                    is_valid_phone, phone_result = validate_phone_number(phone)
                    if not is_valid_phone:
                        tool_result = [phone_result]
                        tool_response_dict = {
                            "type": "tool_result",
                            "tool_use_id": response_item['id'],
                            "content": [{"type": "text", "text": phone_result}]
                        }
                        tool_results.append(tool_response_dict)
                        continue  # Skip further processing if phone is invalid
                    
                    # Use the cleaned phone number (digits only)
                    phone = phone_result
                    
                    # Validate patient credentials
                    valid_patients = {p['name']: p['phone'] for p in patients.values()}
                    if name in valid_patients and valid_patients[name] == phone:
                        # Patient-specific medication information
                        patient_medications = {
                            "PAT1001": {  # John Smith
                                "medications": [
                                    "Lisinopril 10mg - Take once daily in the morning for blood pressure",
                                    "Metformin 500mg - Take twice daily with meals for diabetes",
                                    "Atorvastatin 20mg - Take once daily in the evening for cholesterol"
                                ],
                                "refill_dates": [
                                    "Lisinopril: 2025-10-20",
                                    "Metformin: 2025-10-18", 
                                    "Atorvastatin: 2025-10-22"
                                ]
                            },
                            "PAT1002": {  # Sarah Johnson
                                "medications": [
                                    "Prenatal vitamins - Take once daily with breakfast",
                                    "Folic acid 400mcg - Take once daily for pregnancy support"
                                ],
                                "refill_dates": [
                                    "Prenatal vitamins: 2025-11-15",
                                    "Folic acid: 2025-11-10"
                                ]
                            },
                            "PAT1003": {  # Michael Brown
                                "medications": [
                                    "Ibuprofen 400mg - Take as needed for pain (max 3 times daily)",
                                    "Acetaminophen 500mg - Take as needed for pain relief"
                                ],
                                "refill_dates": [
                                    "Ibuprofen: 2025-10-25",
                                    "Acetaminophen: 2025-10-28"
                                ]
                            },
                            "PAT1004": {  # Emily Davis
                                "medications": [
                                    "Sertraline 50mg - Take once daily in the morning for anxiety",
                                    "Lorazepam 0.5mg - Take as needed for anxiety relief (max 2 times daily)"
                                ],
                                "refill_dates": [
                                    "Sertraline: 2025-11-05",
                                    "Lorazepam: 2025-10-20"
                                ]
                            },
                            "PAT1005": {  # David Wilson
                                "medications": [
                                    "Sumatriptan 50mg - Take as needed for migraine treatment",
                                    "Propranolol 40mg - Take twice daily for migraine prevention",
                                    "Magnesium 400mg - Take once daily for migraine support"
                                ],
                                "refill_dates": [
                                    "Sumatriptan: 2025-11-01",
                                    "Propranolol: 2025-10-25",
                                    "Magnesium: 2025-11-10"
                                ]
                            }
                        }
                        # Map Name and Phone to patient key
                        patient_key = None
                        for k, v in patients.items():
                            if v['name'].lower() == name.lower() and v['phone'].replace(' ', '') == phone.replace(' ', ''):
                                patient_key = k
                                break
                        if action_type == "get_medications":
                            if patient_key and patient_key in patient_medications:
                                patient_meds = patient_medications[patient_key]
                                med_list = "\n".join([f"{i+1}. {med}" for i, med in enumerate(patient_meds["medications"])] )
                                refill_list = "\n".join([f"- {refill}" for refill in patient_meds["refill_dates"]])
                                tool_result = [f"Current Medications:\n\n{med_list}\n\nNext refill dates:\n{refill_list}"]
                            else:
                                tool_result = ["No medications found for this patient."]
                        elif action_type == "add_medication":
                            medication_name = tool_input.get("medication_name", "")
                            dosage = tool_input.get("dosage", "")
                            schedule = tool_input.get("schedule", "")
                            tool_result = [f"Medication added successfully: {medication_name} {dosage} - {schedule}"]
                        elif action_type == "update_medication":
                            medication_name = tool_input.get("medication_name", "")
                            dosage = tool_input.get("dosage", "")
                            schedule = tool_input.get("schedule", "")
                            tool_result = [f"Medication updated successfully: {medication_name} {dosage} - {schedule}"]
                        elif action_type == "remove_medication":
                            medication_name = tool_input.get("medication_name", "")
                            tool_result = [f"Medication {medication_name} has been removed from your list."]
                        else:
                            tool_result = ["Medication action completed successfully."]
                    else:
                        tool_result = ["Invalid patient credentials. Please verify your Name and Phone Number."]
                
                elif tool_name == 'emergency_response':
                    # Handle emergency situations
                    emergency_type = tool_input.get("emergency_type", "")
                    severity = tool_input.get("severity", "")
                    description = tool_input.get("description", "")
                    location = tool_input.get("location", "")
                    
                    if severity in ["high", "critical"]:
                        tool_result = [f"EMERGENCY ALERT: {severity.upper()} {emergency_type} emergency reported. Description: {description}. Location: {location}. Emergency services have been notified. Please call 911 immediately if this is a life-threatening emergency."]
                    else:
                        tool_result = [f"Emergency situation logged: {emergency_type} - {severity} severity. Description: {description}. Location: {location}. Please proceed to the emergency department or call our emergency line."]
                
                elif tool_name == 'symptom_checker':
                    # Provide symptom analysis
                    symptoms = tool_input.get("symptoms", "")
                    duration = tool_input.get("duration", "")
                    severity = tool_input.get("severity", "")
                    additional_info = tool_input.get("additional_info", "")
                    
                    tool_result = [f"Based on your symptoms: {symptoms}\nDuration: {duration}\nSeverity: {severity}\n\nPreliminary Assessment:\nThis appears to be a {severity} condition that has been present for {duration}. Based on the symptoms described, I recommend:\n\n1. Monitor your symptoms closely\n2. Rest and stay hydrated\n3. If symptoms worsen or persist, please schedule an appointment with your doctor\n4. For severe symptoms, consider visiting the emergency department\n\nNote: This is preliminary guidance only. Please consult with a healthcare professional for proper diagnosis and treatment."]
                
                else:
                    # Unknown tool
                    tool_result = ["I'm here to help with your hospital needs. How can I assist you today?"]
                
                # Create tool result message
                try:
                    print(f"Tool result type: {type(tool_result)}")
                    print(f"Tool result content: {tool_result}")
                    
                    tool_response_dict = {
                        "type": "tool_result",
                        "tool_use_id": response_item['id'],  # Use response_item, not action
                        "content": [{"type": "text", "text": "\n".join(tool_result) if isinstance(tool_result, list) else str(tool_result)}]
                    }
                    tool_results.append(tool_response_dict)
                    print(f"Tool response created successfully")
                    
                except Exception as e:
                    print(f"Error creating tool response: {e}")
                    print(f"Response item type: {type(response_item)}")
                    print(f"Response item content: {response_item}")
                    # Create a fallback tool result
                    tool_response_dict = {
                        "type": "tool_result", 
                        "tool_use_id": response_item.get('id', 'unknown'),
                        "content": [{"type": "text", "text": "Error processing tool request"}]
                    }
                    tool_results.append(tool_response_dict)
        
        # Validate tool_results before making second API call
        if tools_used and tool_results:
            print(f"Tool results to send: {tool_results}")
            # Add tool results to chat history
            chat_history.append({'role': 'user', 'content': tool_results})
            
            # Make second API call with tool results
            try:
                response = bedrock_client.invoke_model_with_response_stream(
                    contentType='application/json',
                    body=json.dumps({
                        "anthropic_version": "bedrock-2023-05-31",
                        "max_tokens": 4000,
                        "temperature": 0,
                        "system": prompt,
                        "tools": hospital_tools,
                        "messages": chat_history
                    }),
                    modelId="us.anthropic.claude-3-7-sonnet-20250219-v1:0"
                )
            except Exception as e:
                print("ERROR IN SECOND API CALL:", e)
                # Send error response via WebSocket
                error_response = "I apologize, but I'm having trouble accessing that information right now. Please try again in a moment."
                for word in error_response.split():
                    delta = {'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'text_delta', 'text': word + ' '}}
                    try:
                        api_gateway_client.post_to_connection(ConnectionId=connectionId, Data=json.dumps(delta))
                    except:
                        pass
                stop_answer = {'type': 'content_block_stop', 'index': 0}
                try:
                    api_gateway_client.post_to_connection(ConnectionId=connectionId, Data=json.dumps(stop_answer))
                except:
                    pass
                return {"answer": error_response, "question": chat, "session_id": session_id, "input_tokens": str(input_tokens), "output_tokens": str(output_tokens)}
            
            # Process second response
            final_response = ""
            for item in response['body']:
                content = json.loads(item['chunk']['bytes'].decode())
                if content['type'] == 'content_block_delta':
                    try:
                        api_gateway_client.post_to_connection(ConnectionId=connectionId, Data=json.dumps(content))
                    except api_gateway_client.exceptions.GoneException:
                        print(f"Connection {connectionId} is closed (GoneException) - stop message (tool)")
                    except Exception as e:
                        print(f"WebSocket send error (stop): {e}")
                    if 'delta' in content and isinstance(content['delta'], dict):
                        if content['delta']['type'] == 'text_delta':
                            final_response += content['delta']['text']
                elif content['type'] == 'content_block_stop':
                    try:
                        api_gateway_client.post_to_connection(ConnectionId=connectionId, Data=json.dumps(content))
                    except api_gateway_client.exceptions.GoneException:
                        print(f"Connection {connectionId} is closed (GoneException) - delta message (tool)")
                    except Exception as e:
                        print(f"WebSocket send error (delta): {e}")
                elif content['type'] == 'message_stop':
                    input_tokens += content['amazon-bedrock-invocationMetrics']['inputTokenCount']
                    output_tokens += content['amazon-bedrock-invocationMetrics']['outputTokenCount']
            
            return {"answer": final_response, "question": chat, "session_id": session_id, "input_tokens": str(input_tokens), "output_tokens": str(output_tokens)}
        else:
            print("No valid tool results to process")
            # Handle the case where no tools were successfully processed
        
        # If no tools were used, return the direct response
        if assistant_response and assistant_response[0]['type'] == 'text':
            return {"answer": assistant_response[0]['text'], "question": chat, "session_id": session_id, "input_tokens": str(input_tokens), "output_tokens": str(output_tokens)}
        
        # Fallback response
        return {"answer": "I'm here to help with your hospital needs. How can I assist you today?", "question": chat, "session_id": session_id, "input_tokens": str(input_tokens), "output_tokens": str(output_tokens)}
    except Exception as e:
        print(f"Unexpected error: {e}")
        response = "An Unknown error occurred. Please try again after some time."
        return {
            "statusCode": "500",
            "answer": response,
            "question": chat,
            "session_id": session_id,
            "input_tokens": "0",
            "output_tokens": "0"
        }