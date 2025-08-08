from aws_cdk import (
    Stack,
    aws_iam as iam,
    aws_bedrock as bedrock,
    aws_opensearchserverless as opensearch,
    aws_lambda as lambda_,
    CustomResource,
    Duration,
    custom_resources as cr,
    CfnOutput,
    aws_s3 as s3,
    aws_s3_notifications as s3n,
)
from constructs import Construct
import json
import random
import string
import os
from pathlib import Path
import boto3

def generate_random_alphanumeric(length=6):
    """
    Generates a random name that follows AWS naming requirements.
    - Must be between 3 and 32 characters for most AWS resources.
    - Only contains lowercase letters, numbers, and hyphens.
    - Starts with a lowercase letter.
    - Ends with a lowercase letter or a number.
    """
    if not 3 <= length <= 32:
        raise ValueError("Length must be between 3 and 32 characters.")

    # Characters for the main body of the name (excluding hyphens at start/end)
    body_chars = string.ascii_lowercase + string.digits

    # Characters allowed at the end of the name
    end_chars = string.ascii_lowercase + string.digits

    # Generate the first character (must be lowercase letter)
    first_char = random.choice(string.ascii_lowercase)
    
    # Generate the middle characters (can include hyphens but not at start/end)
    if length > 2:
        middle_chars = ''.join(random.choices(body_chars + '-', k=length - 2))
        # Ensure no consecutive hyphens and no hyphen at the end
        middle_chars = middle_chars.replace('--', '-')
        if middle_chars.endswith('-'):
            middle_chars = middle_chars[:-1] + random.choice(string.ascii_lowercase + string.digits)
    else:
        middle_chars = ''

    # Generate a valid final character
    last_char = random.choice(end_chars)

    return first_char + middle_chars + last_char


