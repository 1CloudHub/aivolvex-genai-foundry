# from faster_whisper import WhisperModel
from flask import Flask, request, jsonify, send_file
import os
from flask_cors import CORS
from asgiref.wsgi import WsgiToAsgi
from botocore.exceptions import ClientError
import base64

import tempfile
import boto3
import json
import time
from uuid import uuid4
import uuid
import soundfile as sf
from pathlib import Path
from openai import OpenAI
from pydub import AudioSegment
from langchain_aws import ChatBedrock 
from langchain.memory import ConversationBufferMemory  # Import the required memory class
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import MessagesState
from langchain.chains import LLMChain
from langchain.prompts import PromptTemplate
import numpy as np
# from transformers import SpeechT5Processor, SpeechT5ForTextToSpeech, SpeechT5HifiGan
from datasets import load_dataset
import torch
import soundfile as sf
from pprint import pprint
import psycopg2
from datetime import datetime
import pytz

# from mouthcuess import PhonemeBasedMouthCueGenerator
import librosa
from langchain_anthropic import ChatAnthropic
from transformers import VitsModel, AutoTokenizer
device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
mms_model = VitsModel.from_pretrained("facebook/mms-tts-eng").to(device)
mms_tokenizer = AutoTokenizer.from_pretrained("facebook/mms-tts-eng")
app = Flask(__name__)
asgi_app = WsgiToAsgi(app)
# client_openai = OpenAI()
CORS(app)
# model_size = "medium"
# model = WhisperModel(model_size, device="cuda", compute_type="float16")
import os
# import whisper
# model_whisper = whisper.load_model("medium")
aws_access_key = os.environ.get("AWS_ACESS_KEY")
aws_secret_key = os.environ.get("AWS_SECRET_KEY")
region = "us-west-2"

print("REGIONNNNNNNNNNNNNNNNNN", region)


def base64_to_text(base64_string):
    """
    Convert a base64 string back to original text
    
    Args:
        base64_string (str): The base64 encoded string
    
    Returns:
        str: The decoded text
    """
    decoded_bytes = base64.b64decode(base64_string)
    return decoded_bytes.decode('utf-8')



def send_private_message(connectionId, body, client):
    print("SENDING PRIVATE MESSAGE")
    print(f"Connection ID: {connectionId}")
    

    try:
        json_data = json.dumps(body)
        
        response = client.post_to_connection(
            ConnectionId=connectionId, 
            Data=json_data.encode('utf-8')
        )
        print(f"Send Response: {response}")
        
    except client.exceptions.GoneException:
        print(f"Connection {connectionId} is closed")
    except Exception as e:
        print(f"Error sending message: {str(e)}")
    
    return True

