import boto3
import os
import json
import time
from requests_aws4auth import AWS4Auth
from opensearchpy import OpenSearch, RequestsHttpConnection
from opensearchpy.exceptions import RequestError


def lambda_handler(event, context):
    """
    Lambda function specifically for creating retail vector indices in OpenSearch Serverless.
    This function creates indices with the exact configuration needed for retail use cases.
    
    Expected environment variables:
    - OPENSEARCH_ENDPOINT: The OpenSearch collection endpoint
    - COLLECTION_NAME: The name of the OpenSearch collection
    - INDEX_NAME: The name of the retail index to create
    
    Expected event structure (CloudFormation Custom Resource):
    {
        "RequestType": "Create|Update|Delete",
        "ResourceProperties": {
            "index_name": "retail-index-name",
            "dimension": 1024,
            "method": "nmslib",
            "engine": "nmslib",
            "space_type": "cosine"
        }
    }
    """
    
    print("=== RETAIL VECTOR INDEX CREATION SCRIPT ===")
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
        method = properties.get('method', 'nmslib')
        engine = properties.get('engine', 'nmslib')
        space_type = properties.get('space_type', 'cosine')
        
        print(f"Creating retail index: {index_name}")
        print(f"Vector dimension: {dimension}")
        print(f"Method: {method}, Engine: {engine}, Space type: {space_type}")
        
        # Get environment variables
        opensearch_endpoint = os.environ.get("OPENSEARCH_ENDPOINT")
        collection_name = os.environ.get("COLLECTION_NAME")
        
        if not opensearch_endpoint:
            raise ValueError("OPENSEARCH_ENDPOINT environment variable is required")
        
        if not collection_name:
            raise ValueError("COLLECTION_NAME environment variable is required")
        
        # Create the retail index with specific configuration
        result = create_retail_vector_index(
            opensearch_endpoint, 
            collection_name, 
            index_name, 
            dimension, 
            method, 
            engine, 
            space_type
        )
        
        if result['success']:
            print("Retail index created successfully")
            return {
                'statusCode': 200,
                'body': json.dumps({
                    'message': f'Successfully created retail vector index {index_name}',
                    'index_name': index_name,
                    'collection_name': collection_name,
                    'dimension': dimension,
                    'method': method,
                    'engine': engine,
                    'space_type': space_type,
                    'vector_field': 'vspmod',
                    'metadata_fields': ['product_description', 's3_uri', 'type']
                })
            }
        else:
            print(f"Failed to create retail index: {result['error']}")
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
        region = os.environ.get('AWS_REGION', 'us-west-2')
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
        index_name = event.get('index_name', f'{collection_name}-retail-index')
        dimension = event.get('dimension', 1024)
        method = event.get('method', 'nmslib')
        engine = event.get('engine', 'nmslib')
        space_type = event.get('space_type', 'cosine')
        
        print(f"Creating retail index: {index_name}")
        print(f"Vector dimension: {dimension}")
        print(f"Method: {method}, Engine: {engine}, Space type: {space_type}")
        
        # Create the retail index with specific configuration
        result = create_retail_vector_index(
            opensearch_endpoint, 
            collection_name, 
            index_name, 
            dimension, 
            method, 
            engine, 
            space_type
        )
        
        if result['success']:
            return {
                'statusCode': 200,
                'body': json.dumps({
                    'message': f'Successfully created retail vector index {index_name}',
                    'index_name': index_name,
                    'collection_name': collection_name,
                    'dimension': dimension,
                    'method': method,
                    'engine': engine,
                    'space_type': space_type,
                    'vector_field': 'vspmod',
                    'metadata_fields': ['product_description', 's3_uri', 'type']
                })
            }
        else:
            raise Exception(result['error'])
            
    except Exception as e:
        print(f"Error: {str(e)}")
        raise e


