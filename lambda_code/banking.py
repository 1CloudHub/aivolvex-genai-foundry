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
bank_kb_id = os.environ['bank_kb_id']
KB_ID = os.environ['KB_ID']
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
chat_history = os.environ['chat_history']
banking_chat_history = os.environ['banking_chat_history']
banking_chat_history_table=os.environ['banking_chat_history_table']
prompt_metadata_table = os.environ['prompt_metadata_table']
model_id = os.environ['model_id']
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

    # body = json.dumps({
    #     "anthropic_version": "bedrock-2023-05-31",
    #     "max_tokens": 2048,
    #     "messages": [{"role": "user", "content": prompt}]
    # })
    
    # response = bedrock.invoke_model(
    #     modelId="us.anthropic.claude-3-7-sonnet-20250219-v1:0",
    #     body=body,
    # )
    
    # final_text = str(json.loads(response.get("body").read())["content"][0]["text"])
    # print("LLM OUTPUT:", final_text)  # Debug print

    # # Try to extract JSON substring if extra text is present
    # import re
    # match = re.search(r'({.*})', final_text, re.DOTALL)
    # if match:
    #     json_str = match.group(1)
    # else:
    #     json_str = final_text  # fallback

    # return json.loads(json_str)

    selected_model = chat_tool_model
    # selected_model = claude_model_name
    is_nova_model = (
        selected_model == 'nova' or
        selected_model.startswith('us.amazon.nova') or
        selected_model.startswith('nova-') or
        ('.nova' in selected_model and 'claude' not in selected_model)
    )

    if is_nova_model:
        print(f"Using Nova model for summary generation: {selected_model}")

        response = bedrock_client.converse(
            modelId=selected_model,
            system=[{"text": prompt}],
            messages=[
                {
                    "role": "user",
                    "content": [{"text": "Follow the system instructions."}]
                }
            ],
            inferenceConfig={"maxTokens": 4000, "temperature": 0.7}
        )

        try:
            assistant_msg = response["output"]["message"]["content"][0]["text"]
        except Exception as e:
            print("Error extracting Nova output:", e)
            raise

        print("NOVA OUTPUT:", assistant_msg)

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

        response = bedrock_client.invoke_model(
            modelId="us.anthropic.claude-3-7-sonnet-20250219-v1:0",
            body=body,
        )

        final_text = json.loads(response.get("body").read())["content"][0]["text"]
        print("LLM OUTPUT:", final_text)

        match = re.search(r'({.*})', final_text, re.DOTALL)
        json_str = match.group(1) if match else final_text

        return json.loads(json_str)
# banking function code ends here .....


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


def nova_banking_agent_invoke_tool(chat_history, session_id, chat, connectionId):
    """
    Nova model banking agent invoke tool function using AWS Bedrock Converse API.
    Uses the same tools and logic as banking_agent_invoke_tool but adapted for Nova Converse API.
    """
    try:
        # Start keepalive thread
        #keepalive_thread = send_keepalive(connectionId, 30)
        import uuid
        import random
        import re
        
        # Fetch base_prompt from the database (same as banking_agent_invoke_tool)
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

## MANDATORY PRE-TOOL CONFIRMATION (ENFORCE BEFORE ANY ACCOUNT ACCESS):
- Before returning any account-specific information or invoking any account-related tool (for example: get_account_summary or file_service_request), you MUST ask one concise, explicit confirmation question to the user requesting permission to use any detected or stored credentials and asking which account(s) or summary to retrieve.
- You MUST wait for an explicit affirmative confirmation from the user (a clear "yes" or an explicit instruction to proceed with the specified Customer ID and account scope) before calling any account-related tool or disclosing any sensitive account data.
- If the user has not provided verified credentials in this session, first request the Customer ID and PIN and obtain explicit confirmation to use them; do not assume permission from context or previously seen numbers.
- If the user replies ambiguously or does not explicitly confirm, do NOT call account-related tools and instead clarify what is required (Customer ID, PIN, and which accounts to retrieve).
- CRITICAL: When a user asks about account-specific information (such as "Can I see my card dues and loan details?" or "What's my account balance?" or "Show me my account summary" or "How do I file a service request?"), you MUST NOT call any account-related tool or provide any account-specific information until you have completed FULL authentication.

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
5. If the identity of the user is not mentioned previously, EXPLICITLY ask the user to confirm which Customer ID to use and verify the PIN before proceeding. 

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