def transcribe_audio_with_aws(audio_data, bucket_name, session, sample_rate=16000):
    """
    Transcribe audio data using Amazon Transcribe
    
    Args:
        audio_data: numpy array from librosa.load()
        sample_rate: sample rate of the audio (default 16000)
    
    Returns:
        str: Transcribed text
    """
    if not bucket_name:
        raise ValueError("S3_PATH environment variable not set")
    
    # Initialize AWS clients
    transcribe_client = session.client('transcribe')
    s3_client = session.client('s3')
    
    # Configuration
    job_name = f'transcription-job-{int(time.time())}'
    
    try:
        # Step 1: Save audio data to temporary WAV file
        with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as temp_file:
            sf.write(temp_file.name, audio_data, sample_rate)
            temp_audio_path = temp_file.name
        
        # Step 2: Upload audio file to S3
        s3_key = f'audio/{job_name}.wav'
        s3_client.upload_file(temp_audio_path, bucket_name, s3_key)
        audio_s3_uri = f's3://{bucket_name}/{s3_key}'
        
        # Step 3: Start transcription job - FIXED SETTINGS
        transcribe_client.start_transcription_job(
            TranscriptionJobName=job_name,
            Media={'MediaFileUri': audio_s3_uri},
            MediaFormat='wav',
            LanguageCode='en-US',
            MediaSampleRateHertz=sample_rate,
            Settings={
                'ShowSpeakerLabels': False  # Removed MaxSpeakerLabels since ShowSpeakerLabels is False
            }
        )
        
        # Step 4: Wait for transcription to complete
        print(f"Starting transcription job: {job_name}")
        while True:
            response = transcribe_client.get_transcription_job(TranscriptionJobName=job_name)
            status = response['TranscriptionJob']['TranscriptionJobStatus']
            
            if status == 'COMPLETED':
                print("Transcription completed!")
                break
            elif status == 'FAILED':
                print(f"Transcription failed: {response['TranscriptionJob'].get('FailureReason', 'Unknown error')}")
                return None
            else:
                print(f"Status: {status}... waiting")
                time.sleep(5)
        
        # Step 5: Get transcription result
        transcript_uri = response['TranscriptionJob']['Transcript']['TranscriptFileUri']
        
        # Download and parse the transcript
        import urllib.request
        with urllib.request.urlopen(transcript_uri) as response_data:
            transcript_json = json.loads(response_data.read().decode())
        
        # Extract the transcribed text
        transcript_text = transcript_json['results']['transcripts'][0]['transcript']
        
        # Cleanup: Delete the transcription job and S3 file
        transcribe_client.delete_transcription_job(TranscriptionJobName=job_name)
        s3_client.delete_object(Bucket=bucket_name, Key=s3_key)
        
        # Clean up temporary file
        os.unlink(temp_audio_path)
        
        return transcript_text
        
    except ClientError as e:
        print(f"AWS Error: {e}")
        return None
    except Exception as e:
        print(f"Error_LALALA: {e}")
        return None


def insert_db(query,values, db_cred):
    connection = psycopg2.connect(
        user=db_cred["db_user"],
        password=db_cred["db_password"],
        host=db_cred["db_host"],
        port=db_cred["db_port"],  # Replace with the SSH tunnel local bind port
        database=db_cred["db_database"]
    )    
                                                                            
    cursor = connection.cursor()
    cursor.execute(query,values)
    connection.commit()
    cursor.close()
    connection.close()
llm =  ChatBedrock(
    # credentials_profile_name="bedrock-admin",  
    # model_id = "anthropic.claude-3-5-sonnet-20240620-v1:0",
    model_id = "anthropic.claude-3-haiku-20240307-v1:0",

    region_name = region,
    model_kwargs={
        "max_tokens": 1000,  
        "temperature": 0.7,
        "anthropic_version": "bedrock-2023-05-31"
    }
)
translate = boto3.client(
    'translate',
    region_name=region

)

transcrippp = {}

import subprocess
import os
import json

rhubarb_path = "/home/ec2-user/rhubarb-lip-sync/build/rhubarb/rhubarb"


import torch

SAMPLING_RATE = 16000
model_vad, utils = torch.hub.load(repo_or_dir='snakers4/silero-vad',
                                model='silero_vad',
                                force_reload=True,
                                onnx=False)

(get_speech_timestamps,
  save_audio,
  read_audio,
  VADIterator,
  collect_chunks) = utils

#database credentials parameters
db_host=os.environ.get('DB_HOST')
db_database=os.environ.get('DB_NAME')
db_password=os.environ.get('DB_PASSWORD')
db_port=int(os.environ.get('DB_PORT', 5432))
db_user="postgres"



# db_host="genai-foundry-db.cduao4qkeseb.ap-southeast-1.rds.amazonaws.com"
# db_database="postgres"
# db_password="Postgres123"
# db_port=5432
# db_user="postgres"

def db_result1(query,values):
    connection = psycopg2.connect(
        user=db_user,
        password=db_password,
        host=db_host,
        port=db_port,  
        database=db_database
    )                                                                            
    cursor = connection.cursor()
    cursor.execute(query,values)
    connection.commit()
    cursor.close()
    connection.close()

