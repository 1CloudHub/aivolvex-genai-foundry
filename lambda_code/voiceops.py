import boto3
import time
import json
import urllib.parse
import os 
from botocore.exceptions import ClientError
import base64
import uuid
import psycopg2
from urllib.parse import urlparse


# Get region from environment variable
aws_region = os.environ.get('aws_region', 'ap-southeast-1')

session = boto3.Session(region_name=aws_region)
transcribe_client = session.client('transcribe', region_name=aws_region)
s3_client = session.client('s3', region_name=aws_region)
retrieve_client = session.client('bedrock-runtime', region_name='us-east-1')  # Bedrock is only available in us-east-1
secrets_client = session.client('secretsmanager', region_name=aws_region)

# Get database credentials from environment variables
db_user = os.environ['db_user']
db_host = os.environ['db_host']                         
db_port = os.environ['db_port']
db_database = os.environ['db_database']
rds_secret_arn = os.environ.get('rds_secret_arn', '')

# Function to get database password from Secrets Manager
def get_db_password():
    if not rds_secret_arn:
        raise Exception("RDS secret ARN not provided")
    
    try:
        response = secrets_client.get_secret_value(SecretId=rds_secret_arn)
        secret_data = json.loads(response['SecretString'])
        return secret_data['password']
    except Exception as e:
        print(f"Error fetching database password: {e}")
        raise

def select_db(query):
    db_password = get_db_password()
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
def insert_db(query,values):
    db_password = get_db_password()
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

def knowledge_base_retrieve_and_generate(query, session_id,box_type):

    try:
        print("IN KNOWLEDGE BASE RETRIEVE AND GENERATE:", query)
        

        
        # Get configuration from environment variables based on box_type
        try:
            # Map box_type to environment variable names
            if box_type == "insurance":
                kb_id = os.environ.get('KB_ID', 'V4VHPF0CDM')  # Insurance KB ID from env
                chat_table_name = 'voice_history'  # Default table name
                print(f"✅ Using Insurance KB from env: {kb_id}")
            elif box_type == "banking":
                kb_id = os.environ.get('bank_kb_id', 'V4VHPF0CDM')  # Banking KB ID from env
                chat_table_name = 'voice_history'  # Default table name
                print(f"✅ Using Banking KB from env: {kb_id}")
            else:
                # Default to insurance
                kb_id = os.environ.get('KB_ID', 'V4VHPF0CDM')
                chat_table_name = 'voice_history'
                print(f"✅ Using default Insurance KB from env: {kb_id}")
            
            prompt_template = None  # Will use default from response object
            print(f"✅ Retrieved config for {box_type} - KB: {kb_id}, Table: {chat_table_name}")
        except Exception as e:
            print(f"❌ Error retrieving voicebot config: {e}")
            # Fallback to default values
            kb_id = "V4VHPF0CDM"
            prompt_template = None  # Will use default from response object
            chat_table_name = 'voice_history'
        
        # Get chat history for context using the dynamic table name
        chat_history_context = ""
        if session_id and session_id != 'null' and session_id != '':
            try:
                schema = 'genaifoundry'
                history_query = f'''select question, answer 
                        from {schema}.{chat_table_name} 
                        where session_id = '{session_id}' 
                        order by created_on desc limit 3;'''
                history_response = select_db(history_query)
                
                if len(history_response) > 0:
                    history_parts = []
                    for voice_session in reversed(history_response):
                        history_parts.append(f"Previous Question: {voice_session[0]}")
                        history_parts.append(f"Previous Answer: {voice_session[1]}")
                    chat_history_context = "\n".join(history_parts) + "\n\nCurrent Question: "
                    print(f"✅ Retrieved chat history from {schema}.{chat_table_name}")
            except Exception as e:
                print(f"❌ Error fetching chat history: {e}")
        
        # Combine history context with current query
        full_query = chat_history_context + query if chat_history_context else query
        
        # Use retrieve and generate with the knowledge base
            
        response = retrieve_client.retrieve_and_generate(
            input={
                'text': full_query
            },
            retrieveAndGenerateConfiguration={
                'type': 'KNOWLEDGE_BASE',
                'knowledgeBaseConfiguration': {
                    'knowledgeBaseId': kb_id,
                    'modelArn': 'arn:aws:bedrock:us-east-1:455389024925:inference-profile/us.anthropic.claude-3-7-sonnet-20250219-v1:0',
                    'retrievalConfiguration': {
                        'vectorSearchConfiguration': {
                            'numberOfResults': 10,
                            'overrideSearchType': 'HYBRID'
                        }
                    },
                    'generationConfiguration': {
                        'inferenceConfig': {
                            'textInferenceConfig': {
                                'temperature': 0.1,
                                'topP': 0.9,
                                'maxTokens': 1000
                            }
                        },
                        'promptTemplate': {
                            'textPromptTemplate': prompt_template
                        }
                    }
                }
            }
        )
        
        # Extract the generated answer
        generated_answer = response.get('output', {}).get('text', '')
        
        # Get token usage if available
        input_tokens = response.get('usage', {}).get('inputTokens', 0)
        output_tokens = response.get('usage', {}).get('outputTokens', 0)
        
        print('KNOWLEDGE BASE GENERATED ANSWER:', generated_answer)
        print('INPUT TOKENS:', input_tokens)
        print('OUTPUT TOKENS:', output_tokens)
        
        # Save to voice history table
        try:
            schema = 'genaifoundry'
            insert_query = f'''
                    INSERT INTO {schema}.{chat_table_name}
                    (session_id, question, answer, input_tokens, output_tokens, created_on, updated_on)
                    VALUES (%s, %s, %s, %s, %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);
                    '''
            values = (str(session_id), str(query), str(generated_answer), str(input_tokens), str(output_tokens))
            insert_db(insert_query, values)
            print(f"✅ Saved chat history to {schema}.{chat_table_name}")
        except Exception as e:
            print(f"❌ Error saving to voice history: {e}")
        
        # Return the generated answer or fallback message
        if generated_answer:
            return generated_answer
        else:
            return "I don't have specific information about that in our current knowledge base. Please contact our customer service team for assistance."
            
    except Exception as e:
        print("An exception occurred while using retrieve and generate:", e)
        return "I'm having trouble accessing that information right now. Please try again in a moment, or contact our customer service team for assistance."