class OpenSearchCollectionStack(Stack):

    def __init__(self, scope: Construct, construct_id: str, **kwargs):
        super().__init__(scope, construct_id, **kwargs)

        # Generate unique name for resources
        name_key = generate_random_alphanumeric(8)  # 8 characters for uniqueness

        # Reusable variables
        s3_bucket_name = "genaifoundy-usecases"
        model_arn = "arn:aws:bedrock:us-east-1::foundation-model/amazon.titan-embed-text-v2:0"

        # Reference existing S3 bucket
        self.data_bucket = s3.Bucket.from_bucket_name(
            self, 
            "ExistingDataBucket",
            bucket_name=s3_bucket_name
        )

        # Create separate OpenSearch Serverless Collections for each KB
        banking_collection_name = f"kb-banking-{name_key}-collection"
        insurance_collection_name = f"kb-insurance-{name_key}-collection"
        
        # Create Banking Collection
        banking_collection = opensearch.CfnCollection(
            self, "BankingKBCollection",
            name=banking_collection_name,
            type="VECTORSEARCH"
        )

        # Create Insurance Collection
        insurance_collection = opensearch.CfnCollection(
            self, "InsuranceKBCollection",
            name=insurance_collection_name,
            type="VECTORSEARCH"
        )

        # Create security policies for banking collection
        banking_encryption_policy = opensearch.CfnSecurityPolicy(
            self, "BankingKBSecurityPolicy",
            name=f"kb-banking-{name_key}-encrypt",
            type="encryption",
            policy=json.dumps({
                "Rules": [{
                    "ResourceType": "collection",
                    "Resource": [f"collection/{banking_collection_name}"]
                }],
                "AWSOwnedKey": True
            })
        )

        banking_network_policy = opensearch.CfnSecurityPolicy(
            self, "BankingKBNetworkPolicy",
            name=f"kb-banking-{name_key}-network",
            type="network",
            policy=json.dumps([{
                "Rules": [{
                    "ResourceType": "collection",
                    "Resource": [f"collection/{banking_collection_name}"]
                }],
                "AllowFromPublic": True
            }])
        )

        # Create security policies for insurance collection
        insurance_encryption_policy = opensearch.CfnSecurityPolicy(
            self, "InsuranceKBSecurityPolicy",
            name=f"kb-insurance-{name_key}-encrypt",
            type="encryption",
            policy=json.dumps({
                "Rules": [{
                    "ResourceType": "collection",
                    "Resource": [f"collection/{insurance_collection_name}"]
                }],
                "AWSOwnedKey": True
            })
        )

        insurance_network_policy = opensearch.CfnSecurityPolicy(
            self, "InsuranceKBNetworkPolicy",
            name=f"kb-insurance-{name_key}-network",
            type="network",
            policy=json.dumps([{
                "Rules": [{
                    "ResourceType": "collection",
                    "Resource": [f"collection/{insurance_collection_name}"]
                }],
                "AllowFromPublic": True
            }])
        )

        # Add dependencies for encryption and network policies
        banking_collection.add_dependency(banking_encryption_policy)
        banking_collection.add_dependency(banking_network_policy)
        insurance_collection.add_dependency(insurance_encryption_policy)
        insurance_collection.add_dependency(insurance_network_policy)

        # Create IAM role for Bedrock Knowledge Base
        bedrock_kb_role = iam.Role(
            self, "BedrockKBRole",
            assumed_by=iam.ServicePrincipal("bedrock.amazonaws.com"),
            description="Role for Bedrock Knowledge Base to access S3 and OpenSearch"
        )

        # Attach only required policies
        bedrock_kb_role.add_to_policy(iam.PolicyStatement(
            actions=[
                "s3:GetObject",
                "s3:ListBucket"
            ],
            resources=[
                f"arn:aws:s3:::{s3_bucket_name}",
                f"arn:aws:s3:::{s3_bucket_name}/*"
            ]
        ))

        bedrock_kb_role.add_to_policy(iam.PolicyStatement(
            actions=[
                "bedrock:InvokeModel",
                "bedrock:Retrieve",
                "bedrock:RetrieveAndGenerate"
            ],
            resources=["*"]
        ))

        bedrock_kb_role.add_to_policy(iam.PolicyStatement(
            actions=[
                "aoss:APIAccessAll",
                "aoss:DescribeCollectionItems",
                "aoss:DescribeVectorIndex"
            ],
            resources=["*"]
        ))

        # Add SageMaker permissions for Hub access
        bedrock_kb_role.add_to_policy(iam.PolicyStatement(
            actions=[
                "sagemaker:ListHubContents",
                "sagemaker:DescribeHub",
                "sagemaker:ListHubs",
                "sagemaker:SearchHubContent",
                "sagemaker:GetHubContent",
                "sagemaker:DescribeHubContent"
            ],
            resources=[
                "arn:aws:sagemaker:*:aws:hub/SageMakerPublicHub",
                "arn:aws:sagemaker:*:aws:hub/SageMakerPublicHub/*",
                "arn:aws:sagemaker:*:*:hub/*"
            ]
        ))

        # Create IAM role for Lambda function to create indices
        lambda_role = iam.Role(
            self, "IndexCreatorLambdaRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSLambdaBasicExecutionRole")
            ],
            inline_policies={
                "OpenSearchServerlessAccess": iam.PolicyDocument(
                    statements=[
                        iam.PolicyStatement(
                            effect=iam.Effect.ALLOW,
                            actions=[
                                "aoss:APIAccessAll",
                                "aoss:CreateIndex",
                                "aoss:DeleteIndex",
                                "aoss:UpdateIndex",
                                "aoss:DescribeIndex",
                                "aoss:ReadDocument",
                                "aoss:WriteDocument",
                                "aoss:CreateCollectionItems",
                                "aoss:DeleteCollectionItems",
                                "aoss:UpdateCollectionItems",
                                "aoss:DescribeCollectionItems",
                                "aoss:DescribeCollection",
                                "aoss:ListCollections"
                            ],
                            resources=[
                                f"arn:aws:aoss:us-east-1:{self.account}:collection/*",
                                f"arn:aws:aoss:us-east-1:{self.account}:index/*",
                                f"arn:aws:aoss:us-east-1:{self.account}:collection/{banking_collection_name}",
                                f"arn:aws:aoss:us-east-1:{self.account}:index/{banking_collection_name}/*",
                                f"arn:aws:aoss:us-east-1:{self.account}:collection/{insurance_collection_name}",
                                f"arn:aws:aoss:us-east-1:{self.account}:index/{insurance_collection_name}/*"
                            ]
                        )
                    ]
                ),
                "LambdaUpdateAccess": iam.PolicyDocument(
                    statements=[
                        iam.PolicyStatement(
                            effect=iam.Effect.ALLOW,
                            actions=[
                                "lambda:UpdateFunctionConfiguration",
                                "lambda:GetFunction"
                            ],
                            resources=[
                                f"arn:aws:lambda:us-east-1:{self.account}:function:*"
                            ]
                        )
                    ]
                )
            }
        )

        # Create IAM role for Auto-Sync Lambda function
        auto_sync_lambda_role = iam.Role(
            self, "AutoSyncLambdaRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSLambdaBasicExecutionRole")
            ],
            inline_policies={
                "BedrockKnowledgeBaseAccess": iam.PolicyDocument(
                    statements=[
                        iam.PolicyStatement(
                            effect=iam.Effect.ALLOW,
                            actions=[
                                "bedrock:StartIngestionJob",
                                "bedrock:GetIngestionJob",
                                "bedrock:ListIngestionJobs",
                                "bedrock:GetKnowledgeBase",
                                "bedrock:GetDataSource",
                                "bedrock:ListDataSources"
                            ],
                            resources=[
                                f"arn:aws:bedrock:us-east-1:{self.account}:knowledge-base/*",
                                f"arn:aws:bedrock:us-east-1:{self.account}:knowledge-base/*/data-source/*"
                            ]
                        )
                    ]
                ),
                "S3Access": iam.PolicyDocument(
                    statements=[
                        iam.PolicyStatement(
                            effect=iam.Effect.ALLOW,
                            actions=[
                                "s3:GetObject",
                                "s3:GetObjectVersion"
                            ],
                            resources=[
                                f"{self.data_bucket.bucket_arn}/*"
                            ]
                        )
                    ]
                )
            }
        )

        # Create data access policy AFTER roles are created
        banking_data_access_policy = opensearch.CfnAccessPolicy(
            self, "BankingKBDataAccessPolicy",
            name=f"kb-banking-{name_key}-access",
            type="data",
            policy=json.dumps([{
                "Rules": [{
                    "ResourceType": "collection",
                    "Resource": [f"collection/{banking_collection_name}"],
                    "Permission": [
                        "aoss:CreateCollectionItems",
                        "aoss:DeleteCollectionItems",
                        "aoss:UpdateCollectionItems",
                        "aoss:DescribeCollectionItems"
                    ]
                }, {
                    "ResourceType": "index",
                    "Resource": [f"index/{banking_collection_name}/*"],
                    "Permission": [
                        "aoss:CreateIndex",
                        "aoss:DeleteIndex",
                        "aoss:UpdateIndex",
                        "aoss:DescribeIndex",
                        "aoss:ReadDocument",
                        "aoss:WriteDocument"
                    ]
                }],
                "Principal": [
                    f"arn:aws:iam::{self.account}:role/{bedrock_kb_role.role_name}",
                    f"arn:aws:iam::{self.account}:role/{lambda_role.role_name}",
                    f"arn:aws:iam::{self.account}:role/{auto_sync_lambda_role.role_name}"
                ],
                "Description": f"Data access policy for {banking_collection_name}"
            }])
        )

        insurance_data_access_policy = opensearch.CfnAccessPolicy(
            self, "InsuranceKBDataAccessPolicy",
            name=f"kb-insurance-{name_key}-access",
            type="data",
            policy=json.dumps([{
                "Rules": [{
                    "ResourceType": "collection",
                    "Resource": [f"collection/{insurance_collection_name}"],
                    "Permission": [
                        "aoss:CreateCollectionItems",
                        "aoss:DeleteCollectionItems",
                        "aoss:UpdateCollectionItems",
                        "aoss:DescribeCollectionItems"
                    ]
                }, {
                    "ResourceType": "index",
                    "Resource": [f"index/{insurance_collection_name}/*"],
                    "Permission": [
                        "aoss:CreateIndex",
                        "aoss:DeleteIndex",
                        "aoss:UpdateIndex",
                        "aoss:DescribeIndex",
                        "aoss:ReadDocument",
                        "aoss:WriteDocument"
                    ]
                }],
                "Principal": [
                    f"arn:aws:iam::{self.account}:role/{bedrock_kb_role.role_name}",
                    f"arn:aws:iam::{self.account}:role/{lambda_role.role_name}",
                    f"arn:aws:iam::{self.account}:role/{auto_sync_lambda_role.role_name}"
                ],
                "Description": f"Data access policy for {insurance_collection_name}"
            }])
        )

        # Add dependencies for data access policy
        banking_collection.add_dependency(banking_data_access_policy)
        insurance_collection.add_dependency(insurance_data_access_policy)

        # Create Lambda function to create OpenSearch indices
        # Get the current directory where this file is located
        current_dir = Path(__file__).parent
        layers_dir = current_dir / "layers"
        lambda_dir = current_dir / "lambda"
        
        # Generate separate index names for each KB
        banking_index_name = f"bedrock-knowledge-base-banking-{name_key}-index"
        insurance_index_name = f"bedrock-knowledge-base-insurance-{name_key}-index"
        
        banking_index_creator_function = lambda_.Function(
            self, "BankingIndexCreatorFunction",
            runtime=lambda_.Runtime.PYTHON_3_9,
            handler="lambda_function.lambda_handler",
            role=lambda_role,
            timeout=Duration.minutes(10),
            environment={
                "OPENSEARCH_ENDPOINT": banking_collection.attr_collection_endpoint,
                "COLLECTION_NAME": banking_collection_name,
                "INDEX_NAME": banking_index_name
            },
            layers=[
                lambda_.LayerVersion(
                    self, "BankingOpenSearchPyLayer",
                    code=lambda_.Code.from_asset(str(layers_dir / "opensearchpy.zip")),
                    compatible_runtimes=[lambda_.Runtime.PYTHON_3_9],
                    description="OpenSearch Python client layer for Banking"
                ),
                lambda_.LayerVersion(
                    self, "BankingAWS4AuthLayer",
                    code=lambda_.Code.from_asset(str(layers_dir / "aws4auth.zip")),
                    compatible_runtimes=[lambda_.Runtime.PYTHON_3_9],
                    description="AWS4Auth layer for Banking"
                )
            ],
            code=lambda_.Code.from_asset(str(lambda_dir))
        )

        insurance_index_creator_function = lambda_.Function(
            self, "InsuranceIndexCreatorFunction",
            runtime=lambda_.Runtime.PYTHON_3_9,
            handler="lambda_function.lambda_handler",
            role=lambda_role,
            timeout=Duration.minutes(10),
            environment={
                "OPENSEARCH_ENDPOINT": insurance_collection.attr_collection_endpoint,
                "COLLECTION_NAME": insurance_collection_name,
                "INDEX_NAME": insurance_index_name
            },
            layers=[
                lambda_.LayerVersion(
                    self, "InsuranceOpenSearchPyLayer",
                    code=lambda_.Code.from_asset(str(layers_dir / "opensearchpy.zip")),
                    compatible_runtimes=[lambda_.Runtime.PYTHON_3_9],
                    description="OpenSearch Python client layer for Insurance"
                ),
                lambda_.LayerVersion(
                    self, "InsuranceAWS4AuthLayer",
                    code=lambda_.Code.from_asset(str(layers_dir / "aws4auth.zip")),
                    compatible_runtimes=[lambda_.Runtime.PYTHON_3_9],
                    description="AWS4Auth layer for Insurance"
                )
            ],
            code=lambda_.Code.from_asset(str(lambda_dir))
        )

        # Add dependency to ensure collection and name generation is complete before Lambda runs
        banking_index_creator_function.node.add_dependency(banking_collection)
        insurance_index_creator_function.node.add_dependency(insurance_collection)
        # Add dependency to ensure data access policy is applied before Lambda runs
        banking_index_creator_function.node.add_dependency(banking_data_access_policy)
        insurance_index_creator_function.node.add_dependency(insurance_data_access_policy)

        # Create separate providers for each collection
        banking_provider = cr.Provider(
            self, "BankingInitProvider",
            on_event_handler=banking_index_creator_function
        )

        insurance_provider = cr.Provider(
            self, "InsuranceInitProvider",
            on_event_handler=insurance_index_creator_function
        )

        # Create custom resource to create banking index
        banking_index_creator = CustomResource(
            self, "BankingIndexCreator",
            service_token=banking_provider.service_token,
            properties={
                "index_name": banking_index_name,
                "dimension": 1024,
                "method": "hnsw",
                "engine": "faiss",
                "space_type": "l2"
            }
        )

        # Create custom resource to create insurance index
        insurance_index_creator = CustomResource(
            self, "InsuranceIndexCreator",
            service_token=insurance_provider.service_token,
            properties={
                "index_name": insurance_index_name,
                "dimension": 1024,
                "method": "hnsw",
                "engine": "faiss",
                "space_type": "l2"
            }
        )

        # Add dependency for index creators
        banking_index_creator.node.add_dependency(banking_collection)
        insurance_index_creator.node.add_dependency(insurance_collection)
        # Add dependency to ensure Lambda function is ready before index creation
        banking_index_creator.node.add_dependency(banking_index_creator_function)
        insurance_index_creator.node.add_dependency(insurance_index_creator_function)

        # Create both knowledge bases - POSITIONED LAST IN THE FLOW
        banking_kb = self.create_kb(
            "genaifoundrybank-1",
            f"s3://{s3_bucket_name}/bank/",
            model_arn,
            bedrock_kb_role.role_arn,
            "bank",
            banking_index_name,
            banking_index_creator_function,
            banking_index_creator,
            banking_provider,
            banking_collection.attr_arn,
            banking_data_access_policy
        )

        insurance_kb = self.create_kb(
            "genaifoundryinsurance-1",
            f"s3://{s3_bucket_name}/insurance/",
            model_arn,
            bedrock_kb_role.role_arn,
            "insurance",
            insurance_index_name,
            insurance_index_creator_function,
            insurance_index_creator,
            insurance_provider,
            insurance_collection.attr_arn,
            insurance_data_access_policy
        )

        # Add dependencies to ensure index is created before Knowledge Bases
        banking_kb.node.add_dependency(banking_data_access_policy)
        insurance_kb.node.add_dependency(insurance_data_access_policy)
        banking_kb.node.add_dependency(banking_index_creator)
        banking_kb.node.add_dependency(banking_index_creator_function)
        banking_kb.node.add_dependency(banking_provider)
        insurance_kb.node.add_dependency(insurance_index_creator)
        insurance_kb.node.add_dependency(insurance_index_creator_function)
        insurance_kb.node.add_dependency(insurance_provider)
        banking_kb.node.add_dependency(bedrock_kb_role)
        insurance_kb.node.add_dependency(bedrock_kb_role)

        # Create Auto-Sync Lambda function AFTER knowledge bases are created
        auto_sync_function = lambda_.Function(
            self, "AutoSyncFunction",
            runtime=lambda_.Runtime.PYTHON_3_9,
            handler="auto_sync_function.handler",
            role=auto_sync_lambda_role,
            timeout=Duration.minutes(15),
            environment={
                "BANKING_KB_ID": banking_kb.attr_knowledge_base_id,
                "INSURANCE_KB_ID": insurance_kb.attr_knowledge_base_id,
                "BANKING_DS_ID": banking_kb.data_source_id,
                "INSURANCE_DS_ID": insurance_kb.data_source_id
            },
            code=lambda_.Code.from_asset(str(lambda_dir))
        )

        # Add dependencies to ensure Knowledge Bases are created before Lambda
        auto_sync_function.node.add_dependency(banking_kb)
        auto_sync_function.node.add_dependency(insurance_kb)

        # Create a custom resource to trigger initial sync after Knowledge Base creation
        initial_sync_function = lambda_.Function(
            self, "InitialSyncFunction",
            runtime=lambda_.Runtime.PYTHON_3_9,
            handler="initial_sync_function.handler",
            role=auto_sync_lambda_role,
            timeout=Duration.minutes(15),
            environment={
                "BANKING_KB_ID": banking_kb.attr_knowledge_base_id,
                "INSURANCE_KB_ID": insurance_kb.attr_knowledge_base_id,
                "BANKING_DS_ID": banking_kb.data_source_id,
                "INSURANCE_DS_ID": insurance_kb.data_source_id
            },
            code=lambda_.Code.from_asset(str(lambda_dir))
        )

        # Add dependencies to ensure Knowledge Bases are created before initial sync
        initial_sync_function.node.add_dependency(banking_kb)
        initial_sync_function.node.add_dependency(insurance_kb)

        # Create provider for initial sync
        initial_sync_provider = cr.Provider(
            self, "InitialSyncProvider",
            on_event_handler=initial_sync_function
        )

        # Create custom resource to trigger initial sync
        initial_sync = CustomResource(
            self, "InitialSync",
            service_token=initial_sync_provider.service_token,
            properties={
                "banking_kb_id": banking_kb.attr_knowledge_base_id,
                "insurance_kb_id": insurance_kb.attr_knowledge_base_id,
                "banking_ds_id": banking_kb.data_source_id,
                "insurance_ds_id": insurance_kb.data_source_id
            }
        )

        # Add dependencies for initial sync
        initial_sync.node.add_dependency(banking_kb)
        initial_sync.node.add_dependency(insurance_kb)
        initial_sync.node.add_dependency(initial_sync_function)

        # Add S3 event notification to trigger auto-sync Lambda
        # Note: This is optional and will be skipped if the bucket doesn't exist or isn't accessible
        try:
            # Check if bucket exists before adding notifications
            s3_client = boto3.client('s3')
            try:
                s3_client.head_bucket(Bucket=s3_bucket_name)
                print(f"Bucket {s3_bucket_name} exists, adding event notifications...")
                
                self.data_bucket.add_event_notification(
                    s3.EventType.OBJECT_CREATED,
                    s3n.LambdaDestination(auto_sync_function),
                    s3.NotificationKeyFilter(prefix="bank/")
                )
                
                self.data_bucket.add_event_notification(
                    s3.EventType.OBJECT_CREATED,
                    s3n.LambdaDestination(auto_sync_function),
                    s3.NotificationKeyFilter(prefix="insurance/")
                )
                print("S3 event notifications added successfully")
            except s3_client.exceptions.NoSuchBucket:
                print(f"Warning: Bucket {s3_bucket_name} does not exist. Skipping S3 event notifications.")
            except s3_client.exceptions.ClientError as e:
                if e.response['Error']['Code'] == '403':
                    print(f"Warning: No permission to access bucket {s3_bucket_name}. Skipping S3 event notifications.")
                else:
                    print(f"Warning: Could not verify bucket {s3_bucket_name}. Skipping S3 event notifications. Error: {e}")
        except Exception as e:
            print(f"Warning: Could not add S3 event notifications to bucket {s3_bucket_name}. Error: {e}")
            print("Auto-sync Lambda can still be triggered manually or via EventBridge")

        # Outputs
        CfnOutput(
            self,
            "ExistingDataBucketName",
            value=self.data_bucket.bucket_name,
            description="Name of the existing S3 bucket containing knowledge base data"
        )

        CfnOutput(
            self,
            "BankingOpenSearchCollectionArn",
            value=banking_collection.attr_arn,
            description="ARN of the Banking OpenSearch Serverless collection"
        )

        CfnOutput(
            self,
            "BankingOpenSearchCollectionEndpoint",
            value=banking_collection.attr_collection_endpoint,
            description="Endpoint of the Banking OpenSearch Serverless collection"
        )

        CfnOutput(
            self,
            "InsuranceOpenSearchCollectionArn",
            value=insurance_collection.attr_arn,
            description="ARN of the Insurance OpenSearch Serverless collection"
        )

        CfnOutput(
            self,
            "InsuranceOpenSearchCollectionEndpoint",
            value=insurance_collection.attr_collection_endpoint,
            description="Endpoint of the Insurance OpenSearch Serverless collection"
        )

        CfnOutput(
            self,
            "BankingKnowledgeBaseId",
            value=banking_kb.attr_knowledge_base_id,
            description="ID of the Banking Knowledge Base"
        )

        CfnOutput(
            self,
            "InsuranceKnowledgeBaseId",
            value=insurance_kb.attr_knowledge_base_id,
            description="ID of the Insurance Knowledge Base"
        )

        CfnOutput(
            self,
            "BankingDataSourceId",
            value=banking_kb.data_source_id,
            description="ID of the Banking Data Source"
        )

        CfnOutput(
            self,
            "InsuranceDataSourceId",
            value=insurance_kb.data_source_id,
            description="ID of the Insurance Data Source"
        )

        CfnOutput(
            self,
            "AutoSyncLambdaArn",
            value=auto_sync_function.function_arn,
            description="ARN of the Auto-Sync Lambda function"
        )

        CfnOutput(
            self,
            "ManualSyncInstructions",
            value=f"To manually trigger sync: aws lambda invoke --function-name {auto_sync_function.function_name} --payload '{{\"Records\":[{{\"eventSource\":\"aws:s3\",\"s3\":{{\"bucket\":{{\"name\":\"{s3_bucket_name}\"}},\"object\":{{\"key\":\"bank/test.pdf\"}}}}}}]}}' response.json",
            description="Command to manually trigger knowledge base sync if S3 events don't work"
        )

        CfnOutput(
            self,
            "InitialSyncFunctionArn",
            value=initial_sync_function.function_arn,
            description="ARN of the Initial Sync Lambda function"
        )

        CfnOutput(
            self,
            "InitialSyncStatus",
            value="Initial sync will be triggered automatically after Knowledge Base creation",
            description="Status of initial data sync"
        )

    def create_kb(self, name: str, s3_uri: str, model_arn: str, role_arn: str, data_prefix: str, index_name: str, index_creator_function: lambda_.Function, index_creator: CustomResource, provider: cr.Provider, collection_arn: str, data_access_policy: opensearch.CfnAccessPolicy):
        """Create a Bedrock Knowledge Base with data source"""
        
        # Create Knowledge Base
        kb = bedrock.CfnKnowledgeBase(
            self, f"{name}KB",
            name=name,
            role_arn=role_arn,
            knowledge_base_configuration=bedrock.CfnKnowledgeBase.KnowledgeBaseConfigurationProperty(
                type="VECTOR",
                vector_knowledge_base_configuration=bedrock.CfnKnowledgeBase.VectorKnowledgeBaseConfigurationProperty(
                    embedding_model_arn=model_arn
                )
            ),
            storage_configuration=bedrock.CfnKnowledgeBase.StorageConfigurationProperty(
                type="OPENSEARCH_SERVERLESS",
                opensearch_serverless_configuration=bedrock.CfnKnowledgeBase.OpenSearchServerlessConfigurationProperty(
                    collection_arn=collection_arn,
                    vector_index_name=index_name,
                    field_mapping=bedrock.CfnKnowledgeBase.OpenSearchServerlessFieldMappingProperty(
                        vector_field="vector",
                        text_field="text",
                        metadata_field="metadata",
                    )
                )
            )
        )
        kb.node.add_dependency(index_creator)
        kb.node.add_dependency(index_creator_function)
        kb.node.add_dependency(provider)
        kb.node.add_dependency(data_access_policy)

        # Add data source to the Knowledge Base
        data_source = bedrock.CfnDataSource(
            self, f"{name}DataSource",
            knowledge_base_id=kb.attr_knowledge_base_id,
            name=f"{name}-data",
            data_source_configuration={
                "type": "S3",
                "s3Configuration": {
                    "bucketArn": f"arn:aws:s3:::{s3_uri.replace('s3://', '').split('/')[0]}",
                    "inclusionPrefixes": [f"{data_prefix}/"]
                }
            },
            vector_ingestion_configuration={
                "chunkingConfiguration": {
                    "chunkingStrategy": "FIXED_SIZE",
                    "fixedSizeChunkingConfiguration": {
                        "maxTokens": 300,
                        "overlapPercentage": 10
                    }
                }
            }
        )
    

        # Store data source ID for later use
        kb.data_source_id = data_source.attr_data_source_id
        
        return kb