def db_result2(query,values):
    connection = psycopg2.connect(
        user=db_user,
        password=db_password,
        host=db_host,
        port=db_port,  
        database=db_database
    )                                                                            
    cursor = connection.cursor()
    cursor.execute(query,values)
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

    #platform_credential_parameters


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

bedrock_client = boto3.client(
    'bedrock-runtime', 
    region_name=region

)

retrieve_client = boto3.client(
    'bedrock-agent-runtime',
    region_name=region,

)
def get_clean_audio(path):
    flagg = None
    wav = read_audio(path, sampling_rate=SAMPLING_RATE)
    speech_timestamps = get_speech_timestamps(wav, model_vad, sampling_rate=SAMPLING_RATE)
    if len(speech_timestamps)> 0:
      print("audio exists")
      



      save_audio(f"{path}",
              collect_chunks(speech_timestamps, wav), sampling_rate=SAMPLING_RATE)
      flagg = True
    else:
       print("Nothing")
       flagg = False 
    return flagg

def reduce_noise_ffmpeg(input_file, output_file):
    # command = f"ffmpeg -i {input_file} -af arnndn=m=cb.rnnn {output_file} -y"
    model_path = "/home/ubuntu/voiceops/voiceops/cb.rnnn"
    command = f"ffmpeg -i {input_file} -af arnndn=m={model_path} {output_file} -y"
    subprocess.run(command, shell=True, check=True)
    return output_file

# generator = PhonemeBasedMouthCueGenerator()

# @app.route('/mouth_cue', methods=['POST'])
# def mouth_cue_shell():
#     if 'audio' not in request.files:
#         return jsonify({'error': 'No audio file uploaded'}), 400
    
#     audio_file = request.files['audio']

#     audio_path = os.path.join(UPLOAD_FOLDER, audio_file.filename)
#     audio_file.save(audio_path)
#     re_json = generator.process_audio(audio_path)
#     return json.dumps(re_json, indent=4)



sessions ={}
def generate_presigned_url(bucket_name, object_name, region, expiration=604800):

    try:
        s3_client = boto3.client(
            's3',
            aws_access_key_id=aws_access_key,
            aws_secret_access_key=aws_secret_key,
            region_name=region
        )
        url = s3_client.generate_presigned_url('get_object',
                                              Params={'Bucket': bucket_name,
                                                     'Key': object_name},
                                              ExpiresIn=expiration)
        return url
    except Exception as e:
        print(f"Error generating presigned URL: {e}")
        return None

def translate_text(text, source_lang='auto', target_lang='en'):
    print(f"AWS Translate: from {source_lang} to {target_lang}, text: {text[:30]}...")
    try:
        response = translate.translate_text(
            Text=text,
            SourceLanguageCode=source_lang,
            TargetLanguageCode=target_lang
        )
        translated = response['TranslatedText']
        print(f"AWS Translation result: {translated[:30]}...")
        return translated
    except Exception as e:
        print(f"Translation error: {e}")
        return f"ERROR_TRANSLATE: {str(e)}"
def upload_to_s3(file_path, bucket_name, object_name, region):
    """
    Upload a file to an S3 bucket
    
    :param file_path: Path to the file to upload
    :param bucket_name: Name of the bucket
    :param object_name: S3 object name (path within the bucket)
    :return: True if file was uploaded, else False
    """
    try:
        s3_client = boto3.client(
            's3',
            aws_access_key_id=aws_access_key,
            aws_secret_access_key=aws_secret_key,
            region_name=region
        )
        s3_client.upload_file(file_path, bucket_name, object_name)
        return True
    except Exception as e:
        print(f"Error uploading to S3: {e}")
        return False

# Add this function to combine audio files
def combine_wav_files(existing_file_path, output_file_path):
    """
    Combine all audio in the existing file and save to output path
    
    :param existing_file_path: Path to the existing audio file
    :param output_file_path: Path to save the combined audio
    """
    try:
        # Just copy the existing file since it already contains all audio
        audio, sample_rate = sf.read(existing_file_path, dtype='int16')
        sf.write(output_file_path, audio, sample_rate, subtype='PCM_24')
        print(f"Combined audio saved at {output_file_path}")
        return True
    except Exception as e:
        print(f"Error combining audio: {e}")
        return False

