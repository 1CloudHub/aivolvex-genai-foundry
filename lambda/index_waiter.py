import boto3
import os
import json
import time
import cfnresponse
from requests_aws4auth import AWS4Auth
from opensearchpy import OpenSearch, RequestsHttpConnection
from opensearchpy.exceptions import RequestError, NotFoundError

def lambda_handler(event, context):
    """
    Lambda function to wait for OpenSearch index to be fully available.
    This function polls the index until it's ready for use.
    """
    
    print("=== INDEX WAITER SCRIPT ===")
    print(f"Event: {json.dumps(event)}")
    
    try:
        if event['RequestType'] == 'Delete':
            cfnresponse.send(event, context, cfnresponse.SUCCESS, {})
            return
        
        properties = event['ResourceProperties']
        index_name = properties.get('index_name')
        max_retries = int(properties.get('max_retries', 60))  # Default to 60
        retry_delay = int(properties.get('retry_delay', 5))    # Default to 5 seconds
        
        print(f"Waiting for index: {index_name}")
        print(f"Max retries: {max_retries}, Retry delay: {retry_delay} seconds")
        print(f"Total timeout: {max_retries * retry_delay} seconds")
        
        # Get environment variables
        opensearch_endpoint = os.environ.get("OPENSEARCH_ENDPOINT")
        collection_name = os.environ.get("COLLECTION_NAME")
        
        if not opensearch_endpoint:
            raise ValueError("OPENSEARCH_ENDPOINT environment variable is required")
        
        if not collection_name:
            raise ValueError("COLLECTION_NAME environment variable is required")
        
        # Wait for index to be available
        result = wait_for_index(opensearch_endpoint, collection_name, index_name, max_retries, retry_delay)
        
        if result['success']:
            print("Index is ready for use")
            cfnresponse.send(event, context, cfnresponse.SUCCESS, {
                'IndexName': index_name,
                'CollectionEndpoint': opensearch_endpoint,
                'Status': 'READY'
            })
        else:
            print(f"Failed to wait for index: {result['error']}")
            cfnresponse.send(event, context, cfnresponse.FAILED, {}, result['error'])
            
    except Exception as e:
        print(f"Error: {str(e)}")
        cfnresponse.send(event, context, cfnresponse.FAILED, {}, str(e))

def wait_for_index(opensearch_endpoint, collection_name, index_name, max_retries, retry_delay):
    """Wait for OpenSearch index to be fully available"""
    try:
        # Get credentials from the Lambda execution environment
        session = boto3.Session()
        credentials = session.get_credentials()
        
        if not credentials:
            raise Exception("Failed to get AWS credentials")
        
        print(f"Running as IAM Role: {boto3.client('sts').get_caller_identity()['Arn']}")
        
        # Set up AWS authentication for OpenSearch
        region = os.environ.get('AWS_REGION', 'us-west-2')  # Get from environment or default
        print(f"Using AWS region: {region}")
        print(f"OpenSearch endpoint: {opensearch_endpoint}")
        print(f"Collection name: {collection_name}")
        print(f"Index name: {index_name}")
        
        awsauth = AWS4Auth(
            credentials.access_key,
            credentials.secret_key,
            region,
            'aoss',
            session_token=credentials.token
        )
        
        # Initialize OpenSearch client
        host = opensearch_endpoint.replace('https://', '').replace('http://', '')
        print(f"Connecting to OpenSearch host: {host}")
        print(f"Using port: 443")
        print(f"Using SSL: True")
        print(f"Using AWS4Auth with region: {region}")
        
        client = OpenSearch(
            hosts=[{'host': host, 'port': 443}],
            http_auth=awsauth,
            use_ssl=True,
            verify_certs=True,
            http_compress=True,
            connection_class=RequestsHttpConnection
        )
        
        print("OpenSearch client initialized successfully")
        
        # Test connection by listing indices
        try:
            print("Testing connection by listing indices...")
            indices = client.indices.get_alias()
            print(f"Successfully connected! Available indices: {list(indices.keys())}")
        except Exception as e:
            print(f"Connection test failed: {e}")
            print(f"Error type: {type(e)}")
            print(f"Error details: {str(e)}")
        
        # Poll for index availability
        for attempt in range(max_retries):
            try:
                print(f"Attempt {attempt + 1}/{max_retries}: Checking if index '{index_name}' is ready...")
                
                # Check if index exists
                index_exists = client.indices.exists(index=index_name)
                if not index_exists:
                    print(f"Index '{index_name}' does not exist yet. Waiting {retry_delay} seconds...")
                    # Check if there are any indices in the collection
                    try:
                        all_indices = client.indices.get_alias()
                        print(f"Available indices in collection: {list(all_indices.keys())}")
                    except Exception as list_error:
                        print(f"Could not list indices: {list_error}")
                    time.sleep(retry_delay)
                    continue
                
                # Check if index is ready (green status)
                try:
                    # First check if index is accessible
                    index_stats = client.indices.stats(index=index_name)
                    print(f"Index exists and is accessible")
                    
                    # Check if index has proper mapping
                    try:
                        index_mapping = client.indices.get_mapping(index=index_name)
                        print(f"Index mapping retrieved successfully")
                        
                        # Check if index has the required fields
                        if 'properties' in index_mapping.get(index_name, {}).get('mappings', {}):
                            properties = index_mapping[index_name]['mappings']['properties']
                            if 'vector' in properties and 'text' in properties:
                                print(f"Index has required fields: vector, text")
                                print(f"Index '{index_name}' is ready for use!")
                                return {'success': True}
                            else:
                                print(f"Index exists but missing required fields. Available: {list(properties.keys())}")
                                time.sleep(retry_delay)
                                continue
                        else:
                            print(f"Index exists but no properties found in mapping")
                            time.sleep(retry_delay)
                            continue
                        
                    except Exception as mapping_error:
                        print(f"Index exists but mapping not ready yet: {mapping_error}")
                        time.sleep(retry_delay)
                        continue
                    
                except Exception as e:
                    print(f"Index exists but not ready yet: {e}")
                    time.sleep(retry_delay)
                    continue
                    
            except Exception as e:
                print(f"Error checking index status: {e}")
                time.sleep(retry_delay)
                continue
        
        # If we get here, the index wasn't ready within the timeout
        raise Exception(f"Index '{index_name}' was not ready within {max_retries * retry_delay} seconds")
        
    except Exception as e:
        print(f"Error in wait_for_index: {str(e)}")
        return {'success': False, 'error': str(e)} 