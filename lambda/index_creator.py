import boto3
import os
import json
import time
from requests_aws4auth import AWS4Auth
from opensearchpy import OpenSearch, RequestsHttpConnection
from opensearchpy.exceptions import RequestError


def lambda_handler(event, context):
    """
    Lambda function to create a vector index for OpenSearch Serverless collection.
    This function uses the IAM role created by the CDK stack.
    
    Expected environment variables:
    - OPENSEARCH_ENDPOINT: The OpenSearch collection endpoint
    - COLLECTION_NAME: The name of the OpenSearch collection
    
    Expected event structure (CloudFormation Custom Resource):
    {
        "RequestType": "Create|Update|Delete",
        "ResourceProperties": {
            "index_name": "optional-custom-index-name",
            "dimension": 1024,
            "method": "hnsw",
            "engine": "faiss",
            "space_type": "l2"
        }
    }
    """
    
    print("=== VECTOR INDEX CREATION SCRIPT ===")
    print(f"Event: {json.dumps(event)}")
    
    # Handle CloudFormation custom resource events
    if 'RequestType' in event:
        return handle_cfn_event(event, context)
    
    # Handle direct Lambda invocation
    return handle_direct_event(event, context)

def handle_cfn_event(event, context):
    """Handle CloudFormation custom resource events"""
    try:
        if event['RequestType'] == 'Delete':
            return {
                'statusCode': 200,
                'body': json.dumps({'message': 'Delete request - no action needed'})
            }
        
        properties = event['ResourceProperties']
        index_name = properties.get('index_name')
        dimension = properties.get('dimension', 1024)
        method = properties.get('method', 'hnsw')
        engine = properties.get('engine', 'faiss')
        space_type = properties.get('space_type', 'l2')
        
        print(f"Creating index: {index_name}")
        print(f"Vector dimension: {dimension}")
        print(f"Method: {method}, Engine: {engine}, Space type: {space_type}")
        
        # Get environment variables
        opensearch_endpoint = os.environ.get("OPENSEARCH_ENDPOINT")
        collection_name = os.environ.get("COLLECTION_NAME")
        
        if not opensearch_endpoint:
            raise ValueError("OPENSEARCH_ENDPOINT environment variable is required")
        
        if not collection_name:
            raise ValueError("COLLECTION_NAME environment variable is required")
        
        # Create the index
        result = create_vector_index(opensearch_endpoint, collection_name, index_name, dimension, method, engine, space_type)
        
        if result['success']:
            print("Index created successfully")
            return {
                'statusCode': 200,
                'body': json.dumps({
                    'message': f'Successfully created vector index {index_name}',
                    'index_name': index_name,
                    'collection_name': collection_name,
                    'dimension': dimension,
                    'method': method,
                    'engine': engine,
                    'space_type': space_type
                })
            }
        else:
            print(f"Failed to create index: {result['error']}")
            raise Exception(result['error'])
            
    except Exception as e:
        print(f"Error: {str(e)}")
        raise e

def handle_direct_event(event, context):
    """Handle direct Lambda invocation events"""
    try:
        # Get environment variables
        opensearch_endpoint = os.environ.get("OPENSEARCH_ENDPOINT")
        collection_name = os.environ.get("COLLECTION_NAME")
        
        if not opensearch_endpoint:
            raise ValueError("OPENSEARCH_ENDPOINT environment variable is required")
        
        if not collection_name:
            raise ValueError("COLLECTION_NAME environment variable is required")
        
        print(f"OpenSearch Endpoint: {opensearch_endpoint}")
        print(f"Collection Name: {collection_name}")
        
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
        
        # Parse event parameters with defaults
        index_name = event.get('index_name', f'{collection_name}-vector-index')
        dimension = event.get('dimension', 1024)
        method = event.get('method', 'hnsw')
        engine = event.get('engine', 'faiss')
        space_type = event.get('space_type', 'l2')
        
        print(f"Creating index: {index_name}")
        print(f"Vector dimension: {dimension}")
        print(f"Method: {method}, Engine: {engine}, Space type: {space_type}")
        
        # Create the index
        result = create_vector_index(opensearch_endpoint, collection_name, index_name, dimension, method, engine, space_type)
        
        if result['success']:
            return {
                'statusCode': 200,
                'body': json.dumps({
                    'message': f'Successfully created vector index {index_name}',
                    'index_name': index_name,
                    'collection_name': collection_name,
                    'dimension': dimension,
                    'method': method,
                    'engine': engine,
                    'space_type': space_type
                })
            }
        else:
            raise Exception(result['error'])
            
    except Exception as e:
        print(f"Error: {str(e)}")
        raise e

