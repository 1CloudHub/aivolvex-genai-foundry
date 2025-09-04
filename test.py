import boto3
import json
import os
import base64
from opensearchpy import OpenSearch, RequestsHttpConnection
from requests_aws4auth import AWS4Auth
from botocore.config import Config

# Configuration
config = Config(
    retries={
        'max_attempts': 3,
        'mode': 'standard'
    }
)

# AWS Configuration
region = "us-west-2"  # Match your collection region
BUCKET_NAME = "genaifoundrya2g7kak5"
S3_PREFIX = "visualproductsearch/"

# AWS Credentials - Replace with your actual credentials


# OpenSearch Configuration
HOST = "y19el0ve4eu7dwn4d50d.us-west-2.aoss.amazonaws.com"
INDEX_NAME = "visual-product-search-index-test"

# Initialize AWS clients with credentials
s3 = boto3.client('s3',
                  region_name=region)

bedrock_client = boto3.client("bedrock-runtime",
                              region_name=region,
                              config=config,
                             )

claude_model_id = "us.anthropic.claude-3-7-sonnet-20250219-v1:0"


def create_opensearch_client():
    """Create and return OpenSearch client with AWS authentication"""
    import boto3
    from botocore.credentials import get_credentials
    
    # Get AWS credentials
    session = boto3.Session()
    credentials = session.get_credentials()
    
    auth = AWS4Auth(
        credentials.access_key,
        credentials.secret_key,
        region,
        'aoss',
        session_token=credentials.token
    )

    client = OpenSearch(
        hosts=[{'host': HOST, 'port': 443}],
        http_auth=auth,
        use_ssl=True,
        verify_certs=True,
        connection_class=RequestsHttpConnection,
        pool_maxsize=300,
        timeout=30,  # Increase timeout to 30 seconds
        max_retries=3,
        retry_on_timeout=True
    )
    return client


