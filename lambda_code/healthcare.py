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
        # Start keepalive thread
        #keepalive_thread = send_keepalive(connectionId, 30)
        import uuid
        import random
        
        base_prompt =f'''
        You are a Virtual Healthcare Assistant for MedCare Hospital, a helpful and accurate chatbot for patients and visitors. You handle patient inquiries, appointment scheduling, medical records access, medication management, and general hospital information.
CRITICAL INSTRUCTIONS:

NEVER reply with any message that says you are checking, looking up, or finding information (such as "I'll check that for you", "Let me look that up", "One moment", "I'll find out", etc.).
NEVER say "To answer your question about [topic], let me check our system" or similar phrases.
After using a tool, IMMEDIATELY provide only the direct answer or summary to the user, with no filler, no explanations, and no mention of checking or looking up.
If a user asks a question that requires a tool, use the tool and reply ONLY with the answer or summary, never with any statement about the process.
For general hospital questions, IMMEDIATELY use the hospital_faq_tool_schema tool WITHOUT any preliminary message.

PATIENT AUTHENTICATION RULES:

ALWAYS verify Patient ID and Date of Birth before proceeding with any patient-specific tools
NEVER proceed with appointment_scheduler, patient_records, or medication_tracker without successful authentication
ONLY use tools after confirming the Patient ID and Date of Birth combination is valid
If authentication fails, provide a clear error message and ask for correct credentials

VALID PATIENT DATA:
Use these exact Patient ID and Date of Birth combinations for verification:

PAT1001 (John Smith) - DOB: 1985-03-15
PAT1002 (Sarah Johnson) - DOB: 1990-07-22
PAT1003 (Michael Brown) - DOB: 1978-11-08
PAT1004 (Emily Davis) - DOB: 1992-05-14
PAT1005 (David Wilson) - DOB: 1983-09-30

SESSION AUTHENTICATION STATE MANAGEMENT:
MAINTAIN SESSION STATE: Once a Patient ID and Date of Birth are successfully verified, store this authentication state for the ENTIRE conversation session
NEVER RE-ASK: Do not ask for Patient ID or Date of Birth again during the same session unless:

User explicitly provides a different Patient ID
Authentication explicitly fails during a tool call
User explicitly requests to switch accounts

AUTHENTICATION PERSISTENCE RULES:

FIRST AUTHENTICATION: Ask for Patient ID and Date of Birth only on the first patient-specific request
SESSION MEMORY: Remember the authenticated Patient ID throughout the conversation
AUTOMATIC REUSE: Use the stored authenticated credentials for ALL subsequent patient-specific tool calls
NO RE-VERIFICATION: Do not re-verify credentials that have already been successfully authenticated in the current session

PRE-AUTHENTICATION CHECK:
Before asking for Patient ID or Date of Birth for ANY patient-specific request:

Scan conversation history for previously provided Patient ID
Check if Date of Birth was already verified for that Patient ID in this session
If both are found and verified, proceed directly with stored credentials
Only ask for credentials that are missing or failed verification

PATIENT ID AND DOB HANDLING RULES:

SESSION-LEVEL STORAGE: Once Patient ID is provided and verified, use it for ALL subsequent requests
ONE-TIME DOB: Ask for Date of Birth only ONCE per Patient ID per session
CONVERSATION CONTEXT: Check the ENTIRE conversation history for previously provided and verified credentials
SMART REUSE: If user asks "I gave you before" or similar, acknowledge and proceed with stored credentials
CONTEXT AWARENESS: Before asking for credentials, always check if they were provided earlier in the conversation
When Patient ID is provided, validate it matches the pattern PAT#### (e.g., PAT1001)
Use the same Patient ID and Date of Birth for all subsequent tool calls in the session until Patient ID changes
ALWAYS verify Date of Birth matches the Patient ID before proceeding on first authentication only

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

2. Appointment Scheduling (AUTHENTICATION REQUIRED)
Use appointment_scheduler tool for:

Scheduling new appointments
Rescheduling existing appointments
Canceling appointments
Checking appointment availability and doctor schedules

Example Flow:

User: "I need to schedule an appointment with a cardiologist."
Assistant: "What is your Patient ID?"
User: "PAT1001"
Assistant: "Please provide your date of birth for verification."
User: "1985-03-15"
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
User: "Show me the available doctors"
Assistant: [Use appointment_scheduler tool with action="check_availability" and department="Cardiology"]
User: "I'd like to see Dr. Sarah Johnson"
Assistant: "What is your preferred date for the appointment?"
User: "Next Tuesday"
Assistant: [Use appointment_scheduler tool with action="get_doctor_times" to show Dr. Sarah Johnson's available times]
User: "10:30 AM works for me"
Assistant: "What is the reason for your visit?"
User: "I've been experiencing chest pain and want to get it checked."
Assistant: [Use appointment_scheduler tool with all details and provide confirmation]

Reschedule Example Flow:

User: "I need to reschedule my appointment"
Assistant: "What is your Patient ID?"
User: "PAT1001"
Assistant: "Please provide your date of birth for verification."
User: "1985-03-15"
Assistant: [Use appointment_scheduler tool with action="reschedule" to show current appointment details]
User: "I want to change it to October 20th at 2:00 PM"
Assistant: [Use appointment_scheduler tool with new date and time to complete reschedule]

Cancel Example Flow:

User: "I need to cancel my appointment"
Assistant: "What is your Patient ID?"
User: "PAT1001"
Assistant: "Please provide your date of birth for verification."
User: "1985-03-15"
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
Assistant: "What is your Patient ID?"
User: "PAT1001"
Assistant: "Please provide your date of birth for verification."
User: "1985-03-15"
Assistant: [Use patient_records tool and provide detailed medical records]

4. Medication Management (AUTHENTICATION REQUIRED)
Use medication_tracker tool for:

Viewing current medications
Adding new medications
Updating medication schedules
Removing medications

Example Flow:

User: "What medications am I currently taking?"
Assistant: "What is your Patient ID?"
User: "PAT1001"
Assistant: "Please provide your date of birth for verification."
User: "1985-03-15"
Assistant: [Use medication_tracker tool and provide detailed medication information]

AUTHENTICATION PROCESS:

Check Session State - Scan conversation for existing authenticated credentials
Collect Patient ID - Ask for Patient ID ONLY if not previously provided and verified
Validate Patient ID - Check if it matches one of the valid Patient IDs above
Collect DOB - Ask for Date of Birth ONLY if not previously provided and verified for current Patient ID
Verify DOB - Check if the Date of Birth matches the Patient ID (only on first authentication)
Store Authentication State - Remember successful authentication for entire session
Proceed with Tools - Use stored credentials for all subsequent patient-specific requests

MANDATORY QUESTION COLLECTION RULES:

ALWAYS collect ALL required information for any tool before using it
NEVER skip any required questions, even if the user provides some information
NEVER assume or guess missing information
NEVER proceed with incomplete information
Ask questions ONE AT A TIME in this exact order:

For appointment_scheduler tool:

Check session state first - Use stored Patient ID and DOB if already authenticated
Patient ID - if not already provided and verified in conversation
Date of Birth - only if not already provided and verified for current Patient ID
VERIFY Patient ID and Date of Birth combination is valid (only on first authentication)
Department selection - ALWAYS show the complete list of available departments first, then ask "Which department would you like to schedule an appointment with?"
Available departments: Cardiology, Psychology, Neurology, Orthopedics, Dermatology, Pediatrics, Internal Medicine, Emergency Medicine, Oncology, Radiology
Action type (schedule, reschedule, cancel, check_availability, get_doctor_times)
If action is "check_availability": Use tool immediately with department
If action is "get_doctor_times": Use tool with department and doctor_name to show available times, then ask for preferred date FIRST
If action is "schedule": Collect doctor preference (optional) - ALWAYS use check_availability tool first to show available doctors list, then ask "Which doctor would you prefer to see?", then ask for preferred date FIRST, then ask for preferred time SECOND, then reason for appointment
If action is "reschedule": IMMEDIATELY show existing appointment details first after authentication, then collect new preferred date FIRST, then new preferred time SECOND, department (if changing - show departments list), doctor preference (if changing - show doctors list)
If action is "cancel": Show all current appointments first, then ask which appointment to cancel, then when user confirms (says yes/yep/cancel/confirm), IMMEDIATELY call appointment_scheduler tool again with action="cancel" and user confirmation in reason field to proceed with cancellation - DO NOT wait for additional input
ONLY proceed with tool call after successful authentication

For patient_records tool:

Check session state first - Use stored Patient ID and DOB if already authenticated
Patient ID - if not already provided and verified in conversation
Date of Birth - only if not already provided and verified for current Patient ID
VERIFY Patient ID and Date of Birth combination is valid (only on first authentication)
Record type needed (all, recent, specific)
ONLY proceed with tool call after successful authentication

For medication_tracker tool:

Check session state first - Use stored Patient ID and DOB if already authenticated
Patient ID - if not already provided and verified in conversation
Date of Birth - only if not already provided and verified for current Patient ID
VERIFY Patient ID and Date of Birth combination is valid (only on first authentication)
Action type (get_medications, add_medication, update_medication, remove_medication)
If adding/updating: Medication name, dosage, schedule
ONLY proceed with tool call after successful authentication

INPUT VALIDATION RULES:

NEVER ask for the same Patient ID twice in a session unless user provides different one
NEVER ask for Date of Birth twice for the same Patient ID in a session
Accept Patient ID in format PAT#### only
Accept Date of Birth in format YYYY-MM-DD
If validation fails, provide a clear, specific error message with examples
ALWAYS verify Date of Birth matches the Patient ID before proceeding (only on first authentication)

AUTHENTICATION ERROR MESSAGES:

If Patient ID is invalid: "Invalid Patient ID. Please provide a valid Patient ID (e.g., PAT1001)."
If Date of Birth is incorrect: "Date of birth doesn't match Patient ID [PAT####]. Please provide the correct date of birth."
If both are wrong: "Invalid Patient ID and Date of Birth combination. Please check your credentials and try again."

TOOL USAGE RULES:

When a user asks about hospital services, visiting hours, or general information, use hospital_faq_tool_schema tool immediately (NO AUTHENTICATION)
When a user wants to schedule, reschedule, or cancel appointments, use appointment_scheduler tool AFTER authentication (use stored credentials if available)
For reschedule: IMMEDIATELY use appointment_scheduler tool with action="reschedule" after authentication to show current appointment details
For cancel: IMMEDIATELY use appointment_scheduler tool with action="cancel" after authentication to show current appointments and ask which one to cancel, then when user confirms (says yes/yep/cancel/confirm), IMMEDIATELY call appointment_scheduler tool again with action="cancel" and user confirmation in reason field to process the cancellation
ALWAYS show the list of available departments first before asking which department they prefer
ALWAYS use check_availability tool first to show the list of available doctors before asking which doctor they prefer
ALWAYS ask for preferred date FIRST, then preferred time SECOND during appointment scheduling
When a user asks about doctor availability or wants to see available doctors in a department, use appointment_scheduler tool with action="check_availability"
When a user selects a specific doctor and you need to show their available times, use appointment_scheduler tool with action="get_doctor_times", then ask for preferred date FIRST, then ask for preferred time SECOND
When a user wants to access medical records or health information, use patient_records tool AFTER authentication (use stored credentials if available)
When a user asks about medications or prescriptions, use medication_tracker tool AFTER authentication (use stored credentials if available)
Do NOT announce that you're using tools or searching for information
Simply use the tool and provide the direct answer

RESPONSE FORMAT:

ALWAYS answer in the shortest, most direct way possible
Do NOT add extra greetings, confirmations, or explanations
Do NOT mention backend systems or tools
Speak naturally as a helpful healthcare assistant who already knows the information

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

EXAMPLES OF CORRECT BEHAVIOR:
First Patient-Specific Request:

User: "I want to schedule an appointment"
Assistant: "What is your Patient ID?"
User: "PAT1001"
Assistant: "Please provide your date of birth for verification."
User: "1985-03-15"
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
User: "Yes, show me the available doctors"
Assistant: [Use appointment_scheduler tool with action="check_availability" and department="Cardiology"]
[Continue collecting doctor preference, date, time, and reason, then use appointment_scheduler tool for scheduling]

Subsequent Patient-Specific Requests in Same Session:

User: "What are your visiting hours?"
Assistant: [Use hospital_faq_tool_schema tool immediately and provide visiting hours]
User: "Can I check my medications?"
Assistant: "What type of medication information would you like? Current medications, add new medication, or update existing?"
[Uses stored PAT1001 authentication, only asks for medication-specific details]

Different Patient ID in Same Session:

User: "Can you check records for PAT1002?"
Assistant: "Please provide your date of birth for Patient ID PAT1002 verification."

EXAMPLES OF INCORRECT BEHAVIOR:
❌ "What's your Patient ID, date of birth, and appointment type?" (asking multiple questions)
❌ Asking for Patient ID again after it was already provided and verified in the session
❌ Asking for Date of Birth again for the same Patient ID in the same session
❌ Skipping Date of Birth verification on first authentication
❌ Proceeding with incomplete information
❌ Not checking conversation history for existing authentication
❌ Re-asking for credentials after using FAQ tool
SECURITY GUIDELINES:

Require Date of Birth verification only once per Patient ID in each session
Never store or reference Date of Birth values in conversation history for security
If user switches to a different Patient ID, ask for the corresponding Date of Birth
Treat all patient and medical information as sensitive and confidential
ALWAYS verify Patient ID and Date of Birth combination before first account access
MAINTAIN authentication state throughout session for user experience

RESPONSE GUIDELINES:

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
                "name": "appointment_scheduler",
                "description": "Schedule, reschedule, or cancel medical appointments for patients",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "patient_id": {
                            "type": "string",
                            "description": "Patient ID (format: PAT####)"
                        },
                        "date_of_birth": {
                            "type": "string",
                            "description": "Patient's date of birth (format: YYYY-MM-DD)"
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
                            "description": "Action to perform: schedule, reschedule, cancel, check_availability, get_doctor_times",
                            "enum": ["schedule", "reschedule", "cancel", "check_availability", "get_doctor_times"]
                        }
                    },
                    "required": ["patient_id", "date_of_birth", "action"]
                }
            },
            {
                "name": "patient_records",
                "description": "Access patient medical records, history, and health information",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "patient_id": {
                            "type": "string",
                            "description": "Patient ID (format: PAT####)"
                        },
                        "date_of_birth": {
                            "type": "string",
                            "description": "Patient's date of birth (format: YYYY-MM-DD)"
                        },
                        "record_type": {
                            "type": "string",
                            "description": "Type of record to retrieve",
                            "enum": ["all", "recent", "specific"]
                        }
                    },
                    "required": ["patient_id", "date_of_birth", "record_type"]
                }
            },
            {
                "name": "medication_tracker",
                "description": "Manage patient medications, prescriptions, and medication schedules",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "patient_id": {
                            "type": "string",
                            "description": "Patient ID (format: PAT####)"
                        },
                        "date_of_birth": {
                            "type": "string",
                            "description": "Patient's date of birth (format: YYYY-MM-DD)"
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
                    "required": ["patient_id", "date_of_birth", "action"]
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
                
                elif tool_name == 'appointment_scheduler':
                    # Simulate appointment scheduling with department and doctor management
                    patient_id = tool_input.get("patient_id", "")
                    date_of_birth = tool_input.get("date_of_birth", "")
                    department = tool_input.get("department", "")
                    doctor_name = tool_input.get("doctor_name", "")
                    preferred_date = tool_input.get("preferred_date", "")
                    preferred_time = tool_input.get("preferred_time", "")
                    reason = tool_input.get("reason", "")
                    action_type = tool_input.get("action", "schedule")
                    
                    print(f"Appointment details: {patient_id}, {date_of_birth}, {department}, {doctor_name}, {preferred_date}, {preferred_time}, {reason}")
                    
                    # Validate patient credentials
                    department_doctors = {
                        "Cardiology": [
                            {"name": "Dr. Sarah Johnson", "specialization": "Interventional Cardiology", "available_times": ["09:00 AM", "10:30 AM", "02:00 PM", "03:30 PM"]},
                            {"name": "Dr. Michael Chen", "specialization": "Electrophysiology", "available_times": ["08:30 AM", "11:00 AM", "01:30 PM", "04:00 PM"]},
                            {"name": "Dr. Emily Rodriguez", "specialization": "Heart Failure", "available_times": ["09:30 AM", "12:00 PM", "02:30 PM", "05:00 PM"]}
                        ],
                        "Psychology": [
                            {"name": "Dr. James Wilson", "specialization": "Clinical Psychology", "available_times": ["10:00 AM", "11:30 AM", "02:00 PM", "03:30 PM"]},
                            {"name": "Dr. Lisa Thompson", "specialization": "Cognitive Behavioral Therapy", "available_times": ["09:00 AM", "12:30 PM", "01:30 PM", "04:30 PM"]},
                            {"name": "Dr. Robert Davis", "specialization": "Child Psychology", "available_times": ["08:00 AM", "10:30 AM", "01:00 PM", "03:00 PM"]}
                        ],
                        "Neurology": [
                            {"name": "Dr. Amanda Foster", "specialization": "Movement Disorders", "available_times": ["09:00 AM", "11:00 AM", "02:00 PM", "04:00 PM"]},
                            {"name": "Dr. Kevin Park", "specialization": "Epilepsy", "available_times": ["08:30 AM", "10:30 AM", "01:30 PM", "03:30 PM"]},
                            {"name": "Dr. Maria Garcia", "specialization": "Multiple Sclerosis", "available_times": ["09:30 AM", "12:00 PM", "02:30 PM", "05:00 PM"]}
                        ],
                        "Orthopedics": [
                            {"name": "Dr. David Miller", "specialization": "Sports Medicine", "available_times": ["08:00 AM", "10:00 AM", "01:00 PM", "03:00 PM"]},
                            {"name": "Dr. Jennifer Lee", "specialization": "Joint Replacement", "available_times": ["09:00 AM", "11:30 AM", "02:00 PM", "04:30 PM"]},
                            {"name": "Dr. Thomas Brown", "specialization": "Spine Surgery", "available_times": ["08:30 AM", "12:00 PM", "01:30 PM", "05:00 PM"]}
                        ],
                        "Dermatology": [
                            {"name": "Dr. Rachel Green", "specialization": "Medical Dermatology", "available_times": ["09:00 AM", "10:30 AM", "02:00 PM", "03:30 PM"]},
                            {"name": "Dr. Mark Taylor", "specialization": "Cosmetic Dermatology", "available_times": ["08:30 AM", "11:00 AM", "01:30 PM", "04:00 PM"]},
                            {"name": "Dr. Susan White", "specialization": "Pediatric Dermatology", "available_times": ["09:30 AM", "12:00 PM", "02:30 PM", "05:00 PM"]}
                        ],
                        "Pediatrics": [
                            {"name": "Dr. Anna Martinez", "specialization": "General Pediatrics", "available_times": ["08:00 AM", "10:00 AM", "01:00 PM", "03:00 PM"]},
                            {"name": "Dr. Christopher Young", "specialization": "Pediatric Cardiology", "available_times": ["09:00 AM", "11:30 AM", "02:00 PM", "04:30 PM"]},
                            {"name": "Dr. Nicole Adams", "specialization": "Pediatric Neurology", "available_times": ["08:30 AM", "12:00 PM", "01:30 PM", "05:00 PM"]}
                        ],
                        "Internal Medicine": [
                            {"name": "Dr. Patricia Clark", "specialization": "General Internal Medicine", "available_times": ["09:00 AM", "10:30 AM", "02:00 PM", "03:30 PM"]},
                            {"name": "Dr. Steven Wright", "specialization": "Endocrinology", "available_times": ["08:30 AM", "11:00 AM", "01:30 PM", "04:00 PM"]},
                            {"name": "Dr. Michelle Hall", "specialization": "Gastroenterology", "available_times": ["09:30 AM", "12:00 PM", "02:30 PM", "05:00 PM"]}
                        ],
                        "Emergency Medicine": [
                            {"name": "Dr. Andrew King", "specialization": "Emergency Medicine", "available_times": ["24/7 Emergency Coverage"]},
                            {"name": "Dr. Stephanie Moore", "specialization": "Trauma Surgery", "available_times": ["24/7 Emergency Coverage"]}
                        ],
                        "Oncology": [
                            {"name": "Dr. Richard Scott", "specialization": "Medical Oncology", "available_times": ["09:00 AM", "10:30 AM", "02:00 PM", "03:30 PM"]},
                            {"name": "Dr. Karen Turner", "specialization": "Radiation Oncology", "available_times": ["08:30 AM", "11:00 AM", "01:30 PM", "04:00 PM"]},
                            {"name": "Dr. Brian Lewis", "specialization": "Surgical Oncology", "available_times": ["09:30 AM", "12:00 PM", "02:30 PM", "05:00 PM"]}
                        ],
                        "Radiology": [
                            {"name": "Dr. Catherine Reed", "specialization": "Diagnostic Radiology", "available_times": ["08:00 AM", "10:00 AM", "01:00 PM", "03:00 PM"]},
                            {"name": "Dr. Daniel Cook", "specialization": "Interventional Radiology", "available_times": ["09:00 AM", "11:30 AM", "02:00 PM", "04:30 PM"]},
                            {"name": "Dr. Laura Bell", "specialization": "Nuclear Medicine", "available_times": ["08:30 AM", "12:00 PM", "01:30 PM", "05:00 PM"]}
                        ]
                    }
                    valid_patients = {
                        "PAT1001": "1985-03-15",
                        "PAT1002": "1990-07-22", 
                        "PAT1003": "1978-11-08",
                        "PAT1004": "1992-05-14",
                        "PAT1005": "1983-09-30"
                    }
                    
                    if patient_id in valid_patients and valid_patients[patient_id] == date_of_birth:
                        if action_type == "check_availability":
                            if department and department in department_doctors:
                                doctors_info = ""
                                for doctor in department_doctors[department]:
                                    doctors_info += f"\n• {doctor['name']} - {doctor['specialization']}"
                                
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
                                    # Assign first available doctor if none specified
                                    selected_doctor = department_doctors[department][0]
                                
                                appointment_id = f"APT{random.randint(100000, 999999)}"
                                tool_result = [f"Appointment scheduled successfully!\n\nAppointment ID: {appointment_id}\nDepartment: {department}\nDoctor: {selected_doctor['name']} - {selected_doctor['specialization']}\nDate: {preferred_date}\nTime: {preferred_time}\nReason: {reason}\n\nPlease arrive 15 minutes early for your appointment."]
                        
                        elif action_type == "reschedule":
                            # Patient-specific existing appointments
                            patient_appointments = {
                                "PAT1001": [
                                    {"id": "APT123456", "department": "Cardiology", "doctor": "Dr. Sarah Johnson", "date": "2025-10-15", "time": "10:00 AM", "reason": "Follow-up consultation"}
                                ],
                                "PAT1002": [
                                    {"id": "APT123457", "department": "Obstetrics", "doctor": "Dr. Lisa Martinez", "date": "2025-11-20", "time": "2:00 PM", "reason": "Prenatal checkup"}
                                ],
                                "PAT1003": [
                                    {"id": "APT123458", "department": "Orthopedics", "doctor": "Dr. Robert Kim", "date": "2025-10-30", "time": "11:30 AM", "reason": "Physical therapy session"}
                                ],
                                "PAT1004": [
                                    {"id": "APT123459", "department": "Psychology", "doctor": "Dr. Jennifer Lee", "date": "2025-10-05", "time": "3:00 PM", "reason": "Therapy session"}
                                ],
                                "PAT1005": [
                                    {"id": "APT123460", "department": "Neurology", "doctor": "Dr. Michael Chen", "date": "2025-11-15", "time": "9:00 AM", "reason": "Migraine follow-up"}
                                ]
                            }
                            
                            if patient_id in patient_appointments and patient_appointments[patient_id]:
                                existing_appointment = patient_appointments[patient_id][0]  # Get first appointment
                                
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
                                                selected_doctor = {"name": existing_appointment['doctor'], "specialization": "Current Doctor"}
                                            else:
                                                selected_doctor = department_doctors[reschedule_department][0]
                                        
                                        tool_result = [f"Appointment Rescheduled Successfully!\n\nPrevious Appointment:\n- ID: {existing_appointment['id']}\n- Department: {existing_appointment['department']}\n- Doctor: {existing_appointment['doctor']}\n- Date: {existing_appointment['date']}\n- Time: {existing_appointment['time']}\n- Reason: {existing_appointment['reason']}\n\nNew Appointment:\n- ID: {existing_appointment['id']} (same)\n- Department: {reschedule_department}\n- Doctor: {selected_doctor['name']} - {selected_doctor.get('specialization', 'Current Doctor')}\n- Date: {preferred_date}\n- Time: {preferred_time}\n- Reason: {existing_appointment['reason']}\n\nPlease arrive 15 minutes early for your rescheduled appointment."]
                                    else:
                                        tool_result = [f"Please specify a valid department for rescheduling. Available departments: {', '.join(department_doctors.keys())}"]
                                else:
                                    tool_result = [f"Current Appointment Details:\n\nAppointment ID: {existing_appointment['id']}\nDepartment: {existing_appointment['department']}\nDoctor: {existing_appointment['doctor']}\nDate: {existing_appointment['date']}\nTime: {existing_appointment['time']}\nReason: {existing_appointment['reason']}\n\nWhat would you like to change? Please provide:\n- New preferred date (FIRST)\n- New preferred time (SECOND)\n- New department (if changing)\n- New doctor (if changing)"]
                            else:
                                tool_result = ["No existing appointments found to reschedule. Would you like to schedule a new appointment instead?"]
                        
                        elif action_type == "cancel":
                            # Patient-specific existing appointments
                            patient_appointments = {
                                "PAT1001": [
                                    {"id": "APT123456", "department": "Cardiology", "doctor": "Dr. Sarah Johnson", "date": "2025-10-15", "time": "10:00 AM", "reason": "Follow-up consultation"}
                                ],
                                "PAT1002": [
                                    {"id": "APT123457", "department": "Obstetrics", "doctor": "Dr. Lisa Martinez", "date": "2025-11-20", "time": "2:00 PM", "reason": "Prenatal checkup"}
                                ],
                                "PAT1003": [
                                    {"id": "APT123458", "department": "Orthopedics", "doctor": "Dr. Robert Kim", "date": "2025-10-30", "time": "11:30 AM", "reason": "Physical therapy session"}
                                ],
                                "PAT1004": [
                                    {"id": "APT123459", "department": "Psychology", "doctor": "Dr. Jennifer Lee", "date": "2025-10-05", "time": "3:00 PM", "reason": "Therapy session"}
                                ],
                                "PAT1005": [
                                    {"id": "APT123460", "department": "Neurology", "doctor": "Dr. Michael Chen", "date": "2025-11-15", "time": "9:00 AM", "reason": "Migraine follow-up"}
                                ]
                            }
                            
                            if patient_id in patient_appointments and patient_appointments[patient_id]:
                                appointments = patient_appointments[patient_id]
                                
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
                                        tool_result = [f"Current Appointment Details:\n\nAppointment ID: {appointment['id']}\nDepartment: {appointment['department']}\nDoctor: {appointment['doctor']}\nDate: {appointment['date']}\nTime: {appointment['time']}\nReason: {appointment['reason']}\n\nWould you like to cancel this appointment? Please confirm by saying 'yes' or 'cancel'."]
                                    else:
                                        appointments_list = "\n".join([f"{i+1}. {apt['department']} - {apt['doctor']} - {apt['date']} at {apt['time']}" for i, apt in enumerate(appointments)])
                                        tool_result = [f"Here are your current appointments:\n\n{appointments_list}\n\nWhich appointment would you like to cancel? Please specify the number (1, 2, etc.) or provide the department/doctor name."]
                            else:
                                tool_result = ["No existing appointments found to cancel. If you need to schedule a new appointment, I'd be happy to help you with that."]
                        
                        elif action_type == "get_doctor_times":
                            if department and doctor_name and department in department_doctors:
                                selected_doctor = None
                                for doctor in department_doctors[department]:
                                    if doctor_name.lower() in doctor['name'].lower():
                                        selected_doctor = doctor
                                        break
                                
                                if selected_doctor:
                                    available_times = "\n".join([f"• {time}" for time in selected_doctor['available_times']])
                                    tool_result = [f"Dr. {selected_doctor['name']} is available at the following times:\n\n{available_times}\n\nWhat date would you prefer for your appointment with Dr. {selected_doctor['name']}?"]
                                else:
                                    tool_result = [f"Doctor {doctor_name} not found in {department} department. Please select from the available doctors."]
                            else:
                                tool_result = ["Please specify both department and doctor name to check available times."]
                        
                        else:
                            tool_result = ["Appointment action completed successfully."]
                    else:
                        tool_result = ["Invalid patient credentials. Please verify your Patient ID and Date of Birth."]
                
                
                elif tool_name == 'patient_records':
                    # Simulate patient records access
                    patient_id = tool_input.get("patient_id", "")
                    date_of_birth = tool_input.get("date_of_birth", "")
                    record_type = tool_input.get("record_type", "all")
                    
                    # Validate patient credentials
                    valid_patients = {
                        "PAT1001": "1985-03-15",
                        "PAT1002": "1990-07-22",
                        "PAT1003": "1978-11-08", 
                        "PAT1004": "1992-05-14",
                        "PAT1005": "1983-09-30"
                    }
                    
                    if patient_id in valid_patients and valid_patients[patient_id] == date_of_birth:
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
                        
                        if patient_id in patient_records:
                            patient = patient_records[patient_id]
                            recent_visits = "\n".join([f"- {visit}" for visit in patient["recent_visits"]])
                            medications = "\n".join([f"- {med}" for med in patient["medications"]])
                            allergies = "\n".join([f"- {allergy}" for allergy in patient["allergies"]])
                            conditions = "\n".join([f"- {condition}" for condition in patient["conditions"]])
                            
                            tool_result = [f"Patient Records for {patient_id} ({patient['name']}, Age {patient['age']}):\n\nRecent Visits:\n{recent_visits}\n\nCurrent Medications:\n{medications}\n\nMedical Conditions:\n{conditions}\n\nAllergies:\n{allergies}\n\nNext Appointment:\n- {patient['next_appointment']}"]
                        else:
                            tool_result = ["Patient records not found. Please contact the hospital directly."]
                    else:
                        tool_result = ["Invalid patient credentials. Please verify your Patient ID and Date of Birth."]
                
                elif tool_name == 'medication_tracker':
                    # Simulate medication tracking
                    patient_id = tool_input.get("patient_id", "")
                    date_of_birth = tool_input.get("date_of_birth", "")
                    action_type = tool_input.get("action", "get_medications")
                    
                    # Validate patient credentials
                    valid_patients = {
                        "PAT1001": "1985-03-15",
                        "PAT1002": "1990-07-22",
                        "PAT1003": "1978-11-08",
                        "PAT1004": "1992-05-14", 
                        "PAT1005": "1983-09-30"
                    }
                    
                    if patient_id in valid_patients and valid_patients[patient_id] == date_of_birth:
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
                        
                        if action_type == "get_medications":
                            if patient_id in patient_medications:
                                patient_meds = patient_medications[patient_id]
                                med_list = "\n".join([f"{i+1}. {med}" for i, med in enumerate(patient_meds["medications"])])
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
                        tool_result = ["Invalid patient credentials. Please verify your Patient ID and Date of Birth."]
                
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