def create_retail_vector_index(opensearch_endpoint, collection_name, index_name, dimension, method, engine, space_type):
    """
    Create retail-specific vector index in OpenSearch with exact configuration.
    
    Vector field configuration:
    - Field name: 'vspmod'
    - Engine: 'nmslib'
    - Precision: 'FP32'
    - Dimensions: 1024
    - Distance type: 'cosine'
    - ef_search: 100
    
    Metadata fields:
    - product_description: text, filterable
    - s3_uri: keyword, filterable
    - type: keyword, filterable
    """
    try:
        # Get credentials from the Lambda execution environment
        session = boto3.Session()
        credentials = session.get_credentials()
        
        if not credentials:
            raise Exception("Failed to get AWS credentials")
        
        print(f"Running as IAM Role: {boto3.client('sts').get_caller_identity()['Arn']}")
        
        # Set up AWS authentication for OpenSearch
        region = os.environ.get('AWS_REGION', 'us-west-2')
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
        
        # Define the retail-specific index mapping to match metadata_ingest_final structure
        request_body = {
            "settings": {
                "index": {
                    "knn": True,
                    "knn.algo_param.ef_search": 100
                }
            },
            "mappings": {
                "properties": {
                    # Vector field configuration - using "vspmod" as per metadata_ingest_final
                    "vspmod": {
                        "type": "knn_vector",
                        "dimension": dimension,
                        "method": {
                            "name": method,
                            "space_type": space_type,
                            "engine": engine
                        }
                    },
                    # Metadata fields - updated configuration
                    "product_description": {
                        "type": "text"
                    },
                    "s3_uri": {
                        "type": "keyword"
                    },
                    "type": {
                        "type": "keyword"
                    }
                }
            }
        }
        
        print(f"Retail index configuration: {json.dumps(request_body, indent=2)}")
        
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
                    
                    # Check if the index has the correct retail field names and types
                    has_correct_retail_fields = (
                        'vspmod' in properties and 
                        properties.get('vspmod', {}).get('type') == 'knn_vector' and
                        'product_description' in properties and
                        's3_uri' in properties and
                        'type' in properties
                    )
                    
                    if has_correct_retail_fields:
                        print(f"Index '{index_name}' has correct retail field names and types. Skipping creation.")
                        return {'success': True}
                    else:
                        print(f"Index '{index_name}' has incorrect retail field names or types. Deleting and recreating...")
                        
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
                            print("Warning: Could not delete existing index. This might cause issues.")
                            raise force_delete_error
                        
        except Exception as e:
            print(f"Error checking if index exists: {e}")
        
        # Create the retail index
        print(f"Creating retail index '{index_name}' with vector field 'vspmod'...")
        response = client.indices.create(
            index=index_name,
            body=request_body
        )
        
        print(f"Successfully created retail index: {response}")
        
        # Wait a bit for the index to be fully created
        time.sleep(10)
        
        # Verify the index was created with correct configuration
        try:
            index_info = client.indices.get(index=index_name)
            print(f"Retail index verification successful: {index_info}")
            
            # Verify the vector field configuration
            mappings = index_info.get(index_name, {}).get('mappings', {})
            properties = mappings.get('properties', {})
            
            if 'vspmod' in properties and properties['vspmod']['type'] == 'knn_vector':
                print("✅ Vector field 'vspmod' created successfully with knn_vector type")
            else:
                print("❌ Vector field 'vspmod' not found or incorrect type")
                
            if 'product_description' in properties and 's3_uri' in properties and 'type' in properties:
                print("✅ All required metadata fields created successfully")
            else:
                print("❌ Some required metadata fields are missing")
                
        except Exception as e:
            print(f"Warning: Could not verify retail index creation: {e}")
        
        return {'success': True}
        
    except RequestError as e:
        print(f"RequestError: {e}")
        return {'success': False, 'error': str(e)}
    except Exception as e:
        print(f"Error in create_retail_vector_index: {str(e)}")
        return {'success': False, 'error': str(e)}
