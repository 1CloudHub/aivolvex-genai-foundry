import os
import boto3
import json
import base64
from opensearchpy import OpenSearch, RequestsHttpConnection
from requests_aws4auth import AWS4Auth
from botocore.config import Config

region = os.environ["AWS_REGION"]
service = "aoss"
HOST = os.environ["OPENSEARCH_ENDPOINT"].replace("https://", "").replace("http://", "")
index_name = os.environ["INDEX_NAME"]
bucket_name = os.environ["BUCKET_NAME"]
prefix = os.environ["S3_PREFIX"]
claude_model_id = os.environ.get("CLAUDE_MODEL_ID", "us.anthropic.claude-3-7-sonnet-20250219-v1:0")

# Configuration for retries
config = Config(
    retries={
        'max_attempts': 3,
        'mode': 'standard'
    }
)

session = boto3.Session()
credentials = session.get_credentials()
awsauth = AWS4Auth(
    credentials.access_key,
    credentials.secret_key,
    region,
    service,
    session_token=credentials.token
)

# OpenSearch client
client = OpenSearch(
    hosts=[{"host": HOST, "port": 443}],
    http_auth=awsauth,
    use_ssl=True,
    verify_certs=True,
    connection_class=RequestsHttpConnection,
    pool_maxsize=300,
    timeout=30,
    max_retries=3,
    retry_on_timeout=True
)

s3 = boto3.client("s3")
bedrock = boto3.client('bedrock-runtime', region_name=region, config=config)

def get_text_embedding_bedrock(text):
    """Create text embedding using Bedrock Titan"""
    try:
            
        body = {"inputText": text}
        response = bedrock.invoke_model(
            body=json.dumps(body),
            modelId="amazon.titan-embed-text-v1",
            accept="application/json",
            contentType="application/json",
        )
        result = json.loads(response['body'].read())
        embedding = result['embedding']
        
        # Ensure 1024 dimensions to match your collection
        if len(embedding) == 1024:
            return embedding
        elif len(embedding) > 1024:
            print(f"Truncating embedding from {len(embedding)} to 1024 dimensions")
            return embedding[:1024]
        else:
            print(f"Padding embedding from {len(embedding)} to 1024 dimensions")
            return embedding + [0.0] * (1024 - len(embedding))
            
    except Exception as e:
        print(f"Error creating text embedding: {e}")
        return None

def create_image_embedding(image_base64):
    """Create image embedding using Bedrock Titan"""
    try:
        image_input = {"inputImage": image_base64}
        response = bedrock.invoke_model(
            body=json.dumps(image_input),
            modelId="amazon.titan-embed-image-v1",
            accept="application/json",
            contentType="application/json"
        )
        result = json.loads(response.get("body").read())
        embedding = result.get("embedding")
        
        # Ensure 1024 dimensions
        if len(embedding) == 1024:
            return embedding
        elif len(embedding) > 1024:
            print(f"Truncating image embedding from {len(embedding)} to 1024 dimensions")
            return embedding[:1024]
        else:
            print(f"Padding image embedding from {len(embedding)} to 1024 dimensions")
            return embedding + [0.0] * (1024 - len(embedding))
            
    except Exception as e:
        print(f"Error creating image embedding: {e}")
        return None

def get_image_description(image_base64, image_key):
    """Generate product description using Claude"""
    system_prompt = '''
    You are an image analysis agent for a retail store.
    You will be given a product image and need to generate an accurate 3-line product description.

    Your task:
    Analyze the product image and generate an accurate 3-line product description.
    Your description must reflect only what is visibly seen in the image.

    Instructions:
    Carefully observe the product's visual features: shape, color, packaging/design, branding, labels, quantity/size, or structure.
    Output exactly 3 lines:
    1. What the product is and how it looks
    2. Key visible features or attributes
    3. Function, type, or where it's typically used (if clearly inferable from image)

    Rules:
    - Do not include any assumptions not visible in the image
    - No promotional language (e.g., "great", "amazing")
    - Do not exceed 3 lines
    - Maintain a neutral and factual tone
    - Focus on the product present in the image rather than the whole image
    '''
    
    try:
        response = bedrock.invoke_model(
            contentType='application/json',
            body=json.dumps({
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": 1000,
                "temperature": 0,
                "top_p": 0.999,
                "top_k": 250,
                "system": system_prompt,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": image_base64}}
                        ]
                    }
                ],
            }),
            modelId=claude_model_id
        )
        
        response_body = json.loads(response['body'].read().decode('utf-8'))
        description_output = response_body['content'][0]['text']
        
        print(f"Generated description for {image_key}: {description_output[:100]}...")
        return description_output
        
    except Exception as e:
        print(f"Error generating description for {image_key}: {e}")
        return f"Product image: {os.path.basename(image_key)}"

