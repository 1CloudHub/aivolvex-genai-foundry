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
# claude_model_name = os.environ["claude_model_name"]
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Get new environment variables for voice operations
region_name = os.environ.get("region_name", region_used)  # Use region_used as fallback
voiceops_bucket_name = os.environ.get("voiceops_bucket_name", "voiceop-default")
ec2_instance_ip = os.environ.get("ec2_instance_ip", "")  # Elastic IP of the T3 medium instance
S3_BUCKET = os.environ.get('S3_BUCKET', '')  # S3 bucket name for visual product search

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
# KB_ID = os.environ['KB_ID']
CHAT_LOG_TABLE = os.environ['CHAT_LOG_TABLE']   
socket_endpoint = os.environ["socket_endpoint"]
# Model selection for chat_tool event type
# Can be set to model name like 'us.amazon.nova-pro-v1:0' or just 'nova'/'claude'
chat_tool_model = os.environ.get("chat_tool_model", "claude").lower()
# HR_KBID = os.environ["hr_kb_id"]
# PRODUCT_KBID = os.environ["product_kb_id"]
# bank_kb_id=os.environ["bank_kb_id"]
# RETAIL_KB_ID=os.environ["RETAIL_KB_ID"]
# health_kb_id=os.environ["health_kb_id"]
MAN_KB_ID=os.environ["MAN_KB_ID"]
banking_chat_history_table=chat_history_table
# retail_chat_history_table=os.environ['retail_chat_history_table']
# hospital_chat_history_table=os.environ['retail_chat_history_table']
# # Use environment region instead of hardcoded regions
retrieve_client = boto3.client('bedrock-agent-runtime', region_name=region_used)
bedrock_client = boto3.client('bedrock-runtime', region_name=region_used)
api_gateway_client = boto3.client('apigatewaymanagementapi', endpoint_url=socket_endpoint)
bedrock = boto3.client('bedrock-runtime', region_name=region_used)

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
# KB_ID = os.environ['KB_ID']
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
def get_gear_faq_chunks(query):
    """
    Retrieve FAQ chunks from the Veltro Motors knowledge base (MAN_KB_ID).
    Used for Product & Feature Q&A, Pricing & Warranty, and Service History & Parts queries.
    """
    try:
        print("IN GEAR FAQ: ", query)
        chat = query['knowledge_base_retrieval_question']
        chunks = []
        response_chunks = retrieve_client.retrieve(
            retrievalQuery={                                                                                
                'text': chat
            },
            knowledgeBaseId=MAN_KB_ID,
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
        
        print('GEAR FAQ CHUNKS: ', chunks)
        
        # Return meaningful chunks or fallback message
        if chunks:
            return chunks
        else:
            return ["I don't have specific information about that in our current knowledge base. Please contact our support team for detailed information."]
            
    except Exception as e:
        print("An exception occurred while retrieving gear FAQ chunks:", e)
        return ["I'm having trouble accessing that information right now. Please try again in a moment, or contact our support team."]

def gear_agent_invoke_tool(chat_history, session_id, chat, connectionId):
    """
    Gear agent invoke tool function for Veltro Motors automotive assistant.
    Handles Product & Feature Q&A, Test Drive & Service Scheduling, Pricing & Warranty, and Service History & Parts.
    """
    try:
        # Start keepalive thread
        keepalive_thread = send_keepalive(connectionId, 30)
        import uuid
        import random
        import re
        
        base_prompt = f'''You are a Virtual Automotive Assistant for Veltro Motors, a helpful and accurate chatbot for vehicle customers. You help customers with vehicle information, test drives, service scheduling, pricing, warranty, and parts inquiries.

## CRITICAL INSTRUCTIONS FOR SERVICE APPOINTMENTS:
- **FOR SERVICE APPOINTMENTS: ALWAYS ASK FOR VIN FIRST, BEFORE ANY OTHER INFORMATION (NAME, PHONE, EMAIL)**
- **NEVER** ask for customer name, phone, or email for service appointments until VIN is provided and validated
- When VIN is provided, validate it using get_customer_by_vin function and retrieve customer data automatically
- If VIN is valid and customer data is found: Use the retrieved customer name, phone, and email automatically - DO NOT ask the user for this information again
- If VIN is invalid or not found: Then ask for Customer Name, Phone Number, and Email
- If user provides a name that doesn't match the VIN database, inform them: "The name you provided doesn't match our records for this VIN. The vehicle with VIN [VIN] is registered to [Name from database]. Please verify your VIN number or contact our support team for assistance."

## CRITICAL INSTRUCTIONS:
- **NEVER** reply with any message that says you are checking, looking up, or finding information (such as "I'll check that for you", "Let me look that up", "One moment", "I'll find out", etc.).
- **NEVER** say "To answer your question about [topic], let me check our knowledge base" or similar phrases.
- After using a tool, IMMEDIATELY provide only the direct answer or summary to the user, with no filler, no explanations, and no mention of checking or looking up.
- If a user asks a question that requires a tool, use the tool and reply ONLY with the answer or summary, never with any statement about the process.
- For questions about vehicles, features, pricing, warranty, service, or parts, IMMEDIATELY use the appropriate tool WITHOUT any preliminary message.

## VIN (Vehicle Identification Number) HANDLING RULES FOR SERVICE APPOINTMENTS:
- **CRITICAL: FOR SERVICE APPOINTMENTS, VIN MUST BE ASKED FIRST, BEFORE ANY OTHER INFORMATION (NAME, PHONE, EMAIL)**
- **NEVER** ask for customer name, phone, or email for service appointments until VIN is provided and validated
- When VIN is provided for service appointments, validate it using get_customer_by_vin function
- If VIN is valid and customer data is found: Use the retrieved customer name, phone, and email automatically - DO NOT ask for these again, proceed directly to asking for date and time
- If VIN is invalid or not found: Then ask for Customer Name, Phone Number, and Email
- **NEVER** ask for VIN if it has already been provided in the conversation history
- If VIN is available in the conversation, use it automatically for all tool calls that require it
- If user says "I gave you before" or similar, acknowledge and proceed with the stored VIN
- When VIN is provided, validate it matches standard VIN format (17 characters, alphanumeric)
- **FOR TEST DRIVES**: VIN is NOT required - ask for Vehicle Model instead

## CUSTOMER DATA VALIDATION RULES:
- **CRITICAL**: When a VIN is provided and customer data is retrieved, ONLY use the customer information associated with that VIN from the database
- **NEVER** accept or use a different name if the user provides one that doesn't match the VIN database
- If user provides a name that doesn't match the VIN database, inform them: "The name you provided doesn't match our records for this VIN. The vehicle with VIN [VIN] is registered to [Name from database]. Please verify your VIN number or contact our support team for assistance."
- If VIN is valid, use the customer data from the database automatically - do not ask the user to confirm or provide it again
- Only ask for customer information manually if VIN is invalid or not found in the database

## MANDATORY QUESTION COLLECTION RULES:
- **ALWAYS** collect ALL required information for any tool before using it
- **NEVER** skip any required questions, even if the user provides some information
- **NEVER** assume or guess missing information
- **NEVER** proceed with incomplete information
- Ask questions ONE AT A TIME in this exact order:

### For schedule_test_drive_service_tool (ask in this exact order):

**FOR TEST DRIVE appointments:**
1. Service Type - "test_drive" (determine from user's request)
2. Customer Name (full name)
3. Customer Phone Number
4. Customer Email
5. Preferred Date (accept any reasonable format: "tomorrow", "next Monday", "July 20", etc.)
6. Preferred Time (accept any reasonable format: "morning", "2-4pm", "afternoon", etc.)
7. Vehicle Model (e.g., "Sedan Pro", "SUV Elite", "Truck Max", "Hatchback Sport", "Coupe GT")

**FOR SERVICE appointments (VIN FIRST - CRITICAL ORDER):**
1. Service Type - "service" (determine from user's request)
2. **VIN (Vehicle Identification Number) - MUST BE ASKED FIRST, BEFORE NAME, PHONE, OR EMAIL** (17 characters)
3. If VIN is valid: Use retrieved customer data automatically (name, phone, email) - DO NOT ask for these
4. If VIN is invalid: Ask for Customer Name, Phone Number, and Email
5. Preferred Date (accept any reasonable format: "tomorrow", "next Monday", "July 20", etc.)
6. Preferred Time (accept any reasonable format: "morning", "2-4pm", "afternoon", etc.)
7. Pickup and Drop-off Service - Ask if customer needs pickup and drop-off service (yes/no)

### For service_history_parts_tool:
1. VIN (Vehicle Identification Number) - if not already provided and required

## NATURAL DATE INTERPRETATION RULE:
- When collecting a date or time-related input, accept natural expressions such as:
  "yesterday", "today", "tomorrow", "next Monday", "July 20", etc.
- Convert these into actual calendar dates based on the current date.
- If a time of day is mentioned (e.g., "tomorrow morning"), assign appropriate time ranges:
  Morning: 9am–12pm
  Afternoon: 1pm–5pm
  Evening: 6pm–8pm

## Tool Usage Rules:
- When a user asks about vehicle features, specifications, safety, or general product questions, IMMEDIATELY use the product_feature_qa_tool
- When a user asks about pricing, warranty coverage, or financing options, IMMEDIATELY use the pricing_warranty_tool
- When a user asks about service history or parts availability/pricing, IMMEDIATELY use the service_history_parts_tool
- When a user wants to schedule a test drive or service appointment, IMMEDIATELY start collecting required information using schedule_test_drive_service_tool
- Do NOT announce that you're using the tool or searching for information
- Simply use the tool and provide the direct answer from the knowledge base
- If the knowledge base doesn't have the information, say "I don't have specific information about that in our current knowledge base. Please contact our support team at +65 1800 800 1234 or visit www.veltro-motors.sg for detailed information."

## Response Format:
- ALWAYS answer in the shortest, most direct way possible
- Do NOT add extra greetings, confirmations, or explanations
- Do NOT mention backend systems or tools
- Speak naturally as a helpful automotive representative who already knows the information
- After every completed tool call (such as scheduling), always include a clear summary of all user-provided inputs involved in that flow, followed by the final result (e.g., Confirmation ID, Appointment details).

The summary must include:
- All collected fields in the order they were asked
- The tool output (e.g., Appointment confirmation or service details)

Example (for a scheduled test drive):
Your test drive has been scheduled.
- Customer Name: John Tan
- Phone: +65 9123 4567
- Email: john.tan@email.com
- Preferred Date: July 20, 2025
- Preferred Time: 2:00 PM - 4:00 PM
- Vehicle Model: Sedan Pro
- Confirmation ID: TD20250720ABC123

Example (for a scheduled service appointment):
Your service appointment has been scheduled.
- Customer Name: John Tan
- Phone: +65 9123 4567
- Email: john.tan@email.com
- Preferred Date: July 20, 2025
- Preferred Time: 9:00 AM - 12:00 PM
- VIN: 1HGBH41JXMN109186
- Pickup and Drop-off: Yes
- Confirmation ID: SRV20250720ABC123

Available Tools:
1. product_feature_qa_tool - Answer product questions using RAG technology from knowledge base
2. schedule_test_drive_service_tool - Schedule test drives and service appointments via CRM integration
3. pricing_warranty_tool - Get information on pricing options and warranty coverage from knowledge base
4. service_history_parts_tool - Check service history and inquire about parts availability from knowledge base

## SYSTEMATIC QUESTION COLLECTION:
- When a user wants to schedule a test drive or service, IMMEDIATELY start collecting required information
- Ask ONLY ONE question at a time
- After each user response, check what information is still missing
- Ask for the NEXT missing required field (in the exact order listed above)
- Do NOT ask multiple questions in one message
- Do NOT skip any required questions
- Do NOT proceed until ALL required information is collected

## EXAMPLES OF CORRECT BEHAVIOR:

### Scheduling a Test Drive:
User: "I'd like to schedule a test drive"
Assistant: "I'll help you schedule a test drive. What is your full name?"

User: "John Tan"
Assistant: "What is your phone number?"

User: "+65 9123 4567"
Assistant: "What is your email address?"

User: "john.tan@email.com"
Assistant: "What date would you prefer for the test drive?"

User: "Tomorrow"
Assistant: "What time would you prefer? You can specify a time range like '2-4pm' or a time of day like 'morning' or 'afternoon'."

User: "2-4pm"
Assistant: "Which vehicle model would you like to test drive?"

User: "Sedan Pro"
Assistant: [Use schedule_test_drive_service_tool with all collected information]

### Scheduling a Service Appointment (VIN FIRST - Valid VIN):
User: "I need to schedule a service appointment"
Assistant: "I'll help you schedule a service appointment. Please provide your vehicle's 17-digit VIN number."

User: "1HGBH41JXMN109186"
Assistant: [VIN validated, customer data retrieved: John Tan, +65 9123 4567, john.tan@email.com]
"Thank you. I found your vehicle information. What date would you prefer for the service?"

User: "Tomorrow"
Assistant: "What time would you prefer? You can specify a time range like '2-4pm' or a time of day like 'morning' or 'afternoon'."

User: "2-4pm"
Assistant: "Would you like pickup and drop-off service? (yes/no)"

User: "Yes"
Assistant: [Use schedule_test_drive_service_tool with all collected information: VIN=1HGBH41JXMN109186, customer_name=John Tan (from VIN), customer_phone=+65 9123 4567 (from VIN), customer_email=john.tan@email.com (from VIN), preferred_date=Tomorrow, preferred_time=2-4pm, pickup_and_drop_off=Yes]

### Scheduling a Service Appointment (VIN FIRST - Invalid VIN):
User: "I need to schedule a service appointment"
Assistant: "I'll help you schedule a service appointment. Please provide your vehicle's 17-digit VIN number."

User: "INVALIDVIN123456"
Assistant: "I couldn't find that VIN in our system. To proceed, I'll need your contact information. What is your full name?"

User: "John Tan"
Assistant: "What is your phone number?"

User: "+65 9123 4567"
Assistant: "What is your email address?"

User: "john.tan@email.com"
Assistant: "What date would you prefer for the service?"

User: "Tomorrow"
Assistant: "What time would you prefer? You can specify a time range like '2-4pm' or a time of day like 'morning' or 'afternoon'."

User: "2-4pm"
Assistant: "Would you like pickup and drop-off service? (yes/no)"

User: "Yes"
Assistant: [Use schedule_test_drive_service_tool with all collected information including VIN (even if invalid), customer data, date, time, and pickup_and_drop_off]

### Scheduling a Service Appointment (VIN FIRST - Name Validation Error):
User: "I need to schedule a service appointment"
Assistant: "I'll help you schedule a service appointment. Please provide your vehicle's 17-digit VIN number."

User: "1HGBH41JXMN109186"
Assistant: [VIN validated, customer data retrieved: John Tan, +65 9123 4567, john.tan@email.com]
"Thank you. I found your vehicle information. What date would you prefer for the service?"

User: "My name is Rick"
Assistant: "The name you provided doesn't match our records for this VIN. The vehicle with VIN 1HGBH41JXMN109186 is registered to John Tan. Please verify your VIN number or contact our support team for assistance."

**IMPORTANT**: Always use the customer information from the VIN database. If user provides conflicting information, inform them and use the database information.

### Product Question:
User: "What safety features does the SUV Elite have?"
Assistant: [IMMEDIATELY use product_feature_qa_tool and provide answer]

### Pricing Question:
User: "What warranty coverage is included?"
Assistant: [IMMEDIATELY use pricing_warranty_tool and provide answer]

## EXAMPLES OF INCORRECT BEHAVIOR:
- ❌ "What's your name, phone, email, date, time, and vehicle model?" (asking multiple questions)
- ❌ Skipping any required questions
- ❌ Proceeding with incomplete information
- ❌ Asking for the same information twice
- ❌ Using hardcoded values without asking the user

## SESSION CONTINUITY:
- Once the user provides their information (name, phone, email, VIN), REMEMBER them for the entire session
- Use the same information for all subsequent tool calls and do NOT ask for them again
- Do NOT repeat questions that have already been answered
- Only ask for information that is still missing

## INPUT ACCEPTANCE RULES:
- Do NOT validate, reject, or question the user's input for required fields
- Accept any reasonable date format (tomorrow, next Monday, July 20, 2025, etc.)
- Accept any reasonable time format (morning, afternoon, 2-4pm, etc.)
- Accept any reasonable vehicle model name
- **NEVER** ask for specific formats - accept what the user provides

## RESPONSE GUIDELINES:
- For product and feature questions, IMMEDIATELY use the product_feature_qa_tool
- For pricing and warranty questions, IMMEDIATELY use the pricing_warranty_tool
- For service history and parts questions, IMMEDIATELY use the service_history_parts_tool
- ALWAYS answer in the shortest, most direct way possible
- Do NOT mention backend systems or tools
- Handle greetings warmly and ask how you can help with their vehicle needs today
'''
        
        # Gear tool schema
        gear_tools = [
            {
                "name": "product_feature_qa_tool",
                "description": "Answers product questions using RAG technology. Use this for questions about vehicle features, specifications, safety, technology, or any product-related inquiries.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "knowledge_base_retrieval_question": {"type": "string", "description": "A question about vehicle products, features, or specifications to retrieve from the knowledge base."}
                    },
                    "required": ["knowledge_base_retrieval_question"]
                }
            },
            {
                "name": "schedule_test_drive_service_tool",
                "description": "Schedule test drives and service appointments via CRM integration. Use this when customers want to book a test drive or service appointment.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "service_type": {"type": "string", "description": "Type of service: 'test_drive' or 'service'"},
                        "customer_name": {"type": "string", "description": "Customer's full name"},
                        "customer_phone": {"type": "string", "description": "Customer's phone number"},
                        "customer_email": {"type": "string", "description": "Customer's email address"},
                        "preferred_date": {"type": "string", "description": "Preferred date for appointment (accept any format)"},
                        "preferred_time": {"type": "string", "description": "Preferred time for appointment (accept any format)"},
                        "vehicle_model": {"type": "string", "description": "Vehicle model for test drive (e.g., 'Sedan Pro', 'SUV Elite')"},
                        "vin": {"type": "string", "description": "Vehicle Identification Number (REQUIRED for service appointments, 17 characters)"},
                        "pickup_and_drop_off": {"type": "string", "description": "Whether customer needs pickup and drop-off service (yes/no) - only for service appointments"}
                    },
                    "required": ["service_type", "customer_name", "customer_phone", "customer_email", "preferred_date", "preferred_time"]
                }
            },
            {
                "name": "pricing_warranty_tool",
                "description": "Get information on pricing options and warranty coverage. Use this for questions about vehicle prices, warranty terms, financing, or warranty coverage.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "knowledge_base_retrieval_question": {"type": "string", "description": "A question about pricing, warranty, financing, or warranty coverage to retrieve from the knowledge base."}
                    },
                    "required": ["knowledge_base_retrieval_question"]
                }
            },
            {
                "name": "service_history_parts_tool",
                "description": "Check service history and inquire about parts availability. Use this for questions about past service records, parts pricing, parts availability, or service recommendations.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "knowledge_base_retrieval_question": {"type": "string", "description": "A question about service history, parts availability, parts pricing, or service recommendations to retrieve from the knowledge base."},
                        "vin": {"type": "string", "description": "Vehicle Identification Number (optional, use if available in conversation)"}
                    },
                    "required": ["knowledge_base_retrieval_question"]
                }
            }
        ]
        
        # Mock VIN database with customer information
        def get_customer_by_vin(vin):
            """Retrieve customer information by VIN number"""
            vin_customer_db = {
                "1HGBH41JXMN109186": {
                    "customer_name": "John Tan",
                    "customer_phone": "+65 9123 4567",
                    "customer_email": "john.tan@email.com",
                    "vehicle_model": "Sedan Pro",
                    "vehicle_type": "Sedan"
                },
                "5YJSA1E11HF123456": {
                    "customer_name": "Sarah Lim",
                    "customer_phone": "+65 8234 5678",
                    "customer_email": "sarah.lim@email.com",
                    "vehicle_model": "SUV Elite",
                    "vehicle_type": "SUV"
                },
                "1FTFW1ET5DFC12345": {
                    "customer_name": "Michael Chen",
                    "customer_phone": "+65 7345 6789",
                    "customer_email": "michael.chen@email.com",
                    "vehicle_model": "Truck Max",
                    "vehicle_type": "Truck"
                },
                "WBA3A5C58EF123456": {
                    "customer_name": "Emily Wong",
                    "customer_phone": "+65 6456 7890",
                    "customer_email": "emily.wong@email.com",
                    "vehicle_model": "Hatchback Sport",
                    "vehicle_type": "Hatchback"
                },
                "1G1BE5SM9F7123456": {
                    "customer_name": "David Ng",
                    "customer_phone": "+65 5567 8901",
                    "customer_email": "david.ng@email.com",
                    "vehicle_model": "Coupe GT",
                    "vehicle_type": "Coupe"
                },
                "1HGBH41JXMN109187": {
                    "customer_name": "Lisa Koh",
                    "customer_phone": "+65 4678 9012",
                    "customer_email": "lisa.koh@email.com",
                    "vehicle_model": "Sedan Pro",
                    "vehicle_type": "Sedan"
                },
                "5YJSA1E11HF123457": {
                    "customer_name": "Robert Teo",
                    "customer_phone": "+65 3789 0123",
                    "customer_email": "robert.teo@email.com",
                    "vehicle_model": "SUV Elite",
                    "vehicle_type": "SUV"
                },
                "1FTFW1ET5DFC12346": {
                    "customer_name": "Jennifer Lee",
                    "customer_phone": "+65 2890 1234",
                    "customer_email": "jennifer.lee@email.com",
                    "vehicle_model": "Truck Max",
                    "vehicle_type": "Truck"
                }
            }
            return vin_customer_db.get(vin.upper() if vin else None)
        
        # Mock service history database
        def get_service_history_by_vin(vin):
            """Retrieve service history by VIN number"""
            service_history_db = {
                "1HGBH41JXMN109186": [
                    {"date": "2024-11-15", "service_type": "Major Service", "mileage": "15,000 km", "description": "Full inspection, oil change, filter replacement, brake check", "cost": "SGD 550", "parts_replaced": ["Engine Oil Filter", "Air Filter"]},
                    {"date": "2024-08-20", "service_type": "Minor Service", "mileage": "10,000 km", "description": "Oil change, filter replacement, basic inspection", "cost": "SGD 220", "parts_replaced": ["Engine Oil Filter"]},
                    {"date": "2024-05-10", "service_type": "Warranty Repair", "mileage": "5,000 km", "description": "Infotainment system software update, ADAS calibration", "cost": "SGD 0 (Warranty)", "parts_replaced": []}
                ],
                "5YJSA1E11HF123456": [
                    {"date": "2024-12-01", "service_type": "Major Service", "mileage": "20,000 km", "description": "Full inspection, oil change, filter replacement, brake pad replacement", "cost": "SGD 690", "parts_replaced": ["Engine Oil Filter", "Air Filter", "Front Brake Pads"]},
                    {"date": "2024-09-15", "service_type": "Minor Service", "mileage": "15,000 km", "description": "Oil change, filter replacement, basic inspection", "cost": "SGD 220", "parts_replaced": ["Engine Oil Filter"]},
                    {"date": "2024-06-05", "service_type": "Minor Service", "mileage": "10,000 km", "description": "Oil change, filter replacement, basic inspection", "cost": "SGD 220", "parts_replaced": ["Engine Oil Filter"]}
                ],
                "1FTFW1ET5DFC12345": [
                    {"date": "2024-11-20", "service_type": "Major Service", "mileage": "25,000 km", "description": "Full inspection, oil change, filter replacement, timing belt inspection", "cost": "SGD 550", "parts_replaced": ["Engine Oil Filter", "Air Filter", "Fuel Filter"]},
                    {"date": "2024-08-10", "service_type": "Minor Service", "mileage": "20,000 km", "description": "Oil change, filter replacement, basic inspection", "cost": "SGD 220", "parts_replaced": ["Engine Oil Filter"]},
                    {"date": "2024-04-25", "service_type": "Repair", "mileage": "15,000 km", "description": "Turbocharger assembly replacement", "cost": "SGD 2,450", "parts_replaced": ["Turbocharger Assembly"]},
                    {"date": "2024-02-15", "service_type": "Minor Service", "mileage": "10,000 km", "description": "Oil change, filter replacement, basic inspection", "cost": "SGD 220", "parts_replaced": ["Engine Oil Filter"]}
                ],
                "WBA3A5C58EF123456": [
                    {"date": "2024-10-30", "service_type": "Major Service", "mileage": "18,000 km", "description": "Full inspection, oil change, filter replacement, brake check", "cost": "SGD 550", "parts_replaced": ["Engine Oil Filter", "Air Filter"]},
                    {"date": "2024-07-18", "service_type": "Minor Service", "mileage": "12,000 km", "description": "Oil change, filter replacement, basic inspection", "cost": "SGD 220", "parts_replaced": ["Engine Oil Filter"]},
                    {"date": "2024-03-12", "service_type": "Minor Service", "mileage": "6,000 km", "description": "Oil change, filter replacement, basic inspection", "cost": "SGD 220", "parts_replaced": ["Engine Oil Filter"]}
                ],
                "1G1BE5SM9F7123456": [
                    {"date": "2024-12-05", "service_type": "Major Service", "mileage": "22,000 km", "description": "Full inspection, oil change, filter replacement, high-performance brake check", "cost": "SGD 780", "parts_replaced": ["Engine Oil Filter", "Air Filter", "High-Performance Brake Discs"]},
                    {"date": "2024-09-22", "service_type": "Minor Service", "mileage": "16,000 km", "description": "Oil change, filter replacement, basic inspection", "cost": "SGD 220", "parts_replaced": ["Engine Oil Filter"]},
                    {"date": "2024-05-30", "service_type": "Repair", "mileage": "10,000 km", "description": "Sport exhaust system installation", "cost": "SGD 1,150", "parts_replaced": ["Sport Exhaust (rear)"]}
                ],
                "1HGBH41JXMN109187": [
                    {"date": "2024-11-10", "service_type": "Major Service", "mileage": "16,000 km", "description": "Full inspection, oil change, filter replacement, brake check", "cost": "SGD 550", "parts_replaced": ["Engine Oil Filter", "Air Filter"]},
                    {"date": "2024-08-05", "service_type": "Minor Service", "mileage": "11,000 km", "description": "Oil change, filter replacement, basic inspection", "cost": "SGD 220", "parts_replaced": ["Engine Oil Filter"]}
                ],
                "5YJSA1E11HF123457": [
                    {"date": "2024-11-25", "service_type": "Major Service", "mileage": "19,000 km", "description": "Full inspection, oil change, filter replacement, ADAS calibration", "cost": "SGD 550", "parts_replaced": ["Engine Oil Filter", "Air Filter"]},
                    {"date": "2024-08-12", "service_type": "Minor Service", "mileage": "13,000 km", "description": "Oil change, filter replacement, basic inspection", "cost": "SGD 220", "parts_replaced": ["Engine Oil Filter"]},
                    {"date": "2024-04-18", "service_type": "Repair", "mileage": "7,000 km", "description": "Blind Spot Sensor replacement", "cost": "SGD 210", "parts_replaced": ["Blind Spot Sensor"]}
                ],
                "1FTFW1ET5DFC12346": [
                    {"date": "2024-12-08", "service_type": "Major Service", "mileage": "24,000 km", "description": "Full inspection, oil change, filter replacement, brake pad replacement", "cost": "SGD 670", "parts_replaced": ["Engine Oil Filter", "Air Filter", "Front Brake Pads"]},
                    {"date": "2024-09-20", "service_type": "Minor Service", "mileage": "18,000 km", "description": "Oil change, filter replacement, basic inspection", "cost": "SGD 220", "parts_replaced": ["Engine Oil Filter"]},
                    {"date": "2024-06-10", "service_type": "Repair", "mileage": "12,000 km", "description": "Underbody Protection Plate installation", "cost": "SGD 680", "parts_replaced": ["Underbody Protection Plate"]}
                ]
            }
            return service_history_db.get(vin.upper() if vin else None)
        
        # Mock tool implementations
        def schedule_test_drive_service(service_type, customer_name, customer_phone, customer_email, preferred_date, preferred_time, vehicle_model=None, vin=None, pickup_and_drop_off=None):
            """Schedule test drive or service appointment"""
            confirmation_id = f"{'TD' if service_type == 'test_drive' else 'SRV'}{str(uuid.uuid4())[:8].upper()}"
            remarks = f"Your {service_type.replace('_', ' ')} has been scheduled. Our team will contact you to confirm the details."
            if service_type == 'service' and pickup_and_drop_off and pickup_and_drop_off.lower() in ['yes', 'y', 'true', '1']:
                remarks += " Pickup and drop-off service has been arranged."
            return {
                "status": "Scheduled",
                "confirmation_id": confirmation_id,
                "service_type": service_type,
                "appointment_date": preferred_date,
                "appointment_time": preferred_time,
                "vin": vin if service_type == 'service' else None,
                "pickup_and_drop_off": pickup_and_drop_off if service_type == 'service' else None,
                "remarks": remarks
            }
        
        input_tokens = 0
        output_tokens = 0
        print("In gear_agent_invoke_tool (Veltro Motors Bot)")
        
        # Extract VIN from chat history if available and retrieve customer data
        extracted_vin = None
        customer_data = None
        for message in chat_history:
            if message['role'] == 'user':
                content_text = message['content'][0]['text']
                # VIN is 17 characters, alphanumeric
                vin_match = re.search(r'\b([A-HJ-NPR-Z0-9]{17})\b', content_text.upper())
                if vin_match:
                    extracted_vin = vin_match.group(1)
                    print(f"Extracted VIN from chat history: {extracted_vin}")
                    # Retrieve customer data from VIN
                    customer_data = get_customer_by_vin(extracted_vin)
                    if customer_data:
                        print(f"Found customer data for VIN {extracted_vin}: {customer_data}")
                    break
        
        # Enhance system prompt with VIN and customer data context if available
        if extracted_vin:
            if customer_data:
                enhanced_prompt = base_prompt + f"\n\nIMPORTANT: The customer's VIN is {extracted_vin}. Customer information retrieved: Name: {customer_data['customer_name']}, Phone: {customer_data['customer_phone']}, Email: {customer_data['customer_email']}, Vehicle: {customer_data['vehicle_model']}. Use this VIN and customer information automatically for service appointments without asking for name, phone, or email again."
            else:
                enhanced_prompt = base_prompt + f"\n\nIMPORTANT: The customer's VIN is {extracted_vin} (not found in database). Use this VIN automatically for any tool calls that require it, but you will need to ask for customer name, phone, and email."
            print(f"Enhanced prompt with VIN: {extracted_vin}, Customer data: {customer_data}")
        else:
            enhanced_prompt = base_prompt
        
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
                    "tools": gear_tools,
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
                if tool_name == 'product_feature_qa_tool':
                    # Send another heartbeat before FAQ retrieval
                    try:
                        heartbeat = {'type': 'heartbeat'}
                        api_gateway_client.post_to_connection(ConnectionId=connectionId, Data=json.dumps(heartbeat))
                    except Exception as e:
                        print(f"Product FAQ heartbeat send error: {e}")
                    
                    tool_result = get_gear_faq_chunks({'knowledge_base_retrieval_question': tool_input['knowledge_base_retrieval_question']})
                    
                    # If FAQ tool returns empty or no results, provide fallback
                    if not tool_result or len(tool_result) == 0:
                        tool_result = ["I don't have specific information about that in our current knowledge base. Please contact our support team at +65 1800 800 1234 or visit www.veltro-motors.sg for detailed information."]
                        
                elif tool_name == 'schedule_test_drive_service_tool':
                    # Use customer data from VIN if available, otherwise use tool input
                    service_type = tool_input.get('service_type', '')
                    if service_type == 'service' and customer_data:
                        # For service appointments, prefer customer data from VIN
                        final_customer_name = tool_input.get('customer_name', '') or customer_data.get('customer_name', '')
                        final_customer_phone = tool_input.get('customer_phone', '') or customer_data.get('customer_phone', '')
                        final_customer_email = tool_input.get('customer_email', '') or customer_data.get('customer_email', '')
                    else:
                        # For test drives or if no customer data, use tool input
                        final_customer_name = tool_input.get('customer_name', '')
                        final_customer_phone = tool_input.get('customer_phone', '')
                        final_customer_email = tool_input.get('customer_email', '')
                    
                    tool_result = schedule_test_drive_service(
                        service_type,
                        final_customer_name,
                        final_customer_phone,
                        final_customer_email,
                        tool_input.get('preferred_date', ''),
                        tool_input.get('preferred_time', ''),
                        tool_input.get('vehicle_model', ''),
                        tool_input.get('vin') or extracted_vin,
                        tool_input.get('pickup_and_drop_off', '')
                    )
                    
                elif tool_name == 'pricing_warranty_tool':
                    # Send another heartbeat before FAQ retrieval
                    try:
                        heartbeat = {'type': 'heartbeat'}
                        api_gateway_client.post_to_connection(ConnectionId=connectionId, Data=json.dumps(heartbeat))
                    except Exception as e:
                        print(f"Pricing FAQ heartbeat send error: {e}")
                    
                    tool_result = get_gear_faq_chunks({'knowledge_base_retrieval_question': tool_input['knowledge_base_retrieval_question']})
                    
                    # If FAQ tool returns empty or no results, provide fallback
                    if not tool_result or len(tool_result) == 0:
                        tool_result = ["I don't have specific information about that in our current knowledge base. Please contact our support team at +65 1800 800 1234 or visit www.veltro-motors.sg for detailed information."]
                        
                elif tool_name == 'service_history_parts_tool':
                    # Check if VIN is provided and question is about service history
                    vin_provided = tool_input.get('vin') or extracted_vin
                    question = tool_input.get('knowledge_base_retrieval_question', '').lower()
                    
                    # If VIN is provided and question is about service history, return actual service history
                    if vin_provided and ('service history' in question or 'service record' in question or 'maintenance history' in question or 'show me' in question):
                        service_history = get_service_history_by_vin(vin_provided)
                        if service_history:
                            # Format service history for display
                            customer_info = get_customer_by_vin(vin_provided)
                            vehicle_model = customer_info.get('vehicle_model', 'Vehicle') if customer_info else 'Vehicle'
                            
                            history_text = f"Service History for {vehicle_model} (VIN: {vin_provided}):\n\n"
                            for record in service_history:
                                history_text += f"Date: {record['date']}\n"
                                history_text += f"Service Type: {record['service_type']}\n"
                                history_text += f"Mileage: {record['mileage']}\n"
                                history_text += f"Description: {record['description']}\n"
                                history_text += f"Cost: {record['cost']}\n"
                                if record.get('parts_replaced'):
                                    history_text += f"Parts Replaced: {', '.join(record['parts_replaced'])}\n"
                                history_text += "\n"
                            
                            tool_result = [history_text]
                        else:
                            tool_result = [f"No service history found for VIN {vin_provided}. Please verify your VIN number or contact our support team at +65 1800 800 1234."]
                    else:
                        # For parts questions or general queries, use knowledge base
                        try:
                            heartbeat = {'type': 'heartbeat'}
                            api_gateway_client.post_to_connection(ConnectionId=connectionId, Data=json.dumps(heartbeat))
                        except Exception as e:
                            print(f"Service FAQ heartbeat send error: {e}")
                        
                        tool_result = get_gear_faq_chunks({'knowledge_base_retrieval_question': tool_input['knowledge_base_retrieval_question']})
                        
                        # If FAQ tool returns empty or no results, provide fallback
                        if not tool_result or len(tool_result) == 0:
                            tool_result = ["I don't have specific information about that in our current knowledge base. Please contact our support team at +65 1800 800 1234 or visit www.veltro-motors.sg for detailed information."]
                
                # Create tool result message (handle both strings and dictionaries)
                try:
                    print(f"Tool result type: {type(tool_result)}")
                    print(f"Tool result content: {tool_result}")
                    
                    # Handle different types of tool results
                    if isinstance(tool_result, list) and tool_result:
                        if isinstance(tool_result[0], dict):
                            # Format list of dictionaries
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
                    elif isinstance(tool_result, dict):
                        # Format dictionary
                        formatted_item = []
                        for key, value in tool_result.items():
                            formatted_item.append(f"{key.replace('_', ' ').title()}: {value}")
                        content_text = "\n".join(formatted_item)
                    else:
                        content_text = str(tool_result) if tool_result else "No information available"
                    
                    tool_response_dict = {
                        "type": "tool_result",
                        "tool_use_id": action['id'],
                        "content": [{"type": "text", "text": content_text}]
                    }
                    tool_results.append(tool_response_dict)
                    print(f"Tool response created successfully")
                    
                except Exception as e:
                    print(f"Error creating tool response: {e}")
                    print(f"Action type: {type(action)}")
                    print(f"Action content: {action}")
                    print(f"Tool result type: {type(tool_result)}")
                    print(f"Tool result content: {tool_result}")
                    import traceback
                    print(f"Traceback: {traceback.format_exc()}")
                    # Skip this tool result instead of crashing
                    continue
        
        # If tools were used, add tool results to chat history and make second API call
        if tools_used:
            # Validate and add tool results to chat history
            if tool_results:
                print(f"Tool results to validate: {tool_results}")
                # Validate tool results before adding to chat history
                valid_tool_results = []
                for tool_result in tool_results:
                    print(f"Validating tool result: {tool_result}")
                    if (tool_result and 
                        isinstance(tool_result, dict) and 
                        'content' in tool_result and 
                        tool_result['content'] and 
                        len(tool_result['content']) > 0 and
                        tool_result['content'][0].get('text', '').strip()):
                        valid_tool_results.append(tool_result)
                        print(f"Tool result is valid: {tool_result}")
                    else:
                        print(f"Tool result is invalid: {tool_result}")
                
                # Only add tool results if we have valid ones
                if valid_tool_results:
                    print(f"Adding {len(valid_tool_results)} valid tool results to chat history")
                    chat_history.append({'role': 'user', 'content': valid_tool_results})
                else:
                    print("No valid tool results to add to chat history")
            
            # Make second API call with tool results
            try:
                response = bedrock_client.invoke_model_with_response_stream(
                    contentType='application/json',
                    body=json.dumps({
                        "anthropic_version": "bedrock-2023-05-31",
                        "max_tokens": 4000,
                        "temperature": 0,
                        "system": prompt,
                        "tools": gear_tools,
                        "messages": chat_history
                    }),
                    modelId=model_id
                )
            except Exception as e:
                print("ERROR IN SECOND API CALL:", e)
                import traceback
                print(f"Full traceback: {traceback.format_exc()}")
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
            
            # Format response to ensure proper markdown with line breaks
            # Ensure each bullet point is on a separate line
            final_ans = final_ans.replace(' - ', '\n- ')  # Add line break before bullets if missing
            
            # If response starts with a dash after initial text, ensure line break
            final_ans = re.sub(r'([.!?])\s*-\s*', r'\1\n\n- ', final_ans)
            
            # Ensure double line break before first bullet point after intro text
            final_ans = re.sub(r'([.!?:])\s*\n-\s*', r'\1\n\n- ', final_ans)
            
            # Clean up any triple+ newlines to max double
            final_ans = re.sub(r'\n{3,}', '\n\n', final_ans)
            
            # Stream response preserving newlines - split by whitespace but keep newlines
            # Replace newlines with a special marker temporarily
            streaming_text = final_ans.replace('\n', ' <NEWLINE> ')
            words = streaming_text.split()
            
            for word in words:
                if word == '<NEWLINE>':
                    # Send actual newline character
                    delta = {'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'text_delta', 'text': '\n'}}
                else:
                    # Send word with space
                    delta = {'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'text_delta', 'text': word + ' '}}
                
                try:
                    api_gateway_client.post_to_connection(ConnectionId=connectionId, Data=json.dumps(delta))
                except Exception as e:
                    print(f"WebSocket send error (delta): {e}")
            
            return {"statusCode": "200", "answer": final_ans, "question": chat, "session_id": session_id, "input_tokens": str(input_tokens), "output_tokens": str(output_tokens)}

        else:
            # No tools called, handle normal response
            for action in assistant_response:
                if action['type'] == 'text':
                    ai_response = action['text']
                    return {"statusCode": "200", "answer": ai_response, "question": chat, "session_id": session_id, "input_tokens": str(input_tokens), "output_tokens": str(output_tokens)}
            
            # Fallback if no text response
            return {"statusCode": "200", "answer": "I'm here to help with your vehicle needs. How can I assist you today?", "question": chat, "session_id": session_id, "input_tokens": str(input_tokens), "output_tokens": str(output_tokens)}
    except Exception as e:
        print(f"Unexpected error: {e}")
        import traceback
        print(f"Full traceback: {traceback.format_exc()}")
        response = "An Unknown error occurred. Please try again after some time."
        return {
            "statusCode": "500",
            "answer": response,
            "question": chat,
            "session_id": session_id,
            "input_tokens": "0",
            "output_tokens": "0"
        }

def nova_gear_agent_invoke_tool(chat_history, session_id, chat, connectionId):
    """
    Nova model gear agent invoke tool function using AWS Bedrock Converse API.
    Uses the same tools and logic as gear_agent_invoke_tool but adapted for Nova Converse API.
    """
    try:
        # Start keepalive thread
        keepalive_thread = send_keepalive(connectionId, 30)
        import uuid
        import random
        import re
        
        base_prompt = f'''You are a Virtual Automotive Assistant for Veltro Motors, a helpful and accurate chatbot for vehicle customers. You help customers with vehicle information, test drives, service scheduling, pricing, warranty, and parts inquiries.

## CRITICAL INSTRUCTIONS FOR SERVICE APPOINTMENTS:
- **FOR SERVICE APPOINTMENTS: ALWAYS ASK FOR VIN FIRST, BEFORE ANY OTHER INFORMATION (NAME, PHONE, EMAIL)**
- **NEVER** ask for customer name, phone, or email for service appointments until VIN is provided and validated
- When VIN is provided, validate it using get_customer_by_vin function and retrieve customer data automatically
- If VIN is valid and customer data is found: Use the retrieved customer name, phone, and email automatically - DO NOT ask the user for this information again
- If VIN is invalid or not found: Then ask for Customer Name, Phone Number, and Email
- If user provides a name that doesn't match the VIN database, inform them: "The name you provided doesn't match our records for this VIN. The vehicle with VIN [VIN] is registered to [Name from database]. Please verify your VIN number or contact our support team for assistance."

## CRITICAL INSTRUCTIONS:
- **NEVER** reply with any message that says you are checking, looking up, or finding information (such as "I'll check that for you", "Let me look that up", "One moment", "I'll find out", etc.).
- **NEVER** say "To answer your question about [topic], let me check our knowledge base" or similar phrases.
- After using a tool, IMMEDIATELY provide only the direct answer or summary to the user, with no filler, no explanations, and no mention of checking or looking up.
- If a user asks a question that requires a tool, use the tool and reply ONLY with the answer or summary, never with any statement about the process.
- For questions about vehicles, features, pricing, warranty, service, or parts, IMMEDIATELY use the appropriate tool WITHOUT any preliminary message.

## VIN (Vehicle Identification Number) HANDLING RULES FOR SERVICE APPOINTMENTS:
- **CRITICAL: FOR SERVICE APPOINTMENTS, VIN MUST BE ASKED FIRST, BEFORE ANY OTHER INFORMATION (NAME, PHONE, EMAIL)**
- **NEVER** ask for customer name, phone, or email for service appointments until VIN is provided and validated
- When VIN is provided for service appointments, validate it using get_customer_by_vin function
- If VIN is valid and customer data is found: Use the retrieved customer name, phone, and email automatically - DO NOT ask for these again, proceed directly to asking for date and time
- If VIN is invalid or not found: Then ask for Customer Name, Phone Number, and Email
- **NEVER** ask for VIN if it has already been provided in the conversation history
- If VIN is available in the conversation, use it automatically for all tool calls that require it
- If user says "I gave you before" or similar, acknowledge and proceed with the stored VIN
- When VIN is provided, validate it matches standard VIN format (17 characters, alphanumeric)
- **FOR TEST DRIVES**: VIN is NOT required - ask for Vehicle Model instead

## CUSTOMER DATA VALIDATION RULES:
- **CRITICAL**: When a VIN is provided and customer data is retrieved, ONLY use the customer information associated with that VIN from the database
- **NEVER** accept or use a different name if the user provides one that doesn't match the VIN database
- If user provides a name that doesn't match the VIN database, inform them: "The name you provided doesn't match our records for this VIN. The vehicle with VIN [VIN] is registered to [Name from database]. Please verify your VIN number or contact our support team for assistance."
- If VIN is valid, use the customer data from the database automatically - do not ask the user to confirm or provide it again
- Only ask for customer information manually if VIN is invalid or not found in the database

## MANDATORY QUESTION COLLECTION RULES:
- **ALWAYS** collect ALL required information for any tool before using it
- **NEVER** skip any required questions, even if the user provides some information
- **NEVER** assume or guess missing information
- **NEVER** proceed with incomplete information
- Ask questions ONE AT A TIME in this exact order:

### For schedule_test_drive_service_tool (ask in this exact order):

**FOR TEST DRIVE appointments:**
1. Service Type - "test_drive" (determine from user's request)
2. Customer Name (full name)
3. Customer Phone Number
4. Customer Email
5. Preferred Date (accept any reasonable format: "tomorrow", "next Monday", "July 20", etc.)
6. Preferred Time (accept any reasonable format: "morning", "2-4pm", "afternoon", etc.)
7. Vehicle Model (e.g., "Sedan Pro", "SUV Elite", "Truck Max", "Hatchback Sport", "Coupe GT")

**FOR SERVICE appointments (VIN FIRST - CRITICAL ORDER):**
1. Service Type - "service" (determine from user's request)
2. **VIN (Vehicle Identification Number) - MUST BE ASKED FIRST, BEFORE NAME, PHONE, OR EMAIL** (17 characters)
3. If VIN is valid: Use retrieved customer data automatically (name, phone, email) - DO NOT ask for these
4. If VIN is invalid: Ask for Customer Name, Phone Number, and Email
5. Preferred Date (accept any reasonable format: "tomorrow", "next Monday", "July 20", etc.)
6. Preferred Time (accept any reasonable format: "morning", "2-4pm", "afternoon", etc.)
7. Pickup and Drop-off Service - Ask if customer needs pickup and drop-off service (yes/no)

### For service_history_parts_tool:
1. VIN (Vehicle Identification Number) - if not already provided and required

## NATURAL DATE INTERPRETATION RULE:
- When collecting a date or time-related input, accept natural expressions such as:
  "yesterday", "today", "tomorrow", "next Monday", "July 20", etc.
- Convert these into actual calendar dates based on the current date.
- If a time of day is mentioned (e.g., "tomorrow morning"), assign appropriate time ranges:
  Morning: 9am–12pm
  Afternoon: 1pm–5pm
  Evening: 6pm–8pm

## Tool Usage Rules:
- When a user asks about vehicle features, specifications, safety, or general product questions, IMMEDIATELY use the product_feature_qa_tool
- When a user asks about pricing, warranty coverage, or financing options, IMMEDIATELY use the pricing_warranty_tool
- When a user asks about service history or parts availability/pricing, IMMEDIATELY use the service_history_parts_tool
- When a user wants to schedule a test drive or service appointment, IMMEDIATELY start collecting required information using schedule_test_drive_service_tool
- Do NOT announce that you're using the tool or searching for information
- Simply use the tool and provide the direct answer from the knowledge base
- If the knowledge base doesn't have the information, say "I don't have specific information about that in our current knowledge base. Please contact our support team at +65 1800 800 1234 or visit www.veltro-motors.sg for detailed information."

## Response Format:
- ALWAYS answer in the shortest, most direct way possible
- Do NOT add extra greetings, confirmations, or explanations
- Do NOT mention backend systems or tools
- Speak naturally as a helpful automotive representative who already knows the information
- After every completed tool call (such as scheduling), always include a clear summary of all user-provided inputs involved in that flow, followed by the final result (e.g., Confirmation ID, Appointment details).

The summary must include:
- All collected fields in the order they were asked
- The tool output (e.g., Appointment confirmation or service details)

Example (for a scheduled test drive):
Your test drive has been scheduled.
- Customer Name: John Tan
- Phone: +65 9123 4567
- Email: john.tan@email.com
- Preferred Date: July 20, 2025
- Preferred Time: 2:00 PM - 4:00 PM
- Vehicle Model: Sedan Pro
- Confirmation ID: TD20250720ABC123

Example (for a scheduled service appointment):
Your service appointment has been scheduled.
- Customer Name: John Tan
- Phone: +65 9123 4567
- Email: john.tan@email.com
- Preferred Date: July 20, 2025
- Preferred Time: 9:00 AM - 12:00 PM
- VIN: 1HGBH41JXMN109186
- Pickup and Drop-off: Yes
- Confirmation ID: SRV20250720ABC123

Available Tools:
1. product_feature_qa_tool - Answer product questions using RAG technology from knowledge base
2. schedule_test_drive_service_tool - Schedule test drives and service appointments via CRM integration
3. pricing_warranty_tool - Get information on pricing options and warranty coverage from knowledge base
4. service_history_parts_tool - Check service history and inquire about parts availability from knowledge base

## SYSTEMATIC QUESTION COLLECTION:
- When a user wants to schedule a test drive or service, IMMEDIATELY start collecting required information
- Ask ONLY ONE question at a time
- After each user response, check what information is still missing
- Ask for the NEXT missing required field (in the exact order listed above)
- Do NOT ask multiple questions in one message
- Do NOT skip any required questions
- Do NOT proceed until ALL required information is collected

## EXAMPLES OF CORRECT BEHAVIOR:

### Scheduling a Test Drive:
User: "I'd like to schedule a test drive"
Assistant: "I'll help you schedule a test drive. What is your full name?"

User: "John Tan"
Assistant: "What is your phone number?"

User: "+65 9123 4567"
Assistant: "What is your email address?"

User: "john.tan@email.com"
Assistant: "What date would you prefer for the test drive?"

User: "Tomorrow"
Assistant: "What time would you prefer? You can specify a time range like '2-4pm' or a time of day like 'morning' or 'afternoon'."

User: "2-4pm"
Assistant: "Which vehicle model would you like to test drive?"

User: "Sedan Pro"
Assistant: [Use schedule_test_drive_service_tool with all collected information]

### Scheduling a Service Appointment (VIN FIRST - Valid VIN):
User: "I need to schedule a service appointment"
Assistant: "I'll help you schedule a service appointment. Please provide your vehicle's 17-digit VIN number."

User: "1HGBH41JXMN109186"
Assistant: [VIN validated, customer data retrieved: John Tan, +65 9123 4567, john.tan@email.com]
"Thank you. I found your vehicle information. What date would you prefer for the service?"

User: "Tomorrow"
Assistant: "What time would you prefer? You can specify a time range like '2-4pm' or a time of day like 'morning' or 'afternoon'."

User: "2-4pm"
Assistant: "Would you like pickup and drop-off service? (yes/no)"

User: "Yes"
Assistant: [Use schedule_test_drive_service_tool with all collected information: VIN=1HGBH41JXMN109186, customer_name=John Tan (from VIN), customer_phone=+65 9123 4567 (from VIN), customer_email=john.tan@email.com (from VIN), preferred_date=Tomorrow, preferred_time=2-4pm, pickup_and_drop_off=Yes]

### Scheduling a Service Appointment (VIN FIRST - Invalid VIN):
User: "I need to schedule a service appointment"
Assistant: "I'll help you schedule a service appointment. Please provide your vehicle's 17-digit VIN number."

User: "INVALIDVIN123456"
Assistant: "I couldn't find that VIN in our system. To proceed, I'll need your contact information. What is your full name?"

User: "John Tan"
Assistant: "What is your phone number?"

User: "+65 9123 4567"
Assistant: "What is your email address?"

User: "john.tan@email.com"
Assistant: "What date would you prefer for the service?"

User: "Tomorrow"
Assistant: "What time would you prefer? You can specify a time range like '2-4pm' or a time of day like 'morning' or 'afternoon'."

User: "2-4pm"
Assistant: "Would you like pickup and drop-off service? (yes/no)"

User: "Yes"
Assistant: [Use schedule_test_drive_service_tool with all collected information including VIN (even if invalid), customer data, date, time, and pickup_and_drop_off]

### Scheduling a Service Appointment (VIN FIRST - Name Validation Error):
User: "I need to schedule a service appointment"
Assistant: "I'll help you schedule a service appointment. Please provide your vehicle's 17-digit VIN number."

User: "1HGBH41JXMN109186"
Assistant: [VIN validated, customer data retrieved: John Tan, +65 9123 4567, john.tan@email.com]
"Thank you. I found your vehicle information. What date would you prefer for the service?"

User: "My name is Rick"
Assistant: "The name you provided doesn't match our records for this VIN. The vehicle with VIN 1HGBH41JXMN109186 is registered to John Tan. Please verify your VIN number or contact our support team for assistance."

**IMPORTANT**: Always use the customer information from the VIN database. If user provides conflicting information, inform them and use the database information.

### Product Question:
User: "What safety features does the SUV Elite have?"
Assistant: [IMMEDIATELY use product_feature_qa_tool and provide answer]

### Pricing Question:
User: "What warranty coverage is included?"
Assistant: [IMMEDIATELY use pricing_warranty_tool and provide answer]

## EXAMPLES OF INCORRECT BEHAVIOR:
- ❌ "What's your name, phone, email, date, time, and vehicle model?" (asking multiple questions)
- ❌ Skipping any required questions
- ❌ Proceeding with incomplete information
- ❌ Asking for the same information twice
- ❌ Using hardcoded values without asking the user

## SESSION CONTINUITY:
- Once the user provides their information (name, phone, email, VIN), REMEMBER them for the entire session
- Use the same information for all subsequent tool calls and do NOT ask for them again
- Do NOT repeat questions that have already been answered
- Only ask for information that is still missing

## INPUT ACCEPTANCE RULES:
- Do NOT validate, reject, or question the user's input for required fields
- Accept any reasonable date format (tomorrow, next Monday, July 20, 2025, etc.)
- Accept any reasonable time format (morning, afternoon, 2-4pm, etc.)
- Accept any reasonable vehicle model name
- **NEVER** ask for specific formats - accept what the user provides

## RESPONSE GUIDELINES:
- For product and feature questions, IMMEDIATELY use the product_feature_qa_tool
- For pricing and warranty questions, IMMEDIATELY use the pricing_warranty_tool
- For service history and parts questions, IMMEDIATELY use the service_history_parts_tool
- ALWAYS answer in the shortest, most direct way possible
- Do NOT mention backend systems or tools
- Handle greetings warmly and ask how you can help with their vehicle needs today
'''
        
        # Gear tool schema - converted to Nova's toolSpec format
        gear_tools_nova = [
            {
                "toolSpec": {
                    "name": "product_feature_qa_tool",
                    "description": "Answers product questions using RAG technology. Use this for questions about vehicle features, specifications, safety, technology, or any product-related inquiries.",
                    "inputSchema": {
                        "json": {
                            "type": "object",
                            "properties": {
                                "knowledge_base_retrieval_question": {"type": "string", "description": "A question about vehicle products, features, or specifications to retrieve from the knowledge base."}
                            },
                            "required": ["knowledge_base_retrieval_question"]
                        }
                    }
                }
            },
            {
                "toolSpec": {
                    "name": "schedule_test_drive_service_tool",
                    "description": "Schedule test drives and service appointments via CRM integration. Use this when customers want to book a test drive or service appointment.",
                    "inputSchema": {
                        "json": {
                            "type": "object",
                            "properties": {
                                "service_type": {"type": "string", "description": "Type of service: 'test_drive' or 'service'"},
                                "customer_name": {"type": "string", "description": "Customer's full name"},
                                "customer_phone": {"type": "string", "description": "Customer's phone number"},
                                "customer_email": {"type": "string", "description": "Customer's email address"},
                                "preferred_date": {"type": "string", "description": "Preferred date for appointment (accept any format)"},
                                "preferred_time": {"type": "string", "description": "Preferred time for appointment (accept any format)"},
                                "vehicle_model": {"type": "string", "description": "Vehicle model for test drive (e.g., 'Sedan Pro', 'SUV Elite')"},
                                "vin": {"type": "string", "description": "Vehicle Identification Number (REQUIRED for service appointments, 17 characters)"},
                                "pickup_and_drop_off": {"type": "string", "description": "Whether customer needs pickup and drop-off service (yes/no) - only for service appointments"}
                            },
                            "required": ["service_type", "customer_name", "customer_phone", "customer_email", "preferred_date", "preferred_time"]
                        }
                    }
                }
            },
            {
                "toolSpec": {
                    "name": "pricing_warranty_tool",
                    "description": "Get information on pricing options and warranty coverage. Use this for questions about vehicle prices, warranty terms, financing, or warranty coverage.",
                    "inputSchema": {
                        "json": {
                            "type": "object",
                            "properties": {
                                "knowledge_base_retrieval_question": {"type": "string", "description": "A question about pricing, warranty, financing, or warranty coverage to retrieve from the knowledge base."}
                            },
                            "required": ["knowledge_base_retrieval_question"]
                        }
                    }
                }
            },
            {
                "toolSpec": {
                    "name": "service_history_parts_tool",
                    "description": "Check service history and inquire about parts availability. Use this for questions about past service records, parts pricing, parts availability, or service recommendations.",
                    "inputSchema": {
                        "json": {
                            "type": "object",
                            "properties": {
                                "knowledge_base_retrieval_question": {"type": "string", "description": "A question about service history, parts availability, parts pricing, or service recommendations to retrieve from the knowledge base."},
                                "vin": {"type": "string", "description": "Vehicle Identification Number (optional, use if available in conversation)"}
                            },
                            "required": ["knowledge_base_retrieval_question"]
                        }
                    }
                }
            }
        ]
        
        # Mock VIN database with customer information (same as gear_agent_invoke_tool)
        def get_customer_by_vin(vin):
            """Retrieve customer information by VIN number"""
            vin_customer_db = {
                "1HGBH41JXMN109186": {
                    "customer_name": "John Tan",
                    "customer_phone": "+65 9123 4567",
                    "customer_email": "john.tan@email.com",
                    "vehicle_model": "Sedan Pro",
                    "vehicle_type": "Sedan"
                },
                "5YJSA1E11HF123456": {
                    "customer_name": "Sarah Lim",
                    "customer_phone": "+65 8234 5678",
                    "customer_email": "sarah.lim@email.com",
                    "vehicle_model": "SUV Elite",
                    "vehicle_type": "SUV"
                },
                "1FTFW1ET5DFC12345": {
                    "customer_name": "Michael Chen",
                    "customer_phone": "+65 7345 6789",
                    "customer_email": "michael.chen@email.com",
                    "vehicle_model": "Truck Max",
                    "vehicle_type": "Truck"
                },
                "WBA3A5C58EF123456": {
                    "customer_name": "Emily Wong",
                    "customer_phone": "+65 6456 7890",
                    "customer_email": "emily.wong@email.com",
                    "vehicle_model": "Hatchback Sport",
                    "vehicle_type": "Hatchback"
                },
                "1G1BE5SM9F7123456": {
                    "customer_name": "David Ng",
                    "customer_phone": "+65 5567 8901",
                    "customer_email": "david.ng@email.com",
                    "vehicle_model": "Coupe GT",
                    "vehicle_type": "Coupe"
                },
                "1HGBH41JXMN109187": {
                    "customer_name": "Lisa Koh",
                    "customer_phone": "+65 4678 9012",
                    "customer_email": "lisa.koh@email.com",
                    "vehicle_model": "Sedan Pro",
                    "vehicle_type": "Sedan"
                },
                "5YJSA1E11HF123457": {
                    "customer_name": "Robert Teo",
                    "customer_phone": "+65 3789 0123",
                    "customer_email": "robert.teo@email.com",
                    "vehicle_model": "SUV Elite",
                    "vehicle_type": "SUV"
                },
                "1FTFW1ET5DFC12346": {
                    "customer_name": "Jennifer Lee",
                    "customer_phone": "+65 2890 1234",
                    "customer_email": "jennifer.lee@email.com",
                    "vehicle_model": "Truck Max",
                    "vehicle_type": "Truck"
                }
            }
            return vin_customer_db.get(vin.upper() if vin else None)
        
        # Mock service history database (same as gear_agent_invoke_tool)
        def get_service_history_by_vin(vin):
            """Retrieve service history by VIN number"""
            service_history_db = {
                "1HGBH41JXMN109186": [
                    {"date": "2024-11-15", "service_type": "Major Service", "mileage": "15,000 km", "description": "Full inspection, oil change, filter replacement, brake check", "cost": "SGD 550", "parts_replaced": ["Engine Oil Filter", "Air Filter"]},
                    {"date": "2024-08-20", "service_type": "Minor Service", "mileage": "10,000 km", "description": "Oil change, filter replacement, basic inspection", "cost": "SGD 220", "parts_replaced": ["Engine Oil Filter"]},
                    {"date": "2024-05-10", "service_type": "Warranty Repair", "mileage": "5,000 km", "description": "Infotainment system software update, ADAS calibration", "cost": "SGD 0 (Warranty)", "parts_replaced": []}
                ],
                "5YJSA1E11HF123456": [
                    {"date": "2024-12-01", "service_type": "Major Service", "mileage": "20,000 km", "description": "Full inspection, oil change, filter replacement, brake pad replacement", "cost": "SGD 690", "parts_replaced": ["Engine Oil Filter", "Air Filter", "Front Brake Pads"]},
                    {"date": "2024-09-15", "service_type": "Minor Service", "mileage": "15,000 km", "description": "Oil change, filter replacement, basic inspection", "cost": "SGD 220", "parts_replaced": ["Engine Oil Filter"]},
                    {"date": "2024-06-05", "service_type": "Minor Service", "mileage": "10,000 km", "description": "Oil change, filter replacement, basic inspection", "cost": "SGD 220", "parts_replaced": ["Engine Oil Filter"]}
                ],
                "1FTFW1ET5DFC12345": [
                    {"date": "2024-11-20", "service_type": "Major Service", "mileage": "25,000 km", "description": "Full inspection, oil change, filter replacement, timing belt inspection", "cost": "SGD 550", "parts_replaced": ["Engine Oil Filter", "Air Filter", "Fuel Filter"]},
                    {"date": "2024-08-10", "service_type": "Minor Service", "mileage": "20,000 km", "description": "Oil change, filter replacement, basic inspection", "cost": "SGD 220", "parts_replaced": ["Engine Oil Filter"]},
                    {"date": "2024-04-25", "service_type": "Repair", "mileage": "15,000 km", "description": "Turbocharger assembly replacement", "cost": "SGD 2,450", "parts_replaced": ["Turbocharger Assembly"]},
                    {"date": "2024-02-15", "service_type": "Minor Service", "mileage": "10,000 km", "description": "Oil change, filter replacement, basic inspection", "cost": "SGD 220", "parts_replaced": ["Engine Oil Filter"]}
                ],
                "WBA3A5C58EF123456": [
                    {"date": "2024-10-30", "service_type": "Major Service", "mileage": "18,000 km", "description": "Full inspection, oil change, filter replacement, brake check", "cost": "SGD 550", "parts_replaced": ["Engine Oil Filter", "Air Filter"]},
                    {"date": "2024-07-18", "service_type": "Minor Service", "mileage": "12,000 km", "description": "Oil change, filter replacement, basic inspection", "cost": "SGD 220", "parts_replaced": ["Engine Oil Filter"]},
                    {"date": "2024-03-12", "service_type": "Minor Service", "mileage": "6,000 km", "description": "Oil change, filter replacement, basic inspection", "cost": "SGD 220", "parts_replaced": ["Engine Oil Filter"]}
                ],
                "1G1BE5SM9F7123456": [
                    {"date": "2024-12-05", "service_type": "Major Service", "mileage": "22,000 km", "description": "Full inspection, oil change, filter replacement, high-performance brake check", "cost": "SGD 780", "parts_replaced": ["Engine Oil Filter", "Air Filter", "High-Performance Brake Discs"]},
                    {"date": "2024-09-22", "service_type": "Minor Service", "mileage": "16,000 km", "description": "Oil change, filter replacement, basic inspection", "cost": "SGD 220", "parts_replaced": ["Engine Oil Filter"]},
                    {"date": "2024-05-30", "service_type": "Repair", "mileage": "10,000 km", "description": "Sport exhaust system installation", "cost": "SGD 1,150", "parts_replaced": ["Sport Exhaust (rear)"]}
                ],
                "1HGBH41JXMN109187": [
                    {"date": "2024-11-10", "service_type": "Major Service", "mileage": "16,000 km", "description": "Full inspection, oil change, filter replacement, brake check", "cost": "SGD 550", "parts_replaced": ["Engine Oil Filter", "Air Filter"]},
                    {"date": "2024-08-05", "service_type": "Minor Service", "mileage": "11,000 km", "description": "Oil change, filter replacement, basic inspection", "cost": "SGD 220", "parts_replaced": ["Engine Oil Filter"]}
                ],
                "5YJSA1E11HF123457": [
                    {"date": "2024-11-25", "service_type": "Major Service", "mileage": "19,000 km", "description": "Full inspection, oil change, filter replacement, ADAS calibration", "cost": "SGD 550", "parts_replaced": ["Engine Oil Filter", "Air Filter"]},
                    {"date": "2024-08-12", "service_type": "Minor Service", "mileage": "13,000 km", "description": "Oil change, filter replacement, basic inspection", "cost": "SGD 220", "parts_replaced": ["Engine Oil Filter"]},
                    {"date": "2024-04-18", "service_type": "Repair", "mileage": "7,000 km", "description": "Blind Spot Sensor replacement", "cost": "SGD 210", "parts_replaced": ["Blind Spot Sensor"]}
                ],
                "1FTFW1ET5DFC12346": [
                    {"date": "2024-12-08", "service_type": "Major Service", "mileage": "24,000 km", "description": "Full inspection, oil change, filter replacement, brake pad replacement", "cost": "SGD 670", "parts_replaced": ["Engine Oil Filter", "Air Filter", "Front Brake Pads"]},
                    {"date": "2024-09-20", "service_type": "Minor Service", "mileage": "18,000 km", "description": "Oil change, filter replacement, basic inspection", "cost": "SGD 220", "parts_replaced": ["Engine Oil Filter"]},
                    {"date": "2024-06-10", "service_type": "Repair", "mileage": "12,000 km", "description": "Underbody Protection Plate installation", "cost": "SGD 680", "parts_replaced": ["Underbody Protection Plate"]}
                ]
            }
            return service_history_db.get(vin.upper() if vin else None)
        
        # Mock tool implementations (same as gear_agent_invoke_tool)
        def schedule_test_drive_service(service_type, customer_name, customer_phone, customer_email, preferred_date, preferred_time, vehicle_model=None, vin=None, pickup_and_drop_off=None):
            """Schedule test drive or service appointment"""
            confirmation_id = f"{'TD' if service_type == 'test_drive' else 'SRV'}{str(uuid.uuid4())[:8].upper()}"
            remarks = f"Your {service_type.replace('_', ' ')} has been scheduled. Our team will contact you to confirm the details."
            if service_type == 'service' and pickup_and_drop_off and pickup_and_drop_off.lower() in ['yes', 'y', 'true', '1']:
                remarks += " Pickup and drop-off service has been arranged."
            return {
                "status": "Scheduled",
                "confirmation_id": confirmation_id,
                "service_type": service_type,
                "appointment_date": preferred_date,
                "appointment_time": preferred_time,
                "vin": vin if service_type == 'service' else None,
                "pickup_and_drop_off": pickup_and_drop_off if service_type == 'service' else None,
                "remarks": remarks
            }
        
        input_tokens = 0
        output_tokens = 0
        print("In nova_gear_agent_invoke_tool (Veltro Motors Bot - Nova)")
        
        # Extract VIN from chat history if available and retrieve customer data
        extracted_vin = None
        customer_data = None
        for message in chat_history:
            if message['role'] == 'user':
                content_text = message['content'][0]['text']
                # VIN is 17 characters, alphanumeric
                vin_match = re.search(r'\b([A-HJ-NPR-Z0-9]{17})\b', content_text.upper())
                if vin_match:
                    extracted_vin = vin_match.group(1)
                    print(f"Extracted VIN from chat history: {extracted_vin}")
                    # Retrieve customer data from VIN
                    customer_data = get_customer_by_vin(extracted_vin)
                    if customer_data:
                        print(f"Found customer data for VIN {extracted_vin}: {customer_data}")
                    break
        
        # Enhance system prompt with VIN and customer data context if available
        if extracted_vin:
            if customer_data:
                enhanced_prompt = base_prompt + f"\n\nIMPORTANT: The customer's VIN is {extracted_vin}. Customer information retrieved: Name: {customer_data['customer_name']}, Phone: {customer_data['customer_phone']}, Email: {customer_data['customer_email']}, Vehicle: {customer_data['vehicle_model']}. Use this VIN and customer information automatically for service appointments without asking for name, phone, or email again."
            else:
                enhanced_prompt = base_prompt + f"\n\nIMPORTANT: The customer's VIN is {extracted_vin} (not found in database). Use this VIN automatically for any tool calls that require it, but you will need to ask for customer name, phone, and email."
            print(f"Enhanced prompt with VIN: {extracted_vin}, Customer data: {customer_data}")
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
        nova_model_name = os.environ.get("nova_model_name", "us.amazon.nova-premier-v1:0")
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
                    "tools": gear_tools_nova
                }
            )
            
            print("Nova Model Response: ", response)
            
            # Parse the response
            assistant_response = []
            output_msg = (response.get('output') or {}).get('message') or {}
            content_items = output_msg.get('content') or []
            
            for item in content_items:
                if 'text' in item:
                    # Filter out thinking tags from Nova responses
                    text_content = item['text']
                    text_content = re.sub(r'<thinking>.*?</thinking>', '', text_content, flags=re.DOTALL)
                    text_content = text_content.strip()
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
                # Process all tool calls
                tools_used = []
                tool_results = []
                
                for tool_call_item in tool_calls:
                    tool_call = tool_call_item['toolUse']
                    tool_name = tool_call.get('name')
                    tool_input = tool_call.get('input', {})
                    tool_use_id = tool_call.get('toolUseId')
                    tool_result = None
                    
                    tools_used.append(tool_name)
                    
                    # Send a heartbeat to keep WebSocket alive during tool execution
                    try:
                        heartbeat = {'type': 'heartbeat'}
                        api_gateway_client.post_to_connection(ConnectionId=connectionId, Data=json.dumps(heartbeat))
                    except Exception as e:
                        print(f"Heartbeat send error: {e}")
                    
                    # Execute the appropriate tool
                    if tool_name == 'product_feature_qa_tool':
                        # Send another heartbeat before FAQ retrieval
                        try:
                            heartbeat = {'type': 'heartbeat'}
                            api_gateway_client.post_to_connection(ConnectionId=connectionId, Data=json.dumps(heartbeat))
                        except Exception as e:
                            print(f"Product FAQ heartbeat send error: {e}")
                        
                        tool_result = get_gear_faq_chunks({'knowledge_base_retrieval_question': tool_input['knowledge_base_retrieval_question']})
                        
                        # If FAQ tool returns empty or no results, provide fallback
                        if not tool_result or len(tool_result) == 0:
                            tool_result = ["I don't have specific information about that in our current knowledge base. Please contact our support team at +65 1800 800 1234 or visit www.veltro-motors.sg for detailed information."]
                            
                    elif tool_name == 'schedule_test_drive_service_tool':
                        # Use customer data from VIN if available, otherwise use tool input
                        service_type = tool_input.get('service_type', '')
                        if service_type == 'service' and customer_data:
                            # For service appointments, prefer customer data from VIN
                            final_customer_name = tool_input.get('customer_name', '') or customer_data.get('customer_name', '')
                            final_customer_phone = tool_input.get('customer_phone', '') or customer_data.get('customer_phone', '')
                            final_customer_email = tool_input.get('customer_email', '') or customer_data.get('customer_email', '')
                        else:
                            # For test drives or if no customer data, use tool input
                            final_customer_name = tool_input.get('customer_name', '')
                            final_customer_phone = tool_input.get('customer_phone', '')
                            final_customer_email = tool_input.get('customer_email', '')
                        
                        tool_result = schedule_test_drive_service(
                            service_type,
                            final_customer_name,
                            final_customer_phone,
                            final_customer_email,
                            tool_input.get('preferred_date', ''),
                            tool_input.get('preferred_time', ''),
                            tool_input.get('vehicle_model', ''),
                            tool_input.get('vin') or extracted_vin,
                            tool_input.get('pickup_and_drop_off', '')
                        )
                        
                    elif tool_name == 'pricing_warranty_tool':
                        # Send another heartbeat before FAQ retrieval
                        try:
                            heartbeat = {'type': 'heartbeat'}
                            api_gateway_client.post_to_connection(ConnectionId=connectionId, Data=json.dumps(heartbeat))
                        except Exception as e:
                            print(f"Pricing FAQ heartbeat send error: {e}")
                        
                        tool_result = get_gear_faq_chunks({'knowledge_base_retrieval_question': tool_input['knowledge_base_retrieval_question']})
                        
                        # If FAQ tool returns empty or no results, provide fallback
                        if not tool_result or len(tool_result) == 0:
                            tool_result = ["I don't have specific information about that in our current knowledge base. Please contact our support team at +65 1800 800 1234 or visit www.veltro-motors.sg for detailed information."]
                            
                    elif tool_name == 'service_history_parts_tool':
                        # Check if VIN is provided and question is about service history
                        vin_provided = tool_input.get('vin') or extracted_vin
                        question = tool_input.get('knowledge_base_retrieval_question', '').lower()
                        
                        # If VIN is provided and question is about service history, return actual service history
                        if vin_provided and ('service history' in question or 'service record' in question or 'maintenance history' in question or 'show me' in question):
                            service_history = get_service_history_by_vin(vin_provided)
                            if service_history:
                                # Format service history for display
                                customer_info = get_customer_by_vin(vin_provided)
                                vehicle_model = customer_info.get('vehicle_model', 'Vehicle') if customer_info else 'Vehicle'
                                
                                history_text = f"Service History for {vehicle_model} (VIN: {vin_provided}):\n\n"
                                for record in service_history:
                                    history_text += f"Date: {record['date']}\n"
                                    history_text += f"Service Type: {record['service_type']}\n"
                                    history_text += f"Mileage: {record['mileage']}\n"
                                    history_text += f"Description: {record['description']}\n"
                                    history_text += f"Cost: {record['cost']}\n"
                                    if record.get('parts_replaced'):
                                        history_text += f"Parts Replaced: {', '.join(record['parts_replaced'])}\n"
                                    history_text += "\n"
                                
                                tool_result = [history_text]
                            else:
                                tool_result = [f"No service history found for VIN {vin_provided}. Please verify your VIN number or contact our support team at +65 1800 800 1234."]
                        else:
                            # For parts questions or general queries, use knowledge base
                            try:
                                heartbeat = {'type': 'heartbeat'}
                                api_gateway_client.post_to_connection(ConnectionId=connectionId, Data=json.dumps(heartbeat))
                            except Exception as e:
                                print(f"Service FAQ heartbeat send error: {e}")
                            
                            tool_result = get_gear_faq_chunks({'knowledge_base_retrieval_question': tool_input['knowledge_base_retrieval_question']})
                            
                            # If FAQ tool returns empty or no results, provide fallback
                            if not tool_result or len(tool_result) == 0:
                                tool_result = ["I don't have specific information about that in our current knowledge base. Please contact our support team at +65 1800 800 1234 or visit www.veltro-motors.sg for detailed information."]
                    
                    # Create tool result message for Nova format
                    try:
                        print(f"Tool result type: {type(tool_result)}")
                        print(f"Tool result content: {tool_result}")
                        
                        # Handle different types of tool results
                        if isinstance(tool_result, list) and tool_result:
                            if isinstance(tool_result[0], dict):
                                # Format list of dictionaries
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
                        elif isinstance(tool_result, dict):
                            # Format dictionary
                            formatted_item = []
                            for key, value in tool_result.items():
                                formatted_item.append(f"{key.replace('_', ' ').title()}: {value}")
                            content_text = "\n".join(formatted_item)
                        else:
                            content_text = str(tool_result) if tool_result else "No information available"
                        
                        # Create tool result block for Nova Converse API (same format as nova_agent_invoke_tool)
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
                
                # Validate and add tool results to message history (same as nova_agent_invoke_tool)
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
                            "tools": gear_tools_nova
                        }
                    )
                    
                    print("Nova Model Final Response: ", final_response)
                    
                    # Extract final answer from Nova response
                    final_output_msg = (final_response.get('output') or {}).get('message') or {}
                    final_content_items = final_output_msg.get('content') or []
                    
                    final_ans = ""
                    for item in final_content_items:
                        if item.get('text'):
                            text_content = item['text']
                            # Filter out <thinking> tags
                            text_content = re.sub(r'<thinking>.*?</thinking>', '', text_content, flags=re.DOTALL | re.IGNORECASE).strip()
                            if text_content:
                                final_ans = text_content
                                break
                    
                    # If no text response, provide fallback
                    if not final_ans:
                        final_ans = "I apologize, but I couldn't retrieve the information at this time. Please try again or contact our support team."
                    
                    # Format response to ensure proper markdown with line breaks
                    # Ensure each bullet point is on a separate line
                    final_ans = final_ans.replace(' - ', '\n- ')  # Add line break before bullets if missing
                    
                    # If response starts with a dash after initial text, ensure line break
                    final_ans = re.sub(r'([.!?])\s*-\s*', r'\1\n\n- ', final_ans)
                    
                    # Ensure double line break before first bullet point after intro text
                    final_ans = re.sub(r'([.!?:])\s*\n-\s*', r'\1\n\n- ', final_ans)
                    
                    # Clean up any triple+ newlines to max double
                    final_ans = re.sub(r'\n{3,}', '\n\n', final_ans)
                    
                    # Stream response preserving newlines - split by whitespace but keep newlines
                    # Replace newlines with a special marker temporarily
                    streaming_text = final_ans.replace('\n', ' <NEWLINE> ')
                    words = streaming_text.split()
                    
                    for word in words:
                        if word == '<NEWLINE>':
                            # Send actual newline character
                            delta = {'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'text_delta', 'text': '\n'}}
                        else:
                            # Send word with space
                            delta = {'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'text_delta', 'text': word + ' '}}
                        
                        try:
                            api_gateway_client.post_to_connection(ConnectionId=connectionId, Data=json.dumps(delta))
                        except Exception as e:
                            print(f"WebSocket send error (delta): {e}")
                    
                    # Send content_block_stop
                    stop_msg = {'type': 'content_block_stop', 'index': 0}
                    try:
                        api_gateway_client.post_to_connection(ConnectionId=connectionId, Data=json.dumps(stop_msg))
                    except Exception as e:
                        print(f"WebSocket send error (stop): {e}")
                    
                    # Send message_stop
                    message_stop = {'type': 'message_stop'}
                    try:
                        api_gateway_client.post_to_connection(ConnectionId=connectionId, Data=json.dumps(message_stop))
                    except Exception as e:
                        print(f"WebSocket send error (message_stop): {e}")
                    
                    # Get token usage from response
                    final_usage = final_response.get('usage', {})
                    input_tokens += final_usage.get('inputTokens', 0)
                    output_tokens += final_usage.get('outputTokens', 0)
                    
                    return {"statusCode": "200", "answer": final_ans, "question": chat, "session_id": session_id, "input_tokens": str(input_tokens), "output_tokens": str(output_tokens)}
                    
                except Exception as e:
                    print(f"Error in final Nova gear response: {e}")
                    import traceback
                    print(f"Full traceback: {traceback.format_exc()}")
                    # Send error response via WebSocket
                    error_response = "I apologize, but I'm having trouble accessing that information right now. Please try again in a moment."
                    words = error_response.split()
                    for word in words:
                        delta = {'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'text_delta', 'text': word + ' '}}
                        try:
                            api_gateway_client.post_to_connection(ConnectionId=connectionId, Data=json.dumps(delta))
                        except:
                            pass
                    stop_msg = {'type': 'content_block_stop', 'index': 0}
                    try:
                        api_gateway_client.post_to_connection(ConnectionId=connectionId, Data=json.dumps(stop_msg))
                    except:
                        pass
                    message_stop = {'type': 'message_stop'}
                    try:
                        api_gateway_client.post_to_connection(ConnectionId=connectionId, Data=json.dumps(message_stop))
                    except:
                        pass
                    return {"answer": error_response, "question": chat, "session_id": session_id}
            else:
                # No tools called, handle normal response
                final_ans = ""
                for item in assistant_response:
                    if item.get('text'):
                        text_content = item['text']
                        text_content = re.sub(r'<thinking>.*?</thinking>', '', text_content, flags=re.DOTALL | re.IGNORECASE).strip()
                        if text_content:
                            final_ans = text_content
                            break
                
                # If no text response, provide fallback
                if not final_ans:
                    final_ans = "I'm here to help with your vehicle needs. How can I assist you today?"
                
                # Format response to ensure proper markdown with line breaks
                # Ensure each bullet point is on a separate line
                final_ans = final_ans.replace(' - ', '\n- ')  # Add line break before bullets if missing
                
                # If response starts with a dash after initial text, ensure line break
                final_ans = re.sub(r'([.!?])\s*-\s*', r'\1\n\n- ', final_ans)
                
                # Ensure double line break before first bullet point after intro text
                final_ans = re.sub(r'([.!?:])\s*\n-\s*', r'\1\n\n- ', final_ans)
                
                # Clean up any triple+ newlines to max double
                final_ans = re.sub(r'\n{3,}', '\n\n', final_ans)
                
                # Stream response preserving newlines - split by whitespace but keep newlines
                # Replace newlines with a special marker temporarily
                streaming_text = final_ans.replace('\n', ' <NEWLINE> ')
                words = streaming_text.split()
                
                for word in words:
                    if word == '<NEWLINE>':
                        # Send actual newline character
                        delta = {'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'text_delta', 'text': '\n'}}
                    else:
                        # Send word with space
                        delta = {'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'text_delta', 'text': word + ' '}}
                    
                    try:
                        api_gateway_client.post_to_connection(ConnectionId=connectionId, Data=json.dumps(delta))
                    except Exception as e:
                        print(f"WebSocket send error (delta): {e}")
                
                # Send content_block_stop
                stop_msg = {'type': 'content_block_stop', 'index': 0}
                try:
                    api_gateway_client.post_to_connection(ConnectionId=connectionId, Data=json.dumps(stop_msg))
                except Exception as e:
                    print(f"WebSocket send error (stop): {e}")
                
                # Send message_stop
                message_stop = {'type': 'message_stop'}
                try:
                    api_gateway_client.post_to_connection(ConnectionId=connectionId, Data=json.dumps(message_stop))
                except Exception as e:
                    print(f"WebSocket send error (message_stop): {e}")
                
                # Get token usage from response
                usage = response.get('usage', {})
                input_tokens = usage.get('inputTokens', 0)
                output_tokens = usage.get('outputTokens', 0)
                
                return {"statusCode": "200", "answer": final_ans, "question": chat, "session_id": session_id, "input_tokens": str(input_tokens), "output_tokens": str(output_tokens)}
                
        except Exception as e:
            print(f"Error invoking Nova gear model: {e}")
            import traceback
            print(f"Full traceback: {traceback.format_exc()}")
            # Send error response via WebSocket
            error_response = "We are unable to assist right now please try again after few minutes"
            words = error_response.split()
            for word in words:
                delta = {'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'text_delta', 'text': word + ' '}}
                try:
                    api_gateway_client.post_to_connection(ConnectionId=connectionId, Data=json.dumps(delta))
                except:
                    pass
            stop_msg = {'type': 'content_block_stop', 'index': 0}
            try:
                api_gateway_client.post_to_connection(ConnectionId=connectionId, Data=json.dumps(stop_msg))
            except:
                pass
            message_stop = {'type': 'message_stop'}
            try:
                api_gateway_client.post_to_connection(ConnectionId=connectionId, Data=json.dumps(message_stop))
            except:
                pass
            return {"answer": error_response, "question": chat, "session_id": session_id}
            
    except Exception as e:
        print(f"Unexpected error in nova_gear_agent_invoke_tool: {e}")
        import traceback
        print(f"Full traceback: {traceback.format_exc()}")
        # Send error response via WebSocket
        error_response = "An Unknown error occurred. Please try again after some time."
        try:
            words = error_response.split()
            for word in words:
                delta = {'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'text_delta', 'text': word + ' '}}
                try:
                    api_gateway_client.post_to_connection(ConnectionId=connectionId, Data=json.dumps(delta))
                except:
                    pass
            stop_msg = {'type': 'content_block_stop', 'index': 0}
            try:
                api_gateway_client.post_to_connection(ConnectionId=connectionId, Data=json.dumps(stop_msg))
            except:
                pass
            message_stop = {'type': 'message_stop'}
            try:
                api_gateway_client.post_to_connection(ConnectionId=connectionId, Data=json.dumps(message_stop))
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
4.Do NOT include any tags (for example <information> or <Reasoning>). These Tags are absolutely unnecessary, avoid them completely. Provide your response in markdown format and act like you are thinking.
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
                # Nova models are typically in us-east-1, and if Converse API is not available,
                # fall back to using Claude model instead
                try:
                    # Try us-east-1 first (where Nova models are typically available)
                    nova_bedrock_client = boto3.client("bedrock-runtime", region_name="us-east-1")
                    
                    # Use Nova Converse API
                    response = nova_bedrock_client.converse(
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
                    extracted_data = response.get("output", {}).get("message", {}).get("content", [])[0].get("text", "")
                except Exception as nova_error:
                    print(f"Nova Converse API error: {nova_error}")
                    print("Falling back to Claude model for KYC extraction")
                    # Fall back to Claude if Nova Converse API is not available
                    is_nova_model = False
                    # Use default Claude model
                    claude_model_id = "us.anthropic.claude-3-7-sonnet-20250219-v1:0"
                    bedrock_client = boto3.client("bedrock-runtime", region_name=region_used)
                    
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
                        modelId=claude_model_id
                    )
                
                    response_body = json.loads(response['body'].read())
                    extracted_data = response_body.get('content', [{}])[0].get('text', '')
            else:  
                print(f"Using Claude model for KYC extraction: us.anthropic.claude-3-7-sonnet-20250219-v1:0")
                # Initialize bedrock client for Claude model
                bedrock_client = boto3.client("bedrock-runtime", region_name=region_used)
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
def lambda_handler(event, context):
    global user_intent_flag, overall_flow_flag, ub_number, ub_user_name, pop, str_intent,json
    print("Event: ",event)
    event_type=event['event_type']
    print("Event_type: ",event_type)
    conv_id = ""
    
    if event_type == 'gear_tool':  
       
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
                    from {schema}.{banking_chat_history_table} 
                    where session_id = '{session_id}' 
                    order by created_on desc limit 20;'''
            history_response = select_db(query)
            print("history_response is ",history_response)

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
        # Check if Nova model should be used (same logic as chat_tool)
        is_nova_model = (
            selected_model == 'nova' or  # Exact match
            selected_model.startswith('us.amazon.nova') or  # Nova model ID pattern
            selected_model.startswith('nova-') or  # Nova variant pattern
            ('.nova' in selected_model and 'claude' not in selected_model)  # Contains .nova but not claude
        )
        
        # Route to appropriate function based on model type
        if is_nova_model:
            print(f"Routing to Nova gear model handler (detected model: {selected_model})")
            tool_response = nova_gear_agent_invoke_tool(chat_history, session_id, chat, connectionId)
        else:
            # Default to Claude model (claude 3.5 or any other claude variant)
            print(f"Routing to Claude gear model handler (detected model: {selected_model})")
            tool_response = gear_agent_invoke_tool(chat_history, session_id, chat, connectionId)
        print("TOOL RESPONSE: ", tool_response)  
        #insert into banking_chat_history_table (same table as banking)
        query = f'''
                INSERT INTO {schema}.{banking_chat_history_table}
                (session_id, question, answer, input_tokens, output_tokens, created_on, updated_on)
                VALUES( %s, %s, %s, %s, %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);
                '''
        # Handle missing keys with default values
        input_tokens = tool_response.get('input_tokens', '0')
        output_tokens = tool_response.get('output_tokens', '0')
        answer = tool_response.get('answer', '')
        
        values = (str(session_id), str(chat), str(answer), str(input_tokens), str(output_tokens))
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

            elif event['box_type'] == 'manufacturing':
                kb_id = MAN_KB_ID
                print("kb_id",kb_id)
                prompt_template=f'''You are a Virtual Automotive Assistant for Veltro Motors. Give quick, helpful answers that sound natural when spoken aloud.

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
            # Get chat_model from event or use default chat_tool_model
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
            print("payload is printed here",payload)
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
    if event_type == "generate_gear_summary":     
        
        print("GEAR SUMMARY GENERATION ")
        session_id = event["session_id"]
        chat_query = f'''
        SELECT question,answer
        FROM {schema}.{banking_chat_history_table}    
        WHERE session_id = '{session_id}';
        '''
    
        chat_details = select_db(chat_query)
        print("GEAR CHAT DETAILS : ",chat_details)
        history = ""
    
        for chat in chat_details:
            history1 = "Human: "+chat[0]
            history2 = "Bot: "+chat[1]
            history += "\n"+history1+"\n"+history2+"\n"
        print("GEAR HISTORY : ",history)
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
        - Provide a clear summary of the conversation, capturing the customer's needs, questions, and any recurring themes.
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
		- Craft a highly personalized follow-up WhatsApp message to engage the customer effectively as a customer sales representative for Veltro Motors.
		- Ensure to provide a concise response and make it as brief as possible. Maximum 2-3 lines as it should be shown in the whatsapp mobile screen, so make the response brief.
        - Incorporate key details from the conversation script to show understanding and attentiveness (VERY IMPORTANT: ONLY INCLUDE DETAILS FROM THE CONVERSATION DO NOT HALLUCINATE ANY DETAILS).
        - Tailor the WhatsApp message to address specific concerns, provide solutions, and include a compelling call-to-action.
        - Infuse a sense of urgency or exclusivity to prompt customer response.
		- Format the WhatsApp message with real line breaks for each paragraph (not the string n). Use actual newlines to separate the greeting, body, call-to-action, and closing. 
	
	Follow the structure of the sample WhatsApp message below:
	<format_for_whatsapp_message>

Hi, Thanks for reaching out to Veltro Motors! 

You had a query about [Inquiry Topic]. Here's what you can do next:

1. [Step 1]  
2. [Step 2]

If you'd like, I can personally help you with [Offer/Action]. Just share your [Details Needed].

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
        print("GEAR PROMPT : ",prompt_template)
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
         # Initialize bedrock_client BEFORE branching so both paths can use it
        bedrock_client = boto3.client("bedrock-runtime", region_name=region_used)

        # Use appropriate API based on model type
        if is_nova_model:
            print(f"Using Nova model for gear summary generation: {selected_model}")
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
    
        # - Ensure the email content is formatted correctly with new lines. USE ONLY "\n" for new lines. 
        #         - Ensure the email content is formatted correctly for new lines instead of using new line characters.
        else:
            print(f"Using Claude model for gear summary generation: {model_id}")
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
                "message" : "Gear Summary Successfully Generated"
            }
    if event_type == 'kyc_extraction':
        return kyc_extraction_api(event)
    if event_type == 'list_gear_summary':
        session_id = event['session_id']
        chat_query = f'''
        SELECT question,answer
        FROM {schema}.{banking_chat_history_table}    
        WHERE session_id = '{session_id}';
        '''
    
        chat_details = select_db(chat_query)
        print("GEAR CHAT DETAILS : ",chat_details)
        history = []
    
        for chat in chat_details:
            history.append({"Human":chat[0],"Bot":chat[1]})
        print("GEAR HISTORY : ",history)  
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