def get_or_create_memory(session_id):
    if session_id not in sessions:
        sessions[session_id] = ConversationBufferMemory(
            return_messages=True
        )
    return sessions[session_id]


prompt_template_tagalog ="""
"You are an AI-powered bilingual casino front desk agent. Your task is to accurately translate text while maintaining a warm, professional, and hospitality-focused tone.

<input_data>
{input}
</input_data>


Here is the text to translate: refer to input_data tag

 

Please translate the above text into tagalog and provide only the transliteration of the translated text.

Guidelines for translation:

Do not include tagalog language scripts, explanations, or additional text.
Maintain the core message and intent.
Use polite and engaging phrasing appropriate for a luxury casino environment.
Adapt idioms and expressions naturally.
Adjust formality levels appropriately.
Do not translate proper nouns like casino names, VIP programs, or location names.

"""


prompt_t = PromptTemplate(
    input_variables=["input"],
    template=prompt_template_tagalog
)


prompt_template_english ="""
"You are an AI-powered bilingual casino front desk agent. Your task is to accurately translate text while maintaining a warm, professional, and hospitality-focused tone.

<input_data>
{input}
</input_data>


Here is the text to translate: refer to input_data tag

 

Please translate the above text into english and provide only the transliteration of the translated text.

Guidelines for translation:

Do not include English language scripts, explanations, or additional text.
Maintain the core message and intent.
Use polite and engaging phrasing appropriate for a luxury casino environment.
Adapt idioms and expressions naturally.
Adjust formality levels appropriately.
Do not translate proper nouns like casino names, VIP programs, or location names.

"""


prompt_e = PromptTemplate(
    input_variables=["input"],
    template=prompt_template_english
)


prompt_senti="""
You are a sentiment analyser, consider the below input of the user and output a single word:
<user_response>
{user_response}
</user_response>

<instruction>
-Output should be a single word
Since the user response is about the health, analyse the sentiment accordingly
</instruction>

Based on the user's response, provide a sentiment analysis of the text. You have the following sentiment options:

"disappointment", "sadness", "annoyance", "neutral", "disapproval", "realization", "nervousness", "approval", "joy", "anger", "embarrassment", "caring", "remorse", "disgust", "grief", "confusion", "relief", "desire", "admiration", "optimism", "fear", "love", "excitement", "curiosity", "amusement", "surprise", "gratitude", "pride"

Note:
-Strictly follow the instructions
"""

prompt_senti_template = PromptTemplate(
    input_variables=["user_response"],
    template=prompt_senti
)

senti_llm = LLMChain(
    llm=llm,
    prompt=prompt_senti_template,
    verbose=True
)

prompt_summary="""
You are going to summarise the transcript provided, consider the below input :
<transcript>
{transcript}
</transcript>

<instruction>
-The transcript is based on a health checkup of a user
-The transcript is of array format, where the odd possitions are user input , and even positons are bot input
-The Odd possitions will, have 3 indexes. 3rd position is the sentiment of the 2 nd index
-Donot mention about the sentiment.
-Summary will be of 30 words.
-Do not mention the word summary in your response.
</instruction>

Note:
-Strictly follow the instructions
"""
prompt_summary_template = PromptTemplate(
    input_variables=["transcript"],
    template=prompt_summary
)

summary_llm = LLMChain(
    llm=llm,
    prompt=prompt_summary_template,
    verbose=True
)





