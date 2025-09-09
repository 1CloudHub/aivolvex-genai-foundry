import json 
import os
import psycopg2
import boto3  
import time
import secrets
import string
from datetime import *
import uuid
import re   
import threading
import sys
import requests
import base64
from opensearchpy import OpenSearch, RequestsHttpConnection
from requests_aws4auth import AWS4Auth
from botocore.config import Config
from time import sleep
from botocore.exceptions import ClientError, BotoCoreError
# gateway_url = os.environ['gateway_url']

# Get database credentials
db_user = os.environ['db_user']
db_host = os.environ['db_host']                         
db_port = os.environ['db_port']
db_database = os.environ['db_database']
region_used = os.environ["region_used"]

# Get new environment variables for voice operations
region_name = os.environ.get("region_name", region_used)  # Use region_used as fallback
ec2_instance_ip = os.environ.get("ec2_instance_ip", "")  # Elastic IP of the T3 medium instance

# OpenSearch configuration
OPENSEARCH_REGION = os.environ.get("OPENSEARCH_REGION", "us-west-2")
OPENSEARCH_HOST = os.environ.get("OPENSEARCH_HOST", "of7eg8ly1gkaw3uv9527.us-west-2.aoss.amazonaws.com").replace("https://", "").replace("http://", "")
OPENSEARCH_INDEX = os.environ.get("OPENSEARCH_INDEX", "visualproductsearchmod")

# Bedrock model configuration
LLAMA3_MODEL_ID = os.environ.get("LLAMA3_MODEL_ID", "us.meta.llama3-3-70b-instruct-v1:0")
NOVA_MODEL_ID = os.environ.get("NOVA_MODEL_ID", "amazon.nova-canvas-v1:0")
NOVA_REEL_MODEL_ID = os.environ.get("NOVA_REEL_MODEL_ID", "amazon.nova-reel-v1:1")
CLAUDE_MODEL_ID = os.environ.get("CLAUDE_MODEL_ID", "us.anthropic.claude-3-7-sonnet-20250219-v1:0")

# S3 configuration
S3_BUCKET = os.environ.get("S3_BUCKET", "genaifoundryc-y2t1oh")
S3_REGION = os.environ.get("S3_REGION", "us-west-2")

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
CHAT_LOG_TABLE = os.environ['CHAT_LOG_TABLE']   
socket_endpoint = os.environ["socket_endpoint"]
RETAIL_KB_ID=os.environ["RETAIL_KB_ID"]

retail_chat_history_table=os.environ['retail_chat_history_table']
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

#flags
user_intent_flag = False
overall_flow_flag = False
pop = ""
ub_user_name = "none"
ub_number = "none"
str_intent = "false"


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

def lambda_handler(event, context):
    global user_intent_flag, overall_flow_flag, ub_number, ub_user_name, pop, str_intent,json
    print("Event: ",event)
    event_type=event['event_type']
    print("Event_type: ",event_type)
    conv_id = ""
    
    # OpenSearch Visual Product Search Functions (defined inside lambda_handler)
    def create_opensearch_client():
        """Create and return OpenSearch client with AWS authentication"""
        region = OPENSEARCH_REGION
        HOST = OPENSEARCH_HOST
        INDEX_NAME = OPENSEARCH_INDEX
        
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
                    "exclude": ["vsp"]  # Exclude vector field from response
                },
                "query": {
                    "knn": {
                        "vsp": {
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
                    "exclude": ["vsp"]  # Exclude vector field from response
                },
                "query": {
                    "bool": {
                        "must": {
                            "knn": {
                                "vsp": {
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
                        
                        # Extract bucket and key from S3 URI
                        if image_s3_uri.startswith('s3://'):
                            # Remove 's3://' and split by '/'
                            path_parts = image_s3_uri[5:].split('/', 1)
                            if len(path_parts) == 2:
                                bucket_name = path_parts[0]
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


#retail event type starts here...

    if event_type == 'visual_product_search':
        return visual_product_search_api(event)
        
    if event_type == 'kyc_extraction':
        return kyc_extraction_api(event)
   
        
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
            

            LLAMA_REGION = region_used
            
            def enhance_prompt_function(simple_prompt, model_id):
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
                        modelId=model_id,
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

                except json.JSONDecodeError as e:
                    print(f"LLM response was not valid JSON. Error: {e}")
                    print(f"Raw output: {generation}")
                    return None, None
                except (ClientError, Exception) as e:
                    print(f"ERROR: Could not invoke Llama 3 model. Reason: {e}")
                    return None, None
            
            print(f"Enhancing prompt: '{simple_prompt}'")
            print(f"Using model: {LLAMA3_MODEL_ID}")
            print(f"Using region: {LLAMA_REGION}")
            
            text_prompt, negative_prompt = enhance_prompt_function(simple_prompt, LLAMA3_MODEL_ID)
            
            print(f"Enhanced text prompt: {text_prompt}")
            print(f"Enhanced negative prompt: {negative_prompt}")
            
            if not text_prompt or not negative_prompt:
                print("ERROR: Failed to get valid prompts from LLM")
                return {
                    "statusCode": 500,
                    "message": "Failed to enhance prompt - LLM did not return valid prompts"
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
            import traceback
            print(f"Traceback: {traceback.format_exc()}")
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
            NOVA_REGION = region_used
            
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
        
  

    if event_type == 'virtual_tryon':
        try:
            # Import required modules
            import boto3
            import json
            import base64
            from botocore.config import Config
            from botocore.exceptions import ClientError
            
            # AWS Configuration
            AWS_REGION = region_used
            
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
                    
                    # Parse S3 URI
                    if s3_uri.startswith('s3://'):
                        s3_uri = s3_uri[5:]  # Remove 's3://' prefix
                    
                    bucket_name, key = s3_uri.split('/', 1)
                    
                    # Create S3 client
                    s3_client = boto3.client(
                        's3',
                        region_name=AWS_REGION
                    )
                    
                    # Download image from S3
                    response = s3_client.get_object(Bucket=bucket_name, Key=key)
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
    if event_type == 'genai_product_desc':
        return describe_image(event)
#retail event type ends here....
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
        s3_region = S3_REGION
        model_id = NOVA_REEL_MODEL_ID
        bucket = S3_BUCKET
        prefix = f"videos/{session_id}"
        s3_uri = f"s3://{bucket}/{prefix}/"
        s3_key = f"{prefix}/output.mp4"

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
        model_id = NOVA_REEL_MODEL_ID
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

    bedrock = boto3.client("bedrock-runtime", region_name=region_used)
    body = json.dumps({
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 2048,
        "messages": [{"role": "user", "content": prompt}]
    })

    response = bedrock.invoke_model(
        modelId=CLAUDE_MODEL_ID,
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
                        "order_id": {"type": "string", "description": "Order reference number (e.g., ORD789012)"}
                    },
                    "required": ["order_id"]
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
        def get_order_status(order_id):
            # No authentication required for order status
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
                    tool_result = get_order_status(tool_input['order_id'])
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




    