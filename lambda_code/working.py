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
HR_KBID = os.environ["hr_kb_id"]
PRODUCT_KBID = os.environ["product_kb_id"]
bank_kb_id=os.environ["bank_kb_id"]
RETAIL_KB_ID=os.environ["RETAIL_KB_ID"]
banking_chat_history_table=os.environ['banking_chat_history_table']
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
            password=os.environ['db_password'],
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
        base_prompt = select_db(select_query)[0][0]
        
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
        base_prompt = select_db(select_query)[0][0]
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
You are a financial risk assessment engine for ValueMax, a licensed pawnshop-style lender in Singapore. ValueMax provides short-term secured loans against pledged assets such as gold, cars, watches, and designer bags.

Your role is to evaluate the risk of lending based on the provided applicant, financial, loan, and collateral data, along with optional free-text comments from the agent. Use this to generate a clear, consistent, and structured response for the agent to make a lending decision.

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

def lambda_handler(event, context):
    global user_intent_flag, overall_flow_flag, ub_number, ub_user_name, pop, str_intent,json
    print("Event: ",event)
    event_type=event['event_type']
    print("Event_type: ",event_type)
    conv_id = ""
    
    # OpenSearch Visual Product Search Functions (defined inside lambda_handler)
    def create_opensearch_client():
        """Create and return OpenSearch client with AWS authentication"""
        region = "us-east-1"
        HOST = "533c5s545r19b5fkiyue.us-east-1.aoss.amazonaws.com"
        INDEX_NAME = "visualproductsearch"
        
        # AWS Credentials - Use IAM role (Lambda will automatically use its role)
        # No need for explicit credentials when using Lambda with IAM roles
        auth = AWS4Auth(
            '',  # Empty for IAM role authentication
            '',  # Empty for IAM role authentication
            region,
            'aoss'
        )

        client = OpenSearch(
            hosts=[{'host': HOST, 'port': 443}],
            http_auth=auth,
            use_ssl=True,
            verify_certs=True,
            connection_class=RequestsHttpConnection,
            pool_maxsize=300
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
        """Create image embedding using Bedrock Titan"""
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
            
            # Use the same pattern as ROXA_Search_Lambda.py
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
            print(f"Error creating image embedding: {e}")
            
            # Provide more specific error messages
            if "ValidationException" in str(e):
                if "Unable to process provided image" in str(e):
                    print("❌ The image format or size is not supported by Bedrock")
                    print("💡 Try using a smaller image (max 5MB) in JPEG or PNG format")
                else:
                    print("❌ Validation error with the image data")
            elif "AccessDeniedException" in str(e):
                print("❌ Access denied to Bedrock model")
            elif "ThrottlingException" in str(e):
                print("❌ Rate limit exceeded, try again later")
            else:
                print("❌ Unknown error occurred")
                
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
                    "bool": {
                        "filter": [
                            {"term": {"type": "text"}}
                        ],
                        "should": [
                            {
                                "knn": {
                                    "vsp": {
                                        "vector": search_vector,
                                        "k": limit
                                    }
                                }
                            }
                        ]
                    }
                },
                "fields": ["product_description", "s3_uri", "type"]
            }
            
            print("Searching OpenSearch for text query...")
            response = client.search(index="visualproductsearch", body=body)
            
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

    def search_products_image_opensearch(image_base64, limit=5):
        """Search products using image query in OpenSearch"""
        try:
            client = create_opensearch_client()
            
            # Create image embedding
            search_vector = create_image_embedding(image_base64)
            if search_vector is None:
                print("Error creating image embedding")
                return []
            
            # Build search query for image search - similar to ROXA_Search_Lambda.py
            body = {
                "size": limit,
                "_source": {
                    "exclude": ["vsp"]  # Exclude vector field from response
                },
                "query": {
                    "bool": {
                        "must": {
                            "knn": {
                                "vsp": {
                                    "vector": search_vector,
                                    "k": 20
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
                "fields": ["product_description", "s3_uri", "type"]
            }
            
            print("Searching OpenSearch for image query...")
            response = client.search(index="visualproductsearch", body=body)
            
            results = []
            for hit in response['hits']['hits']:
                score = hit['_score']
                source = hit['_source']
                
                # Skip low similarity scores for image search
                if score < 0.005:  # Adjusted threshold
                    continue
                
                results.append({
                    "score": score,
                    "product_description": source['product_description'],
                    "s3_uri": source['s3_uri'],
                    "type": source['type']
                })
            
            return results
            
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
        """API for visual product search using OpenSearch"""
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
                        
                        # Try to validate image format
                        try:
                            from PIL import Image
                            import io
                            img = Image.open(io.BytesIO(decoded))
                            print(f"✅ Valid image format: {img.format}, size: {img.size}")
                            
                            # Resize if too large (max 1024x1024)
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
                            print("⚠️ PIL not available, skipping image validation")
                        except Exception as e:
                            print(f"⚠️ Image validation error: {e}")
                            
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
                results = search_products_image_opensearch(image_base64, limit=5)
                
                # Validate search results using LLM for image search
                if results:
                    print("🔍 Validating image search results with LLM...")
                    # For image search, we'll use a generic search query since we don't have text input
                    validation_result = validate_search_results_with_llm("image search", results)
                    
                    if validation_result.get('is_valid', False):
                        print(f"✅ LLM validation passed: {validation_result.get('matched_category')}")
                        response_text = f"Found {len(results)} similar products:\n\n"
                        for i, result in enumerate(results, 1):
                            response_text += f"{i}. Score: {result['score']:.4f}\n"
                            response_text += f"   Description: {result['product_description'][:100]}...\n"
                            response_text += f"   S3 URI: {result['s3_uri']}\n\n"
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
        prompt_response = select_db(prompt_query)
        prompt_template = prompt_response[0][0]
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
        prompt_response = select_db(prompt_query)
        prompt_template = prompt_response[0][0]
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





    