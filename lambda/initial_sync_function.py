import json
import boto3
import os
import time
import cfnresponse

def handler(event, context):
    """
    Lambda function to trigger initial sync of Bedrock Knowledge Base data sources
    after Knowledge Base creation.
    """
    
    print(f"Initial sync event: {json.dumps(event)}")
    
    try:
        # Handle CloudFormation events
        if 'RequestType' in event:
            if event['RequestType'] == 'Delete':
                cfnresponse.send(event, context, cfnresponse.SUCCESS, {})
                return
            
            properties = event.get('ResourceProperties', {})
        else:
            properties = event
        
        bedrock_agent = boto3.client('bedrock-agent')
        
        # Get knowledge base and data source IDs
        banking_kb_id = properties.get('banking_kb_id') or os.environ.get('BANKING_KB_ID')
        insurance_kb_id = properties.get('insurance_kb_id') or os.environ.get('INSURANCE_KB_ID')
        retail_kb_id = properties.get('retail_kb_id') or os.environ.get('RETAIL_KB_ID')
        healthcare_kb_id = properties.get('healthcare_kb_id') or os.environ.get('HEALTHCARE_KB_ID')
        banking_ds_id = properties.get('banking_ds_id') or os.environ.get('BANKING_DS_ID')
        insurance_ds_id = properties.get('insurance_ds_id') or os.environ.get('INSURANCE_DS_ID')
        retail_ds_id = properties.get('retail_ds_id') or os.environ.get('RETAIL_DS_ID')
        healthcare_ds_id = properties.get('healthcare_ds_id') or os.environ.get('HEALTHCARE_DS_ID')
        
        print(f"Initial sync - Banking KB: {banking_kb_id}, Insurance KB: {insurance_kb_id}, Retail KB: {retail_kb_id}, Healthcare KB: {healthcare_kb_id}")
        print(f"Initial sync - Banking DS: {banking_ds_id}, Insurance DS: {insurance_ds_id}, Retail DS: {retail_ds_id}, Healthcare DS: {healthcare_ds_id}")
        
        # Sync both knowledge bases
        sync_results = {}
        
        # Sync Banking Knowledge Base
        if banking_kb_id and banking_ds_id:
            try:
                print(f"Starting initial sync for Banking Knowledge Base...")
                sync_results['banking'] = sync_knowledge_base(
                    bedrock_agent, banking_kb_id, banking_ds_id, "Banking"
                )
            except Exception as e:
                print(f"Error syncing Banking Knowledge Base: {str(e)}")
                sync_results['banking'] = {'error': str(e)}
        
        # Sync Insurance Knowledge Base
        if insurance_kb_id and insurance_ds_id:
            try:
                print(f"Starting initial sync for Insurance Knowledge Base...")
                sync_results['insurance'] = sync_knowledge_base(
                    bedrock_agent, insurance_kb_id, insurance_ds_id, "Insurance"
                )
            except Exception as e:
                print(f"Error syncing Insurance Knowledge Base: {str(e)}")
                sync_results['insurance'] = {'error': str(e)}

        # Sync Retail Knowledge Base
        if retail_kb_id and retail_ds_id:
            try:
                print(f"Starting initial sync for Retail Knowledge Base...")
                sync_results['retail'] = sync_knowledge_base(
                    bedrock_agent, retail_kb_id, retail_ds_id, "Retail"
                )
            except Exception as e:
                print(f"Error syncing Retail Knowledge Base: {str(e)}")
                sync_results['retail'] = {'error': str(e)}

        # Sync Healthcare Knowledge Base
        if healthcare_kb_id and healthcare_ds_id:
            try:
                print(f"Starting initial sync for Healthcare Knowledge Base...")
                sync_results['healthcare'] = sync_knowledge_base(
                    bedrock_agent, healthcare_kb_id, healthcare_ds_id, "Healthcare"
                )
            except Exception as e:
                print(f"Error syncing Healthcare Knowledge Base: {str(e)}")
                sync_results['healthcare'] = {'error': str(e)}
        
        print(f"Initial sync completed: {json.dumps(sync_results)}")
        
        # Send success response to CloudFormation
        if 'RequestType' in event:
            cfnresponse.send(event, context, cfnresponse.SUCCESS, sync_results)
        
        return {
            'statusCode': 200,
            'body': json.dumps(sync_results)
        }
        
    except Exception as e:
        print(f"Error in initial sync function: {str(e)}")
        if 'RequestType' in event:
            cfnresponse.send(event, context, cfnresponse.FAILED, {'error': str(e)})
        raise e

def sync_knowledge_base(bedrock_agent, kb_id, ds_id, kb_type):
    """
    Sync a specific knowledge base by starting an ingestion job.
    """
    
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
                return {
                    'status': 'already_running',
                    'ingestion_job_id': latest_job['ingestionJobId'],
                    'message': f'Ingestion job already running for {kb_type} Knowledge Base'
                }
    
    except Exception as e:
        print(f"Error checking existing ingestion jobs for {kb_type} KB: {str(e)}")
    
    # Start new ingestion job
    try:
        ingestion_response = bedrock_agent.start_ingestion_job(
            knowledgeBaseId=kb_id,
            dataSourceId=ds_id,
            description=f"Initial sync for {kb_type} Knowledge Base"
        )
        
        ingestion_job_id = ingestion_response['ingestionJob']['ingestionJobId']
        print(f"Started initial ingestion job {ingestion_job_id} for {kb_type} Knowledge Base")
        
        # Wait a bit and check status
        time.sleep(5)
        status_response = bedrock_agent.get_ingestion_job(
            knowledgeBaseId=kb_id,
            dataSourceId=ds_id,
            ingestionJobId=ingestion_job_id
        )
        
        status = status_response['ingestionJob']['status']
        print(f"Initial ingestion job status for {kb_type} KB: {status}")
        
        return {
            'status': 'started',
            'ingestion_job_id': ingestion_job_id,
            'job_status': status,
            'message': f'Initial sync started for {kb_type} Knowledge Base'
        }
        
    except Exception as e:
        print(f"Error starting initial ingestion job for {kb_type} KB: {str(e)}")
        return {
            'status': 'error',
            'error': str(e),
            'message': f'Failed to start initial sync for {kb_type} Knowledge Base'
        } 