def check_and_create_index():
    """Check if index exists and create it if it doesn't"""
    try:
        if client.indices.exists(index=index_name):
            print(f"✅ Index '{index_name}' already exists")
            return True
        else:
            print(f"❌ Index '{index_name}' does not exist. Creating it...")
            
            # Create index with proper mapping - matching visual product search configuration
            index_body = {
                    "settings": {
                        "index": {
                            "knn": True,
                            "knn.algo_param.ef_search": 100
                        }
                    },
                    "mappings": {
                        "properties": {
                            "vspmod": {
                                "type": "knn_vector",
                                "dimension": 1024,
                                "method": {
                                    "name": "hnsw",
                                    "space_type": "cosinesimil",  # changed from "cosine"
                                    "engine": "nmslib"
                                }
                            },
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
            
            client.indices.create(index=index_name, body=index_body)
            print(f"✅ Index '{index_name}' created successfully")
            return True
            
    except Exception as e:
        print(f"Error checking/creating index: {e}")
        return False


def get_s3_files():
    """Get all files from S3 bucket with the specified prefix"""
    try:
        response = s3.list_objects_v2(
            Bucket=bucket_name,
            Prefix=prefix
        )
        contents = response.get('Contents', [])
        
        # Filter for image files and metadata files
        image_files = []
        metadata_files = []
        
        for obj in contents:
            key = obj['Key']
            if key.endswith(('.jpg', '.jpeg', '.png', '.webp')):
                image_files.append(key)
            elif key.endswith('-metadata.json'):
                metadata_files.append(key)
        
        print(f"Found {len(image_files)} image files and {len(metadata_files)} metadata files")
        return image_files, metadata_files
        
    except Exception as e:
        print(f"Error listing S3 files: {e}")
        return [], []

def process_metadata_files(metadata_files):
    """Process metadata files and create text embeddings"""
    print("\n=== Processing Metadata Files ===")
    indexed_count = 0
    
    for metadata_file in metadata_files:
        try:
            print(f"\nProcessing metadata: {metadata_file}")
            
            # Download metadata file
            response = s3.get_object(Bucket=bucket_name, Key=metadata_file)
            metadata_content = response['Body'].read().decode('utf-8')
            metadata = json.loads(metadata_content)
            
            # Create product description from metadata
            product_description = f"{metadata.get('title', '')} {metadata.get('description', '')} {' '.join(metadata.get('features', []))}"
            
            # Create text embedding
            text_embedding = get_text_embedding_bedrock(product_description)
            if text_embedding is None:
                print(f"Skipping {metadata_file} due to embedding creation failure")
                continue
            
            # Get corresponding image S3 URI
            base_name = os.path.basename(metadata_file).replace('-metadata.json', '')
            s3_uri = f"s3://{bucket_name}/{prefix}{base_name}.jpg"  # Assuming jpg extension
            
            # Prepare document for OpenSearch
            document = {
                "vspmod": text_embedding,  # Vector field
                "product_description": product_description,
                "s3_uri": s3_uri,
                "type": "text"
            }
            
            # Index document
            response = client.index(
                index=index_name,
                body=document
            )
            
            print(f"✅ Successfully indexed metadata for {metadata.get('title', base_name)} with ID: {response['_id']}")
            indexed_count += 1
            
        except Exception as e:
            print(f"❌ Error processing {metadata_file}: {e}")
            continue
    
    return indexed_count

def process_image_files(image_files):
    """Process image files and create image embeddings with descriptions"""
    print("\n=== Processing Image Files ===")
    indexed_count = 0
    
    for image_file in image_files:
        try:
            print(f"\nProcessing image: {image_file}")
            
            # Download image file
            image_data = s3.get_object(Bucket=bucket_name, Key=image_file)['Body'].read()
            base64_encoded_image = base64.b64encode(image_data).decode('utf-8')
            
            # Create image embedding
            image_embedding = create_image_embedding(base64_encoded_image)
            if image_embedding is None:
                print(f"Skipping {image_file} due to image embedding creation failure")
                continue
            
            # Generate product description using Claude
            product_description = get_image_description(base64_encoded_image, image_file)
            
            # Prepare document for OpenSearch
            document = {
                "vspmod": image_embedding,  # Vector field
                "product_description": product_description,
                "s3_uri": f"s3://{bucket_name}/{image_file}",
                "type": "image"
            }
            
            # Index document
            response = client.index(
                index=index_name,
                body=document
            )
            
            print(f"✅ Successfully indexed image {os.path.basename(image_file)} with ID: {response['_id']}")
            indexed_count += 1
            
        except Exception as e:
            print(f"❌ Error processing {image_file}: {e}")
            continue

    return indexed_count

def perform_data_ingestion():
    """Perform the complete data ingestion process"""
    try:
        print(f"Starting data ingestion for s3://{bucket_name}/{prefix}")
        print(f"OpenSearch Host: {HOST}")
        print(f"Index Name: {index_name}")

        # Check and create index if it doesn't exist
        print("\n=== Checking/Creating Index ===")
        if not check_and_create_index():
            return {
                "status": "error",
                "message": "Failed to create or access index"
            }

        # Get files from S3
        print("\n=== Getting files from S3 ===")
        image_files, metadata_files = get_s3_files()
        
        if not image_files and not metadata_files:
            return {
                "status": "error",
                "message": "No files found to process"
            }
        
        total_indexed = 0
        
        # Process metadata files
        if metadata_files:
            metadata_count = process_metadata_files(metadata_files)
            total_indexed += metadata_count
        
        # Process image files
        if image_files:
            image_count = process_image_files(image_files)
            total_indexed += image_count
        
        # Get final statistics
        print("\n=== Final Statistics ===")
        try:
            stats = client.indices.stats(index=index_name)
            doc_count = stats['indices'][index_name]['total']['docs']['count']
            print(f"📊 Total documents in index: {doc_count}")
        except Exception as e:
            print(f"Error getting statistics: {e}")
        
        return {
            "status": "success",
            "indexed_count": total_indexed,
            "message": f"Successfully indexed {total_indexed} products"
        }
        
    except Exception as e:
        print(f"Error during data ingestion: {e}")
        return {
            "status": "error",
            "message": f"Data ingestion failed: {str(e)}"
        }

def lambda_handler(event, context):
    """Main Lambda handler for CloudFormation custom resource events"""
    print(f"Received event: {json.dumps(event)}")
    
    # Handle CloudFormation custom resource events
    if 'RequestType' in event:
        print("CloudFormation custom resource event detected")
        print(f"RequestType: {event['RequestType']}")
        
        if event['RequestType'] == 'Delete':
            print("Delete request - no action needed for data ingestion")
            return {
                'statusCode': 200,
                'body': json.dumps({'message': 'Delete request - no action needed'})
            }
        
        # For Create/Update, perform data ingestion
        print("Create/Update request - performing data ingestion")
        result = perform_data_ingestion()
        
        # Return proper CloudFormation response format
        if result['status'] == 'success':
            return {
                'statusCode': 200,
                'body': json.dumps({
                    'message': 'Data ingestion completed successfully',
                    'indexed_count': result.get('indexed_count', 0),
                    'details': result
                })
            }
        else:
            return {
                'statusCode': 500,
                'body': json.dumps({
                    'message': 'Data ingestion failed',
                    'error': result.get('message', 'Unknown error'),
                    'details': result
                })
            }
    
    # Default: perform data ingestion (backward compatibility)
    print("No CloudFormation event detected, performing default data ingestion")
    result = perform_data_ingestion()
    return {
        'statusCode': 200 if result['status'] == 'success' else 500,
        'body': json.dumps(result)
    }