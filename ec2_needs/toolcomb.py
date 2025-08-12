import os
import json
import uuid
from flask import Flask, request, send_file, jsonify
from transformers import VitsModel, AutoTokenizer
import torch
import soundfile as sf
import scipy.signal as signal
import psycopg2
import boto3
import tempfile
import subprocess
from pydub import AudioSegment
import time
import librosa
import whisper
from pathlib import Path
from asgiref.wsgi import WsgiToAsgi
app = Flask(__name__)
asgi_app = WsgiToAsgi(app)
# --- Mock tool implementations ---
def get_user_policies(crn):
    mock_policies = {
        "CUST1001": [
            {"policy_id": "POL1001", "plan_name": "MediPlus Secure", "policy_type": "Health", "coverage_amount": "SGD 150,000/year", "start_date": "2022-04-15", "premium_amount": "SGD 120", "next_premium_due": "2025-11-01", "status": "Active"},
            {"policy_id": "POL1002", "plan_name": "LifeSecure Term Advantage", "policy_type": "Life", "coverage_amount": "SGD 500,000", "start_date": "2021-09-10", "premium_amount": "SGD 95", "next_premium_due": "2025-11-10", "status": "Active"}
        ],
        "CUST1002": [
            {"policy_id": "POL2001", "plan_name": "FamilyCare Protect", "policy_type": "Family", "coverage_amount": "SGD 250,000", "start_date": "2023-01-05", "premium_amount": "SGD 290", "next_premium_due": "2025-11-05", "status": "Active"},
            {"policy_id": "POL2002", "plan_name": "ActiveShield PA", "policy_type": "Accident", "coverage_amount": "SGD 100,000", "start_date": "2022-08-22", "premium_amount": "SGD 40", "next_premium_due": "2025-11-22", "status": "Active"}
        ],
        "CUST1003": [
            {"policy_id": "POL3001", "plan_name": "SilverShield Health", "policy_type": "Senior", "coverage_amount": "SGD 100,000/year", "start_date": "2021-11-30", "premium_amount": "SGD 320", "next_premium_due": "2025-11-30", "status": "Active"}
        ],
        "CUST1004": [
            {"policy_id": "POL4001", "plan_name": "MediPlus Secure", "policy_type": "Health", "coverage_amount": "SGD 150,000/year", "start_date": "2023-03-18", "premium_amount": "SGD 120", "next_premium_due": "2025-11-18", "status": "Active"}
        ],
        "CUST1005": [
            {"policy_id": "POL5001", "plan_name": "LifeSecure Term Advantage", "policy_type": "Life", "coverage_amount": "SGD 300,000", "start_date": "2020-06-25", "premium_amount": "SGD 85", "next_premium_due": "2025-11-25", "status": "Active"}
        ]
    }
    return mock_policies.get(crn, [])