def get_text_embedding_bedrock(text):
    """Create text embedding using Bedrock Titan"""
    try:
        body = {"inputText": text}
        response = bedrock_client.invoke_model(
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
        response = bedrock_client.invoke_model(
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
        response = bedrock_client.invoke_model(
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


def get_s3_files():
    """Get all files from S3 bucket with the specified prefix"""
    try:
        response = s3.list_objects_v2(
            Bucket=BUCKET_NAME,
            Prefix=S3_PREFIX
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


def process_metadata_files(metadata_files, opensearch_client):
    """Process metadata files and create text embeddings"""
    print("\n=== Processing Metadata Files ===")
    
    for metadata_file in metadata_files:
        try:
            print(f"\nProcessing metadata: {metadata_file}")
            
            # Download metadata file
            response = s3.get_object(Bucket=BUCKET_NAME, Key=metadata_file)
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
            s3_uri = f"s3://{BUCKET_NAME}/{S3_PREFIX}{base_name}.jpg"  # Assuming jpg extension
            
            # Prepare document for OpenSearch
            document = {
                "vsp": text_embedding,  # Vector field
                "product_description": product_description,
                "s3_uri": s3_uri,
                "type": "text"
            }
            
            # Index document
            response = opensearch_client.index(
                index=INDEX_NAME,
                body=document
            )
            
            print(f"✅ Successfully indexed metadata for {metadata.get('title', base_name)} with ID: {response['_id']}")
            
        except Exception as e:
            print(f"❌ Error processing {metadata_file}: {e}")
            continue


def process_image_files(image_files, opensearch_client):
    """Process image files and create image embeddings with descriptions"""
    print("\n=== Processing Image Files ===")
    
    for image_file in image_files:
        try:
            print(f"\nProcessing image: {image_file}")
            
            # Download image file
            image_data = s3.get_object(Bucket=BUCKET_NAME, Key=image_file)['Body'].read()
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
                "vsp": image_embedding,  # Vector field
                "product_description": product_description,
                "s3_uri": f"s3://{BUCKET_NAME}/{image_file}",
                "type": "image"
            }
            
            # Index document
            response = opensearch_client.index(
                index=INDEX_NAME,
                body=document
            )
            
            print(f"✅ Successfully indexed image {os.path.basename(image_file)} with ID: {response['_id']}")
            
        except Exception as e:
            print(f"❌ Error processing {image_file}: {e}")
            continue


def search_products(query, search_type="text", limit=5):
    """Search products using vector similarity"""
    try:
        client = create_opensearch_client()
        
        print(f"\nSearching for: '{query}' (type: {search_type})")
        
        # Create query embedding based on search type
        if search_type == "text":
            query_embedding = get_text_embedding_bedrock(query)
        else:
            # For image search, query should be base64 encoded image
            query_embedding = create_image_embedding(query)
        
        if query_embedding is None:
            print("Error creating query embedding")
            return []
        
        # Build search query
        search_body = {
            "size": limit,
            "query": {
                "bool": {
                    "must": {
                        "knn": {
                            "vsp": {
                                "vector": query_embedding,
                                "k": limit
                            }
                        }
                    },
                    "filter": {
                        "term": {
                            "type": search_type
                        }
                    }
                }
            },
            "_source": ["product_description", "s3_uri", "type"]
        }
        
        response = client.search(index=INDEX_NAME, body=search_body)
        
        results = []
        for hit in response['hits']['hits']:
            results.append({
                'id': hit['_id'],
                'score': hit['_score'],
                'product_description': hit['_source']['product_description'],
                's3_uri': hit['_source']['s3_uri'],
                'type': hit['_source']['type']
            })
        
        return results
        
    except Exception as e:
        print(f"Error searching products: {e}")
        return []


def test_connection():
    """Test OpenSearch connection"""
    try:
        client = create_opensearch_client()
        
        # Test if index exists (skip basic connection test since it returns 404)
        print(f"Checking if index '{INDEX_NAME}' exists...")
        if client.indices.exists(index=INDEX_NAME):
            print(f"✅ Index '{INDEX_NAME}' exists")
            
            # Get index info
            index_info = client.indices.get(index=INDEX_NAME)
            print(f"✅ Index info retrieved successfully")
            
            # Try to get index stats (optional - might fail for empty indices)
            try:
                stats = client.indices.stats(index=INDEX_NAME)
                doc_count = stats['indices'][INDEX_NAME]['total']['docs']['count']
                print(f"📊 Current document count: {doc_count}")
            except Exception as stats_error:
                print(f"Note: Could not get index stats (this is normal for empty indices): {stats_error}")
            
        else:
            print(f"❌ Index '{INDEX_NAME}' does not exist")
            print("Creating index...")
            try:
                # Create index with proper mapping
                index_body = {
                    "mappings": {
                        "properties": {
                            "vsp": {
                                "type": "knn_vector",
                                "dimension": 1024,
                                "method": {
                                    "name": "hnsw",
                                    "space_type": "l2",
                                    "engine": "faiss"
                                }
                            },
                            "product_description": {
                                "type": "text"
                            },
                            "s3_uri": {
                                "type": "text"
                            },
                            "type": {
                                "type": "keyword"
                            }
                        }
                    }
                }
                
                client.indices.create(index=INDEX_NAME, body=index_body)
                print(f"✅ Index '{INDEX_NAME}' created successfully")
            except Exception as create_error:
                print(f"❌ Failed to create index: {create_error}")
                return False
        
        return True
        
    except Exception as e:
        print(f"❌ Connection failed: {e}")
        print(f"Error type: {type(e).__name__}")
        import traceback
        print("Full traceback:")
        traceback.print_exc()
        
        # Additional debugging info
        print("\n🔍 Debugging Information:")
        print(f"Host: {HOST}")
        print(f"Region: {region}")
        print(f"Index: {INDEX_NAME}")
        
        # Test if it's an authentication issue
        if "403" in str(e) or "Forbidden" in str(e):
            print("❗ This appears to be an authentication/authorization issue")
            print("Please check:")
            print("- AWS credentials are correct")
            print("- Data access policy allows your user/role")
            print("- Collection access policy is properly configured")
        elif "404" in str(e):
            print("❗ This appears to be a 'not found' issue")
            print("Please check:")
            print("- Collection endpoint URL is correct")
            print("- Collection exists and is active")
            print("- Region matches the collection region")
        
        return False


def check_aws_credentials():
    """Check AWS credentials and permissions"""
    print("\n🔍 Checking AWS Credentials and Permissions:")
    
    try:
        # Test basic AWS access
        session = boto3.Session()
        credentials = session.get_credentials()
        
        if not credentials:
            print("❌ No AWS credentials found")
            print("Please configure AWS credentials using:")
            print("- AWS CLI: aws configure")
            print("- Environment variables: AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY")
            print("- IAM role (if running on EC2)")
            return False
        
        print(f"✅ AWS credentials found")
        print(f"   Access Key: {credentials.access_key[:8]}...")
        
        # Test STS to get current identity
        sts = boto3.client('sts', region_name=region)
        identity = sts.get_caller_identity()
        print(f"✅ AWS Identity verified:")
        print(f"   Account: {identity.get('Account')}")
        print(f"   User/Role: {identity.get('Arn')}")
        
        # Test OpenSearch Serverless permissions
        try:
            opensearch_client = boto3.client('opensearchserverless', region_name=region)
            collections = opensearch_client.list_collections()
            print(f"✅ OpenSearch Serverless access confirmed")
            print(f"   Found {len(collections.get('collectionSummaries', []))} collections")
            
            # Check if our collection is in the list
            collection_id = HOST.split('.')[0]
            for collection in collections.get('collectionSummaries', []):
                if collection['id'] == collection_id:
                    print(f"✅ Found target collection: {collection['name']} (Status: {collection['status']})")
                    
                    # Check data access policies
                    try:
                        policies = opensearch_client.list_access_policies(
                            type='data',
                            resource=[f"collection/{collection_id}"]
                        )
                        print(f"📋 Data Access Policies for collection:")
                        for policy in policies.get('accessPolicySummaries', []):
                            print(f"   - {policy['name']}: {policy['type']}")
                        
                        # Check if current user has access
                        current_arn = identity.get('Arn')
                        has_access = False
                        for policy in policies.get('accessPolicySummaries', []):
                            try:
                                policy_detail = opensearch_client.get_access_policy(
                                    name=policy['name'],
                                    type='data'
                                )
                                policy_doc = policy_detail['accessPolicyDetail']['policy']
                                if current_arn in policy_doc:
                                    has_access = True
                                    print(f"✅ Found access policy: {policy['name']}")
                                    break
                            except:
                                continue
                        
                        if not has_access:
                            print(f"❌ No data access policy found for user: {current_arn}")
                            print_data_access_policy_instructions(current_arn, collection_id)
                        
                    except Exception as policy_error:
                        print(f"⚠️  Could not check data access policies: {policy_error}")
                        print_data_access_policy_instructions(identity.get('Arn'), collection_id)
                    
                    return True
            
            print(f"⚠️  Target collection {collection_id} not found in accessible collections")
            return False
            
        except Exception as e:
            print(f"❌ OpenSearch Serverless access failed: {e}")
            print("This suggests insufficient permissions for OpenSearch Serverless")
            return False
            
    except Exception as e:
        print(f"❌ AWS credentials check failed: {e}")
        return False


def print_data_access_policy_instructions(user_arn, collection_id):
    """Print instructions for creating data access policy"""
    print(f"\n🔧 To fix the authorization issue, you need to create a Data Access Policy:")
    print(f"1. Go to AWS OpenSearch Serverless Console")
    print(f"2. Navigate to 'Data access policies'")
    print(f"3. Create a new policy with the following JSON:")
    print(f"""
{{
    "Rules": [
        {{
            "ResourceType": "collection",
            "Resource": ["collection/{collection_id}"],
            "Permission": [
                "aoss:CreateCollectionItems",
                "aoss:DeleteCollectionItems", 
                "aoss:UpdateCollectionItems",
                "aoss:DescribeCollectionItems"
            ]
        }},
        {{
            "ResourceType": "index",
            "Resource": ["index/{collection_id}/*"],
            "Permission": [
                "aoss:CreateIndex",
                "aoss:DeleteIndex",
                "aoss:UpdateIndex", 
                "aoss:DescribeIndex",
                "aoss:ReadDocument",
                "aoss:WriteDocument"
            ]
        }}
    ],
    "Principal": ["{user_arn}"]
}}
    """)
    print(f"4. Save the policy and try running the script again")


def verify_collection_details():
    """Verify collection endpoint and details"""
    print("🔍 Verifying Collection Details:")
    print(f"Collection Endpoint: https://{HOST}")
    print(f"Region: {region}")
    print(f"Index Name: {INDEX_NAME}")
    
    # Extract collection ID from HOST
    collection_id = HOST.split('.')[0]
    print(f"Extracted Collection ID from endpoint: {collection_id}")
    
    if collection_id != "y19el0ve4eu7dwn4d50d":
        print("⚠️  Collection ID mismatch detected!")
        print("Please verify your collection endpoint URL")


def list_collections():
    """List available collections (if possible)"""
    try:
        # This requires different permissions and may not work with AOSS
        import boto3
        opensearch_client = boto3.client('opensearchserverless', 
                                       region_name=region)
        
        response = opensearch_client.list_collections()
        print("\n📋 Available Collections:")
        for collection in response.get('collectionSummaries', []):
            print(f"  - Name: {collection['name']}")
            print(f"    ID: {collection['id']}")
            print(f"    Status: {collection['status']}")
            print(f"    Endpoint: https://{collection['id']}.{region}.aoss.amazonaws.com")
            print()
            
    except Exception as e:
        print(f"❌ Could not list collections: {e}")
        print("This might be due to insufficient permissions or the service client not being available")


def main():
    """Main function to run the ingestion process"""
    print("🚀 Starting metadata ingestion for existing OpenSearch collection...")
    
    # Step 0: Check AWS credentials and permissions
    print("\n=== Step 0: Checking AWS Credentials and Permissions ===")
    if not check_aws_credentials():
        print("❌ AWS credentials or permissions issue. Please fix before proceeding.")
        return
    
    # Step 1: Test connection
    print("\n=== Step 1: Testing OpenSearch Connection ===")
    if not test_connection():
        print("❌ Connection failed. Please check your configuration.")
        return
    
    # Get files from S3
    print("\n=== Step 2: Getting files from S3 ===")
    image_files, metadata_files = get_s3_files()
    
    if not image_files and not metadata_files:
        print("❌ No files found to process")
        return
    
    # Create OpenSearch client
    opensearch_client = create_opensearch_client()
    
    # Process metadata files
    if metadata_files:
        process_metadata_files(metadata_files, opensearch_client)
    
    # Process image files
    if image_files:
        process_image_files(image_files, opensearch_client)
    
    # Get final statistics
    print("\n=== Final Statistics ===")
    try:
        stats = opensearch_client.indices.stats(index=INDEX_NAME)
        doc_count = stats['indices'][INDEX_NAME]['total']['docs']['count']
        print(f"📊 Total documents in index: {doc_count}")
    except Exception as e:
        print(f"Error getting statistics: {e}")
    
    # Test search functionality
    print("\n=== Step 3: Testing Search Functionality ===")
    results = search_products("headphones", search_type="text", limit=3)
    print(f"\n🔍 Search results for 'headphones':")
    for i, result in enumerate(results, 1):
        print(f"{i}. Score: {result['score']:.3f}")
        print(f"   Description: {result['product_description'][:100]}...")
        print(f"   S3 URI: {result['s3_uri']}")
        print(f"   Type: {result['type']}")
        print()
    
    print("\n✅ Ingestion completed!")


if __name__ == "__main__":
    main()