UPLOAD_FOLDER = './uploads'
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)
# @app.route('/')
@app.route('/fetchaudio', methods=['POST'])
def fetch_audio():
    try:
        # Fetch combined data
        combined_query = """
            SELECT a.id, a.audio, a.created_at, a.session_id, 
                   t.transcript_json, t.created_at 
            FROM coaching_assist.audio_summary a
            LEFT JOIN coaching_assist.transcript t 
            ON a.session_id = t.session_id
            ORDER BY a.created_at DESC;
        """
        combined_result = db_result(combined_query)

        # Debugging: Print fetched data to check
        # print("Fetched Data:", combined_result)

        # Construct response
        audio_records = []
        for record in combined_result:
            record_data = {
                "id": record[0],
                "audio_url": record[1],
                "created_at": record[2].strftime("%Y-%m-%d %H:%M:%S") if record[2] else None,
                "session_id": record[3],
                "transcript_json": record[4] if record[4] else "DEBUG_EMPTY",
                "transcript_created_at": record[5].strftime("%Y-%m-%d %H:%M:%S") if record[5] else None
            }
            audio_records.append(record_data)

        return jsonify({"status": "success", "data": audio_records})
    except Exception as e:
        print(f"Error fetching audio records: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500
# --- Mock tool implementations ---

@app.route('/transcribe', methods=['POST'])
def transcribe_audio():
    try:

        # print("QQQQQQQQQQQQQQQQQQQQQ", request.json)
        data_aud = request.json
        session_id = data_aud.get('session_id')
        print("SESSSSSIIOONN", session_id)
        connection_id = data_aud.get("connection_id")
        connection_url = data_aud.get("connection_url")
        bucket_name = data_aud.get("bucket_name")
        total_region = data_aud.get("region_name", "us-west-2")
        print("TOTALLLLLLLLL", total_region)
        kb_id = data_aud.get("kb_id")
        prompt_template_front = data_aud.get("prompt_template")
        db_cred = data_aud.get("db_cred")
        open_ai_key = base64_to_text("c2stcHJvai0xS18tUXdxVWNUM2wtZFVjTTlhdHNBdjk0OGpZejJBTDhIVWlPaTR4SWxyYTZIZU9sT0tzaE5uV1E4WmVBUGZhdUNMMkJXd2NpVVQzQmxia0ZKVWxqLXdmS2NpSnpCYi14X1pmV3pSSlVuTnVJWlAzb0pYazNrc1VLNllCaG43QTlmUDNIQy1DandPX1NzZ21maGp5ZUZSVVdGVUE=")
        print("KEYYYYYYYYYY", open_ai_key)
        # region = data_aud.get("region")
        # status_flag = request.form.get('status_flag')
        # t_language = request.form.get('language')
        # trans_type = request.form.get('type', 'llm') 
        box_type = data_aud.get('box_type')
        print("box_type",box_type)
        session_awsss = boto3.Session(region_name=total_region)

        if not session_id:
            session_id = str(uuid4())
        t_language = data_aud.get('language')
        # if 'audio' not in request.files:
        #     return jsonify({'error': 'No audio file uploaded', 'session_id': session_id}), 400
        
        # Create unique folder for this request to avoid race conditions
        request_id = str(uuid4())
        request_folder = os.path.join(UPLOAD_FOLDER, f"{session_id}")
        os.makedirs(request_folder, exist_ok=True)
        print(f"📁 Created unique folder: {request_folder}")
        
        # Save uploaded audio file
        # audio_file = request.files['audio']
        audio_file = data_aud.get("audio")
        audio_path = os.path.join(request_folder, f"{session_id}.wav")
        print(f"💾 Saving uploaded audio file to: {audio_path}")
        # audio_file.save(audio_path)
        import base64
        wav_data = base64.b64decode(audio_file)

# Write the bytes to a WAV file
        with open(audio_path, "wb") as wav_file:
            wav_file.write(wav_data)

        print("WAV file created successfully!")
        print(f"Successfully saved uploaded audio file: {audio_path}")
        
        import librosa
        audio_data, _ = librosa.load(str(audio_path), sr=16000)
        print("Transcribing audio with Whisper (default: English)...")
        # result_obj = model_whisper.transcribe(audio_data, language="en", fp16=True)
        # result = result_obj['text']
        result = transcribe_audio_with_aws(audio_data, bucket_name, session_awsss)
        print("Whisper transcription result:", result)
        chat = result
        connectionId = request.form.get('connectionId', 'default-conn-id')
        print("Calling knowledge_base_retrieve_and_generate with transcription...")
        answer = knowledge_base_retrieve_and_generate(chat, session_id, kb_id, box_type, prompt_template_front, db_cred)
        print("Knowledge base answer:", answer)
        
        # Clean up uploaded file immediately after processing
        print("Cleaning up uploaded audio file immediately...")
        # try:
        #     if os.path.exists(audio_path):
        #         os.remove(audio_path)
        #         print(f"Successfully cleaned up uploaded audio file: {audio_path}")
        #     else:
        #         print(f" Uploaded audio file not found: {audio_path}")
        # except Exception as e:
        #     print(f" Error cleaning up uploaded file: {e}")
        #     print(f"🔍 Upload cleanup error type: {type(e).__name__}")
        
        # Create TTS response file in the unique folder
        ww = f"tts_response_{uuid4()}.wav"
        temp_speech_path = os.path.join(request_folder, ww)
        print(f"🎵 Creating TTS response file at: {temp_speech_path}")
        print(f"🎵 Synthesizing TTS to {temp_speech_path} ...")
        tts_openAi(answer, temp_speech_path, open_ai_key)
        print(f"✅ Successfully created TTS response file: {temp_speech_path}")

        upload_to_s3(temp_speech_path, bucket_name, f"audio/{ww}", total_region)

        hh = generate_presigned_url(bucket_name, f"audio/{ww}", total_region) 

        print("QQQQQQQQQQQQQQQQQQQQQQQQQ", hh)
        
        with open(temp_speech_path, 'rb') as audio_file:
            audio_bytes = audio_file.read()
            audio_base64 = base64.b64encode(audio_bytes).decode('utf-8')
        
        
        # Create JSON response with base64 audio
        response = {
            'audio': hh,
            'session_id': session_id,
            'message': 'TTS response generated successfully',
            'audio_format': 'wav'
        }
        # response.headers['X-Session-Id'] = session_id
        
        # Clean up TTS file and request folder immediately after sending response
        client_apigateway = boto3.client('apigatewaymanagementapi', region_name=region,
                            endpoint_url=connection_url)
        
        send_private_message(connection_id, response, client_apigateway)
        
        print("🔄 Cleaning up files created for this request...")
        # try:
        #     # Clean up TTS response file
        #     if os.path.exists(temp_speech_path):
        #         os.remove(temp_speech_path)
        #         print(f"✅ Successfully cleaned up TTS response file: {temp_speech_path}")
        #     else:
        #         print(f"⚠️ TTS response file not found: {temp_speech_path}")
            
        #     # Clean up uploaded audio file (if not already cleaned)
        #     if os.path.exists(audio_path):
        #         os.remove(audio_path)
        #         print(f"✅ Successfully cleaned up uploaded audio file: {audio_path}")
            
        #     # Remove the request folder (since it was created specifically for this request)
        #     import shutil
        #     if os.path.exists(request_folder):
        #         shutil.rmtree(request_folder)
        #         print(f"✅ Successfully cleaned up request folder: {request_folder}")
        #     else:
        #         print(f"⚠️ Request folder not found: {request_folder}")
        # except Exception as e:
        #     print(f"❌ Error cleaning up files: {e}")
        #     print(f"🔍 File cleanup error type: {type(e).__name__}")
        
        return {"status": 200}
    except Exception as e:

        print("❌ Error in /transcribe:", e)
        print(f"🔍 Error type: {type(e).__name__}")
        import traceback
        print(f"📋 Full traceback: {traceback.format_exc()}")
        
        # Clean up uploaded file and request folder even if there's an error
        print("🔄 Attempting emergency cleanup after error...")
        try:
            print("dddd")
            # if 'audio_path' in locals() and os.path.exists(audio_path):
            #     os.remove(audio_path)
            #     print(f"✅ Successfully cleaned up uploaded audio file after error: {audio_path}")
            # else:
            #     print(f"⚠️ Could not clean up uploaded file - path not found or not accessible")
            
            # # Clean up request folder if it exists
            # if 'request_folder' in locals() and os.path.exists(request_folder):
            #     import shutil
            #     shutil.rmtree(request_folder)
            #     print(f"✅ Successfully cleaned up request folder after error: {request_folder}")
        except Exception as cleanup_error:
            print(f"❌ Error during emergency cleanup: {cleanup_error}")
            print(f"🔍 Emergency cleanup error type: {type(cleanup_error).__name__}")
        
        session_id = session_id if 'session_id' in locals() and session_id else str(uuid4())
        return jsonify({"error": str(e), "session_id": session_id}), 500
def select_db(query, db_cred):
    connection = psycopg2.connect(  
        user=db_cred["db_user"],
        password=db_cred["db_password"],
        host=db_cred["db_host"],
        port=db_cred["db_port"],
        database=db_cred["db_database"]
    )     
    print(db_user, db_password)                 
    cursor = connection.cursor()
    cursor.execute(query)
    result = cursor.fetchall()
    connection.commit()
    cursor.close()
    connection.close()
    return result


def upload_file_to_s3(file_path, bucket_name, object_key=None):
    """
    Upload a file to an S3 bucket
    
    Args:
        file_path (str): Path to the local file to upload
        bucket_name (str): Name of the S3 bucket
        object_key (str): S3 object key (filename in S3). If None, uses the local filename
    
    Returns:
        bool: True if file was uploaded successfully, False otherwise
    """
    # If no object key specified, use the filename
    if object_key is None:
        object_key = file_path.split('/')[-1]
    
    # Create S3 client
    s3_client = boto3.client('s3')
    
    try:
        # Upload the file
        s3_client.upload_file(file_path, bucket_name, object_key)
        print(f"File {file_path} uploaded successfully to {bucket_name}/{object_key}")
        return True
    except FileNotFoundError:
        print(f"Error: File {file_path} not found")
        return False
    except ClientError as e:
        print(f"Error uploading file: {e}")
        return False

def knowledge_base_retrieve_and_generate(query, session_id,kb_id, box_type, prompt_template, db_cred):
    print("aaaaaaaaaaaaaaaa", db_host, db_database, db_password)
    try:
        print("IN KNOWLEDGE BASE RETRIEVE AND GENERATE:", query)
        

        
        # Get configuration from voicebot_meta table based on box_type
        try:
            schema = 'genaifoundry'
            meta_table = 'voicebot_meta'
            
            # Map box_type to meta table id
            if box_type == "insurance":
                meta_id = 1
            elif box_type == "banking":
                meta_id = 2  # Assuming banking uses id = 2
            else:
                meta_id = 1  # Default to insurance
            
            meta_query = f'''SELECT kb_id, prompt_template, table_name FROM {schema}.{meta_table} WHERE id = {meta_id}'''
            print("SSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSS")
            meta_result = select_db(meta_query, db_cred)
            
            if meta_result and len(meta_result) > 0:
                # kb_id = meta_result[0][0]  # Get kb_id
                chat_table_name = meta_result[0][2]  # Get table_name for chat history
            else:
                # Fallback to default values
                # kb_id = "V4VHPF0CDM"
                chat_table_name = 'voice_history'
        except Exception as e:
            print(f"❌ Error retrieving voicebot config: {e}")
            # Fallback to default values
            # kb_id = "V4VHPF0CDM"
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
                history_response = select_db(history_query, db_cred)
                
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
        model_id="us.anthropic.claude-3-7-sonnet-20250219-v1:0"
        response = retrieve_client.retrieve_and_generate(
            input={
                'text': full_query
            },
            retrieveAndGenerateConfiguration={
                'type': 'KNOWLEDGE_BASE',
                'knowledgeBaseConfiguration': {
                    'knowledgeBaseId': kb_id,
                    # 'modelArn': 'arn:aws:bedrock:us-east-1:455389024925:inference-profile/us.anthropic.claude-3-7-sonnet-20250219-v1:0',
                    'modelArn':model_id,
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
            insert_db(insert_query, values, db_cred)
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
def tts_openAi(text, pathh, open_ai_key):

    temp_file_path = Path(pathh)
        
    with open(temp_file_path, "wb") as file:
        client_openai = OpenAI(api_key=open_ai_key)

        response = client_openai.audio.speech.create(
            model="tts-1",
            voice="sage",
            input=text,
            response_format="wav"
        )
        for chunk in response.iter_bytes():
            file.write(chunk)
def tts_mms(text, pathh, pitch_factor=1.0):
    global mms_model, mms_tokenizer, device
    
    inputs = mms_tokenizer(text, return_tensors="pt").to(device)
    
    with torch.no_grad():
        output = mms_model(**inputs).waveform
    
    # Move back to CPU for numpy conversion
    audio_data = output.squeeze().cpu().numpy()
    
    # Original sampling rate
    original_sr = mms_model.config.sampling_rate
    
    # Target sampling rate (higher = deeper voice/slower, lower = higher voice/faster)
    target_sr = int(original_sr * pitch_factor)
    
    # Resample using scipy
    import scipy.signal as signal
    
    # Calculate new length after resampling
    new_length = int(len(audio_data) * target_sr / original_sr)
    
    # Resample the audio
    resampled_audio = signal.resample(audio_data, new_length)
    
    # Normalize and convert to int16
    resampled_audio = (resampled_audio / np.max(np.abs(resampled_audio)) * 32767).astype(np.int16)
    
    # Save with original sampling rate (the pitch effect comes from resampling)
    sf.write(pathh, resampled_audio, samplerate=original_sr)

def get_response(user_input, session_id, tr_lang, trans_type="llm"):
    print("HIIIII< RESPONSEEE")
    
    # If using AWS Translate
    print("ttt",trans_type)
    if trans_type == "translate":
        print(f"Using AWS Translate with tr_lang={tr_lang}")
        if tr_lang == "english":
            # FROM Tagalog TO English
            print("Translating FROM Tagalog TO English")
            response = translate_text(user_input, source_lang='tl', target_lang='en')
        elif tr_lang == "tagalog":
            # FROM English TO Tagalog
            print("Translating FROM English TO Tagalog")
            response = translate_text(user_input, source_lang='en', target_lang='tl')
        
        # Store in memory for continuity
        memory = get_or_create_memory(session_id)
        memory.chat_memory.add_user_message(user_input)
        memory.chat_memory.add_ai_message(response)
        return response
    
    # Otherwise use LLM (Haiku)
    else:
        memory = get_or_create_memory(session_id)
        print(f"Using LLM with tr_lang={tr_lang}")
        if tr_lang == "tagalog":
            conversation = LLMChain(
                llm=llm,
                prompt=prompt_t,
                memory=memory,
                verbose=True
            )
        elif tr_lang == "english":
            conversation = LLMChain(
                llm=llm,
                prompt=prompt_e,
                memory=memory,
                verbose=True
            )
            
        response = conversation.predict(input=user_input)
        memory.chat_memory.add_user_message(user_input)
        memory.chat_memory.add_ai_message(response)
        return response
def get_sentiment(user_input):
    response = senti_llm.predict(user_response=user_input)
    return response

def get_summary(trasn):
    response = summary_llm.predict(transcript=trasn)
    return response

@app.route("/ping", methods = ["GET"])
def ping():
    print("ping")
    return "pong"

@app.route('/tlang', methods=['POST'])
def t_lang():
    data = request.json  # Extract JSON data from the request
    
    # Get language from the request
    language = data.get("language")
    # Set default language to 'english' if not provided
    if not language:
        language = 'english'
    if not language:
        return jsonify({"error": "No language provided"}), 400
    
    query=f'''INSERT INTO coaching_assist.pca_summary(language)VALUES(%s);'''     
    values=(language)
    db_result1(query,values)

    return jsonify({
        "language": language,
        "message": f"Language received: {language}"
    })

if __name__ == '__main__':
    print("TESTTTTTTTT GAAA")
    app.run(host='0.0.0.0', port=8000)