IMPORTANT: Under no circumstances should the assistant provide account numbers, balances, or any sensitive account details unless the user has explicitly provided and completed authentication (Customer ID + PIN) during this session. If authentication has not been completed, politely request the required credentials first.

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
- Accept Customer ID in format CUST#### or cust#### only
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
        
        # Banking tool schema - converted to Nova's toolSpec format
        banking_tools_nova = [
            {
                "toolSpec": {
                    "name": "get_account_summary",
                    "description": "Retrieve customer's financial summary across savings, current, credit card, and loan accounts",
                    "inputSchema": {
                        "json": {
                            "type": "object",
                            "properties": {
                                "customer_id": {"type": "string", "description": "Unique customer identifier (e.g., CUST1001)"},
                                "pin": {"type": "string", "description": "4-6 digit numeric PIN for authentication"}
                            },
                            "required": ["customer_id", "pin"]
                        }
                    }
                }
            },
            {
                "toolSpec": {
                    "name": "file_service_request",
                    "description": "File a customer service request for follow-up by AnyBank SG's support team",
                    "inputSchema": {
                        "json": {
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
                    }
                }
            },
            {
                "toolSpec": {
                    "name": "banking_faq_tool_schema",
                    "description": "Retrieve answers from the banking knowledge base for general banking questions, policies, and procedures",
                    "inputSchema": {
                        "json": {
                            "type": "object",
                            "properties": {
                                "knowledge_base_retrieval_question": {"type": "string", "description": "A question to retrieve from the banking knowledge base about banking services, policies, procedures, or general information."}
                            },
                            "required": ["knowledge_base_retrieval_question"]
                        }
                    }
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

        def get_banking_faq_chunks(query, model_type='nova'):
            try:
                print("IN BANKING FAQ: ", query)
                chunks = []
                # Use the banking knowledge base ID from environment
                banking_kb_id = os.environ['bank_kb_id']
                
                # Use text-based retrieval (same as get_FAQ_chunks_tool)
                # The knowledge base handles embeddings internally if configured
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
        print("In nova_banking_agent_invoke_tool (Banking Bot - Nova)")

        
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
        
        # Enhance system prompt with customer ID and PIN context (same as banking_agent_invoke_tool)
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
        
        print("Nova Banking Model - Chat History: ", message_history)
        
        # Nova model configuration
        nova_model_name = os.environ.get("nova_model_name", "us.amazon.nova-pro-v1:0")
        nova_region = os.environ.get("region_used", region_used)
        nova_bedrock_client = boto3.client("bedrock-runtime", region_name=nova_region)
        
        # First API call to get initial response
        try:
            response = nova_bedrock_client.converse(
                modelId=nova_model_name,
                messages=message_history,
                system=[{"text": prompt}],
                inferenceConfig={
                    "temperature": 0,
                    "topP": 0.9
                },
                toolConfig={
                    "tools": banking_tools_nova
                }
            )
            
            print("Nova Banking Model Response: ", response)
            
            # Parse the response
            assistant_response = []
            output_msg = (response.get('output') or {}).get('message') or {}
            content_items = output_msg.get('content') or []
            
            for item in content_items:
                if item.get('text'):
                    assistant_response.append({'type': 'text', 'text': item['text']})
                elif item.get('toolUse'):
                    tool_use = item['toolUse']
                    assistant_response.append({
                        'type': 'tool_use',
                        'id': tool_use.get('toolUseId'),
                        'name': tool_use.get('name'),
                        'input': tool_use.get('input', {})
                    })
            
            # Filter out <thinking> tags from text responses
            for item in assistant_response:
                if item.get('type') == 'text' and 'text' in item:
                    item['text'] = re.sub(r'<thinking>.*?</thinking>', '', item['text'], flags=re.DOTALL | re.IGNORECASE).strip()
            
            # Check if any tools were called
            tools_used = []
            tool_results = []
            
            for action in assistant_response:
                if action.get('type') == 'tool_use':
                    tools_used.append(action['name'])
                    tool_name = action['name']
                    tool_input = action.get('input', {})
                    tool_use_id = action.get('id')
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
                        tool_result = get_account_summary(tool_input.get('customer_id'), tool_input.get('pin'))
                        # Check for authentication error
                        if isinstance(tool_result, dict) and 'error' in tool_result:
                            print(f"Authentication failed for get_account_summary: {tool_result['error']}")
                    elif tool_name == 'file_service_request':
                        tool_result = file_service_request(
                            tool_input.get('customer_id'),
                            tool_input.get('pin'),
                            tool_input.get('category'),
                            tool_input.get('description'),
                            tool_input.get('preferred_contact_method')
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
                        
                        tool_result = get_banking_faq_chunks(tool_input.get('knowledge_base_retrieval_question'), model_type='nova')
                        
                        # If FAQ tool returns empty or no results, provide fallback
                        if not tool_result or len(tool_result) == 0:
                            tool_result = ["I don't have specific information about that in our current banking knowledge base. Let me schedule a callback with one of our banking agents who can provide detailed information."]
                    
                    # Create tool result message (handle both strings and dictionaries)
                    try:
                        print(f"Tool result type: {type(tool_result)}")
                        print(f"Tool result content: {tool_result}")
                        
                        # Handle different types of tool results
                        if isinstance(tool_result, list) and tool_result:
                            if isinstance(tool_result[0], dict):
                                # Format list of dictionaries (like account data)
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
                        continue
            
            # If tools were used, add tool results to chat history and make second API call
            if tools_used:
                # First, add the assistant's message with tool uses to message history
                # Extract tool uses from the assistant response in the correct format
                assistant_message_content = []
                for action in assistant_response:
                    if action.get('type') == 'tool_use':
                        assistant_message_content.append({
                            'toolUse': {
                                'toolUseId': action.get('id'),
                                'name': action.get('name'),
                                'input': action.get('input', {})
                            }
                        })
                
                # Only add assistant message if we have tool uses
                if assistant_message_content:
                    message_history.append({
                        'role': 'assistant',
                        'content': assistant_message_content
                    })
                    print(f"Added assistant message with {len(assistant_message_content)} tool uses to message history")
                
                # Then add tool results to message history for Nova
                # Ensure we have exactly one tool result per tool use
                if tool_results and len(tool_results) == len(assistant_message_content):
                    # Format tool results correctly for Nova Converse API
                    formatted_tool_results = []
                    for tool_result_block in tool_results:
                        if 'toolResult' in tool_result_block:
                            formatted_tool_results.append(tool_result_block)
                    
                    if formatted_tool_results:
                        message_history.append({
                            'role': 'user',
                            'content': formatted_tool_results
                        })
                        print(f"Added user message with {len(formatted_tool_results)} tool results to message history")
                else:
                    print(f"Warning: Tool results count ({len(tool_results) if tool_results else 0}) doesn't match tool uses count ({len(assistant_message_content)})")
                
                # Make second API call with tool results
                try:
                    final_response = nova_bedrock_client.converse(
                        modelId=nova_model_name,
                        messages=message_history,
                        system=[{"text": prompt}],
                        inferenceConfig={
                            "temperature": 0,
                            "topP": 0.9
                        },
                        toolConfig={
                            "tools": banking_tools_nova
                        }
                    )
                    
                    print("Nova Banking Model Final Response: ", final_response)
                    
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
                    
                    # Simulate streaming by sending chunks via WebSocket
                    words = final_ans.split()
                    for word in words:
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
                    usage = final_response.get('usage', {})
                    input_tokens = usage.get('inputTokens', 0)
                    output_tokens = usage.get('outputTokens', 0)
                    
                    return {"statusCode": "200", "answer": final_ans, "question": chat, "session_id": session_id, "input_tokens": str(input_tokens), "output_tokens": str(output_tokens)}
                    
                except Exception as e:
                    print(f"Error in final Nova banking response: {e}")
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
                    if item.get('type') == 'text' and 'text' in item:
                        final_ans = item['text']
                        break
                
                # If no text response, provide fallback
                if not final_ans:
                    final_ans = "I'm here to help with your banking needs. How can I assist you today?"
                
                # Simulate streaming by sending chunks via WebSocket
                words = final_ans.split()
                for word in words:
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
            print(f"Error invoking Nova banking model: {e}")
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
        print(f"Unexpected error in nova_banking_agent_invoke_tool: {e}")
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




def lambda_handler(event, context):
    global user_intent_flag, overall_flow_flag, ub_number, ub_user_name, pop, str_intent,json
    print("Event: ",event)
    event_type=event['event_type']
    print("Event_type: ",event_type)
    conv_id = ""


    if event_type == 'risk_sandbox':
        
        return generate_risk_sandbox(event)
    
#banking event_type starts here ..
    elif event_type == 'banking_chat_tool':  
       
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

        # Get model from environment variable (defaults to 'claude' if not set)
        # Can be set to model name like 'us.amazon.nova-pro-v1:0' or just 'nova'/'claude'
        selected_model = chat_tool_model
        print(f"Using model from environment variable: {selected_model}")
        
        # Check if Nova model should be used (same logic as chat_tool)
        is_nova_model = (
            selected_model == 'nova' or  # Exact match
            selected_model.startswith('us.amazon.nova') or  # Nova model ID pattern
            selected_model.startswith('nova-') or  # Nova variant pattern
            ('.nova' in selected_model and 'claude' not in selected_model)  # Contains .nova but not claude
        )
        
        # Route to appropriate function based on model type
        if is_nova_model:
            print(f"Routing to Nova banking model handler (detected model: {selected_model})")
            tool_response = nova_banking_agent_invoke_tool(chat_history, session_id, chat, connectionId)
        else:
            # Default to Claude model (claude 3.5 or any other claude variant)
            print(f"Routing to Claude banking model handler (detected model: {selected_model})")
            tool_response = banking_agent_invoke_tool(chat_history, session_id, chat, connectionId)
        
        print("TOOL RESPONSE: ", tool_response)  
        #insert into banking_chat_history_table
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
    elif event_type == "generate_banking_summary":     
        
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
    
        # - Ensure the email content is formatted correctly with new lines. USE ONLY "\n" for new lines. 
        #         - Ensure the email content is formatted correctly for new lines instead of using new line characters.
        else:
            print(f"Using Claude model for summary generation: {model_id}")
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
                "message" : "Banking Summary Successfully Generated"
            }

    elif event_type == 'list_banking_summary':
        session_id = event['session_id']
        chat_query = f'''
        SELECT question,answer
        FROM {schema}.{banking_chat_history}    
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
