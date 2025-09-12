import json 
import os
import psycopg2
import boto3  
import time
import secrets
import string
import logging
import random
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

# Fix: Uncomment and properly define schema
schema = os.environ.get('schema', 'genaifoundry')  # Default to genaifoundry if not set
chat_history_table = os.environ['chat_history_table']
prompt_metadata_table = os.environ['prompt_metadata_table']
model_id = os.environ['model_id']
CHAT_LOG_TABLE = os.environ['CHAT_LOG_TABLE']   
socket_endpoint = os.environ["socket_endpoint"]
health_kb_id=os.environ["KB_ID"]
hospital_chat_history_table=os.environ['chat_history_table']
# Use environment region instead of hardcoded regions
retrieve_client = boto3.client('bedrock-agent-runtime', region_name=region_used)
bedrock_client = boto3.client('bedrock-runtime', region_name=region_used)
api_gateway_client = boto3.client('apigatewaymanagementapi', endpoint_url=socket_endpoint)
bedrock = boto3.client('bedrock-runtime', region_name=region_used)
# Fix: Add missing bedrock_runtime client
bedrock_runtime = boto3.client('bedrock-runtime', region_name=region_used)
TAVILY_API_KEY = os.getenv('TAVILY_API_KEY')  # Get from environment variables
TAVILY_BASE_URL = "https://api.tavily.com"
if not TAVILY_API_KEY:
    raise ValueError("TAVILY_API_KEY environment variable is not set. Please set it in your environment variables.")

# Helper function to generate dynamic dates
def get_dynamic_date(days_ahead=2):
    """Generate a date that is 'days_ahead' days from current date"""
    current_date = datetime.now()
    # current_date = datetime(2025, 9, 18, 4, 22, 10, 472744)
    future_date = current_date + timedelta(days=days_ahead)
    return future_date.strftime('%Y-%m-%d')

def get_dynamic_datetime(days_ahead=2):
    """Generate a datetime that is 'days_ahead' days from current date"""
    current_date = datetime.now()
    future_date = current_date + timedelta(days=days_ahead)
    return future_date.strftime('%Y-%m-%dT%H:%M:%S+00:00')

# Generate a small set of available dates for a doctor (randomly select 3 dates from September 19-25)
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

# Add missing retail_chat_history_table variable
retail_chat_history_table = os.environ.get("chat_history_table")

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