def track_claim_status(crn):
    mock_claims = {
        "CUST1001": [
            {"claim_id": "CLM1001", "policy_id": "POL1001", "claim_type": "Hospitalisation", "claim_status": "Under Review", "claim_amount": "SGD 12,000", "date_filed": "2024-11-15", "last_updated": "2024-11-21", "remarks": "Awaiting final approval from claims officer"},
            {"claim_id": "CLM1002", "policy_id": "POL1002", "claim_type": "Terminal Illness", "claim_status": "Submitted", "claim_amount": "SGD 250,000", "date_filed": "2025-01-10", "last_updated": "2025-01-15", "remarks": "Doctor's certification under verification"}
        ],
        "CUST1002": [
            {"claim_id": "CLM2001", "policy_id": "POL2002", "claim_type": "Accident Medical", "claim_status": "Approved", "claim_amount": "SGD 7,500", "date_filed": "2024-08-12", "last_updated": "2024-08-20", "remarks": "Payout issued to registered bank account"},
            {"claim_id": "CLM2002", "policy_id": "POL2001", "claim_type": "Outpatient Family Cover", "claim_status": "Submitted", "claim_amount": "SGD 4,200", "date_filed": "2025-06-02", "last_updated": "2025-06-04", "remarks": "Pending document verification"}
        ],
        "CUST1003": [
            {"claim_id": "CLM3001", "policy_id": "POL3001", "claim_type": "Hospitalisation", "claim_status": "Rejected", "claim_amount": "SGD 9,200", "date_filed": "2024-12-05", "last_updated": "2024-12-10", "remarks": "Missing discharge summary and itemised bill"}
        ],
        "CUST1004": [
            {"claim_id": "CLM4001", "policy_id": "POL4001", "claim_type": "Hospitalisation", "claim_status": "Submitted", "claim_amount": "SGD 6,800", "date_filed": "2025-03-18", "last_updated": "2025-03-19", "remarks": "Documents received, pending review"}
        ],
        "CUST1005": [
            {"claim_id": "CLM5001", "policy_id": "POL5001", "claim_type": "Death", "claim_status": "Under Review", "claim_amount": "SGD 300,000", "date_filed": "2025-06-01", "last_updated": "2025-06-09", "remarks": "Awaiting legal verification and supporting documents"}
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

# Database credentials parameters (set these as needed)
region = 'ap-southeast-1'
db_host = "genai-foundry-db.cduao4qkeseb.ap-southeast-1.rds.amazonaws.com"
db_database = "postgres"
db_password = "Postgres123"
db_port = "5432"
db_user = "postgres"
model_whisper = whisper.load_model("medium")
def db_result1(query, values):
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

def db_result2(query, values):
    connection = psycopg2.connect(
        user=db_user,
        password=db_password,
        host=db_host,
        port=db_port,  
        database=db_database
    )                                                                            
    cursor = connection.cursor()
    cursor.execute(query, values)
    last_inserted_id = cursor.fetchone()[0]
    connection.commit()
    cursor.close()
    connection.close()
    return last_inserted_id 

def db_result(query):
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
   
def update_db(query, params):
    connection = psycopg2.connect(
        user=db_user,
        password=db_password,
        host=db_host,
        port=db_port,
        database=db_database
    )
    cursor = connection.cursor()
    cursor.execute(query, params)
    connection.commit()
    cursor.close()
    connection.close()

def db_result3(query):
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

def reset_wav_file(SONG_PATH):
    silent_audio = AudioSegment.silent(duration=1)
    silent_audio.export(SONG_PATH, format="wav")

def get_clean_audio(path):
    flagg = None
    # wav = read_audio(path, sampling_rate=SAMPLING_RATE)
    # speech_timestamps = get_speech_timestamps(wav, model_vad, sampling_rate=SAMPLING_RATE)
    # if len(speech_timestamps) > 0:
    #     print("audio exists")
    #     save_audio(f"{path}", collect_chunks(speech_timestamps, wav), sampling_rate=SAMPLING_RATE)
    #     flagg = True
    # else:
    #     print("Nothing")
    #     flagg = False
    # return flagg
    # TODO: Uncomment and implement above if dependencies are available
    return flagg

def reduce_noise_ffmpeg(input_file, output_file):
    model_path = "/home/ubuntu/voiceops/voiceops/cb.rnnn"
    command = f"ffmpeg -i {input_file} -af arnndn=m={model_path} {output_file} -y"
    subprocess.run(command, shell=True, check=True)
    return output_file

# Add/expand utility for handling audio uploads and transcription

def save_uploaded_audio(audio_file, upload_folder, session_id):
    audio_path = os.path.join(upload_folder, audio_file.filename)
    audio_file.save(audio_path)
    return audio_path

# Example function for transcribing audio using Whisper (requires model_whisper)
def transcribe_audio_file(audio_path, language=None):
    if not language:
        language = "en"
    # Load audio for Whisper
    audio_data, _ = librosa.load(str(audio_path), sr=16000)
    # TODO: model_whisper must be defined and loaded elsewhere
    if language == "en":
        result = model_whisper.transcribe(audio_data, language="tl", fp16=True)
    elif language == "tl":
        result = model_whisper.transcribe(audio_data, language="en", fp16=True)
    else:
        result = model_whisper.transcribe(audio_data, language=language, fp16=True)
    return result['text']

# Example usage in a route (not a full route, just logic):
# language = data.get('language', 'en')  # Default to 'en' if not provided
# audio_file = request.files['audio']
# audio_path = save_uploaded_audio(audio_file, UPLOAD_FOLDER, session_id)
# reduce_noise_ffmpeg(audio_path, corrected_audio_path)
# res_flag = get_clean_audio(corrected_audio_path)
# if res_flag:
#     transcript = transcribe_audio_file(corrected_audio_path, language)
#     ...
# else:
#     ...

# TODO: Add/define model_whisper, transcrippp, UPLOAD_FOLDER, and any other session/audio state as needed for your app.

device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
mms_model = VitsModel.from_pretrained("facebook/mms-tts-eng").to(device)
mms_tokenizer = AutoTokenizer.from_pretrained("facebook/mms-tts-eng")

def tts_mms(text, pathh, pitch_factor=1.0):
    inputs = mms_tokenizer(text, return_tensors="pt").to(device)
    with torch.no_grad():
        output = mms_model(**inputs).waveform
    audio_data = output.squeeze().cpu().numpy()
    original_sr = mms_model.config.sampling_rate
    target_sr = int(original_sr * pitch_factor)
    new_length = int(len(audio_data) * target_sr / original_sr)
    resampled_audio = signal.resample(audio_data, new_length)
    resampled_audio = (resampled_audio / max(abs(resampled_audio)) * 32767).astype("int16")
    sf.write(pathh, resampled_audio, samplerate=original_sr)

def select_db(query, values=None):
    db_user = os.environ.get('db_user', 'postgres')
    db_password = os.environ.get('db_password', 'Postgres123')
    db_host = os.environ.get('db_host', 'localhost')
    db_port = os.environ.get('db_port', '5432')
    db_database = os.environ.get('db_database', 'postgres')
    connection = psycopg2.connect(
        user=db_user,
        password=db_password,
        host=db_host,
        port=db_port,
        database=db_database
    )
    cursor = connection.cursor()
    if values:
        cursor.execute(query, values)
    else:
        cursor.execute(query)
    result = cursor.fetchall()
    connection.commit()
    cursor.close()
    connection.close()
    return result

def build_chat_history(session_id, chat):
    chat_history = []
    try:
        query = f'''SELECT question, answer FROM chat_history_table WHERE session_id = %s ORDER BY created_on DESC LIMIT 5;'''
        history_response = select_db(query, (session_id,))
        for chat_session in reversed(history_response):
            chat_history.append({'role': 'user', 'content': [{"type": "text", 'text': chat_session[0]}]})
            chat_history.append({'role': 'assistant', 'content': [{"type": "text", 'text': chat_session[1]}]})
    except Exception as e:
        print("Error building chat history:", e)
    chat_history.append({'role': 'user', 'content': [{"type": "text", 'text': chat}]})
    return chat_history

# --- FAQ/Knowledge Base Retrieval Tool ---
# NOTE: You must initialize 'retrieve_client' and 'KB_ID' appropriately in this file.
# Example:
# from your_retrieval_module import YourRetrieveClient
# retrieve_client = YourRetrieveClient(...)
# KB_ID = 'your-knowledge-base-id'
retrieve_client = boto3.client('bedrock-agent-runtime',region_name = 'us-east-1',aws_access_key_id='AKIAWUB2NCKOVTAOOBPU',aws_secret_access_key='QTXeHtqVwxzalrtCaM/rP0d0iQ3KCGA3JYs2dL7V')
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
            knowledgeBaseId='V4VHPF0CDM',
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

def agent_invoke_tool(chat_history, session_id, chat, connectionId):
    try:
        schema = os.environ.get('schema', 'public')
        prompt_metadata_table = os.environ.get('prompt_metadata_table', 'prompt_metadata')
        model_id = os.environ.get('model_id', 'anthropic.claude-3-haiku-20240307-v1:0')
        select_query = f'''select base_prompt from {schema}.{prompt_metadata_table} where id =1;'''
        base_prompt = select_db(select_query)[0][0]
        bedrock_client = boto3.client('bedrock-runtime', region_name='us-east-1',aws_access_key_id='AKIAWUB2NCKOVTAOOBPU',aws_secret_access_key='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')
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
                        "claim_type": {"type": "string", "description": "Type of claim (e.g., Hospitalisation, Accident, Terminal Illness, Death)"},
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
                        "preferred_timeslot": {"type": "string", "description": "Preferred time slot (e.g., '13 July, 2-4pm')"},
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
        # First API call to get initial response
        request_body = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 4000,
            "temperature": 0,
            "system": base_prompt,
            "tools": insurance_tools,
            "messages": chat_history
        }
        response = bedrock_client.invoke_model(
            modelId=model_id,
            body=json.dumps(request_body),
            contentType="application/json",
            accept="application/json"
        )
        inference_result = response['body'].read().decode('utf-8')
        final = json.loads(inference_result)
        answer = None
        for content in final.get('content', []):
            if content.get('type') == 'text':
                answer = content['text']
                break
        if not answer:
            answer = "Sorry, I couldn't process your request."
        return {"answer": answer, "question": chat, "session_id": session_id}
    except Exception as e:
        print("Error in agent_invoke_tool:", e)
        return {"answer": "Sorry, I couldn't process your request.", "question": chat, "session_id": session_id}

app = Flask(__name__)


@app.route("/ping", methods = ["GET"])
def ping():
    print("ping")
    return "pong"


@app.route('/transcribe', methods=['POST'])
def transcribe():
    try:
        data = request.get_json()
        chat = data.get('chat')
        session_id = data.get('session_id')
        connectionId = data.get('connectionId')
        chat_history = data.get('chat_history', [])

        # Get the agent's answer (text) using the advanced tool logic
        result = agent_invoke_tool(chat_history, session_id, chat, connectionId)
        answer = result["answer"] if isinstance(result, dict) and "answer" in result else str(result)

        # Synthesize the answer to speech using TTS
        with tempfile.NamedTemporaryFile(delete=False, suffix='.wav') as temp_audio:
            temp_speech_path = temp_audio.name
        tts_mms(answer, temp_speech_path, pitch_factor=1.0)

        # Return the audio file as the response
        response = send_file(
            temp_speech_path,
            mimetype="audio/wav",
            as_attachment=True,
            download_name="response.wav"
        )

        # Optionally, clean up the temp file after sending
        @response.call_on_close
        def cleanup():
            try:
                os.remove(temp_speech_path)
            except Exception:
                pass

        return response
    except Exception as e:
        print("Error in /chat_tool:", e)
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
