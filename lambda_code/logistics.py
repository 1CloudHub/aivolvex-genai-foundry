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

# Get database credentials
db_user = os.environ['db_user']
db_host = os.environ['db_host']
db_port = os.environ['db_port']
db_database = os.environ['db_database']
region_used = os.environ["region_used"]

logger = logging.getLogger()
logger.setLevel(logging.INFO)

region_name = os.environ.get("region_name", region_used)


# ─── Secrets Manager ──────────────────────────────────────────────────────────

def get_db_password():
    try:
        secretsmanager = boto3.client('secretsmanager', region_name=region_used)
        secret_response = secretsmanager.get_secret_value(SecretId=os.environ['rds_secret_name'])
        secret = json.loads(secret_response['SecretString'])
        return secret['password']
    except Exception as e:
        print(f"Error retrieving password from Secrets Manager: {e}")
        return None


db_password = get_db_password()

# ─── Environment variables ─────────────────────────────────────────────────────

schema = os.environ.get('schema', 'genaifoundry')
chat_history_table = os.environ['chat_history_table']
prompt_metadata_table = os.environ['prompt_metadata_table']
model_id = os.environ['model_id']
KB_ID = os.environ['KB_ID']
CHAT_LOG_TABLE = os.environ['CHAT_LOG_TABLE']
socket_endpoint = os.environ["socket_endpoint"]

# Model selection for logistics_tool event type
logistics_chat_tool_model = os.environ.get("logistics_chat_tool_model", "claude").lower()

banking_chat_history_table = os.environ['banking_chat_history_table']

# ─── AWS clients ───────────────────────────────────────────────────────────────

retrieve_client = boto3.client('bedrock-agent-runtime', region_name=region_used)
bedrock_client = boto3.client('bedrock-runtime', region_name=region_used)
api_gateway_client = boto3.client('apigatewaymanagementapi', endpoint_url=socket_endpoint)
bedrock = boto3.client('bedrock-runtime', region_name=region_used)
bedrock_runtime = boto3.client('bedrock-runtime', region_name=region_used)

# ─── Database helpers ──────────────────────────────────────────────────────────

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


def insert_db(query, values):
    connection = psycopg2.connect(
        user=db_user,
        password=db_password,
        host=db_host,
        port=db_port,
        database=db_database
    )
    cursor = connection.cursor()
    cursor.execute(query, values)
    connection.commit()
    cursor.close()
    connection.close()


# ─── WebSocket keepalive ───────────────────────────────────────────────────────

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


# ─── Validation helpers ────────────────────────────────────────────────────────

def validate_phone_number(phone):
    """
    Validate phone number format - must contain exactly 8 digits (local) or 10 digits (with country code 65)
    after stripping all non-digit characters
    """
    if not phone:
        return False, "Phone number is required"

    digits_only = re.sub(r'[^\d]', '', str(phone))

    if len(digits_only) == 8:
        return True, digits_only
    elif len(digits_only) == 10 and digits_only.startswith('65'):
        return True, digits_only[2:]
    else:
        return False, f"Invalid phone number. Please provide a phone number with exactly 8 digits (or 10 digits with country code 65). You provided {len(digits_only)} digits."


def validate_email(email):
    """Validate email address format"""
    if not email:
        return False, "Email address is required"

    email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'

    if re.match(email_pattern, str(email).strip()):
        return True, str(email).strip()
    else:
        return False, "Invalid email address format. Please provide a valid email address (e.g., name@example.com)."


# ─── Summary extraction helper ─────────────────────────────────────────────────