def lambda_handler(event, context):
    """
    Main Lambda handler function that routes events based on event_type
    """
    try:
        event_type = event.get('event_type')
        print("Event_type: ", event_type)
        
        if event_type == 'deep_research':
            return deep_research_assistant_api(event)
        elif event_type == 'healthcare_chat_tool':
            return healthcare_chat_tool_handler(event)
        elif event_type == 'generate_retail_summary':
            return generate_retail_summary_handler(event)
        elif event_type == 'list_retail_summary':
            return list_retail_summary_handler(event)
        elif event_type == 'kyc_extraction':
            return kyc_extraction_api(event)
        else:
            return {
                'statusCode': 400,
                'body': json.dumps({
                    'error': f'Unsupported event type: {event_type}'
                })
            }
    except Exception as e:
        logger.error(f"Error in lambda_handler: {e}")
        return {
            'statusCode': 500,
            'body': json.dumps({
                'error': f'Internal server error: {str(e)}'
            })
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

def generate_retail_summary_handler(event):
    """
    Handle retail summary generation events
    """
    try:
        print("RETAIL SUMMARY GENERATION")
        session_id = event.get("session_id")
        if not session_id:
            return {
                'statusCode': 400,
                'body': json.dumps({
                    'error': 'Missing required parameter: session_id'
                })
            }
        
        chat_query = f'''
        SELECT question,answer
        FROM {schema}.{retail_chat_history_table}    
        WHERE session_id = '{session_id}';
        '''

        chat_details = select_db(chat_query)
        print("RETAIL CHAT DETAILS : ", chat_details)
        history = ""

        for chat in chat_details:
            history1 = "Human: " + chat[0]
            history2 = "Bot: " + chat[1]
            history += "\n" + history1 + "\n" + history2 + "\n"
        
        print("RETAIL HISTORY : ", history)
        
        prompt_query = f"SELECT analytics_prompt from {schema}.{prompt_metadata_table} where id = 5;"
        prompt_response = select_db(prompt_query)
        
        prompt_template = f''' <Instruction>
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
        
        print("RETAIL PROMPT : ", prompt_template)
        template = f'''
        <Conversation>
        {history}
        </Conversation>
        {prompt_template}
        '''

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
        
        # Initialize variables with default values
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
            conversation_sentiment_generated_details = ""
            
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
            
        # Escape single quotes for database insertion
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
            "message": "Retail Summary Successfully Generated"
        }
        
    except Exception as e:
        logger.error(f"Error in generate_retail_summary_handler: {e}")
        return {
            'statusCode': 500,
            'body': json.dumps({
                'error': f'Retail summary generation error: {str(e)}'
            })
        }

def list_retail_summary_handler(event):
    """
    Handle retail summary listing events
    """
    try:
        session_id = event.get('session_id')
        
        if not session_id:
            return {
                'statusCode': 400,
                'body': json.dumps({
                    'error': 'Missing required parameter: session_id'
                })
            }
        
        chat_query = f'''
        SELECT question,answer
        FROM {schema}.{retail_chat_history_table}    
        WHERE session_id = '{session_id}';
        '''

        chat_details = select_db(chat_query)
        print("RETAIL CHAT DETAILS : ", chat_details)
        history = []

        for chat in chat_details:
            history.append({"Human": chat[0], "Bot": chat[1]})
        
        print("RETAIL HISTORY : ", history)  
        
        select_query = f'''select summary, whatsapp_content, sentiment, topic from {schema}.{CHAT_LOG_TABLE} ccl where session_id = '{session_id}';'''
        summary_details = select_db(select_query)
        final_summary = {}
        
        for i in summary_details:  
            final_summary['summary'] = i[0]
            final_summary['whatsapp_content'] = i[1]
            final_summary['sentiment'] = i[2]
            final_summary['Topic'] = i[3]   
            
        return {
            "statusCode": 200,
            "body": json.dumps({
                "transcript": history,
                "final_summary": final_summary
            })
        }
        
    except Exception as e:
        logger.error(f"Error in list_retail_summary_handler: {e}")
        return {
            'statusCode': 500,
            'body': json.dumps({
                'error': f'Retail summary listing error: {str(e)}'
            })
        }

def healthcare_chat_tool_handler(event):
    """
    Handle healthcare chat tool events
    """
    try:
        # Extract required parameters from event
        chat = event.get('chat')
        session_id = event.get('session_id')   
        connectionId = event.get("connectionId")
        
        if not chat:
            return {
                'statusCode': 400,
                'body': json.dumps({
                    'error': 'Missing required parameter: chat'
                })
            }
        
        print(f"ConnectionId: {connectionId}")
        chat_history = []

        # Generate session_id if not provided
        if session_id is None or session_id == 'null' or session_id == '':
            session_id = str(uuid.uuid4())
        
        else:
            # Retrieve chat history from database
            query = f'''select question,answer 
                    from {schema}.{hospital_chat_history_table} 
                    where session_id = '{session_id}' 
                    order by created_on desc limit 20;'''
            history_response = select_db(query)
            print("history_response is ", history_response)

            if len(history_response) > 0:
                for chat_session in reversed(history_response):  
                    chat_history.append({'role': 'user', 'content': [{"type" : "text",'text': chat_session[0]}]})
                    chat_history.append({'role': 'assistant', 'content': [{"type" : "text",'text': chat_session[1]}]})
        
        # Append current user question
        chat_history.append({'role': 'user', 'content': [{"type" : "text",'text': chat}]})
            
        print("CHAT HISTORY : ", chat_history)

        # Call the hospital agent tool
        tool_response = hospital_agent_invoke_tool(chat_history, session_id, chat, connectionId)
        print("TOOL RESPONSE: ", tool_response)  
        
        # Insert into hospital_chat_history_table
        query = f'''
                INSERT INTO {schema}.{hospital_chat_history_table}
                (session_id, question, answer, input_tokens, output_tokens, created_on, updated_on)
                VALUES( %s, %s, %s, %s, %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);
                '''
        values = (str(session_id), str(chat), str(tool_response['answer']), str(tool_response['input_tokens']), str(tool_response['output_tokens']))
        res = insert_db(query, values)
        print("response:", res)

        # Insert into chat logs
        insert_query = f'''INSERT INTO {schema}.{CHAT_LOG_TABLE}      
        (created_on, environment, session_time, "lead", enquiry, complaint, summary, whatsapp_content, next_best_action, session_id, lead_explanation, sentiment, sentiment_explanation, connectionid, input_token, output_token, topic)
        VALUES(CURRENT_TIMESTAMP, %s, CURRENT_TIMESTAMP, %s, 0, 0, %s, %s, %s, %s, %s, %s, %s, %s, 0, 0, %s);'''             
        values = ('', None, '', '', '', session_id, '', '', '', connectionId, '')            
        res = insert_db(insert_query, values)   
        
        return tool_response
        
    except Exception as e:
        logger.error(f"Error in healthcare_chat_tool_handler: {e}")
        return {
            'statusCode': 500,
            'body': json.dumps({
                'error': f'Healthcare chat tool error: {str(e)}'
            })
        }

def deep_research_assistant_api(event):
    """
    Optimized Deep Research Assistant API using Tavily and AWS Bedrock
    
    Performance Optimizations:
    - Parallel processing of research queries
    - Reduced API calls and content extraction
    - Streamlined workflow based on notebook best practices
    - Faster response times with maintained quality
    """
    try:
        # Extract parameters from event
        research_query = event.get('research_query')
        research_depth = event.get('research_depth', 'basic')  # 'basic', 'medium', 'comprehensive'
        max_sources = event.get('max_sources', 3)  # Reduced default
        time_range = event.get('time_range', 'month')
        domain_filter = event.get('domain_filter', [])
        output_format = event.get('output_format', 'summary')
        
        print(f"🔍 Deep Research Query: {research_query}")
        print(f"📊 Research Depth: {research_depth}")
        print(f"📈 Max Sources: {max_sources}")
        print(f"⏰ Time Range: {time_range}")
        
        if not research_query:
            return {
                'statusCode': 400,
                'body': json.dumps({
                    'error': 'Research query is required'
                })
            }
        
        # Step 0: Validate if query is medical/healthcare related
        print("🏥 Step 0: Validating medical/healthcare relevance...")
        validation_result = validate_medical_query(research_query)
        print(f"🔍 Validation result: {validation_result}")
        
        if not validation_result.get('is_medical', False):
            return {
                'statusCode': 200,
                'body': json.dumps({
                    'message': 'Query validation completed',
                    'research_query': research_query,
                    'validation_result': validation_result,
                    'report': f"""# Medical Research Query Validation

## Query Analysis
**Your Query:** "{research_query}"

## Validation Result
❌ **Not Medical/Healthcare Related**

## Recommendation
Please provide a research query related to medical, healthcare, or health topics such as:

### Medical Topics:
- Diseases, conditions, and treatments
- Medications and pharmaceuticals
- Medical procedures and surgeries
- Medical devices and technologies
- Clinical trials and research

### Healthcare Topics:
- Healthcare systems and policies
- Public health initiatives
- Healthcare technologies
- Medical education and training
- Healthcare management

### Health Topics:
- Preventive medicine
- Nutrition and wellness
- Mental health
- Epidemiology
- Health outcomes and statistics

## Example Queries:
- "Latest treatments for diabetes"
- "New cancer immunotherapy drugs"
- "Healthcare AI applications in diagnosis"
- "Mental health interventions for depression"
- "Preventive measures for heart disease"

Please rephrase your query to focus on medical, healthcare, or health-related topics.""",
                    'metadata': {
                        'validation_timestamp': datetime.now().isoformat(),
                        'is_medical': False,
                        'suggested_topics': validation_result.get('suggested_topics', [])
                    }
                })
            }
        
        print(f"✅ Query validated as medical/healthcare related: {validation_result['confidence']}")
        
        # Step 1: Optimized query decomposition (reduced sub-questions)
        print("🧠 Step 1: Decomposing research query...")
        sub_questions = decompose_research_query_optimized(research_query, research_depth)
        print(f"📝 Generated {len(sub_questions)} sub-questions")
        
        # Step 2: Parallel research execution
        print("🔍 Step 2: Conducting parallel research...")
        research_results = conduct_parallel_research(
            sub_questions=sub_questions,
            max_sources=max_sources,
            time_range=time_range,
            domain_filter=domain_filter
        )
        
        # Step 3: Quick synthesis and report generation
        print("🧠 Step 3: Synthesizing findings...")
        final_report = generate_optimized_research_report(
            query=research_query,
            research_results=research_results,
            output_format=output_format
        )
        
        # Calculate metrics
        total_sources = sum(len(r.get('search_results', [])) for r in research_results)
        unique_domains = set()
        for r in research_results:
            for result in r.get('search_results', []):
                domain = urlparse(result['url']).netloc
                unique_domains.add(domain)
        
        return {
            'statusCode': 200,
            'body': json.dumps({
                'message': 'Deep research completed successfully',
                'research_query': research_query,
                'research_depth': research_depth,
                'report': final_report,  # Back to 'report' to match notebook format
                'metadata': {
                    'sub_questions_count': len(sub_questions),
                    'total_sources': total_sources,
                    'unique_domains': len(unique_domains),
                    'research_timestamp': datetime.now().isoformat(),
                    'sub_questions': sub_questions
                }
            })
        }
        
    except Exception as e:
        logger.error(f"Error in deep research assistant API: {e}")
        return {
            'statusCode': 500,
            'body': json.dumps({
                'error': f'Research failed: {str(e)}'
            })
        }

def decompose_research_query_optimized(query: str, depth: str) -> List[str]:
    """
    Optimized query decomposition with fewer, more focused sub-questions
    """
    try:
        # Reduced question counts for faster processing
        question_counts = {
            'basic': 2,        # Reduced from 3
            'medium': 3,       # Reduced from 5  
            'comprehensive': 4  # Reduced from 8
        }
        target_count = question_counts.get(depth, 2)
        
        prompt = f"""Break down this research query into {target_count} focused, searchable sub-questions:

Query: {query}

Requirements:
- Each question should be specific and searchable
- Cover the most important aspects
- Avoid redundancy
- Make questions suitable for web search

Return only the sub-questions, one per line, numbered 1-{target_count}."""

        response = call_bedrock_llm(prompt)
        
        # Parse sub-questions
        sub_questions = []
        lines = response.strip().split('\n')
        
        for line in lines:
            line = line.strip()
            if line and (line[0].isdigit() or line.startswith('-') or line.startswith('•')):
                question = re.sub(r'^\d+\.?\s*|-\s*|•\s*', '', line).strip()
                if question and question.endswith('?'):
                    sub_questions.append(question)
        
        # Fallback if parsing fails
        if not sub_questions:
            sub_questions = [
                f"What are the latest developments in {query}?",
                f"What are the key challenges with {query}?"
            ]
        
        return sub_questions[:target_count]
        
    except Exception as e:
        logger.error(f"Error decomposing research query: {e}")
        return [
            f"What are the latest developments in {query}?",
            f"What are the key challenges with {query}?"
        ]

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

def get_hospital_faq_chunks(query):
    """
    Retrieve hospital FAQ chunks from the knowledge base
    """
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
        return ["I don't have specific information about that in our current hospital knowledge base. Please contact our hospital directly for detailed information."]

def extract_sections(llm_response):
    """
    Extract sections from LLM response using regex patterns
    """
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
        
        base_prompt =f'''
    
    You are a Virtual Healthcare Assistant for MedCare Hospital, a helpful and accurate chatbot for patients and visitors. You handle patient inquiries, appointment scheduling, medical records access, medication management, and general hospital information.

    ===== ABSOLUTE RULE: DATE HANDLING =====
    When a user provides ANY date during rescheduling (like "September 20 would be great", "20th September", "20/09/2025"), you MUST IMMEDIATELY call the reschedule_appointment tool with name, phone, and preferred_date parameters. DO NOT respond with any text about format - ONLY call the tool.

    EXAMPLE:
    User: "September 20 would be great"
    Assistant: [CALL reschedule_appointment tool with name="Emily Davis", phone="97651823", preferred_date="September 20 would be great"]
    DO NOT SAY: "I apologize for the error" or "Let me try again" - ONLY CALL THE TOOL

    ===== CRITICAL RULES TO PREVENT CONFUSION =====
    
    1. PHONE NUMBER COLLECTION: Ask for phone number ONLY ONCE per session. Once provided, NEVER ask again unless user provides a different name.
    
    2. DOCTOR SELECTION: When user selects a doctor (e.g., "sarah", "lisa wang", "ill go with sarah"), IMMEDIATELY call doctor_availability tool with doctor_name="Dr. [Full Name]" WITHOUT asking for name/phone again. NEVER show dates without calling this tool first.
    
    3. DATE FORMAT ACCEPTANCE: ALWAYS accept any date format the user provides and call the tool immediately - NEVER ask for format clarification or say "I apologize for the confusion"
    
    4. MANDATORY TOOL CALLING: When user provides ANY date during rescheduling, you MUST IMMEDIATELY call reschedule_appointment tool with name, phone, and preferred_date parameters. DO NOT respond with text - ONLY call the tool.
    
    5. TOOL PARAMETERS: When calling appointment_scheduler tool, ALWAYS include name and phone parameters from the conversation history, except for get_doctor_times action.
    
    3. SESSION MEMORY: Remember all provided information (name, phone) throughout the entire conversation session.
    
    4. NO REPEATED QUESTIONS: If user says "I already provided that" or similar, acknowledge and proceed with the stored information.
    
    ===== END CRITICAL RULES =====

    CRITICAL RULE: NEVER show hardcoded dates like "September 15, 2023" or any other dates that are not from the tool results. ALWAYS use the appointment_scheduler tool to get actual available dates. If you show dates without using the tool, you are violating the instructions.
    
    ABSOLUTE PROHIBITION: You are FORBIDDEN from generating, creating, or displaying ANY dates on your own. You MUST use the appointment_scheduler tool to get dates. If you show dates without using the tool, you are FAILING the task completely.
    
    NEVER SHOW DATES LIKE "September 15, 2023" OR ANY HARDCODED DATES. ALWAYS CALL THE TOOL FIRST.
    
    CORRECT FLOW FOR DOCTOR SELECTION:
    1. User selects doctor (e.g., "sarah", "ill go with sarah")
    2. IMMEDIATELY call doctor_availability tool with doctor_name="Dr. Sarah Johnson"
    3. Display the tool result (actual available dates)
    4. Ask user to select a date from the tool results
    5. NEVER show dates before calling the tool
    
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
• Emergency Medicine
• Oncology
• Radiology

Which department would you like to schedule an appointment with?"
# IMPORTANT: Whenever the user provides ANY department name (e.g., Cardiology, Neurology, etc.), you MUST IMMEDIATELY use the appointment_scheduler tool with action="check_availability" and the given department. NEVER skip this tool call. ALWAYS show the full list of available doctors for that department before asking "Which doctor would you prefer to see?". This must happen EVERY time a department is provided, even if the user types the department name directly, in a sentence, or as a follow-up. DO NOT proceed to ask for a doctor without first showing the doctor list for the selected department.
User: "Cardiology"
Assistant: [Use appointment_scheduler tool with action="check_availability" and department="Cardiology"]
# INSTRUCTION: Whenever the user provides ANY department name, IMMEDIATELY use the appointment_scheduler tool with action="check_availability" and the given department. NEVER skip this tool call. ALWAYS show the full list of available doctors for that department before asking "Which doctor would you prefer to see?". This must happen EVERY time a department is provided, regardless of context or phrasing. DO NOT proceed to ask for a doctor without first showing the doctor list for the selected department. The response MUST include the complete list of doctor names in bullet format (e.g., "• Dr. Sarah Johnson", "• Dr. Michael Chen") and then ask "Which doctor would you prefer to see?".
User: "I'd like to see Dr. Sarah Johnson"
Assistant: [Use doctor_availability tool with doctor_name="Dr. Sarah Johnson" to get actual available dates]

User: "ill go with sarah"
Assistant: [Use doctor_availability tool with doctor_name="Dr. Sarah Johnson" to get actual available dates]

User: "sarah"
Assistant: [Use doctor_availability tool with doctor_name="Dr. Sarah Johnson" to get actual available dates]

User: "jennifer lee"
Assistant: [Use doctor_availability tool with doctor_name="Dr. Jennifer Lee" to get actual available dates]

User: "lisa wang"
Assistant: [Use doctor_availability tool with doctor_name="Dr. Lisa Wang" to get actual available dates]
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
Available departments: Cardiology, Psychology, Neurology, Orthopedics, Dermatology, Pediatrics, Internal Medicine, Emergency Medicine, Oncology, Radiology
Action type (schedule, reschedule, cancel, check_availability, get_doctor_times)
If action is "check_availability": Use tool immediately with department
If action is "get_doctor_times": Use tool with department and doctor_name to show available times, then ask for preferred date FIRST
If action is "schedule": Collect doctor preference (optional) - ALWAYS use check_availability tool first to show available doctors list, then ask "Which doctor would you prefer to see?", then ask for preferred date then show available times for that date, then ask for preferred time, then ask for reason for appointment. Only date, time, and reason are required after doctor selection.
If action is "reschedule": IMMEDIATELY show existing appointment details first after authentication, then ask "Would you like to reschedule this appointment?" and wait for user confirmation. After confirmation, show available dates for the same doctor, then collect new preferred date FIRST, then show available times for that date, then collect new preferred time SECOND.

CRITICAL RESCHEDULE FLOW: 
1. When user confirms rescheduling (e.g., "yes", "I would like to reschedule"), call appointment_scheduler with action="reschedule", name, phone, and reason="yes" to show available dates
2. When user provides a new date during rescheduling (e.g., "September 27 would be great", "September 20, 2025", "20th September"), ALWAYS call reschedule_appointment tool with name, phone, and preferred_date parameters - DO NOT ask for format clarification
3. ACCEPT ALL DATE FORMATS: The system should accept various date formats including "September 20, 2025", "September 20", "20th September", "september 20 is cool", "20/09/2025", etc.
4. NEVER say "I apologize for the confusion" or ask for date format clarification - ALWAYS call the tool with the user's input
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
• Emergency Medicine
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
• Emergency Medicine
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
                            "enum": ["Cardiology", "Psychology", "Neurology", "Orthopedics", "Dermatology", "Pediatrics", "Internal Medicine", "Emergency Medicine", "Oncology", "Radiology"]
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
                
                elif tool_name == 'doctor_availability':
                    # Get doctor availability - simple tool that only requires doctor name
                    doctor_name = tool_input.get("doctor_name", "")
                    
                    # Define department doctors data
                    department_doctors = {
                        "Cardiology": [
                            {"name": "Dr. Sarah Johnson", "available_times": ["09:00 AM", "10:30 AM", "02:00 PM", "03:30 PM"], "available_dates": ["2025-09-19", "2025-09-22", "2025-09-25"]},
                            {"name": "Dr. Michael Chen", "available_times": ["08:30 AM", "11:00 AM", "01:30 PM", "04:00 PM"], "available_dates": ["2025-09-20", "2025-09-23", "2025-09-24"]},
                            {"name": "Dr. Emily Rodriguez", "available_times": ["09:30 AM", "12:00 PM", "02:30 PM", "05:00 PM"], "available_dates": ["2025-09-21", "2025-09-22", "2025-09-25"]}
                        ],
                        "Psychology": [
                            {"name": "Dr. James Wilson", "available_times": ["10:00 AM", "11:30 AM", "02:00 PM", "03:30 PM"], "available_dates": ["2025-09-19", "2025-09-21", "2025-09-24"]},
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
                            {"name": "Dr. Robert Chen", "available_times": ["09:00 AM", "11:00 AM", "02:00 PM", "04:00 PM"], "available_dates": ["2025-09-21", "2025-09-23", "2025-09-25"]},
                            {"name": "Dr. Lisa Wang", "available_times": ["08:30 AM", "10:30 AM", "01:30 PM", "03:30 PM"], "available_dates": ["2025-09-19", "2025-09-22", "2025-09-24"]},
                            {"name": "Dr. James Wilson", "available_times": ["09:30 AM", "12:00 PM", "02:30 PM", "05:00 PM"], "available_dates": ["2025-09-20", "2025-09-23", "2025-09-25"]}
                        ],
                        "Dermatology": [
                            {"name": "Dr. Jennifer Lee", "available_times": ["09:00 AM", "10:30 AM", "02:00 PM", "03:30 PM"], "available_dates": ["2025-09-19", "2025-09-21", "2025-09-24"]},
                            {"name": "Dr. Mark Taylor", "available_times": ["08:30 AM", "11:00 AM", "01:30 PM", "04:00 PM"], "available_dates": ["2025-09-20", "2025-09-22", "2025-09-25"]},
                            {"name": "Dr. Sarah Kim", "available_times": ["09:30 AM", "12:00 PM", "02:30 PM", "05:00 PM"], "available_dates": ["2025-09-19", "2025-09-23", "2025-09-24"]}
                        ],
                        "Pediatrics": [
                            {"name": "Dr. David Rodriguez", "available_times": ["08:00 AM", "10:00 AM", "01:00 PM", "03:00 PM"], "available_dates": ["2025-09-20", "2025-09-21", "2025-09-25"]},
                            {"name": "Dr. Anna Martinez", "available_times": ["09:00 AM", "11:30 AM", "02:00 PM", "04:30 PM"], "available_dates": ["2025-09-19", "2025-09-22", "2025-09-24"]},
                            {"name": "Dr. Chris Anderson", "available_times": ["08:30 AM", "10:30 AM", "01:30 PM", "03:30 PM"], "available_dates": ["2025-09-21", "2025-09-23", "2025-09-25"]}
                        ],
                        "Internal Medicine": [
                            {"name": "Dr. Robert Williams", "available_times": ["09:00 AM", "10:30 AM", "02:00 PM", "03:30 PM"], "available_dates": ["2025-09-19", "2025-09-20", "2025-09-24"]},
                            {"name": "Dr. Jennifer Brown", "available_times": ["08:30 AM", "11:00 AM", "01:30 PM", "04:00 PM"], "available_dates": ["2025-09-21", "2025-09-22", "2025-09-25"]},
                            {"name": "Dr. Michael Davis", "available_times": ["09:30 AM", "12:00 PM", "02:30 PM", "05:00 PM"], "available_dates": ["2025-09-19", "2025-09-23", "2025-09-24"]}
                        ],
                        "Emergency Medicine": [
                            {"name": "Dr. Alex Thompson", "available_times": ["08:00 AM", "10:00 AM", "01:00 PM", "03:00 PM"], "available_dates": ["2025-09-20", "2025-09-21", "2025-09-25"]},
                            {"name": "Dr. Rachel Green", "available_times": ["09:00 AM", "11:30 AM", "02:00 PM", "04:30 PM"], "available_dates": ["2025-09-19", "2025-09-22", "2025-09-24"]},
                            {"name": "Dr. Tom Wilson", "available_times": ["08:30 AM", "10:30 AM", "01:30 PM", "03:30 PM"], "available_dates": ["2025-09-21", "2025-09-23", "2025-09-25"]}
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
                                dt = datetime.strptime(date_str, '%Y-%m-%d')
                                readable_dates.append(dt.strftime('%B %d, %Y'))
                            except:
                                readable_dates.append(date_str)
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
                                
                                # Define department doctors data (same as in other tools)
                                department_doctors = {
                                    "Cardiology": [
                                        {"name": "Dr. Sarah Johnson", "available_times": ["09:00 AM", "10:30 AM", "02:00 PM", "03:30 PM"], "available_dates": ["2025-09-19", "2025-09-22", "2025-09-25"]},
                                        {"name": "Dr. Michael Chen", "available_times": ["08:30 AM", "11:00 AM", "01:30 PM", "04:00 PM"], "available_dates": ["2025-09-20", "2025-09-23", "2025-09-24"]},
                                        {"name": "Dr. Emily Rodriguez", "available_times": ["09:30 AM", "12:00 PM", "02:30 PM", "05:00 PM"], "available_dates": ["2025-09-21", "2025-09-22", "2025-09-25"]}
                                    ],
                                    "Psychology": [
                                        {"name": "Dr. James Wilson", "available_times": ["10:00 AM", "11:30 AM", "02:00 PM", "03:30 PM"], "available_dates": ["2025-09-19", "2025-09-21", "2025-09-24"]},
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
                                        {"name": "Dr. Robert Chen", "available_times": ["09:00 AM", "11:00 AM", "02:00 PM", "04:00 PM"], "available_dates": ["2025-09-21", "2025-09-23", "2025-09-25"]},
                                        {"name": "Dr. Lisa Wang", "available_times": ["08:30 AM", "10:30 AM", "01:30 PM", "03:30 PM"], "available_dates": ["2025-09-19", "2025-09-22", "2025-09-24"]},
                                        {"name": "Dr. James Wilson", "available_times": ["09:30 AM", "12:00 PM", "02:30 PM", "05:00 PM"], "available_dates": ["2025-09-20", "2025-09-23", "2025-09-25"]}
                                    ]
                                }
                                
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
                    # Validate patient credentials
                    department_doctors = {
                        "Cardiology": [
                            {"name": "Dr. Sarah Johnson", "available_times": ["09:00 AM", "10:30 AM", "02:00 PM", "03:30 PM"], "available_dates": ["2025-09-19", "2025-09-22", "2025-09-25"]},
                            {"name": "Dr. Michael Chen", "available_times": ["08:30 AM", "11:00 AM", "01:30 PM", "04:00 PM"], "available_dates": ["2025-09-20", "2025-09-23", "2025-09-24"]},
                            {"name": "Dr. Emily Rodriguez", "available_times": ["09:30 AM", "12:00 PM", "02:30 PM", "05:00 PM"], "available_dates": ["2025-09-21", "2025-09-22", "2025-09-25"]}
                        ],
                        "Psychology": [
                            {"name": "Dr. James Wilson", "available_times": ["10:00 AM", "11:30 AM", "02:00 PM", "03:30 PM"], "available_dates": ["2025-09-19", "2025-09-21", "2025-09-24"]},
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
                            {"name": "Dr. Jennifer Lee", "available_times": ["09:00 AM", "11:30 AM", "02:00 PM", "04:30 PM"], "available_dates": ["2025-09-21", "2025-09-23", "2025-09-25"]},
                            {"name": "Dr. Thomas Brown", "available_times": ["08:30 AM", "12:00 PM", "01:30 PM", "05:00 PM"], "available_dates": ["2025-09-20", "2025-09-22", "2025-09-24"]}
                        ],
                        "Dermatology": [
                            {"name": "Dr. Rachel Green", "available_times": ["09:00 AM", "10:30 AM", "02:00 PM", "03:30 PM"], "available_dates": ["2025-09-19", "2025-09-21", "2025-09-23"]},
                            {"name": "Dr. Mark Taylor", "available_times": ["08:30 AM", "11:00 AM", "01:30 PM", "04:00 PM"], "available_dates": ["2025-09-20", "2025-09-22", "2025-09-24"]},
                            {"name": "Dr. Susan White", "available_times": ["09:30 AM", "12:00 PM", "02:30 PM", "05:00 PM"], "available_dates": ["2025-09-19", "2025-09-23", "2025-09-25"]}
                        ],
                        "Pediatrics": [
                            {"name": "Dr. Anna Martinez", "available_times": ["08:00 AM", "10:00 AM", "01:00 PM", "03:00 PM"], "available_dates": ["2025-09-20", "2025-09-21", "2025-09-24"]},
                            {"name": "Dr. Christopher Young", "available_times": ["09:00 AM", "11:30 AM", "02:00 PM", "04:30 PM"], "available_dates": ["2025-09-19", "2025-09-22", "2025-09-25"]},
                            {"name": "Dr. Nicole Adams", "available_times": ["08:30 AM", "12:00 PM", "01:30 PM", "05:00 PM"], "available_dates": ["2025-09-21", "2025-09-23", "2025-09-24"]}
                        ],
                        "Internal Medicine": [
                            {"name": "Dr. Patricia Clark", "available_times": ["09:00 AM", "10:30 AM", "02:00 PM", "03:30 PM"], "available_dates": ["2025-09-19", "2025-09-20", "2025-09-25"]},
                            {"name": "Dr. Steven Wright", "available_times": ["08:30 AM", "11:00 AM", "01:30 PM", "04:00 PM"], "available_dates": ["2025-09-21", "2025-09-22", "2025-09-24"]},
                            {"name": "Dr. Michelle Hall", "available_times": ["09:30 AM", "12:00 PM", "02:30 PM", "05:00 PM"], "available_dates": ["2025-09-20", "2025-09-23", "2025-09-25"]}
                        ],
                        "Emergency Medicine": [
                            {"name": "Dr. Andrew King", "available_times": ["24/7 Emergency Coverage"], "available_dates": ["2025-09-19", "2025-09-22", "2025-09-24"]},
                            {"name": "Dr. Stephanie Moore", "available_times": ["24/7 Emergency Coverage"], "available_dates": ["2025-09-20", "2025-09-23", "2025-09-25"]}
                        ],
                        "Oncology": [
                            {"name": "Dr. Richard Scott", "available_times": ["09:00 AM", "10:30 AM", "02:00 PM", "03:30 PM"], "available_dates": ["2025-09-19", "2025-09-21", "2025-09-24"]},
                            {"name": "Dr. Karen Turner", "available_times": ["08:30 AM", "11:00 AM", "01:30 PM", "04:00 PM"], "available_dates": ["2025-09-20", "2025-09-22", "2025-09-25"]},
                            {"name": "Dr. Brian Lewis", "available_times": ["09:30 AM", "12:00 PM", "02:30 PM", "05:00 PM"], "available_dates": ["2025-09-21", "2025-09-23", "2025-09-24"]}
                        ],
                        "Radiology": [
                            {"name": "Dr. Catherine Reed", "available_times": ["08:00 AM", "10:00 AM", "01:00 PM", "03:00 PM"], "available_dates": ["2025-09-19", "2025-09-22", "2025-09-25"]},
                            {"name": "Dr. Daniel Cook", "available_times": ["09:00 AM", "11:30 AM", "02:00 PM", "04:30 PM"], "available_dates": ["2025-09-20", "2025-09-23", "2025-09-24"]},
                            {"name": "Dr. Laura Bell", "available_times": ["08:30 AM", "12:00 PM", "01:30 PM", "05:00 PM"], "available_dates": ["2025-09-21", "2025-09-22", "2025-09-25"]}
                        ]
                    }

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
                                                    tool_result = [f"Great! Dr. {existing_doctor} is available on {preferred_date}. Here are the available times:\n\n{times_list}\n\nWhat time would you prefer for your appointment?"]
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
