import json
import boto3
import os
import time
from urllib.parse import unquote_plus

def handler(event, context):
    """
    Lambda function to automatically sync Bedrock Knowledge Base data sources
    when new files are uploaded to S3.
    """
    
    bedrock_agent = boto3.client('bedrock-agent')
    
    # Get environment variables
    banking_kb_id = os.environ.get('BANKING_KB_ID')
    insurance_kb_id = os.environ.get('INSURANCE_KB_ID')
    retail_kb_id = os.environ.get('RETAIL_KB_ID')
    banking_ds_id = os.environ.get('BANKING_DS_ID')
    insurance_ds_id = os.environ.get('INSURANCE_DS_ID')
    retail_ds_id = os.environ.get('RETAIL_DS_ID')
    
    print(f"Environment variables - Banking KB: {banking_kb_id}, Insurance KB: {insurance_kb_id}, Retail KB: {retail_kb_id}")
    print(f"Environment variables - Banking DS: {banking_ds_id}, Insurance DS: {insurance_ds_id}, Retail DS: {retail_ds_id}")
    print(f"Event: {json.dumps(event)}")
    
    try:
        # Process S3 event records
        for record in event.get('Records', []):
            if record.get('eventSource') == 'aws:s3':
                bucket_name = record['s3']['bucket']['name']
                object_key = unquote_plus(record['s3']['object']['key'])
                event_name = record['eventName']
                
                print(f"Processing {event_name} for object: {object_key} in bucket: {bucket_name}")
                
                # Only process PUT events (new files or updates)
                if not event_name.startswith('ObjectCreated'):
                    print(f"Skipping event {event_name}")
                    continue
                
                # Determine which knowledge base to sync based on the file path
                kb_id = None
                ds_id = None
                kb_type = None
                
                if object_key.startswith('bank/'):
                    kb_id = banking_kb_id
                    ds_id = banking_ds_id
                    kb_type = "Banking"
                elif object_key.startswith('insurance/'):
                    kb_id = insurance_kb_id
                    ds_id = insurance_ds_id
                    kb_type = "Insurance"
                elif object_key.startswith('kb/retail/') or object_key.startswith('retail/'):
                    kb_id = retail_kb_id
                    ds_id = retail_ds_id
                    kb_type = "Retail"
                else:
                    print(f"Object {object_key} doesn't match any knowledge base prefix (bank/, insurance/, kb/retail/)")
                    continue
                
                if not kb_id or not ds_id:
                    print(f"Missing KB ID or DS ID for {kb_type} knowledge base")
                    continue
                
                print(f"Starting ingestion job for {kb_type} Knowledge Base (KB: {kb_id}, DS: {ds_id})")
                
                # Check if there's already a running ingestion job
                try:
                    response = bedrock_agent.list_ingestion_jobs(
                        knowledgeBaseId=kb_id,
                        dataSourceId=ds_id,
                        maxResults=1
                    )
                    
                    # Check if the latest job is still running
                    if response.get('ingestionJobSummaries'):
                        latest_job = response['ingestionJobSummaries'][0]
                        if latest_job['status'] in ['STARTING', 'IN_PROGRESS']:
                            print(f"Ingestion job already running for {kb_type} KB: {latest_job['ingestionJobId']}")
                            continue
                    
                except Exception as e:
                    print(f"Error checking existing ingestion jobs: {str(e)}")
                
                # Start new ingestion job
                try:
                    ingestion_response = bedrock_agent.start_ingestion_job(
                        knowledgeBaseId=kb_id,
                        dataSourceId=ds_id,
                        description=f"Auto-sync triggered by S3 object: {object_key}"
                    )
                    
                    ingestion_job_id = ingestion_response['ingestionJob']['ingestionJobId']
                    print(f"Started ingestion job {ingestion_job_id} for {kb_type} Knowledge Base")
                    
                    # Optional: Wait a bit and check status
                    time.sleep(5)
                    status_response = bedrock_agent.get_ingestion_job(
                        knowledgeBaseId=kb_id,
                        dataSourceId=ds_id,
                        ingestionJobId=ingestion_job_id
                    )
                    
                    print(f"Ingestion job status: {status_response['ingestionJob']['status']}")
                    
                except Exception as e:
                    print(f"Error starting ingestion job for {kb_type} KB: {str(e)}")
                    # Don't raise here to allow processing other records
                    continue
        
        return {
            'statusCode': 200,
            'body': json.dumps('Auto-sync completed successfully')
        }
        
    except Exception as e:
        print(f"Error in auto-sync function: {str(e)}")
        raise e 