def transcribe_audio_with_aws(audio_data, sample_rate=16000):
    """
    Transcribe audio data using Amazon Transcribe
    Args:
        audio_data: base64 encoded audio data
        sample_rate: sample rate of the audio (default 16000)
    Returns:
        str: Transcribed text
    """
    bucket_name = os.environ.get('voice_bucket_name', 'voiceopstst')
    
    # Decode audio data
    audio_bytes = base64.b64decode(audio_data)
    
    # Generate a unique job name (required by Transcribe)
    job_name = f"direct-transcribe-{str(uuid.uuid4())}"
    
    # Supported formats: mp3, wav, flac, ogg, amr, webm
    media_format = 'wav'
    
    # Save audio temporarily (Lambda /tmp storage)
    temp_audio_path = f"/tmp/audio_{job_name}.{media_format}"
    with open(temp_audio_path, 'wb') as f:
        f.write(audio_bytes)
    
    try:
        # Step 1: Upload audio file to S3
        s3_key = f'audio/{job_name}.{media_format}'
        s3_client.upload_file(temp_audio_path, bucket_name, s3_key)
        audio_s3_uri = f's3://{bucket_name}/{s3_key}'
        print(f"Audio uploaded to: {audio_s3_uri}")
        
        # Step 2: Start transcription job with custom output location
        transcribe_client.start_transcription_job(
            TranscriptionJobName=job_name,
            LanguageCode='en-US',
            MediaFormat=media_format,
            Media={'MediaFileUri': audio_s3_uri},
            OutputBucketName=bucket_name,
            OutputKey=f'transcripts/{job_name}.json',  # Specify custom output path
            Settings={
                'ShowSpeakerLabels': True,
                'MaxSpeakerLabels': 2
            }
        )
        
        # Step 3: Wait for completion (max 5 min, adjust Lambda timeout)
        max_wait_time = 300  # 5 minutes
        wait_time = 0
        
        while wait_time < max_wait_time:
            status = transcribe_client.get_transcription_job(TranscriptionJobName=job_name)
            job_status = status['TranscriptionJob']['TranscriptionJobStatus']
            
            if job_status in ['COMPLETED', 'FAILED']:
                break
                
            time.sleep(5)
            wait_time += 5
        
        if job_status == 'FAILED':
            failure_reason = status['TranscriptionJob'].get('FailureReason', 'Unknown error')
            raise Exception(f"Transcription failed: {failure_reason}")
        
        if job_status != 'COMPLETED':
            raise Exception(f"Transcription timed out. Status: {job_status}")
        
        # Step 4: Get transcript from the specified output location
        transcript_key = f'transcripts/{job_name}.json'
        
        try:
            print(f"Fetching transcript from: {transcript_key}")
            transcript_obj = s3_client.get_object(Bucket=bucket_name, Key=transcript_key)
            transcript_data = json.loads(transcript_obj['Body'].read().decode('utf-8'))
            transcript_text = transcript_data['results']['transcripts'][0]['transcript']
            
        except s3_client.exceptions.NoSuchKey:
            # Fallback: Try to get from the default Transcribe location
            transcript_uri = status['TranscriptionJob']['Transcript']['TranscriptFileUri']
            print(f"Trying fallback URI: {transcript_uri}")
            
            # Parse the S3 URI to get bucket and key
            parsed_uri = urlparse(transcript_uri)
            transcript_bucket = parsed_uri.netloc
            transcript_key = parsed_uri.path.lstrip('/')
            
            transcript_obj = s3_client.get_object(Bucket=transcript_bucket, Key=transcript_key)
            transcript_data = json.loads(transcript_obj['Body'].read().decode('utf-8'))
            transcript_text = transcript_data['results']['transcripts'][0]['transcript']
        
        # Step 5: Clean up temporary files
        try:
            os.remove(temp_audio_path)
            # Optionally clean up S3 files
            s3_client.delete_object(Bucket=bucket_name, Key=s3_key)
            s3_client.delete_object(Bucket=bucket_name, Key=transcript_key)
        except Exception as cleanup_error:
            print(f"Cleanup warning: {cleanup_error}")
        
        return transcript_text
        
    except Exception as e:
        # Clean up on error
        try:
            os.remove(temp_audio_path)
        except:
            pass
        raise Exception(f"Transcription error: {str(e)}")
    except ClientError as e:
        print(f"AWS Error: {e}")
        return None
    except Exception as e:
        print(f"Error_LALALA: {e}")
        return None