def create_vector_index(opensearch_endpoint, collection_name, index_name, dimension, method, engine, space_type):
    """Create vector index in OpenSearch"""
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
        
        # Define the index mapping and settings for Bedrock Knowledge Base
        request_body = {
            "settings": {
                "index": {
                    "knn": True,
                    "knn.algo_param.ef_search": 512
                }
            },
            "mappings": {
                "dynamic": True,
                "properties": {
                    "vector": {
                        "type": "knn_vector",
                        "dimension": dimension,
                        "method": {
                            "name": method,
                            "space_type": space_type,
                            "engine": engine
                        }
                    },
                    "text": {
                        "type": "text"
                    },
                    "metadata": {
                        "type": "text"
                    }
                }
            }
        }
        
        print(f"Index body: {json.dumps(request_body, indent=2)}")
        
        # Check if index already exists and verify field names
        try:
            index_exists = client.indices.exists(index=index_name)
            if index_exists:
                print(f"Index '{index_name}' already exists. Checking field names...")
                
                # Get the existing index mapping
                try:
                    existing_mapping = client.indices.get_mapping(index=index_name)
                    properties = existing_mapping.get(index_name, {}).get('mappings', {}).get('properties', {})
                    
                    print(f"Existing index properties: {json.dumps(properties, indent=2)}")
                    
                    # Check if the index has the correct field names and types
                    has_correct_fields = (
                        'vector' in properties and 
                        'text' in properties and 
                        'metadata' in properties and
                        properties.get('vector', {}).get('type') == 'knn_vector' and
                        properties.get('metadata', {}).get('type') == 'text'
                    )
                    
                    if has_correct_fields:
                        print(f"Index '{index_name}' has correct field names and types. Skipping creation.")
                        return {'success': True}
                    else:
                        print(f"Index '{index_name}' has incorrect field names or types. Deleting and recreating...")
                        print(f"Expected metadata type: 'text', Found: {properties.get('metadata', {}).get('type', 'NOT_FOUND')}")
                        
                        # Delete the existing index
                        try:
                            # First, check if the index is in use by trying to close it
                            try:
                                client.indices.close(index=index_name)
                                print(f"Closed index '{index_name}' before deletion")
                            except Exception as close_error:
                                print(f"Index '{index_name}' was not open or already closed: {close_error}")
                            
                            # Now delete the index
                            client.indices.delete(index=index_name)
                            print(f"Successfully deleted existing index '{index_name}'")
                            # Wait a bit for deletion to complete
                            time.sleep(5)
                        except Exception as delete_error:
                            print(f"Error deleting index: {delete_error}")
                            # Try to force delete by closing the index first
                            try:
                                client.indices.close(index=index_name)
                                client.indices.delete(index=index_name)
                                print(f"Successfully force-deleted existing index '{index_name}'")
                                time.sleep(5)
                            except Exception as force_delete_error:
                                print(f"Error force-deleting index: {force_delete_error}")
                                # If we still can't delete, we might need to wait or try a different approach
                                print("Warning: Could not delete existing index. This might cause issues.")
                                raise force_delete_error
                        
                except Exception as mapping_error:
                    print(f"Error checking index mapping: {mapping_error}")
                    print("Deleting existing index and recreating...")
                    try:
                        # First, check if the index is in use by trying to close it
                        try:
                            client.indices.close(index=index_name)
                            print(f"Closed index '{index_name}' before deletion")
                        except Exception as close_error:
                            print(f"Index '{index_name}' was not open or already closed: {close_error}")
                        
                        # Now delete the index
                        client.indices.delete(index=index_name)
                        print(f"Deleted existing index '{index_name}'")
                        time.sleep(5)
                    except Exception as delete_error:
                        print(f"Error deleting index: {delete_error}")
                        # Try to force delete by closing the index first
                        try:
                            client.indices.close(index=index_name)
                            client.indices.delete(index=index_name)
                            print(f"Successfully force-deleted existing index '{index_name}'")
                            time.sleep(5)
                        except Exception as force_delete_error:
                            print(f"Error force-deleting index: {force_delete_error}")
                            # If we still can't delete, we might need to wait or try a different approach
                            print("Warning: Could not delete existing index. This might cause issues.")
                            raise force_delete_error
                        
        except Exception as e:
            print(f"Error checking if index exists: {e}")
        
        # Create the index
        print(f"Creating index '{index_name}'...")
        response = client.indices.create(
            index=index_name,
            body=request_body
        )
        
        print(f"Successfully created index: {response}")
        
        # Wait a bit for the index to be fully created
        time.sleep(10)
        
        # Verify the index was created
        try:
            index_info = client.indices.get(index=index_name)
            print(f"Index verification successful: {index_info}")
        except Exception as e:
            print(f"Warning: Could not verify index creation: {e}")
        
        return {'success': True}
        
    except RequestError as e:
        print(f"RequestError: {e}")
        return {'success': False, 'error': str(e)}
    except Exception as e:
        print(f"Error in create_vector_index: {str(e)}")
        return {'success': False, 'error': str(e)} 