def extract_sections(llm_response):
    patterns = {
        "Topic": r'"Topic":\s*"([^"]+)"',
        "Conversation Type": r'"Conversation Type":\s*"([^"]+)"',
        "Conversation Summary Explanation": r'"Conversation Summary Explanation":\s*"([^"]+)"',
        "Detailed Summary": r'"Detailed Summary":\s*"([^"]+)"',
        "Conversation Sentiment": r'"Conversation Sentiment":\s*"([^"]+)"',
        "Conversation Sentiment Generated Details": r'"Conversation Sentiment Generated Details":\s*"([^"]+)"',
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


# ─── Logistics KB retrieval ────────────────────────────────────────────────────

def get_logistics_faq_chunks(query):
    """
    Retrieve FAQ chunks from the Logistics knowledge base.
    Used for Logistics FAQs queries.
    """
    try:
        print("IN LOGISTICS FAQ: ", query)
        chat = query['knowledge_base_retrieval_question']
        chunks = []
        logistics_kb_id = os.environ.get('LOGISTICS_KB_ID', KB_ID)
        response_chunks = retrieve_client.retrieve(
            retrievalQuery={
                'text': chat
            },
            knowledgeBaseId=logistics_kb_id,
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

        print('LOGISTICS FAQ CHUNKS: ', chunks)

        if chunks:
            return chunks
        else:
            return ["I don't have specific information about that in our current knowledge base. Please contact our support team for detailed information."]

    except Exception as e:
        print("An exception occurred while retrieving logistics FAQ chunks:", e)
        return ["I'm having trouble accessing that information right now. Please try again in a moment, or contact our support team."]


# ─── logistics_agent_invoke_tool (Claude) ─────────────────────────────────────

def logistics_agent_invoke_tool(chat_history, session_id, chat, connectionId):
    """
    Logistics agent invoke tool function for logistics operations.
    Handles Shipment Tracking, Port-to-Port Routes, Cargo Claims Filing, and Logistics FAQs.
    """
    try:
        keepalive_thread = send_keepalive(connectionId, 30)
        import uuid
        import random
        import re

        base_prompt = f'''You are a Virtual Logistics Assistant, a helpful and accurate chatbot for logistics and shipping operations. You help customers with shipment tracking, port-to-port route information, cargo claims filing, and general logistics questions.

## CRITICAL INSTRUCTIONS:
- **NEVER** reply with any message that says you are checking, looking up, or finding information (such as "I'll check that for you", "Let me look that up", "One moment", "I'll find out", etc.).
- **NEVER** say "To answer your question about [topic], let me check our knowledge base" or similar phrases.
- After using a tool, IMMEDIATELY provide only the direct answer or summary to the user, with no filler, no explanations, and no mention of checking or looking up.
- If a user asks a question that requires a tool, use the tool and reply ONLY with the answer or summary, never with any statement about the process.
- For questions about shipments, routes, claims, or general logistics, IMMEDIATELY use the appropriate tool WITHOUT any preliminary message.

## Tool Usage Rules:
- When a user asks to track a shipment (e.g., "Track my shipment SHIP-12345"), IMMEDIATELY use the shipment_tracking_tool
- When a user asks about port-to-port routes (e.g., "What's the route from Singapore to Los Angeles?"), IMMEDIATELY use the port_to_port_routes_tool
- When a user wants to file a cargo claim (e.g., "I need to file a cargo claim"), IMMEDIATELY start collecting required information using the cargo_claims_filing_tool
- When a user asks general logistics questions (e.g., "What documents do I need for international shipping?"), IMMEDIATELY use the logistics_faq_tool
- Do NOT announce that you're using the tool or searching for information
- Simply use the tool and provide the direct answer from the knowledge base or system
- If the knowledge base doesn't have the information, say "I don't have specific information about that in our current knowledge base. Please contact our support team for detailed information."

## MANDATORY QUESTION COLLECTION RULES FOR CARGO CLAIMS:
- **ALWAYS** collect ALL required information for cargo claims before using the tool
- **NEVER** skip any required questions, even if the user provides some information
- **NEVER** assume or guess missing information
- **NEVER** proceed with incomplete information
- **CRITICAL VALIDATION RULE**: Validate each field IMMEDIATELY after the user provides it. Do NOT wait to collect all three fields before validating. If ANY validation fails, STOP the conversation immediately and inform the user. DO NOT proceed with asking for shipment or claim details.
- Ask questions ONE AT A TIME in this exact order:

### For cargo_claims_filing_tool (ask in this exact order):
1. Customer Name (full name) - MUST validate IMMEDIATELY when provided: at least 2 characters, only letters/spaces/hyphens/apostrophes. If invalid, STOP immediately.
2. Customer Email - MUST validate IMMEDIATELY when provided: proper email format (e.g., name@example.com). If invalid, STOP immediately.
3. Customer Phone Number - MUST validate IMMEDIATELY when provided: exactly 8 digits (after removing non-digit characters). If invalid, STOP immediately.
5. Shipment ID (e.g., "SHIP-12345") - ONLY ask if customer details are validated
6. Claim Type (e.g., "damage", "loss", "delay") - ONLY ask if customer details are validated
7. Date of Incident (accept any reasonable format: "yesterday", "January 15", "2025-01-15", etc.) - ONLY ask if customer details are validated
8. Description of Incident (brief description of what happened) - ONLY ask if customer details are validated
9. Claim Amount (e.g., "SGD 5000", "USD 10000") - ONLY ask if customer details are validated

## VALIDATION RULES FOR CUSTOMER DETAILS (VALIDATE IMMEDIATELY):
- **CRITICAL**: Validate each field IMMEDIATELY after the user provides it. Do NOT wait to collect all fields before validating.
- **CRITICAL**: NEVER ask for information that has already been provided. Always check chat history to see what information you already have before asking any question.
- **CRITICAL**: You MUST validate using the customer database below and shipment_tracking_tool. Do NOT proceed to the next question until validation passes.

## CUSTOMER DATABASE (for validation):
Use this database to validate email-phone matching:
- john.tan@email.com → Phone: 6591234567 (or +65 9123 4567)
- sarah.lim@email.com → Phone: 6582345678 (or +65 8234 5678)
- michael.chen@email.com → Phone: 6573456789 (or +65 7345 6789)
- emily.wong@email.com → Phone: 6564567890 (or +65 6456 7890)
- david.ng@email.com → Phone: 6555678901 (or +65 5567 8901)

**Phone Number Normalization**: When comparing phone numbers, normalize them by removing all non-digit characters. If the result is 8 digits, assume Singapore country code 65 (making it 10 digits). Compare the normalized 10-digit numbers.

- **Email Validation** (validate immediately after user provides it):
- Step 1: Must be in valid email format (e.g., name@example.com)
- Step 2: Must exist in our customer database above (registered email)
- Validate BOTH format AND database existence immediately when user provides it
- If format is invalid, say: "Invalid email address format. Please provide a valid email address (e.g., name@example.com)." Then STOP the conversation immediately.
- If format is valid but email is not in database above, say: "The email address [email] is not registered in our system. Please provide a valid registered email address." Then STOP the conversation immediately.
- DO NOT ask for phone number or any other details if email validation fails

- **Phone Number Validation** (validate immediately after user provides it):
- Step 1: Must contain exactly 8 digits OR 10 digits with country code 65 (after removing all non-digit characters)
- Step 2: Must match the email address provided using the customer database above (email and phone must belong to the same customer)
- **HOW TO VALIDATE**: Normalize the phone number (remove all non-digits). If 8 digits, add "65" prefix to make 10 digits. Look up the email in the customer database above and compare the normalized phone with the stored phone.
- Validate BOTH format AND email-phone matching immediately when user provides it
- If format is invalid, say: "Invalid phone number. Please provide a phone number with exactly 8 digits (or 10 digits with country code 65)." Then STOP the conversation immediately.
- If format is valid but phone doesn't match email in the database above, say: "The phone number does not match the email address [email]. Please provide the correct phone number." Then STOP the conversation immediately. DO NOT reveal the correct phone number.
- DO NOT ask for shipment ID or any other details if phone validation fails

- **Shipment ID Validation** (validate immediately after user provides it):
- **HOW TO VALIDATE**: Use the shipment_tracking_tool to check if the shipment exists and get its customer information
- After getting shipment info from the tool, check:
    1. Shipment must exist (tool returns shipment info)
    2. Shipment's customer_email must match the email provided by user
    3. Shipment's customer_phone (normalized) must match the phone provided by user
- Validate BOTH existence AND customer ownership immediately when user provides it
- If shipment not found (tool returns "not found"), say: "Shipment [shipment_id] not found. Please verify your shipment ID." Then STOP.
- If shipment exists but customer_email doesn't match, say: "Shipment [shipment_id] does not belong to your account. Please provide the correct shipment ID for your account." Then STOP.
- If shipment exists but customer_phone doesn't match, say: "Shipment [shipment_id] does not belong to your account. Please provide the correct shipment ID for your account." Then STOP.
- DO NOT proceed with claim details if shipment validation fails

- **CRITICAL VALIDATION FLOW**:
1. Ask for Customer Email → User provides → Validate format AND database existence immediately using the customer database above → If invalid, STOP and inform user
2. If email is valid, ask for Phone → User provides → Validate format AND email-phone matching immediately using the customer database above → If invalid, STOP and inform user
3. If both email and phone are validated, ask for Shipment ID → User provides → IMMEDIATELY use shipment_tracking_tool to validate existence AND customer ownership → If invalid, STOP and inform user
4. Only if all three validations pass, proceed to ask for claim type, date, description, and amount
5. If ANY validation fails at ANY point, inform the user and STOP the conversation immediately
6. DO NOT proceed with asking for claim details if any validation fails
7. NEVER ask for information that has already been provided - always check chat history first
8. **MANDATORY**: You MUST use shipment_tracking_tool immediately after user provides shipment ID to validate it belongs to them. Do NOT wait to collect all information before validating.

## Response Format:
- ALWAYS answer in the shortest, most direct way possible
- Do NOT add extra greetings, confirmations, or explanations
- Do NOT mention backend systems or tools
- Speak naturally as a helpful logistics representative who already knows the information
- After every completed tool call (such as filing a claim), always include a clear summary of all user-provided inputs involved in that flow, followed by the final result (e.g., Claim ID, Tracking details).

The summary must include:
- All collected fields in the order they were asked
- The tool output (e.g., Claim confirmation or tracking details)

Example (for a filed cargo claim):
Your cargo claim has been filed successfully.
- Email: john.tan@email.com
- Phone: +65 9123 4567
- Shipment ID: SHIP-12345
- Claim Type: Damage
- Date of Incident: January 15, 2025
- Description: Container damaged during transit
- Claim Amount: SGD 5,000
- Claim ID: CLM20250115ABC123

**IMPORTANT**: When displaying the date of incident in the summary, use the formatted date from the tool result (date_of_incident_display field). If the user said "yesterday", the tool will convert it to the actual date (e.g., "January 15, 2025"). Always use the formatted date from the tool result, not the raw user input.

Available Tools:
1. shipment_tracking_tool - Track shipments and get real-time status updates
2. port_to_port_routes_tool - Lookup and optimize shipping routes between ports
3. cargo_claims_filing_tool - File cargo damage or loss claims
4. logistics_faq_tool - Answer general logistics questions using RAG technology from knowledge base

## SYSTEMATIC QUESTION COLLECTION:
- When a user wants to file a cargo claim, IMMEDIATELY start collecting required information
- Ask ONLY ONE question at a time
- After each user response, check what information is still missing
- Ask for the NEXT missing required field (in the exact order listed above)
- Do NOT ask multiple questions in one message
- Do NOT skip any required questions
- Do NOT proceed until ALL required information is collected

## HANDLING OUT-OF-ORDER INFORMATION:
- **CRITICAL**: Users may provide information out of order (e.g., providing a shipment ID when asked for email)
- **ALWAYS** recognize and store information even when provided out of order
- If a user provides a shipment ID (e.g., "SHIP-11111", "SHIP12345") when asked for email or phone:
- Recognize it as a shipment ID and store it for later use
- Acknowledge it briefly (e.g., "Got it, I've noted your shipment ID SHIP-11111.")
- Continue asking for the originally requested field (email or phone)
- If a user provides an email when asked for phone, or vice versa:
- Validate it immediately according to validation rules
- Store it and continue with the next required field
- **NEVER** reject or ignore valid information just because it was provided out of order
- **NEVER** ask for information that has already been provided, even if out of order
- After storing out-of-order information, continue with the normal question flow for remaining missing fields

## SESSION CONTINUITY AND MEMORY:
- **CRITICAL**: Once the user provides their information (email, phone, shipment ID), REMEMBER them for the entire session
- **CRITICAL**: ALWAYS check chat history before asking any question to see what information has already been provided
- **NEVER** ask for information that has already been provided in the current conversation
- **NEVER** re-ask for email, phone, or shipment ID if they were already provided and validated
- Use the same information for all subsequent tool calls and do NOT ask for them again
- Do NOT repeat questions that have already been answered
- Only ask for information that is still missing
- If user provides information out of order, store it and continue with the normal flow - do NOT ask for it again later

## INPUT ACCEPTANCE RULES:
- Do NOT validate, reject, or question the user's input for required fields
- Accept any reasonable date format (yesterday, January 15, 2025-01-15, etc.)
- Accept any reasonable amount format (SGD 5000, $5000, 5000 SGD, etc.)
- **NEVER** ask for specific formats - accept what the user provides

## RESPONSE GUIDELINES:
- For shipment tracking, IMMEDIATELY use the shipment_tracking_tool
- For port route queries, IMMEDIATELY use the port_to_port_routes_tool
- For cargo claims, IMMEDIATELY start collecting required information using cargo_claims_filing_tool
- For general logistics questions, IMMEDIATELY use the logistics_faq_tool
- ALWAYS answer in the shortest, most direct way possible
- Do NOT mention backend systems or tools
- Handle greetings warmly and ask how you can help with their logistics needs today
'''

        # Logistics tool schema
        logistics_tools = [
            {
                "name": "shipment_tracking_tool",
                "description": "Track shipments and get real-time status updates. Use this when customers want to track their shipment status.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "shipment_id": {"type": "string", "description": "Shipment ID (e.g., 'SHIP-12345', 'SHIP12345', or any shipment identifier provided by the user)"}
                    },
                    "required": ["shipment_id"]
                }
            },
            {
                "name": "port_to_port_routes_tool",
                "description": "Lookup and optimize shipping routes between ports. Use this when customers ask about routes between ports or want route optimization.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "origin_port": {"type": "string", "description": "Origin port name (e.g., 'Singapore', 'Los Angeles', 'Shanghai')"},
                        "destination_port": {"type": "string", "description": "Destination port name (e.g., 'Singapore', 'Los Angeles', 'Shanghai')"}
                    },
                    "required": ["origin_port", "destination_port"]
                }
            },
            {
                "name": "cargo_claims_filing_tool",
                "description": "File cargo damage or loss claims. Use this when customers want to file a claim for damaged, lost, or delayed cargo.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "shipment_id": {"type": "string", "description": "Shipment ID (e.g., 'SHIP-12345')"},
                        "claim_type": {"type": "string", "description": "Type of claim: 'damage', 'loss', or 'delay'"},
                        "date_of_incident": {"type": "string", "description": "Date of the incident (accept any format)"},
                        "description": {"type": "string", "description": "Brief description of what happened"},
                        "claim_amount": {"type": "string", "description": "Claim amount (e.g., 'SGD 5000', 'USD 10000')"},
                        "customer_email": {"type": "string", "description": "Customer's email address"},
                        "customer_phone": {"type": "string", "description": "Customer's phone number"}
                    },
                    "required": ["shipment_id", "claim_type", "date_of_incident", "description", "claim_amount", "customer_email", "customer_phone"]
                }
            },
            {
                "name": "logistics_faq_tool",
                "description": "Answer general logistics questions using RAG technology from knowledge base. Use this for questions about logistics procedures, documentation, shipping requirements, or general logistics information.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "knowledge_base_retrieval_question": {"type": "string", "description": "A question about logistics procedures, documentation, shipping requirements, or general logistics information to retrieve from the knowledge base."}
                    },
                    "required": ["knowledge_base_retrieval_question"]
                }
            }
        ]

        def validate_customer_email_phone_match(customer_email, customer_phone):
            """
            Validate that the email and phone number belong to the same customer in the shipment database.
            Returns (is_valid, message)
            """
            import re
            phone_digits = re.sub(r'[^\d]', '', str(customer_phone))

            if len(phone_digits) == 8:
                phone_digits = "65" + phone_digits
            elif len(phone_digits) == 10 and phone_digits.startswith('65'):
                pass
            else:
                return False, f"Invalid phone number format. Please provide a phone number with exactly 8 digits (or 10 digits with country code 65)."

            now = datetime.now()
            ordered_date = (now - timedelta(days=8)).strftime('%Y-%m-%d')

            customer_db = {
                "john.tan@email.com": {"phone": "6591234567", "name": "John Tan"},
                "sarah.lim@email.com": {"phone": "6582345678", "name": "Sarah Lim"},
                "michael.chen@email.com": {"phone": "6573456789", "name": "Michael Chen"},
                "emily.wong@email.com": {"phone": "6564567890", "name": "Emily Wong"},
                "david.ng@email.com": {"phone": "6555678901", "name": "David Ng"}
            }

            email_normalized = customer_email.lower().strip()

            if email_normalized not in customer_db:
                return False, f"The email address {customer_email} is not registered in our system. Please provide a valid registered email address."

            expected_phone = customer_db[email_normalized]["phone"]
            if phone_digits != expected_phone:
                return False, f"The phone number does not match the email address {customer_email}. Please provide the correct phone number."

            return True, "Valid"

        def validate_shipment_belongs_to_customer(shipment_id, customer_email, customer_phone):
            """
            Validate that the shipment ID belongs to the customer (email and phone).
            Returns (is_valid, message)
            """
            import re
            shipment_info = track_shipment(shipment_id)

            if not shipment_info:
                return False, f"Shipment {shipment_id} not found. Please verify your shipment ID."

            email_normalized = customer_email.lower().strip()
            shipment_email = shipment_info.get('customer_email', '').lower().strip()

            phone_digits = re.sub(r'[^\d]', '', str(customer_phone))
            shipment_phone_digits = re.sub(r'[^\d]', '', str(shipment_info.get('customer_phone', '')))

            if len(phone_digits) == 8:
                phone_digits = "65" + phone_digits
            elif len(phone_digits) == 10 and phone_digits.startswith('65'):
                pass

            if len(shipment_phone_digits) == 8:
                shipment_phone_digits = "65" + shipment_phone_digits
            elif len(shipment_phone_digits) == 10 and shipment_phone_digits.startswith('65'):
                pass

            if email_normalized != shipment_email:
                return False, f"Shipment {shipment_id} does not belong to the email address {customer_email}. Please provide the correct shipment ID for your account."

            if phone_digits != shipment_phone_digits:
                return False, f"Shipment {shipment_id} does not belong to the phone number provided. Please provide the correct shipment ID for your account."

            return True, "Valid"

        def track_shipment(shipment_id):
            """Track shipment status by shipment ID"""
            now = datetime.now()
            current_date = now.strftime('%Y-%m-%d')
            current_datetime = now.strftime('%Y-%m-%d %H:%M:%S')
            ordered_date = (now - timedelta(days=8)).strftime('%Y-%m-%d')

            shipment_base_data = {
                "SHIP-12345": {
                    "shipment_id": "SHIP-12345",
                    "status": "In Transit",
                    "current_location": "Port of Singapore",
                    "destination": "Port of Los Angeles",
                    "carrier": "Maersk Line",
                    "container_number": "MSKU1234567",
                    "customer_name": "John Tan",
                    "customer_phone": "+65 9123 4567",
                    "customer_email": "john.tan@email.com",
                    "ordered_date": ordered_date,
                    "eta_days": 12
                },
                "SHIP-67890": {
                    "shipment_id": "SHIP-67890",
                    "status": "Delivered",
                    "current_location": "Port of Los Angeles",
                    "destination": "Port of Los Angeles",
                    "carrier": "COSCO Shipping",
                    "container_number": "COSCO9876543",
                    "customer_name": "Sarah Lim",
                    "customer_phone": "+65 8234 5678",
                    "customer_email": "sarah.lim@email.com",
                    "ordered_date": ordered_date,
                    "delivery_days_ago": 2
                },
                "SHIP-11111": {
                    "shipment_id": "SHIP-11111",
                    "status": "At Origin",
                    "current_location": "Port of Shanghai",
                    "destination": "Port of Singapore",
                    "carrier": "Evergreen Line",
                    "container_number": "EGLV5555555",
                    "customer_name": "Michael Chen",
                    "customer_phone": "+65 7345 6789",
                    "customer_email": "michael.chen@email.com",
                    "ordered_date": ordered_date,
                    "eta_days": 18
                },
                "SHIP-22222": {
                    "shipment_id": "SHIP-22222",
                    "status": "In Transit",
                    "current_location": "Port of Hong Kong",
                    "destination": "Port of New York",
                    "carrier": "CMA CGM",
                    "container_number": "CMAU2222222",
                    "customer_name": "Emily Wong",
                    "customer_phone": "+65 6456 7890",
                    "customer_email": "emily.wong@email.com",
                    "ordered_date": ordered_date,
                    "eta_days": 15
                },
                "SHIP-33333": {
                    "shipment_id": "SHIP-33333",
                    "status": "Customs",
                    "current_location": "Port of Busan",
                    "destination": "Port of Antwerp",
                    "carrier": "Hapag-Lloyd",
                    "container_number": "HLBU3333333",
                    "customer_name": "David Ng",
                    "customer_phone": "+65 5567 8901",
                    "customer_email": "david.ng@email.com",
                    "ordered_date": ordered_date,
                    "eta_days": 8
                }
            }

            shipment_db = {}
            for key, base_data in shipment_base_data.items():
                shipment_info = base_data.copy()
                shipment_info["last_update"] = current_datetime
                shipment_info["ordered_date"] = base_data["ordered_date"]

                if base_data["status"] == "Delivered":
                    delivery_date = (now - timedelta(days=base_data["delivery_days_ago"])).strftime('%Y-%m-%d')
                    shipment_info["estimated_arrival"] = delivery_date
                    shipment_info["actual_arrival"] = delivery_date
                else:
                    eta_date = (now + timedelta(days=base_data["eta_days"])).strftime('%Y-%m-%d')
                    shipment_info["estimated_arrival"] = eta_date

                shipment_info.pop("eta_days", None)
                shipment_info.pop("delivery_days_ago", None)

                shipment_db[key] = shipment_info

            normalized_id = shipment_id.upper().replace('-', '')
            if shipment_id.upper() in shipment_db:
                return shipment_db[shipment_id.upper()]
            for key, value in shipment_db.items():
                if key.replace('-', '').upper() == normalized_id:
                    return value
            return None

        def get_port_route(origin_port, destination_port):
            """Get route information between two ports"""
            routes_db = {
                ("Singapore", "Los Angeles"): {
                    "origin": "Singapore",
                    "destination": "Los Angeles",
                    "distance": "8,500 nautical miles",
                    "estimated_transit_time": "18-22 days",
                    "common_carriers": ["Maersk Line", "COSCO Shipping", "CMA CGM"],
                    "route_description": "Trans-Pacific route via Pacific Ocean",
                    "major_ports_en_route": ["Hong Kong", "Tokyo", "Long Beach"]
                },
                ("Los Angeles", "Singapore"): {
                    "origin": "Los Angeles",
                    "destination": "Singapore",
                    "distance": "8,500 nautical miles",
                    "estimated_transit_time": "18-22 days",
                    "common_carriers": ["Maersk Line", "COSCO Shipping", "CMA CGM"],
                    "route_description": "Trans-Pacific route via Pacific Ocean",
                    "major_ports_en_route": ["Tokyo", "Hong Kong"]
                },
                ("Singapore", "Shanghai"): {
                    "origin": "Singapore",
                    "destination": "Shanghai",
                    "distance": "1,800 nautical miles",
                    "estimated_transit_time": "5-7 days",
                    "common_carriers": ["COSCO Shipping", "Evergreen Line", "OOCL"],
                    "route_description": "Intra-Asia route",
                    "major_ports_en_route": ["Hong Kong"]
                },
                ("Shanghai", "Singapore"): {
                    "origin": "Shanghai",
                    "destination": "Singapore",
                    "distance": "1,800 nautical miles",
                    "estimated_transit_time": "5-7 days",
                    "common_carriers": ["COSCO Shipping", "Evergreen Line", "OOCL"],
                    "route_description": "Intra-Asia route",
                    "major_ports_en_route": ["Hong Kong"]
                }
            }
            origin_normalized = origin_port.strip().title()
            dest_normalized = destination_port.strip().title()
            return routes_db.get((origin_normalized, dest_normalized))

        def parse_date_of_incident(date_input):
            """
            Parse date of incident from user input.
            Handles relative dates and converts them to actual dates.
            Returns formatted date string (YYYY-MM-DD) and display format.
            """
            from datetime import datetime, timedelta
            import re

            if not date_input:
                return None, None

            date_input_lower = date_input.strip().lower()
            now = datetime.now()

            if date_input_lower in ['yesterday', 'yday']:
                incident_date = now - timedelta(days=1)
            elif date_input_lower in ['today', 'now']:
                incident_date = now
            elif date_input_lower == 'tomorrow':
                incident_date = now + timedelta(days=1)
            elif 'day ago' in date_input_lower or 'days ago' in date_input_lower:
                match = re.search(r'(\d+)\s*days?\s*ago', date_input_lower)
                if match:
                    days = int(match.group(1))
                    incident_date = now - timedelta(days=days)
                else:
                    incident_date = now - timedelta(days=1)
            elif 'week ago' in date_input_lower or 'weeks ago' in date_input_lower:
                match = re.search(r'(\d+)\s*weeks?\s*ago', date_input_lower)
                if match:
                    weeks = int(match.group(1))
                    incident_date = now - timedelta(weeks=weeks)
                else:
                    incident_date = now - timedelta(weeks=1)
            elif 'month ago' in date_input_lower or 'months ago' in date_input_lower:
                match = re.search(r'(\d+)\s*months?\s*ago', date_input_lower)
                if match:
                    months = int(match.group(1))
                    incident_date = now - timedelta(days=months * 30)
                else:
                    incident_date = now - timedelta(days=30)
            else:
                try:
                    date_formats = [
                        '%Y-%m-%d',
                        '%m/%d/%Y',
                        '%d/%m/%Y',
                        '%B %d, %Y',
                        '%b %d, %Y',
                        '%d %B %Y',
                        '%d %b %Y',
                        '%Y-%m-%d %H:%M:%S',
                    ]

                    incident_date = None
                    for fmt in date_formats:
                        try:
                            incident_date = datetime.strptime(date_input.strip(), fmt)
                            break
                        except ValueError:
                            continue

                    if incident_date is None:
                        incident_date = now
                except Exception:
                    incident_date = now

            formatted_date = incident_date.strftime('%Y-%m-%d')
            display_date = incident_date.strftime('%B %d, %Y')

            return formatted_date, display_date

        def file_cargo_claim(shipment_id, claim_type, date_of_incident, description, claim_amount, customer_email, customer_phone, customer_name=None):
            """File a cargo claim"""
            claim_id = f"CLM{str(uuid.uuid4())[:8].upper()}"
            formatted_date, display_date = parse_date_of_incident(date_of_incident)
            return {
                "claim_id": claim_id,
                "status": "Submitted",
                "shipment_id": shipment_id,
                "claim_type": claim_type,
                "date_of_incident": formatted_date,
                "date_of_incident_display": display_date,
                "description": description,
                "claim_amount": claim_amount,
                "customer_email": customer_email,
                "customer_phone": customer_phone,
                "remarks": "Your cargo claim has been submitted. Our claims team will review it, and an agent will reach out to you shortly."
            }

        input_tokens = 0
        output_tokens = 0
        print("In logistics_agent_invoke_tool (Logistics Bot)")

        prompt = base_prompt

        try:
            response = bedrock_client.invoke_model_with_response_stream(
                contentType='application/json',
                body=json.dumps({
                    "anthropic_version": "bedrock-2023-05-31",
                    "max_tokens": 4000,
                    "temperature": 0,
                    "top_p": 0.999,
                    "system": prompt,
                    "tools": logistics_tools,
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

        tools_used = []
        tool_results = []

        for action in assistant_response:
            if action['type'] == 'tool_use':
                tools_used.append(action['name'])
                tool_name = action['name']
                tool_input = action['input']
                tool_result = None

                try:
                    heartbeat = {'type': 'heartbeat'}
                    api_gateway_client.post_to_connection(ConnectionId=connectionId, Data=json.dumps(heartbeat))
                except Exception as e:
                    print(f"Heartbeat send error: {e}")

                if tool_name == 'shipment_tracking_tool':
                    shipment_id = tool_input.get('shipment_id', '')
                    tracking_info = track_shipment(shipment_id)
                    if tracking_info:
                        tracking_text = f"Shipment Tracking Information:\n\n"
                        tracking_text += f"Shipment ID: {tracking_info['shipment_id']}\n"
                        tracking_text += f"Status: {tracking_info['status']}\n"
                        if 'ordered_date' in tracking_info:
                            tracking_text += f"Ordered Date: {tracking_info['ordered_date']}\n"
                        tracking_text += f"Current Location: {tracking_info['current_location']}\n"
                        tracking_text += f"Destination: {tracking_info['destination']}\n"
                        tracking_text += f"Estimated Arrival: {tracking_info.get('estimated_arrival', 'N/A')}\n"
                        if 'actual_arrival' in tracking_info:
                            tracking_text += f"Actual Arrival: {tracking_info['actual_arrival']}\n"
                        tracking_text += f"Carrier: {tracking_info['carrier']}\n"
                        tracking_text += f"Container Number: {tracking_info['container_number']}\n"
                        if 'customer_name' in tracking_info:
                            tracking_text += f"Customer Name: {tracking_info['customer_name']}\n"
                        if 'customer_phone' in tracking_info:
                            tracking_text += f"Customer Phone: {tracking_info['customer_phone']}\n"
                        if 'customer_email' in tracking_info:
                            tracking_text += f"Customer Email: {tracking_info['customer_email']}\n"
                        tracking_text += f"Last Update: {tracking_info['last_update']}\n"
                        tool_result = [tracking_text]
                    else:
                        tool_result = [f"Shipment {shipment_id} not found. Please verify your shipment ID or contact our support team."]

                elif tool_name == 'port_to_port_routes_tool':
                    origin_port = tool_input.get('origin_port', '')
                    destination_port = tool_input.get('destination_port', '')
                    route_info = get_port_route(origin_port, destination_port)
                    if route_info:
                        route_text = f"Port-to-Port Route Information:\n\n"
                        route_text += f"Origin: {route_info['origin']}\n"
                        route_text += f"Destination: {route_info['destination']}\n"
                        route_text += f"Distance: {route_info['distance']}\n"
                        route_text += f"Estimated Transit Time: {route_info['estimated_transit_time']}\n"
                        route_text += f"Route Description: {route_info['route_description']}\n"
                        route_text += f"Common Carriers: {', '.join(route_info['common_carriers'])}\n"
                        if 'major_ports_en_route' in route_info:
                            route_text += f"Major Ports En Route: {', '.join(route_info['major_ports_en_route'])}\n"
                        tool_result = [route_text]
                    else:
                        tool_result = [f"Route information not available for {origin_port} to {destination_port}. Please contact our support team for detailed route information."]

                elif tool_name == 'cargo_claims_filing_tool':
                    customer_email = tool_input.get('customer_email', '').strip()
                    customer_phone = tool_input.get('customer_phone', '').strip()
                    shipment_id = tool_input.get('shipment_id', '').strip()

                    is_valid_email, email_result = validate_email(customer_email)
                    if not is_valid_email:
                        tool_result = [email_result]
                    else:
                        is_valid_match, match_result = validate_customer_email_phone_match(customer_email, customer_phone)
                        if not is_valid_match:
                            tool_result = [match_result]
                        else:
                            is_valid_phone, phone_result = validate_phone_number(customer_phone)
                            if not is_valid_phone:
                                tool_result = [phone_result]
                            else:
                                is_valid_shipment, shipment_result = validate_shipment_belongs_to_customer(shipment_id, customer_email, customer_phone)
                                if not is_valid_shipment:
                                    tool_result = [shipment_result]
                                else:
                                    tool_result = file_cargo_claim(
                                        shipment_id,
                                        tool_input.get('claim_type', ''),
                                        tool_input.get('date_of_incident', ''),
                                        tool_input.get('description', ''),
                                        tool_input.get('claim_amount', ''),
                                        customer_email,
                                        phone_result,
                                        tool_input.get('customer_name', None)
                                    )

                elif tool_name == 'logistics_faq_tool':
                    try:
                        heartbeat = {'type': 'heartbeat'}
                        api_gateway_client.post_to_connection(ConnectionId=connectionId, Data=json.dumps(heartbeat))
                    except Exception as e:
                        print(f"Logistics FAQ heartbeat send error: {e}")

                    tool_result = get_logistics_faq_chunks({'knowledge_base_retrieval_question': tool_input['knowledge_base_retrieval_question']})

                    if not tool_result or len(tool_result) == 0:
                        tool_result = ["I don't have specific information about that in our current knowledge base. Please contact our support team for detailed information."]

                try:
                    print(f"Tool result type: {type(tool_result)}")
                    print(f"Tool result content: {tool_result}")

                    if isinstance(tool_result, list) and tool_result:
                        if isinstance(tool_result[0], dict):
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
                            content_text = "\n".join(str(item) for item in tool_result)
                    elif isinstance(tool_result, dict):
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
                    import traceback
                    print(f"Traceback: {traceback.format_exc()}")
                    continue

        if tools_used:
            if tool_results:
                print(f"Tool results to validate: {tool_results}")
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

                if valid_tool_results:
                    print(f"Adding {len(valid_tool_results)} valid tool results to chat history")
                    chat_history.append({'role': 'user', 'content': valid_tool_results})
                else:
                    print("No valid tool results to add to chat history")

            try:
                response = bedrock_client.invoke_model_with_response_stream(
                    contentType='application/json',
                    body=json.dumps({
                        "anthropic_version": "bedrock-2023-05-31",
                        "max_tokens": 4000,
                        "temperature": 0,
                        "system": prompt,
                        "tools": logistics_tools,
                        "messages": chat_history
                    }),
                    modelId=model_id
                )
            except Exception as e:
                print("AN ERROR OCCURRED IN SECOND CALL: ", e)
                error_response = "We are unable to assist right now please try again after few minutes"
                return {"answer": error_response, "question": chat, "session_id": session_id}

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

            final_answer = ''
            for block in assistant_response:
                if block['type'] == 'text':
                    final_answer += block.get('text', '')

            return {
                "answer": final_answer,
                "question": chat,
                "session_id": session_id,
                "input_tokens": str(input_tokens),
                "output_tokens": str(output_tokens)
            }
        else:
            final_answer = ''
            for block in assistant_response:
                if block['type'] == 'text':
                    final_answer += block.get('text', '')

            return {
                "answer": final_answer,
                "question": chat,
                "session_id": session_id,
                "input_tokens": str(input_tokens),
                "output_tokens": str(output_tokens)
            }

    except Exception as e:
        print(f"Unexpected error in logistics_agent_invoke_tool: {e}")
        import traceback
        print(f"Full traceback: {traceback.format_exc()}")
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


# ─── nova_logistics_agent_invoke_tool (Nova/Converse API) ─────────────────────

def nova_logistics_agent_invoke_tool(chat_history, session_id, chat, connectionId):
    """
    Nova model logistics agent invoke tool function using AWS Bedrock Converse API.
    Uses the same tools and logic as logistics_agent_invoke_tool but adapted for Nova Converse API.
    """
    try:
        keepalive_thread = send_keepalive(connectionId, 30)
        import uuid
        import random
        import re

        nova_region = os.environ.get("nova_region", region_used)
        nova_model_name = os.environ.get("nova_model_name", "us.amazon.nova-premier-v1:0")
        nova_bedrock_client = boto3.client("bedrock-runtime", region_name=nova_region)

        base_prompt = f'''You are a Virtual Logistics Assistant, a helpful and accurate chatbot for logistics and shipping operations. You help customers with shipment tracking, port-to-port route information, cargo claims filing, and general logistics questions.

## CRITICAL INSTRUCTIONS:
- **NEVER** reply with any message that says you are checking, looking up, or finding information (such as "I'll check that for you", "Let me look that up", "One moment", "I'll find out", etc.).
- **NEVER** say "To answer your question about [topic], let me check our knowledge base" or similar phrases.
- After using a tool, IMMEDIATELY provide only the direct answer or summary to the user, with no filler, no explanations, and no mention of checking or looking up.
- If a user asks a question that requires a tool, use the tool and reply ONLY with the answer or summary, never with any statement about the process.
- For questions about shipments, routes, claims, or general logistics, IMMEDIATELY use the appropriate tool WITHOUT any preliminary message.

## Tool Usage Rules:
- When a user asks to track a shipment (e.g., "Track my shipment SHIP-12345"), IMMEDIATELY use the shipment_tracking_tool
- When a user asks about port-to-port routes (e.g., "What's the route from Singapore to Los Angeles?"), IMMEDIATELY use the port_to_port_routes_tool
- When a user wants to file a cargo claim (e.g., "I need to file a cargo claim"), IMMEDIATELY start collecting required information using the cargo_claims_filing_tool
- When a user asks general logistics questions (e.g., "What documents do I need for international shipping?"), IMMEDIATELY use the logistics_faq_tool
- Do NOT announce that you're using the tool or searching for information
- Simply use the tool and provide the direct answer from the knowledge base or system
- If the knowledge base doesn't have the information, say "I don't have specific information about that in our current knowledge base. Please contact our support team for detailed information."

## MANDATORY QUESTION COLLECTION RULES FOR CARGO CLAIMS:
- **ALWAYS** collect ALL required information for cargo claims before using the tool
- **NEVER** skip any required questions, even if the user provides some information
- **NEVER** assume or guess missing information
- **NEVER** proceed with incomplete information
- **CRITICAL VALIDATION RULE**: Validate each field IMMEDIATELY after the user provides it. Do NOT wait to collect all three fields before validating. If ANY validation fails, STOP the conversation immediately and inform the user. DO NOT proceed with asking for shipment or claim details.
- Ask questions ONE AT A TIME in this exact order:

### For cargo_claims_filing_tool (ask in this exact order):
1. Customer Name (full name) - MUST validate IMMEDIATELY when provided: at least 2 characters, only letters/spaces/hyphens/apostrophes. If invalid, STOP immediately.
2. Customer Email - MUST validate IMMEDIATELY when provided: proper email format (e.g., name@example.com). If invalid, STOP immediately.
3. Customer Phone Number - MUST validate IMMEDIATELY when provided: exactly 8 digits (after removing non-digit characters). If invalid, STOP immediately.
5. Shipment ID (e.g., "SHIP-12345") - ONLY ask if customer details are validated
6. Claim Type (e.g., "damage", "loss", "delay") - ONLY ask if customer details are validated
7. Date of Incident (accept any reasonable format: "yesterday", "January 15", "2025-01-15", etc.) - ONLY ask if customer details are validated
8. Description of Incident (brief description of what happened) - ONLY ask if customer details are validated
9. Claim Amount (e.g., "SGD 5000", "USD 10000") - ONLY ask if customer details are validated

## VALIDATION RULES FOR CUSTOMER DETAILS (VALIDATE IMMEDIATELY):
- **CRITICAL**: Validate each field IMMEDIATELY after the user provides it. Do NOT wait to collect all fields before validating.
- **CRITICAL**: NEVER ask for information that has already been provided. Always check chat history to see what information you already have before asking any question.
- **CRITICAL**: You MUST validate using the customer database below and shipment_tracking_tool. Do NOT proceed to the next question until validation passes.

## CUSTOMER DATABASE (for validation):
Use this database to validate email-phone matching:
- john.tan@email.com → Phone: 6591234567 (or +65 9123 4567)
- sarah.lim@email.com → Phone: 6582345678 (or +65 8234 5678)
- michael.chen@email.com → Phone: 6573456789 (or +65 7345 6789)
- emily.wong@email.com → Phone: 6564567890 (or +65 6456 7890)
- david.ng@email.com → Phone: 6555678901 (or +65 5567 8901)

**Phone Number Normalization**: When comparing phone numbers, normalize them by removing all non-digit characters. If the result is 8 digits, assume Singapore country code 65 (making it 10 digits). Compare the normalized 10-digit numbers.

- **Email Validation** (validate immediately after user provides it):
- Step 1: Must be in valid email format (e.g., name@example.com)
- Step 2: Must exist in our customer database above (registered email)
- Validate BOTH format AND database existence immediately when user provides it
- If format is invalid, say: "Invalid email address format. Please provide a valid email address (e.g., name@example.com)." Then STOP the conversation immediately.
- If format is valid but email is not in database above, say: "The email address [email] is not registered in our system. Please provide a valid registered email address." Then STOP the conversation immediately.
- DO NOT ask for phone number or any other details if email validation fails

- **Phone Number Validation** (validate immediately after user provides it):
- Step 1: Must contain exactly 8 digits OR 10 digits with country code 65 (after removing all non-digit characters)
- Step 2: Must match the email address provided using the customer database above (email and phone must belong to the same customer)
- **HOW TO VALIDATE**: Normalize the phone number (remove all non-digits). If 8 digits, add "65" prefix to make 10 digits. Look up the email in the customer database above and compare the normalized phone with the stored phone.
- Validate BOTH format AND email-phone matching immediately when user provides it
- If format is invalid, say: "Invalid phone number. Please provide a phone number with exactly 8 digits (or 10 digits with country code 65)." Then STOP the conversation immediately.
- If format is valid but phone doesn't match email in the database above, say: "The phone number does not match the email address [email]. Please provide the correct phone number." Then STOP the conversation immediately. DO NOT reveal the correct phone number.
- DO NOT ask for shipment ID or any other details if phone validation fails

- **Shipment ID Validation** (validate immediately after user provides it):
- **HOW TO VALIDATE**: Use the shipment_tracking_tool to check if the shipment exists and get its customer information
- After getting shipment info from the tool, check:
    1. Shipment must exist (tool returns shipment info)
    2. Shipment's customer_email must match the email provided by user
    3. Shipment's customer_phone (normalized) must match the phone provided by user
- Validate BOTH existence AND customer ownership immediately when user provides it
- If shipment not found (tool returns "not found"), say: "Shipment [shipment_id] not found. Please verify your shipment ID." Then STOP.
- If shipment exists but customer_email doesn't match, say: "Shipment [shipment_id] does not belong to your account. Please provide the correct shipment ID for your account." Then STOP.
- If shipment exists but customer_phone doesn't match, say: "Shipment [shipment_id] does not belong to your account. Please provide the correct shipment ID for your account." Then STOP.
- DO NOT proceed with claim details if shipment validation fails

- **CRITICAL VALIDATION FLOW**:
1. Ask for Customer Email → User provides → Validate format AND database existence immediately using the customer database above → If invalid, STOP and inform user
2. If email is valid, ask for Phone → User provides → Validate format AND email-phone matching immediately using the customer database above → If invalid, STOP and inform user
3. If both email and phone are validated, ask for Shipment ID → User provides → IMMEDIATELY use shipment_tracking_tool to validate existence AND customer ownership → If invalid, STOP and inform user
4. Only if all three validations pass, proceed to ask for claim type, date, description, and amount
5. If ANY validation fails at ANY point, inform the user and STOP the conversation immediately
6. DO NOT proceed with asking for claim details if any validation fails
7. NEVER ask for information that has already been provided - always check chat history first
8. **MANDATORY**: You MUST use shipment_tracking_tool immediately after user provides shipment ID to validate it belongs to them. Do NOT wait to collect all information before validating.

## Response Format:
- ALWAYS answer in the shortest, most direct way possible
- Do NOT add extra greetings, confirmations, or explanations
- Do NOT mention backend systems or tools
- Speak naturally as a helpful logistics representative who already knows the information
- After every completed tool call (such as filing a claim), always include a clear summary of all user-provided inputs involved in that flow, followed by the final result (e.g., Claim ID, Tracking details).

Available Tools:
1. shipment_tracking_tool - Track shipments and get real-time status updates
2. port_to_port_routes_tool - Lookup and optimize shipping routes between ports
3. cargo_claims_filing_tool - File cargo damage or loss claims
4. logistics_faq_tool - Answer general logistics questions using RAG technology from knowledge base

## SESSION CONTINUITY AND MEMORY:
- **CRITICAL**: Once the user provides their information (email, phone, shipment ID), REMEMBER them for the entire session
- **CRITICAL**: ALWAYS check chat history before asking any question to see what information has already been provided
- **NEVER** ask for information that has already been provided in the current conversation
- **NEVER** re-ask for email, phone, or shipment ID if they were already provided and validated

## RESPONSE GUIDELINES:
- For shipment tracking, IMMEDIATELY use the shipment_tracking_tool
- For port route queries, IMMEDIATELY use the port_to_port_routes_tool
- For cargo claims, IMMEDIATELY start collecting required information using cargo_claims_filing_tool
- For general logistics questions, IMMEDIATELY use the logistics_faq_tool
- ALWAYS answer in the shortest, most direct way possible
- Do NOT mention backend systems or tools
- Handle greetings warmly and ask how you can help with their logistics needs today
'''

        # Logistics tool schema for Nova (Converse API format)
        logistics_tools_nova = [
            {
                "toolSpec": {
                    "name": "shipment_tracking_tool",
                    "description": "Track shipments and get real-time status updates. Use this when customers want to track their shipment status.",
                    "inputSchema": {
                        "json": {
                            "type": "object",
                            "properties": {
                                "shipment_id": {"type": "string", "description": "Shipment ID (e.g., 'SHIP-12345', 'SHIP12345', or any shipment identifier provided by the user)"}
                            },
                            "required": ["shipment_id"]
                        }
                    }
                }
            },
            {
                "toolSpec": {
                    "name": "port_to_port_routes_tool",
                    "description": "Lookup and optimize shipping routes between ports. Use this when customers ask about routes between ports or want route optimization.",
                    "inputSchema": {
                        "json": {
                            "type": "object",
                            "properties": {
                                "origin_port": {"type": "string", "description": "Origin port name (e.g., 'Singapore', 'Los Angeles', 'Shanghai')"},
                                "destination_port": {"type": "string", "description": "Destination port name (e.g., 'Singapore', 'Los Angeles', 'Shanghai')"}
                            },
                            "required": ["origin_port", "destination_port"]
                        }
                    }
                }
            },
            {
                "toolSpec": {
                    "name": "cargo_claims_filing_tool",
                    "description": "File cargo damage or loss claims. Use this when customers want to file a claim for damaged, lost, or delayed cargo.",
                    "inputSchema": {
                        "json": {
                            "type": "object",
                            "properties": {
                                "shipment_id": {"type": "string", "description": "Shipment ID (e.g., 'SHIP-12345')"},
                                "claim_type": {"type": "string", "description": "Type of claim: 'damage', 'loss', or 'delay'"},
                                "date_of_incident": {"type": "string", "description": "Date of the incident (accept any format)"},
                                "description": {"type": "string", "description": "Brief description of what happened"},
                                "claim_amount": {"type": "string", "description": "Claim amount (e.g., 'SGD 5000', 'USD 10000')"},
                                "customer_email": {"type": "string", "description": "Customer's email address"},
                                "customer_phone": {"type": "string", "description": "Customer's phone number"}
                            },
                            "required": ["shipment_id", "claim_type", "date_of_incident", "description", "claim_amount", "customer_email", "customer_phone"]
                        }
                    }
                }
            },
            {
                "toolSpec": {
                    "name": "logistics_faq_tool",
                    "description": "Answer general logistics questions using RAG technology from knowledge base. Use this for questions about logistics procedures, documentation, shipping requirements, or general logistics information.",
                    "inputSchema": {
                        "json": {
                            "type": "object",
                            "properties": {
                                "knowledge_base_retrieval_question": {"type": "string", "description": "A question about logistics procedures, documentation, shipping requirements, or general logistics information to retrieve from the knowledge base."}
                            },
                            "required": ["knowledge_base_retrieval_question"]
                        }
                    }
                }
            }
        ]

        def validate_customer_email_phone_match(customer_email, customer_phone):
            import re
            phone_digits = re.sub(r'[^\d]', '', str(customer_phone))

            if len(phone_digits) == 8:
                phone_digits = "65" + phone_digits
            elif len(phone_digits) == 10 and phone_digits.startswith('65'):
                pass
            else:
                return False, f"Invalid phone number format. Please provide a phone number with exactly 8 digits (or 10 digits with country code 65)."

            now = datetime.now()
            ordered_date = (now - timedelta(days=8)).strftime('%Y-%m-%d')

            customer_db = {
                "john.tan@email.com": {"phone": "6591234567", "name": "John Tan"},
                "sarah.lim@email.com": {"phone": "6582345678", "name": "Sarah Lim"},
                "michael.chen@email.com": {"phone": "6573456789", "name": "Michael Chen"},
                "emily.wong@email.com": {"phone": "6564567890", "name": "Emily Wong"},
                "david.ng@email.com": {"phone": "6555678901", "name": "David Ng"}
            }

            email_normalized = customer_email.lower().strip()

            if email_normalized not in customer_db:
                return False, f"The email address {customer_email} is not registered in our system. Please provide a valid registered email address."

            expected_phone = customer_db[email_normalized]["phone"]
            if phone_digits != expected_phone:
                return False, f"The phone number does not match the email address {customer_email}. Please provide the correct phone number."

            return True, "Valid"

        def validate_shipment_belongs_to_customer(shipment_id, customer_email, customer_phone):
            import re
            shipment_info = track_shipment(shipment_id)

            if not shipment_info:
                return False, f"Shipment {shipment_id} not found. Please verify your shipment ID."

            email_normalized = customer_email.lower().strip()
            shipment_email = shipment_info.get('customer_email', '').lower().strip()

            phone_digits = re.sub(r'[^\d]', '', str(customer_phone))
            shipment_phone_digits = re.sub(r'[^\d]', '', str(shipment_info.get('customer_phone', '')))

            if len(phone_digits) == 8:
                phone_digits = "65" + phone_digits
            elif len(phone_digits) == 10 and phone_digits.startswith('65'):
                pass

            if len(shipment_phone_digits) == 8:
                shipment_phone_digits = "65" + shipment_phone_digits
            elif len(shipment_phone_digits) == 10 and shipment_phone_digits.startswith('65'):
                pass

            if email_normalized != shipment_email:
                return False, f"Shipment {shipment_id} does not belong to the email address {customer_email}. Please provide the correct shipment ID for your account."

            if phone_digits != shipment_phone_digits:
                return False, f"Shipment {shipment_id} does not belong to the phone number provided. Please provide the correct shipment ID for your account."

            return True, "Valid"

        def track_shipment(shipment_id):
            now = datetime.now()
            current_date = now.strftime('%Y-%m-%d')
            current_datetime = now.strftime('%Y-%m-%d %H:%M:%S')
            ordered_date = (now - timedelta(days=8)).strftime('%Y-%m-%d')

            shipment_base_data = {
                "SHIP-12345": {
                    "shipment_id": "SHIP-12345",
                    "status": "In Transit",
                    "current_location": "Port of Singapore",
                    "destination": "Port of Los Angeles",
                    "carrier": "Maersk Line",
                    "container_number": "MSKU1234567",
                    "customer_name": "John Tan",
                    "customer_phone": "+65 9123 4567",
                    "customer_email": "john.tan@email.com",
                    "ordered_date": ordered_date,
                    "eta_days": 12
                },
                "SHIP-67890": {
                    "shipment_id": "SHIP-67890",
                    "status": "Delivered",
                    "current_location": "Port of Los Angeles",
                    "destination": "Port of Los Angeles",
                    "carrier": "COSCO Shipping",
                    "container_number": "COSCO9876543",
                    "customer_name": "Sarah Lim",
                    "customer_phone": "+65 8234 5678",
                    "customer_email": "sarah.lim@email.com",
                    "ordered_date": ordered_date,
                    "delivery_days_ago": 2
                },
                "SHIP-11111": {
                    "shipment_id": "SHIP-11111",
                    "status": "At Origin",
                    "current_location": "Port of Shanghai",
                    "destination": "Port of Singapore",
                    "carrier": "Evergreen Line",
                    "container_number": "EGLV5555555",
                    "customer_name": "Michael Chen",
                    "customer_phone": "+65 7345 6789",
                    "customer_email": "michael.chen@email.com",
                    "ordered_date": ordered_date,
                    "eta_days": 18
                },
                "SHIP-22222": {
                    "shipment_id": "SHIP-22222",
                    "status": "In Transit",
                    "current_location": "Port of Hong Kong",
                    "destination": "Port of New York",
                    "carrier": "CMA CGM",
                    "container_number": "CMAU2222222",
                    "customer_name": "Emily Wong",
                    "customer_phone": "+65 6456 7890",
                    "customer_email": "emily.wong@email.com",
                    "ordered_date": ordered_date,
                    "eta_days": 15
                },
                "SHIP-33333": {
                    "shipment_id": "SHIP-33333",
                    "status": "Customs",
                    "current_location": "Port of Busan",
                    "destination": "Port of Antwerp",
                    "carrier": "Hapag-Lloyd",
                    "container_number": "HLBU3333333",
                    "customer_name": "David Ng",
                    "customer_phone": "+65 5567 8901",
                    "customer_email": "david.ng@email.com",
                    "ordered_date": ordered_date,
                    "eta_days": 8
                }
            }

            shipment_db = {}
            for key, base_data in shipment_base_data.items():
                shipment_info = base_data.copy()
                shipment_info["last_update"] = current_datetime
                shipment_info["ordered_date"] = base_data["ordered_date"]

                if base_data["status"] == "Delivered":
                    delivery_date = (now - timedelta(days=base_data["delivery_days_ago"])).strftime('%Y-%m-%d')
                    shipment_info["estimated_arrival"] = delivery_date
                    shipment_info["actual_arrival"] = delivery_date
                else:
                    eta_date = (now + timedelta(days=base_data["eta_days"])).strftime('%Y-%m-%d')
                    shipment_info["estimated_arrival"] = eta_date

                shipment_info.pop("eta_days", None)
                shipment_info.pop("delivery_days_ago", None)

                shipment_db[key] = shipment_info

            normalized_id = shipment_id.upper().replace('-', '')
            if shipment_id.upper() in shipment_db:
                return shipment_db[shipment_id.upper()]
            for key, value in shipment_db.items():
                if key.replace('-', '').upper() == normalized_id:
                    return value
            return None

        def get_port_route(origin_port, destination_port):
            routes_db = {
                ("Singapore", "Los Angeles"): {
                    "origin": "Singapore",
                    "destination": "Los Angeles",
                    "distance": "8,500 nautical miles",
                    "estimated_transit_time": "18-22 days",
                    "common_carriers": ["Maersk Line", "COSCO Shipping", "CMA CGM"],
                    "route_description": "Trans-Pacific route via Pacific Ocean",
                    "major_ports_en_route": ["Hong Kong", "Tokyo", "Long Beach"]
                },
                ("Los Angeles", "Singapore"): {
                    "origin": "Los Angeles",
                    "destination": "Singapore",
                    "distance": "8,500 nautical miles",
                    "estimated_transit_time": "18-22 days",
                    "common_carriers": ["Maersk Line", "COSCO Shipping", "CMA CGM"],
                    "route_description": "Trans-Pacific route via Pacific Ocean",
                    "major_ports_en_route": ["Tokyo", "Hong Kong"]
                },
                ("Singapore", "Shanghai"): {
                    "origin": "Singapore",
                    "destination": "Shanghai",
                    "distance": "1,800 nautical miles",
                    "estimated_transit_time": "5-7 days",
                    "common_carriers": ["COSCO Shipping", "Evergreen Line", "OOCL"],
                    "route_description": "Intra-Asia route",
                    "major_ports_en_route": ["Hong Kong"]
                },
                ("Shanghai", "Singapore"): {
                    "origin": "Shanghai",
                    "destination": "Singapore",
                    "distance": "1,800 nautical miles",
                    "estimated_transit_time": "5-7 days",
                    "common_carriers": ["COSCO Shipping", "Evergreen Line", "OOCL"],
                    "route_description": "Intra-Asia route",
                    "major_ports_en_route": ["Hong Kong"]
                }
            }
            origin_normalized = origin_port.strip().title()
            dest_normalized = destination_port.strip().title()
            return routes_db.get((origin_normalized, dest_normalized))

        def parse_date_of_incident(date_input):
            from datetime import datetime, timedelta
            import re

            if not date_input:
                return None, None

            date_input_lower = date_input.strip().lower()
            now = datetime.now()

            if date_input_lower in ['yesterday', 'yday']:
                incident_date = now - timedelta(days=1)
            elif date_input_lower in ['today', 'now']:
                incident_date = now
            elif date_input_lower == 'tomorrow':
                incident_date = now + timedelta(days=1)
            elif 'day ago' in date_input_lower or 'days ago' in date_input_lower:
                match = re.search(r'(\d+)\s*days?\s*ago', date_input_lower)
                if match:
                    days = int(match.group(1))
                    incident_date = now - timedelta(days=days)
                else:
                    incident_date = now - timedelta(days=1)
            elif 'week ago' in date_input_lower or 'weeks ago' in date_input_lower:
                match = re.search(r'(\d+)\s*weeks?\s*ago', date_input_lower)
                if match:
                    weeks = int(match.group(1))
                    incident_date = now - timedelta(weeks=weeks)
                else:
                    incident_date = now - timedelta(weeks=1)
            elif 'month ago' in date_input_lower or 'months ago' in date_input_lower:
                match = re.search(r'(\d+)\s*months?\s*ago', date_input_lower)
                if match:
                    months = int(match.group(1))
                    incident_date = now - timedelta(days=months * 30)
                else:
                    incident_date = now - timedelta(days=30)
            else:
                try:
                    date_formats = [
                        '%Y-%m-%d',
                        '%m/%d/%Y',
                        '%d/%m/%Y',
                        '%B %d, %Y',
                        '%b %d, %Y',
                        '%d %B %Y',
                        '%d %b %Y',
                        '%Y-%m-%d %H:%M:%S',
                    ]
                    incident_date = None
                    for fmt in date_formats:
                        try:
                            incident_date = datetime.strptime(date_input.strip(), fmt)
                            break
                        except ValueError:
                            continue
                    if incident_date is None:
                        incident_date = now
                except Exception:
                    incident_date = now

            formatted_date = incident_date.strftime('%Y-%m-%d')
            display_date = incident_date.strftime('%B %d, %Y')
            return formatted_date, display_date

        def file_cargo_claim(shipment_id, claim_type, date_of_incident, description, claim_amount, customer_email, customer_phone, customer_name=None):
            claim_id = f"CLM{str(uuid.uuid4())[:8].upper()}"
            formatted_date, display_date = parse_date_of_incident(date_of_incident)
            return {
                "claim_id": claim_id,
                "status": "Submitted",
                "shipment_id": shipment_id,
                "claim_type": claim_type,
                "date_of_incident": formatted_date,
                "date_of_incident_display": display_date,
                "description": description,
                "claim_amount": claim_amount,
                "customer_email": customer_email,
                "customer_phone": customer_phone,
                "remarks": "Your cargo claim has been submitted. Our claims team will review it, and an agent will reach out to you shortly."
            }

        input_tokens = 0
        output_tokens = 0
        print("In nova_logistics_agent_invoke_tool (Logistics Bot - Nova)")

        # Convert chat_history to Nova format
        message_history = []
        for msg in chat_history:
            if msg['role'] == 'user':
                content_items = []
                for content_item in msg['content']:
                    if isinstance(content_item, dict):
                        if content_item.get('type') == 'text':
                            content_items.append({"text": content_item.get('text', '')})
                        elif content_item.get('type') == 'tool_result':
                            content_items.append({
                                "toolResult": {
                                    "toolUseId": content_item.get('tool_use_id', ''),
                                    "content": content_item.get('content', []),
                                    "status": "success"
                                }
                            })
                if content_items:
                    message_history.append({"role": "user", "content": content_items})
            elif msg['role'] == 'assistant':
                content_items = []
                for content_item in msg['content']:
                    if isinstance(content_item, dict):
                        if content_item.get('type') == 'text':
                            content_items.append({"text": content_item.get('text', '')})
                if content_items:
                    message_history.append({"role": "assistant", "content": content_items})

        enhanced_prompt = base_prompt

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
                    "tools": logistics_tools_nova
                }
            )
        except Exception as e:
            print("AN ERROR OCCURRED IN NOVA LOGISTICS: ", e)
            error_response = "We are unable to assist right now please try again after few minutes"
            return {"answer": error_response, "question": chat, "session_id": session_id}

        output_msg = (response.get('output') or {}).get('message') or {}
        assistant_response = output_msg.get('content') or []

        usage = response.get('usage', {})
        input_tokens += usage.get('inputTokens', 0)
        output_tokens += usage.get('outputTokens', 0)

        message_history.append({'role': 'assistant', 'content': assistant_response})

        tool_calls = []
        for item in assistant_response:
            if item.get('toolUse'):
                tool_calls.append(item)

        if tool_calls:
            tools_used = []
            tool_results = []
            processed_tool_use_ids = set()

            for tool_call_item in tool_calls:
                tool_call = tool_call_item['toolUse']
                tool_name = tool_call.get('name')
                tool_input = tool_call.get('input', {})
                tool_use_id = tool_call.get('toolUseId')

                if not tool_use_id:
                    print(f"Warning: tool_use_id is missing for tool {tool_name}, skipping")
                    continue

                if tool_use_id in processed_tool_use_ids:
                    print(f"Warning: Duplicate tool_use_id {tool_use_id} detected, skipping duplicate")
                    continue

                processed_tool_use_ids.add(tool_use_id)
                tool_result = None
                tools_used.append(tool_name)

                try:
                    heartbeat = {'type': 'heartbeat'}
                    api_gateway_client.post_to_connection(ConnectionId=connectionId, Data=json.dumps(heartbeat))
                except Exception as e:
                    print(f"Heartbeat send error: {e}")

                if tool_name == 'shipment_tracking_tool':
                    shipment_id = tool_input.get('shipment_id', '')
                    tracking_info = track_shipment(shipment_id)
                    if tracking_info:
                        tracking_text = f"Shipment Tracking Information:\n\n"
                        tracking_text += f"Shipment ID: {tracking_info['shipment_id']}\n"
                        tracking_text += f"Status: {tracking_info['status']}\n"
                        if 'ordered_date' in tracking_info:
                            tracking_text += f"Ordered Date: {tracking_info['ordered_date']}\n"
                        tracking_text += f"Current Location: {tracking_info['current_location']}\n"
                        tracking_text += f"Destination: {tracking_info['destination']}\n"
                        tracking_text += f"Estimated Arrival: {tracking_info.get('estimated_arrival', 'N/A')}\n"
                        if 'actual_arrival' in tracking_info:
                            tracking_text += f"Actual Arrival: {tracking_info['actual_arrival']}\n"
                        tracking_text += f"Carrier: {tracking_info['carrier']}\n"
                        tracking_text += f"Container Number: {tracking_info['container_number']}\n"
                        if 'customer_name' in tracking_info:
                            tracking_text += f"Customer Name: {tracking_info['customer_name']}\n"
                        if 'customer_phone' in tracking_info:
                            tracking_text += f"Customer Phone: {tracking_info['customer_phone']}\n"
                        if 'customer_email' in tracking_info:
                            tracking_text += f"Customer Email: {tracking_info['customer_email']}\n"
                        tracking_text += f"Last Update: {tracking_info['last_update']}\n"
                        tool_result = [tracking_text]
                    else:
                        tool_result = [f"Shipment {shipment_id} not found. Please verify your shipment ID or contact our support team."]

                elif tool_name == 'port_to_port_routes_tool':
                    origin_port = tool_input.get('origin_port', '')
                    destination_port = tool_input.get('destination_port', '')
                    route_info = get_port_route(origin_port, destination_port)
                    if route_info:
                        route_text = f"Port-to-Port Route Information:\n\n"
                        route_text += f"Origin: {route_info['origin']}\n"
                        route_text += f"Destination: {route_info['destination']}\n"
                        route_text += f"Distance: {route_info['distance']}\n"
                        route_text += f"Estimated Transit Time: {route_info['estimated_transit_time']}\n"
                        route_text += f"Route Description: {route_info['route_description']}\n"
                        route_text += f"Common Carriers: {', '.join(route_info['common_carriers'])}\n"
                        if 'major_ports_en_route' in route_info:
                            route_text += f"Major Ports En Route: {', '.join(route_info['major_ports_en_route'])}\n"
                        tool_result = [route_text]
                    else:
                        tool_result = [f"Route information not available for {origin_port} to {destination_port}. Please contact our support team for detailed route information."]

                elif tool_name == 'cargo_claims_filing_tool':
                    customer_email = tool_input.get('customer_email', '').strip()
                    customer_phone = tool_input.get('customer_phone', '').strip()

                    is_valid_email, email_result = validate_email(customer_email)
                    if not is_valid_email:
                        tool_result = [email_result]
                    else:
                        is_valid_phone, phone_result = validate_phone_number(customer_phone)
                        if not is_valid_phone:
                            tool_result = [phone_result]
                        else:
                            is_valid_match, match_result = validate_customer_email_phone_match(customer_email, customer_phone)
                            if not is_valid_match:
                                tool_result = [match_result]
                            else:
                                tool_result = file_cargo_claim(
                                    tool_input.get('shipment_id', ''),
                                    tool_input.get('claim_type', ''),
                                    tool_input.get('date_of_incident', ''),
                                    tool_input.get('description', ''),
                                    tool_input.get('claim_amount', ''),
                                    customer_email,
                                    phone_result,
                                    tool_input.get('customer_name', None)
                                )

                elif tool_name == 'logistics_faq_tool':
                    try:
                        heartbeat = {'type': 'heartbeat'}
                        api_gateway_client.post_to_connection(ConnectionId=connectionId, Data=json.dumps(heartbeat))
                    except Exception as e:
                        print(f"Logistics FAQ heartbeat send error: {e}")

                    tool_result = get_logistics_faq_chunks({'knowledge_base_retrieval_question': tool_input['knowledge_base_retrieval_question']})

                    if not tool_result or len(tool_result) == 0:
                        tool_result = ["I don't have specific information about that in our current knowledge base. Please contact our support team for detailed information."]

                try:
                    print(f"Tool result type: {type(tool_result)}")
                    print(f"Tool result content: {tool_result}")

                    if isinstance(tool_result, list) and tool_result:
                        if isinstance(tool_result[0], dict):
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
                            content_text = "\n".join(str(item) for item in tool_result)
                    elif isinstance(tool_result, dict):
                        formatted_item = []
                        for key, value in tool_result.items():
                            formatted_item.append(f"{key.replace('_', ' ').title()}: {value}")
                        content_text = "\n".join(formatted_item)
                    else:
                        content_text = str(tool_result) if tool_result else "No information available"

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
                    import traceback
                    print(f"Traceback: {traceback.format_exc()}")
                    continue

            if tool_results:
                print(f"Tool results to validate: {tool_results}")
                print(f"Number of tool calls: {len(tool_calls)}, Number of tool results: {len(tool_results)}")

                valid_tool_results = []
                seen_tool_use_ids = set()

                for tool_result in tool_results:
                    print(f"Validating tool result: {tool_result}")
                    if (tool_result and
                            isinstance(tool_result, dict) and
                            'toolResult' in tool_result and
                            tool_result['toolResult'].get('toolUseId') and
                            tool_result['toolResult'].get('content') and
                            len(tool_result['toolResult']['content']) > 0 and
                            tool_result['toolResult']['content'][0].get('text', '').strip()):

                        tool_use_id = tool_result['toolResult'].get('toolUseId')
                        if tool_use_id in seen_tool_use_ids:
                            print(f"Warning: Duplicate tool_use_id {tool_use_id} in tool results, skipping")
                            continue

                        seen_tool_use_ids.add(tool_use_id)
                        valid_tool_results.append(tool_result)
                        print(f"Tool result is valid: {tool_result}")
                    else:
                        print(f"Tool result is invalid: {tool_result}")

                if len(valid_tool_results) != len(tool_calls):
                    print(f"Warning: Tool result count ({len(valid_tool_results)}) doesn't match tool call count ({len(tool_calls)})")
                    if len(valid_tool_results) == 0:
                        print("Error: No valid tool results, cannot proceed")
                        return {"answer": "Error processing tool results", "question": chat, "session_id": session_id}

                if valid_tool_results and len(valid_tool_results) == len(tool_calls):
                    print(f"Adding {len(valid_tool_results)} valid tool results to chat history (matches {len(tool_calls)} tool calls)")
                    message_history.append({
                        "role": "user",
                        "content": valid_tool_results
                    })
                else:
                    print(f"Warning: Cannot add tool results - count mismatch or no valid results")
                    print(f"Valid results: {len(valid_tool_results)}, Tool calls: {len(tool_calls)}")
            else:
                print("No tool results to add to chat history")

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
                        "tools": logistics_tools_nova
                    }
                )

                print("Nova Model Final Response: ", final_response)

                final_output_msg = (final_response.get('output') or {}).get('message') or {}
                final_content_items = final_output_msg.get('content') or []

                final_ans = ""
                for item in final_content_items:
                    if item.get('text'):
                        text_content = item['text']
                        text_content = re.sub(r'<thinking>.*?</thinking>', '', text_content, flags=re.DOTALL | re.IGNORECASE).strip()
                        if text_content:
                            final_ans = text_content
                            break

                if not final_ans:
                    final_ans = "I apologize, but I couldn't retrieve the information at this time. Please try again or contact our support team."

                words = final_ans.split()
                for word in words:
                    delta = {'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'text_delta', 'text': word + ' '}}
                    try:
                        api_gateway_client.post_to_connection(ConnectionId=connectionId, Data=json.dumps(delta))
                    except Exception as e:
                        print(f"WebSocket send error (delta): {e}")

                stop_msg = {'type': 'content_block_stop', 'index': 0}
                try:
                    api_gateway_client.post_to_connection(ConnectionId=connectionId, Data=json.dumps(stop_msg))
                except Exception as e:
                    print(f"WebSocket send error (stop): {e}")

                message_stop = {'type': 'message_stop'}
                try:
                    api_gateway_client.post_to_connection(ConnectionId=connectionId, Data=json.dumps(message_stop))
                except Exception as e:
                    print(f"WebSocket send error (message_stop): {e}")

                final_usage = final_response.get('usage', {})
                input_tokens += final_usage.get('inputTokens', 0)
                output_tokens += final_usage.get('outputTokens', 0)

                return {"statusCode": "200", "answer": final_ans, "question": chat, "session_id": session_id, "input_tokens": str(input_tokens), "output_tokens": str(output_tokens)}

            except Exception as e:
                print(f"Error in final Nova logistics response: {e}")
                import traceback
                print(f"Full traceback: {traceback.format_exc()}")
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
            final_ans = ""
            for item in assistant_response:
                if item.get('text'):
                    text_content = item['text']
                    text_content = re.sub(r'<thinking>.*?</thinking>', '', text_content, flags=re.DOTALL | re.IGNORECASE).strip()
                    if text_content:
                        final_ans = text_content
                        break

            if not final_ans:
                final_ans = "I'm here to help with your logistics needs. How can I assist you today?"

            words = final_ans.split()
            for word in words:
                delta = {'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'text_delta', 'text': word + ' '}}
                try:
                    api_gateway_client.post_to_connection(ConnectionId=connectionId, Data=json.dumps(delta))
                except Exception as e:
                    print(f"WebSocket send error (delta): {e}")

            stop_msg = {'type': 'content_block_stop', 'index': 0}
            try:
                api_gateway_client.post_to_connection(ConnectionId=connectionId, Data=json.dumps(stop_msg))
            except Exception as e:
                print(f"WebSocket send error (stop): {e}")

            message_stop = {'type': 'message_stop'}
            try:
                api_gateway_client.post_to_connection(ConnectionId=connectionId, Data=json.dumps(message_stop))
            except Exception as e:
                print(f"WebSocket send error (message_stop): {e}")

            return {"statusCode": "200", "answer": final_ans, "question": chat, "session_id": session_id, "input_tokens": str(input_tokens), "output_tokens": str(output_tokens)}

    except Exception as e:
        print(f"Unexpected error in nova_logistics_agent_invoke_tool: {e}")
        import traceback
        print(f"Full traceback: {traceback.format_exc()}")
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


# ─── Lambda handler ────────────────────────────────────────────────────────────

def lambda_handler(event, context):
    import json
    print(f"[RAW EVENT FULL]: {json.dumps(event)}")
    print("Event: ", event)

    event_type = event.get("event_type", "")

    # ── logistics_tool ──────────────────────────────────────────────────────────
    if event_type == 'logistics_tool':

        chat = event['chat']
        session_id = event['session_id']
        connectionId = event["connectionId"]
        print(connectionId, "connectionid_printtt")

        selected_model = logistics_chat_tool_model
        print(f"Using model from environment variable: {selected_model}")
        chat_history = []

        if session_id is None or session_id == 'null' or session_id == '':
            session_id = str(uuid.uuid4())
        else:
            query = f'''select question,answer 
                    from {schema}.{banking_chat_history_table} 
                    where session_id = '{session_id}' 
                    order by created_on desc limit 20;'''
            history_response = select_db(query)
            print("history_response is ", history_response)

            if len(history_response) > 0:
                for chat_session in reversed(history_response):
                    if chat_session[0] and str(chat_session[0]).strip():
                        chat_history.append({'role': 'user', 'content': [{"type": "text", 'text': str(chat_session[0]).strip()}]})
                    if chat_session[1] and str(chat_session[1]).strip():
                        chat_history.append({'role': 'assistant', 'content': [{"type": "text", 'text': str(chat_session[1]).strip()}]})

        if chat and str(chat).strip():
            chat_history.append({'role': 'user', 'content': [{"type": "text", 'text': str(chat).strip()}]})

        print("CHAT HISTORY : ", chat_history)

        is_nova_model = (
            selected_model == 'nova' or
            selected_model.startswith('us.amazon.nova') or
            selected_model.startswith('nova-') or
            ('.nova' in selected_model and 'claude' not in selected_model)
        )

        if is_nova_model:
            print(f"Routing to Nova logistics model handler (detected model: {selected_model})")
            tool_response = nova_logistics_agent_invoke_tool(chat_history, session_id, chat, connectionId)
        else:
            print(f"Routing to Claude logistics model handler (detected model: {selected_model})")
            tool_response = logistics_agent_invoke_tool(chat_history, session_id, chat, connectionId)

        print("TOOL RESPONSE: ", tool_response)

        query = f'''
                INSERT INTO {schema}.{banking_chat_history_table}
                (session_id, question, answer, input_tokens, output_tokens, created_on, updated_on)
                VALUES( %s, %s, %s, %s, %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);
                '''
        input_tokens = tool_response.get('input_tokens', '0')
        output_tokens = tool_response.get('output_tokens', '0')
        answer = tool_response.get('answer', '')

        values = (str(session_id), str(chat), str(answer), str(input_tokens), str(output_tokens))
        res = insert_db(query, values)
        print("response:", res)

        print(type(session_id))
        insert_query = f'''  INSERT INTO genaifoundry.ce_cexp_logs      
(created_on, environment, session_time, "lead", enquiry, complaint, summary, whatsapp_content, next_best_action, session_id, lead_explanation, sentiment, sentiment_explanation, connectionid, input_token, output_token,topic)
VALUES(CURRENT_TIMESTAMP, %s, CURRENT_TIMESTAMP, %s, 0, 0, %s, %s, %s, %s, %s, %s, %s, %s, 0, 0,%s);'''
        values = ('', None, '', '', '', session_id, '', '', '', '', '')
        res = insert_db(insert_query, values)
        return tool_response

    # ── generate_logistics_summary ──────────────────────────────────────────────
    if event_type == "generate_logistics_summary":

        print("LOGISTICS SUMMARY GENERATION ")
        session_id = event["session_id"]
        chat_query = f'''
        SELECT question,answer
        FROM {schema}.{banking_chat_history_table}    
        WHERE session_id = '{session_id}';
        '''

        chat_details = select_db(chat_query)
        print("LOGISTICS CHAT DETAILS : ", chat_details)
        history = ""

        for chat in chat_details:
            history1 = "Human: " + chat[0]
            history2 = "Bot: " + chat[1]
            history += "\n" + history1 + "\n" + history2 + "\n"
        print("LOGISTICS HISTORY : ", history)

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
        - Craft a highly personalized follow-up WhatsApp message to engage the customer effectively as a customer sales representative for Logistics Services.
        - Ensure to provide a concise response and make it as brief as possible. Maximum 2-3 lines as it should be shown in the whatsapp mobile screen, so make the response brief.
        - Incorporate key details from the conversation script to show understanding and attentiveness (VERY IMPORTANT: ONLY INCLUDE DETAILS FROM THE CONVERSATION DO NOT HALLUCINATE ANY DETAILS).
        - Tailor the WhatsApp message to address specific concerns, provide solutions, and include a compelling call-to-action.
        - Infuse a sense of urgency or exclusivity to prompt customer response.
        - Format the WhatsApp message with real line breaks for each paragraph (not the string n). Use actual newlines to separate the greeting, body, call-to-action, and closing. 
    
    Follow the structure of the sample WhatsApp message below (NOTE: DO NOT include the <format_for_whatsapp_message> tags in your output, these are only for reference):
    <format_for_whatsapp_message>

Hi, Thanks for reaching out to our Logistics Services! 

You had a query about [Inquiry Topic]. Here\'s what you can do next:

1. [Step 1]  
2. [Step 2]

If you\'d like, I can personally help you with [Offer/Action]. Just share your [Details Needed].

Looking forward to hearing from you soon.

</format_for_whatsapp_message>
    - CRITICAL: Your WhatsApp message should follow the format shown above, but DO NOT include the XML tags (<format_for_whatsapp_message> or </format_for_whatsapp_message>) in your response. Only provide the actual message content.
    - Before providing the whatsapp response, it is very critical that you double check if its in the provided format and that you have NOT included any XML tags


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
        print("LOGISTICS PROMPT : ", prompt_template)
        template = f'''
        <Conversation>
        {history}
        </Conversation>
        {prompt_template}
        '''

        selected_model = logistics_chat_tool_model
        is_nova_model = (
            selected_model == 'nova' or
            selected_model.startswith('us.amazon.nova') or
            selected_model.startswith('nova-') or
            ('.nova' in selected_model and 'claude' not in selected_model)
        )

        import boto3
        bedrock_client = boto3.client("bedrock-runtime", region_name=region_used)

        if is_nova_model:
            print(f"Using Nova model for logistics summary generation: {selected_model}")
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

            try:
                out = response.get("output", {}).get("message", {}).get("content", [])[0].get("text", "")
            except Exception as e:
                print(f"Error extracting Nova response: {e}")
                import traceback
                print(f"Full traceback: {traceback.format_exc()}")
                out = ""
        else:
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
            enquiry, complaint = 0, 0

        try:
            if 'Conversation Summary Explanation' in llm_out:
                conversation_summary_explanation = llm_out['Conversation Summary Explanation']
        except:
            conversation_summary_explanation = ""

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
                email_creation = email_creation.replace('\\n', '\n').replace('\\r', '\r').replace('\\t', '\t')
        except:
            email_creation = ""

        detailed_summary = detailed_summary.replace("'", "''")
        email_creation = email_creation.replace("'", "''")
        action_to_be_taken = action_to_be_taken.replace("'", "''")
        leads_generated_details = leads_generated_details.replace("'", "''")
        conversation_sentiment_generated_details = conversation_sentiment_generated_details.replace("'", "''")

        print("LEAD : ", lead)
        print("ENQUIRY : ", enquiry)
        print("COMPLAINT : ", complaint)
        print("conversation_type:", conversation_type)
        print("Topic: ", topic)
        print("Sentiment Explanation:", conversation_summary_explanation)
        print("Detailed summary:", detailed_summary)
        print("CONVERSATION SENTIMENT :", conversation_sentiment)
        print("CONVERSATION SENTIMENT DETAILS:", conversation_sentiment_generated_details)
        print("lead Sentiment:", lead_sentiment)
        print("lead explanation:", leads_generated_details)
        print("next_best_action:", action_to_be_taken)
        print("email_content:", email_creation)
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
            "statusCode": 200,
            "message": "Logistics Summary Successfully Generated"
        }

    # ── list_logistics_summary ──────────────────────────────────────────────────
    if event_type == 'list_logistics_summary':
        session_id = event['session_id']
        chat_query = f'''
        SELECT question,answer
        FROM {schema}.{banking_chat_history_table}    
        WHERE session_id = '{session_id}';
        '''

        chat_details = select_db(chat_query)
        print("LOGISTICS CHAT DETAILS : ", chat_details)
        history = []

        for chat in chat_details:
            history.append({"Human": chat[0], "Bot": chat[1]})
        print("LOGISTICS HISTORY : ", history)
        select_query = f'''select summary, whatsapp_content, sentiment, topic  from genaifoundry.ce_cexp_logs ccl where session_id = '{session_id}';'''
        summary_details = select_db(select_query)
        final_summary = {}
        for i in summary_details:
            final_summary['summary'] = i[0]
            final_summary['whatsapp_content'] = i[1]
            final_summary['sentiment'] = i[2]
            final_summary['Topic'] = i[3]

        return {"transcript": history, "final_summary": final_summary}

    return {"statusCode": 400, "message": f"Unknown event_type: {event_type}"}
