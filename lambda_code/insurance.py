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
CHAT_LOG_TABLE = os.environ['CHAT_LOG_TABLE']   
socket_endpoint = os.environ["socket_endpoint"]
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




def lambda_handler(event, context):
    global user_intent_flag, overall_flow_flag, ub_number, ub_user_name, pop, str_intent,json
    print("Event: ",event)
    event_type=event['event_type']
    print("Event_type: ",event_type)
    conv_id = ""

    if event_type == 'mediplus_assess':
        return generate_mediplus_assessment(event)
    elif event_type == 'lifesecure_assess':
        return generate_lifesecure_assessment(event)
    
    elif event_type == 'chat_tool':  
       
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
    