def lambda_handler(event, context):
   
    try:
        audio_data = event['content']
        session_id = event['session_id']
        box_type=event['box_type']
        # print("Whisper transcription result:", result)
        transcribe_text=transcribe_audio_with_aws(audio_data)
        print('transcribed_text',transcribe_text)
        chat=transcribe_text
        answer = knowledge_base_retrieve_and_generate(chat, session_id, box_type)
        print("Knowledge base answer:", answer)
        url = "https://api.openai.com/v1/audio/speech"
        headers = {
            "Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}",
            "Content-Type": "application/json"
        }
        data = {
            "model": "tts-1",
            "voice": "alloy",  # alloy | echo | nova | shimmer
            "input": answer,
            "response_format": "wav"  # wav | mp3 | aac | opus
        }
    
        # 3. Call OpenAI API
        req = urllib.request.Request(
            url,
            data=json.dumps(data).encode('utf-8'),
            headers=headers,
            method='POST'
        )
        
        with urllib.request.urlopen(req) as response:
            audio_data = response.read()
        
        # 4. Return binary audio
        return {
            'statusCode': 200,
            'headers': {
                'Content-Type': 'audio/wav',
                'Content-Disposition': 'attachment; filename="speech.wav"'
            },
            'body': base64.b64encode(audio_data).decode('utf-8'),
            'isBase64Encoded': True
        }
        

    

    except Exception as e:
        return {
            'statusCode': 500,
            'body': json.dumps({
                'message': 'Error processing transcription',
                'error': str(e)
            })
        }