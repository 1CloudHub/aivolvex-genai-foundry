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
chat_tool_model = os.environ.get("chat_tool_model", "claude").lower()
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

def nova_agent_invoke_tool(chat_history, session_id, chat, connectionId):
    """
    Nova model agent invoke tool function using AWS Bedrock Converse API.
    Uses the same tools and logic as agent_invoke_tool but adapted for Nova Converse API.
    """
    try:
        # Start keepalive thread
        keepalive_thread = send_keepalive(connectionId, 30)
        import uuid
        import random
        import re
        
        # Fetch base_prompt from the database (same as agent_invoke_tool)
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
2. Policy ID (from user's active policies) - ALWAYS use get_user_policies tool first to show available policies, then ask which policy to use
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
	
	"yesterday", "today", "tomorrow", "last night", etc.
	
- Convert these into actual calendar dates based on the current date.
	
- If a time of day is mentioned (e.g., "yesterday evening"), assign a random time in that time range:
	
	Morning: 8am–12pm
	
	Afternoon: 1pm–5pm
	
	Evening: 6pm–9pm
	
	Night: 9pm–11pm
	
- Examples:
	
	"yesterday" → 2025-07-30
	
	"today afternoon" → 2025-07-31, 2:34 PM (randomized)
	
	"tomorrow morning" → 2025-08-01, 9:12 AM (randomized)

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
Assistant: [Use get_user_policies tool and display policies directly]
You have two active policies:
POL1001: MediPlus Secure (Health) - Coverage: SGD 150,000/year
POL1002: LifeSecure Term Advantage (Life) - Coverage: SGD 500,000
Which policy ID would you like to use for your claim?

User: "POL1001"
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

## POLICY DISPLAY RULES FOR CLAIMS:
- When a user wants to file a claim and provides their CRN, IMMEDIATELY use the get_user_policies tool to retrieve and display their active policies
- Do NOT say "Let me check your active policies first" or similar phrases
- Directly display the policy details in a clear format showing Policy ID, Plan Name, Policy Type, and Coverage Amount
- After displaying the policies, ask "Which policy ID would you like to use for your claim?"
- NEVER mention checking, looking up, or finding information - just display the policies directly

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
        
        # Insurance tool schema - converted to Nova's toolSpec format
        insurance_tools_nova = [
            {
                "toolSpec": {
                    "name": "get_user_policies",
                    "description": "Retrieve active insurance policies for a customer based on their CRN",
                    "inputSchema": {
                        "json": {
                            "type": "object",
                            "properties": {
                                "crn": {"type": "string", "description": "Customer Reference Number (e.g., CUST1001)"}
                            },
                            "required": ["crn"]
                        }
                    }
                }
            },
            {
                "toolSpec": {
                    "name": "track_claim_status",
                    "description": "Check the status of insurance claims for a customer",
                    "inputSchema": {
                        "json": {
                            "type": "object",
                            "properties": {
                                "crn": {"type": "string", "description": "Customer Reference Number (e.g., CUST1001)"}
                            },
                            "required": ["crn"]
                        }
                    }
                }
            },
            {
                "toolSpec": {
                    "name": "file_claim",
                    "description": "Submit a new insurance claim",
                    "inputSchema": {
                        "json": {
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
                    }
                }
            },
            {
                "toolSpec": {
                    "name": "schedule_agent_callback",
                    "description": "Schedule a callback from a human insurance agent",
                    "inputSchema": {
                        "json": {
                            "type": "object",
                            "properties": {
                                "crn": {"type": "string", "description": "Customer Reference Number"},
                                "reason": {"type": "string", "description": "Reason for callback request"},
                                "preferred_timeslot": {"type": "string", "description": "Preferred time slot (e.g., '2-4pm')"},
                                "preferred_contact_method": {"type": "string", "description": "Preferred contact method (phone or email)"}
                            },
                            "required": ["crn", "reason", "preferred_timeslot", "preferred_contact_method"]
                        }
                    }
                }
            },
            {
                "toolSpec": {
                    "name": "faq_tool_schema",
                    "description": "Retrieve answers from the insurance knowledge base",
                    "inputSchema": {
                        "json": {
                            "type": "object",
                            "properties": {
                                "knowledge_base_retrieval_question": {"type": "string", "description": "A question to retrieve from the insurance knowledge base."}
                            },
                            "required": ["knowledge_base_retrieval_question"]
                        }
                    }
                }
            }
        ]

        # --- Mock tool implementations (same as agent_invoke_tool) ---
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
        print("In nova_agent_invoke_tool (Insurance Bot - Nova)")
        
        # Extract CRN from chat history (same as agent_invoke_tool)
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
        
        # Convert chat history format for Nova Converse API
        message_history = []
        for msg in chat_history:
            if msg['role'] in ['user', 'assistant']:
                content_items = msg.get('content', [])
                text_content = None
                
                if isinstance(content_items, list) and len(content_items) > 0:
                    for content_item in content_items:
                        if isinstance(content_item, dict):
                            if 'type' in content_item and content_item['type'] == 'text':
                                text_content = content_item.get('text', '')
                                break
                            elif 'text' in content_item:
                                text_content = content_item['text']
                                break
                
                if text_content and text_content.strip():
                    message_history.append({
                        'role': msg['role'],
                        'content': [{'text': text_content.strip()}]
                    })
        
        print("Nova Model - Chat History: ", message_history)
        
        # Nova model configuration
        nova_model_name = os.environ.get("nova_model_name", "us.amazon.nova-pro-v1:0")
        nova_region = os.environ.get("region_used", region_used)
        nova_bedrock_client = boto3.client("bedrock-runtime", region_name=nova_region)
        
        # First API call to get initial response
        try:
            response = nova_bedrock_client.converse(
                modelId=nova_model_name,
                messages=message_history,
                system=[{"text": enhanced_prompt}],
                inferenceConfig={
                    "temperature": 0,
                    "topP": 0.9
                },
                toolConfig={
                    "tools": insurance_tools_nova
                }
            )
            
            print("Nova Model Response: ", response)
            
            # Parse the response
            assistant_response = []
            output_msg = (response.get('output') or {}).get('message') or {}
            content_items = output_msg.get('content') or []
            
            for item in content_items:
                if 'text' in item:
                    # Filter out thinking tags from Nova responses (same behavior as Claude)
                    text_content = item['text']
                    # Remove thinking tags if present
                    text_content = re.sub(r'<thinking>.*?</thinking>', '', text_content, flags=re.DOTALL)
                    text_content = text_content.strip()
                    # Only add non-empty text (after removing thinking tags)
                    if text_content:
                        assistant_response.append({"text": text_content})
                elif 'toolUse' in item:
                    tu = item['toolUse'] or {}
                    assistant_response.append({
                        "toolUse": {
                            "name": tu.get('name'),
                            "toolUseId": tu.get('toolUseId'),
                            "input": tu.get('input', {})
                        }
                    })
            
            usage = response.get('usage') or {}
            input_tokens = usage.get('inputTokens', 0)
            output_tokens = usage.get('outputTokens', 0)
            
            # Append assistant response to chat history
            message_history.append({'role': 'assistant', 'content': assistant_response})
            
            # Check if any tools were called
            tool_calls = [a for a in assistant_response if 'toolUse' in a]
            print("Nova Tool calls: ", tool_calls)
            
            if tool_calls:
                # Process all tool calls (same logic as agent_invoke_tool)
                tools_used = []
                tool_results = []
                
                for tool_call_item in tool_calls:
                    tool_call = tool_call_item['toolUse']
                    tool_name = tool_call.get('name')
                    tool_input = tool_call.get('input', {})
                    tool_use_id = tool_call.get('toolUseId')
                    tool_result = None
                    
                    tools_used.append(tool_name)
                    
                    # Send a heartbeat to keep WebSocket alive during tool execution (same as agent_invoke_tool)
                    try:
                        heartbeat = {'type': 'heartbeat'}
                        api_gateway_client.post_to_connection(ConnectionId=connectionId, Data=json.dumps(heartbeat))
                    except Exception as e:
                        print(f"Heartbeat send error: {e}")
                    
                    # Execute the appropriate tool (same logic as agent_invoke_tool)
                    if tool_name == 'get_user_policies':
                        # Use CRN from tool_input or fallback to extracted CRN
                        crn = tool_input.get('crn') or extracted_crn
                        if not crn:
                            tool_result = ["Error: Customer Reference Number (CRN) is required. Please provide your CRN."]
                        else:
                            tool_result = get_user_policies(crn)
                    elif tool_name == 'track_claim_status':
                        # Use CRN from tool_input or fallback to extracted CRN
                        crn = tool_input.get('crn') or extracted_crn
                        if not crn:
                            tool_result = ["Error: Customer Reference Number (CRN) is required. Please provide your CRN."]
                        else:
                            tool_result = track_claim_status(crn)
                    elif tool_name == 'file_claim':
                        # Use CRN from tool_input or fallback to extracted CRN
                        crn = tool_input.get('crn') or extracted_crn
                        if not crn:
                            tool_result = ["Error: Customer Reference Number (CRN) is required. Please provide your CRN."]
                        else:
                            tool_result = file_claim(
                                crn,
                                tool_input.get('policy_id', ''),
                                tool_input.get('claim_type', ''),
                                tool_input.get('date_of_incident', ''),
                                tool_input.get('claim_amount', ''),
                                tool_input.get('description', '')
                            )
                    elif tool_name == 'schedule_agent_callback':
                        # Use CRN from tool_input or fallback to extracted CRN
                        crn = tool_input.get('crn') or extracted_crn
                        if not crn:
                            tool_result = ["Error: Customer Reference Number (CRN) is required. Please provide your CRN."]
                        else:
                            tool_result = schedule_agent_callback(
                                crn,
                                tool_input.get('reason', ''),
                                tool_input.get('preferred_timeslot', ''),
                                tool_input.get('preferred_contact_method', '')
                            )
                    elif tool_name == 'faq_tool_schema':
                        # Send another heartbeat before FAQ retrieval (same as agent_invoke_tool)
                        try:
                            heartbeat = {'type': 'heartbeat'}
                            api_gateway_client.post_to_connection(ConnectionId=connectionId, Data=json.dumps(heartbeat))
                        except Exception as e:
                            print(f"FAQ heartbeat send error: {e}")
                        
                        tool_result = get_FAQ_chunks_tool(tool_input)
                        
                        # If FAQ tool returns empty or no results, provide fallback
                        if not tool_result or len(tool_result) == 0:
                            tool_result = ["I don't have specific information about that in our current knowledge base. Let me schedule a callback with one of our agents who can provide detailed information."]
                    
                    # Create tool result message (handle both strings and dictionaries) - same as agent_invoke_tool
                    try:
                        print(f"Tool result type: {type(tool_result)}")
                        print(f"Tool result content: {tool_result}")
                        
                        # Handle different types of tool results (same logic as agent_invoke_tool)
                        if isinstance(tool_result, list) and tool_result:
                            if isinstance(tool_result[0], dict):
                                # Format list of dictionaries (like policy data)
                                formatted_results = []
                                for item in tool_result:
                                    if isinstance(item, dict):
                                        formatted_item = []
                                        for key, value in item.items():
                                            formatted_item.append(f"{key.replace('_', ' ').title()}: {value}")
                                        formatted_results.append("\n".join(formatted_item))
                                    else:
                                        formatted_results.append(str(item))
                                content_text = "\n\n".join(formatted_results)
                            else:
                                # Handle list of strings
                                content_text = "\n".join(str(item) for item in tool_result)
                        else:
                            content_text = str(tool_result) if tool_result else "No information available"
                        
                        # Create tool result block for Nova Converse API
                        tool_result_block = {
                            "toolResult": {
                                "toolUseId": tool_use_id,
                                "content": [{"text": content_text}],
                                "status": "success"
                            }
                        }
                        tool_results.append(tool_result_block)
                        print(f"Tool response created successfully")
                        
                    except Exception as e:
                        print(f"Error creating tool response: {e}")
                        print(f"Tool result type: {type(tool_result)}")
                        print(f"Tool result content: {tool_result}")
                        import traceback
                        print(f"Traceback: {traceback.format_exc()}")
                        # Skip this tool result instead of crashing
                        continue
                
                # Validate and add tool results to message history (same as agent_invoke_tool)
                if tool_results:
                    print(f"Tool results to validate: {tool_results}")
                    # Validate tool results before adding to chat history
                    valid_tool_results = []
                    for tool_result in tool_results:
                        print(f"Validating tool result: {tool_result}")
                        if (tool_result and 
                            isinstance(tool_result, dict) and 
                            'toolResult' in tool_result and 
                            tool_result['toolResult'].get('content') and 
                            len(tool_result['toolResult']['content']) > 0 and
                            tool_result['toolResult']['content'][0].get('text', '').strip()):
                            valid_tool_results.append(tool_result)
                            print(f"Tool result is valid: {tool_result}")
                        else:
                            print(f"Tool result is invalid: {tool_result}")
                    
                    # Only add tool results if we have valid ones
                    if valid_tool_results:
                        print(f"Adding {len(valid_tool_results)} valid tool results to chat history")
                        message_history.append({
                            "role": "user",
                            "content": valid_tool_results
                        })
                    else:
                        print("No valid tool results to add to chat history")
                
                # Make second API call with tool results
                try:
                    final_response = nova_bedrock_client.converse(
                        modelId=nova_model_name,
                        messages=message_history,
                        system=[{"text": enhanced_prompt}],
                        inferenceConfig={
                            "temperature": 0,
                            "topP": 0.9
                        },
                        toolConfig={
                            "tools": insurance_tools_nova
                        }
                    )
                    
                    # Extract final answer (same logic as agent_invoke_tool - take first text response only)
                    final_output_msg = (final_response.get('output') or {}).get('message') or {}
                    final_content_items = final_output_msg.get('content') or []
                    final_answer = ""
                    
                    # Take the first text response only (same as agent_invoke_tool line 2096-2101)
                    for item in final_content_items:
                        if 'text' in item:
                            # Filter out thinking tags from Nova responses
                            text_content = item['text']
                            text_content = re.sub(r'<thinking>.*?</thinking>', '', text_content, flags=re.DOTALL)
                            text_content = text_content.strip()
                            if text_content:
                                final_answer = text_content  # Take first text response only, don't concatenate
                                break  # Break after first text response (same as agent_invoke_tool)
                    
                    # If no text response, provide fallback (same as agent_invoke_tool)
                    if not final_answer:
                        final_answer = "I apologize, but I couldn't retrieve the information at this time. Please try again or contact our support team."
                    
                    # Send response via WebSocket in streaming format (same as agent_invoke_tool)
                    # Since Nova Converse API doesn't support streaming, simulate it by sending in chunks
                    try:
                        # Send the answer in chunks to simulate streaming (frontend expects content_block_delta format)
                        words = final_answer.split()
                        for i, word in enumerate(words):
                            delta_message = {
                                'type': 'content_block_delta',
                                'index': 0,
                                'delta': {
                                    'type': 'text_delta',
                                    'text': word + (' ' if i < len(words) - 1 else '')
                                }
                            }
                            try:
                                api_gateway_client.post_to_connection(ConnectionId=connectionId, Data=json.dumps(delta_message))
                            except api_gateway_client.exceptions.GoneException:
                                print(f"Connection {connectionId} is closed (GoneException) - delta message (Nova)")
                            except Exception as e:
                                print(f"WebSocket send error (delta, Nova): {e}")
                        
                        # Send content_block_stop message
                        stop_message = {'type': 'content_block_stop', 'index': 0}
                        try:
                            api_gateway_client.post_to_connection(ConnectionId=connectionId, Data=json.dumps(stop_message))
                        except api_gateway_client.exceptions.GoneException:
                            print(f"Connection {connectionId} is closed (GoneException) - stop message (Nova)")
                        except Exception as e:
                            print(f"WebSocket send error (stop, Nova): {e}")
                        
                        # Send message_stop message (optional, but frontend may expect it)
                        message_stop = {
                            'type': 'message_stop',
                            'amazon-bedrock-invocationMetrics': {
                                'inputTokenCount': input_tokens,
                                'outputTokenCount': output_tokens
                            }
                        }
                        try:
                            api_gateway_client.post_to_connection(ConnectionId=connectionId, Data=json.dumps(message_stop))
                        except api_gateway_client.exceptions.GoneException:
                            print(f"Connection {connectionId} is closed (GoneException) - message_stop (Nova)")
                        except Exception as e:
                            print(f"WebSocket send error (message_stop, Nova): {e}")
                    except Exception as e:
                        print(f"Error sending WebSocket messages for Nova: {e}")
                    
                    # Update token counts
                    final_usage = final_response.get('usage') or {}
                    input_tokens += final_usage.get('inputTokens', 0)
                    output_tokens += final_usage.get('outputTokens', 0)
                    
                    return {
                        "statusCode": "200",
                        "answer": final_answer if final_answer else "I apologize, but I couldn't retrieve the information at this time. Please try again or contact our support team.",
                        "question": chat,
                        "session_id": session_id,
                        "input_tokens": str(input_tokens),
                        "output_tokens": str(output_tokens)
                    }
                    
                except Exception as e:
                    print(f"Error in final Nova response: {e}")
                    import traceback
                    print(f"Full traceback: {traceback.format_exc()}")
                    error_response = "I apologize, but I'm having trouble accessing that information right now. Please try again in a moment."
                    
                    # Send error response via WebSocket (same as agent_invoke_tool)
                    try:
                        words = error_response.split()
                        for i, word in enumerate(words):
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
                    except:
                        pass
                    
                    return {
                        "statusCode": "500",
                        "answer": error_response,
                        "question": chat,
                        "session_id": session_id,
                        "input_tokens": str(input_tokens),
                        "output_tokens": str(output_tokens)
                    }
            else:
                # No tools called, handle normal response (same logic as agent_invoke_tool)
                answer_text = ""
                for item in assistant_response:
                    if 'text' in item:
                        # Filter out thinking tags
                        text_content = item['text']
                        text_content = re.sub(r'<thinking>.*?</thinking>', '', text_content, flags=re.DOTALL)
                        text_content = text_content.strip()
                        if text_content:
                            answer_text = text_content
                            break  # Take first text response (same as agent_invoke_tool)
                
                # Fallback if no text response (same as agent_invoke_tool)
                if not answer_text:
                    answer_text = "I'm here to help with your insurance needs. How can I assist you today?"
                
                # Send response via WebSocket in streaming format (same as agent_invoke_tool)
                # Since Nova Converse API doesn't support streaming, simulate it by sending in chunks
                try:
                    # Send the answer in chunks to simulate streaming (frontend expects content_block_delta format)
                    words = answer_text.split()
                    for i, word in enumerate(words):
                        delta_message = {
                            'type': 'content_block_delta',
                            'index': 0,
                            'delta': {
                                'type': 'text_delta',
                                'text': word + (' ' if i < len(words) - 1 else '')
                            }
                        }
                        try:
                            api_gateway_client.post_to_connection(ConnectionId=connectionId, Data=json.dumps(delta_message))
                        except api_gateway_client.exceptions.GoneException:
                            print(f"Connection {connectionId} is closed (GoneException) - delta message (Nova, no tools)")
                        except Exception as e:
                            print(f"WebSocket send error (delta, Nova, no tools): {e}")
                    
                    # Send content_block_stop message
                    stop_message = {'type': 'content_block_stop', 'index': 0}
                    try:
                        api_gateway_client.post_to_connection(ConnectionId=connectionId, Data=json.dumps(stop_message))
                    except api_gateway_client.exceptions.GoneException:
                        print(f"Connection {connectionId} is closed (GoneException) - stop message (Nova, no tools)")
                    except Exception as e:
                        print(f"WebSocket send error (stop, Nova, no tools): {e}")
                    
                    # Send message_stop message (optional, but frontend may expect it)
                    message_stop = {
                        'type': 'message_stop',
                        'amazon-bedrock-invocationMetrics': {
                            'inputTokenCount': input_tokens,
                            'outputTokenCount': output_tokens
                        }
                    }
                    try:
                        api_gateway_client.post_to_connection(ConnectionId=connectionId, Data=json.dumps(message_stop))
                    except api_gateway_client.exceptions.GoneException:
                        print(f"Connection {connectionId} is closed (GoneException) - message_stop (Nova, no tools)")
                    except Exception as e:
                        print(f"WebSocket send error (message_stop, Nova, no tools): {e}")
                except Exception as e:
                    print(f"Error sending WebSocket messages for Nova (no tools): {e}")
                
                return {
                    "statusCode": "200",
                    "answer": answer_text,
                    "question": chat,
                    "session_id": session_id,
                    "input_tokens": str(input_tokens),
                    "output_tokens": str(output_tokens)
                }
                
        except Exception as e:
            print("AN ERROR OCCURRED : ", e)
            import traceback
            print(f"Full traceback: {traceback.format_exc()}")
            response = "We are unable to assist right now please try again after few minutes"
            
            # Send error response via WebSocket (same as agent_invoke_tool)
            try:
                words = response.split()
                for i, word in enumerate(words):
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
            except:
                pass
            
            return {"answer": response, "question": chat, "session_id": session_id}
            
    except Exception as e:
        print(f"Unexpected error in nova_agent_invoke_tool: {e}")
        import traceback
        print(f"Full traceback: {traceback.format_exc()}")
        error_response = "An unknown error occurred. Please try again after some time."
        
        # Send error response via WebSocket if connectionId is available
        try:
            if connectionId:
                words = error_response.split()
                for i, word in enumerate(words):
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
        except:
            pass
        
        return {
            "statusCode": "500",
            "answer": error_response,
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
   

    selected_model = chat_tool_model
    # selected_model = claude_model_name
    is_nova_model = (
        selected_model == 'nova' or  # Exact match
        selected_model.startswith('us.amazon.nova') or  # Nova model ID pattern
        selected_model.startswith('nova-') or  # Nova variant pattern
        ('.nova' in selected_model and 'claude' not in selected_model)  # Contains .nova but not claude
    )
    
    # Use appropriate API based on model type
    if is_nova_model:
        print(f"Using Nova model for summary generation: {selected_model}")
        # Use Nova Converse API
        response = bedrock_client.converse(
            modelId=selected_model,
            system=[
                {"text": prompt}
            ],
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"text": "Follow the system instructions."}
                    ]
                }
            ],
            inferenceConfig={
                "maxTokens": 4000,
                "temperature": 0.7
            }
        )
        # Extract Nova output
        try:
            assistant_msg = response["output"]["message"]["content"][0]["text"]
        except Exception as e:
            print("Error extracting Nova output:", e)
            raise

        print("NOVA OUTPUT:", assistant_msg)

        # In case Nova adds extra narration, strip to JSON
        match = re.search(r'({.*})', assistant_msg, re.DOTALL)
        json_str = match.group(1) if match else assistant_msg

        return json.loads(json_str)

    else:
        print(f"Using Claude model for summary generation: {selected_model}")

        body = json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 2048,
            "messages": [{"role": "user", "content": prompt}]
        })

        response = bedrock.invoke_model(
            modelId="us.anthropic.claude-3-7-sonnet-20250219-v1:0",
            body=body,
        )

        final_text = json.loads(response.get("body").read())["content"][0]["text"]
        print("LLM OUTPUT:", final_text)

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


    selected_model = chat_tool_model
    # selected_model = claude_model_name
    is_nova_model = (
        selected_model == 'nova' or  # Exact match
        selected_model.startswith('us.amazon.nova') or  # Nova model ID pattern
        selected_model.startswith('nova-') or  # Nova variant pattern
        ('.nova' in selected_model and 'claude' not in selected_model)  # Contains .nova but not claude
    )
    
    # Use appropriate API based on model type
    if is_nova_model:
        print(f"Using Nova model for summary generation: {selected_model}")
        # Use Nova Converse API
        response = bedrock_client.converse(
            modelId=selected_model,
            system=[
                {"text": prompt}
            ],
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"text": "Follow the system instructions."}
                    ]
                }
            ],
            inferenceConfig={
                "maxTokens": 4000,
                "temperature": 0.7
            }
        )
        # Extract Nova output
        try:
            assistant_msg = response["output"]["message"]["content"][0]["text"]
        except Exception as e:
            print("Error extracting Nova output:", e)
            raise

        print("NOVA OUTPUT:", assistant_msg)

        # In case Nova adds extra narration, strip to JSON
        match = re.search(r'({.*})', assistant_msg, re.DOTALL)
        json_str = match.group(1) if match else assistant_msg

        return json.loads(json_str)

    else:
        print(f"Using Claude model for summary generation: {selected_model}")

        body = json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 2048,
            "messages": [{"role": "user", "content": prompt}]
        })

        response = bedrock.invoke_model(
            modelId="us.anthropic.claude-3-7-sonnet-20250219-v1:0",
            body=body,
        )

        final_text = json.loads(response.get("body").read())["content"][0]["text"]
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
            # Check if Nova model should be used for KYC extraction
            selected_model = chat_tool_model
            is_nova_model = (
                selected_model == 'nova' or  # Exact match
                selected_model.startswith('us.amazon.nova') or  # Nova model ID pattern
                selected_model.startswith('nova-') or  # Nova variant pattern
                ('.nova' in selected_model and 'claude' not in selected_model)  # Contains .nova but not claude
            )
            
            # Use appropriate API based on model type
            if is_nova_model:
                print(f"Using Nova model for KYC extraction: {selected_model}")
                # Use Nova Converse API
                response = bedrock_client.converse(
                    modelId=selected_model,
                    system=[
                        {"text": "You are a document data viewer, your task is to view the information provided to you and extract relevant information in a neat and clear manner."}
                    ],
                    messages=[
                        {
                            "role": "user",
                            "content": [
                                {"text": prompt_template}
                            ]
                        }
                    ],
                    inferenceConfig={
                        "maxTokens": 4000,
                        "temperature": 0.1
                    }
                )
                
                # Extract Nova reply text
                try:
                    extracted_data = response.get("output", {}).get("message", {}).get("content", [])[0].get("text", "")
                except Exception as e:
                    print(f"Error extracting Nova response: {e}")
                    import traceback
                    print(f"Full traceback: {traceback.format_exc()}")
                    extracted_data = ""
            else:  
                print(f"Using Claude model for KYC extraction: us.anthropic.claude-3-7-sonnet-20250219-v1:0")
                # Use Claude invoke_model API (existing implementation)
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

    #     try:

    #         # Prepare the request body for Bedrock

    #         body = json.dumps({

    #             "anthropic_version": "bedrock-2023-05-31",

    #             "max_tokens": 4000,

    #             "messages": [

    #                 {

    #                     "role": "user",

    #                     "content": prompt_template

    #                 }

    #             ]

    #         })

           

    #         # Call Bedrock using the same pattern as other functions

    #         response = bedrock_client.invoke_model(

    #             contentType='application/json',

    #             body=body,

    #             modelId="us.anthropic.claude-3-7-sonnet-20250219-v1:0"

    #         )

           

    #         response_body = json.loads(response['body'].read())

    #         extracted_data = response_body.get('content', [{}])[0].get('text', '')

           

    #         # Create response data

    #         response_data = {

    #             "extracted_kyc_data": extracted_data,

    #             "timestamp": datetime.now().isoformat(),

    #             "session_id": session_id

    #         }

           

    #         # Log the KYC extraction

    #         try:

    #             log_query = """

    #                 INSERT INTO {}.{} (session_id, query, response, timestamp, api_type)

    #                 VALUES (%s, %s, %s, %s, %s)

    #             """.format(schema, CHAT_LOG_TABLE)

               

    #             log_values = (

    #                 session_id,

    #                 json.dumps({"document_data": bool(document_data)}),

    #                 json.dumps(response_data),

    #                 datetime.now(),

    #                 'kyc_extraction'

    #             )

               

    #             insert_db(log_query, log_values)

    #         except Exception as e:

    #             print(f"Error logging KYC extraction: {e}")

           

    #         return {

    #             "statusCode": 200,

    #             "session_id": session_id,

    #             "response_data": response_data

    #         }

           

    #     except Exception as e:

    #         print(f"Error calling Bedrock for KYC extraction: {e}")

    #         return {

    #             "statusCode": 500,

    #             "error": "Error processing KYC extraction",

    #             "session_id": session_id

    #         }

       

    # except Exception as e:

    #     print(f"Error in KYC extraction API: {e}")

    #     return {

    #         "statusCode": 500,

    #         "error": "Internal server error during KYC extraction",

    #         "session_id": session_id if 'session_id' in locals() else str(uuid.uuid4())

    #     }
        
    




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
        # Get model from environment variable (defaults to 'claude' if not set)
        # Can be set to model name like 'us.amazon.nova-pro-v1:0' or just 'nova'/'claude'
        selected_model = chat_tool_model
        print(f"Using model from environment variable: {selected_model}")
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
                    # Only add non-empty messages to chat history
                    if chat_session[0] and str(chat_session[0]).strip():
                        chat_history.append({'role': 'user', 'content': [{"type" : "text",'text': str(chat_session[0]).strip()}]})
                    if chat_session[1] and str(chat_session[1]).strip():
                        chat_history.append({'role': 'assistant', 'content': [{"type" : "text",'text': str(chat_session[1]).strip()}]})
        
            #APPENDING CURRENT USER QUESTION
        # Only add non-empty current user question
        if chat and str(chat).strip():
            chat_history.append({'role': 'user', 'content': [{"type" : "text",'text': str(chat).strip()}]})
            
        print("CHAT HISTORY : ",chat_history)

        # Route to appropriate function based on environment variable
        # Check if model name contains 'nova' (handles both 'nova' and 'us.amazon.nova-pro-v1:0')
        if 'nova' in selected_model:
            print(f"Routing to Nova model handler")
            tool_response = nova_agent_invoke_tool(chat_history, session_id, chat, connectionId)
        else:
            # Default to Claude model (claude 3.5 or any other claude variant)
            print(f"Routing to Claude model handler")
            tool_response = agent_invoke_tool(chat_history, session_id, chat, connectionId)
        print("TOOL RESPONSE: ", tool_response)  
        #insert into chat_history_table
        query = f'''
                INSERT INTO {schema}.{chat_history_table}
                (session_id, question, answer, input_tokens, output_tokens, created_on, updated_on)
                VALUES( %s, %s, %s, %s, %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);
                '''
        # Handle missing keys with default values
        input_tokens = tool_response.get('input_tokens', '0')
        output_tokens = tool_response.get('output_tokens', '0')
        answer = tool_response.get('answer', '')
        
        values = (str(session_id), str(chat), str(answer), str(input_tokens), str(output_tokens))
        res = insert_db(query, values) 


        
        print(type(session_id))   
        insert_query = f'''  INSERT INTO genaifoundry.ce_cexp_logs      
(created_on, environment, session_time, "lead", enquiry, complaint, summary, whatsapp_content, next_best_action, session_id, lead_explanation, sentiment, sentiment_explanation, connectionid, input_token, output_token,topic)
VALUES(CURRENT_TIMESTAMP, %s, CURRENT_TIMESTAMP, %s, 0, 0, %s, %s, %s, %s, %s, %s, %s, %s, 0, 0,%s);'''             
        values = ('',None,'','','',session_id,'','','','','')            
        res = insert_db(insert_query,values)   
        return tool_response  

    elif event_type == 'kyc_extraction':
        return kyc_extraction_api(event)
    elif event_type == 'voiceops':
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
            chat_model = event.get('chat_model', chat_tool_model)
            print("chat_model",chat_model)
            payload = json.dumps({
            "kb_id": kb_id,
            "session_id": event['session_id'],
            "audio": event['audio'],
            "connection_id":event['connectionId'],
            "connection_url":event['connection_url'],
            "box_type": event['box_type'],
            "prompt_template":prompt_template,
            "chat_model": chat_model,
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
    elif event_type == "generate_summary":     
        
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

Hi, Thanks for reaching out! 

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
    
        # Check if Nova model should be used for summary generation
        selected_model = chat_tool_model
        is_nova_model = (
            selected_model == 'nova' or  # Exact match
            selected_model.startswith('us.amazon.nova') or  # Nova model ID pattern
            selected_model.startswith('nova-') or  # Nova variant pattern
            ('.nova' in selected_model and 'claude' not in selected_model)  # Contains .nova but not claude
        )

        import boto3
        bedrock_client = boto3.client("bedrock-runtime", region_name=region_used)
        
        # Use appropriate API based on model type
        if is_nova_model:
            print(f"Using Nova model for summary generation: {selected_model}")
            # Use Nova Converse API
            response = bedrock_client.converse(
                modelId=selected_model,
                system=[
                    {"text": prompt_template}
                ],
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"text": template}
                        ]
                    }
                ],
                inferenceConfig={
                    "maxTokens": 4000,
                    "temperature": 0.7
                }
            )
            
            # Extract Nova reply text
            try:
                out = response.get("output", {}).get("message", {}).get("content", [])[0].get("text", "")
            except Exception as e:
                print(f"Error extracting Nova response: {e}")
                import traceback
                print(f"Full traceback: {traceback.format_exc()}")
                out = ""
        else:
            print(f"Using Claude model for summary generation: {model_id}")
            # Use Claude invoke_model API (existing implementation)
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
    
            # Extract Claude reply text
            inference_result = response['body'].read().decode('utf-8')
            final = json.loads(inference_result)
            out = final['content'][0]['text']
        
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

    elif event_type == 'list_chat_summary':
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
    
