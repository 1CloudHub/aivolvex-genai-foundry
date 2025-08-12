from aws_cdk import (
    Stack,
    aws_ec2 as ec2,
    aws_s3 as s3,
    aws_iam as iam,
    RemovalPolicy,
    aws_lambda as lambda_,
      aws_bedrock as bedrock,
    aws_cloudfront as cloudfront,
    aws_cloudfront_origins as origins,
    aws_s3_deployment as s3deploy,
    CustomResource,
    Duration,
    custom_resources as cr,
    CfnOutput,
    aws_opensearchserverless as opensearch,
    aws_s3 as s3,
    aws_s3_notifications as s3n,
        aws_rds as rds,
        aws_apigateway as apigateway,
    aws_apigatewayv2 as apigatewayv2,
    aws_apigatewayv2_integrations as apigatewayv2_integrations,
    aws_lambda_event_sources as lambda_event_sources,
    RemovalPolicy,
    aws_s3_deployment as s3deploy,
    CfnOutput,
    Duration,
    CfnOutput,
    Size
    
)
from aws_cdk import Tags

import boto3
import os
from constructs import Construct
import json 
import random
from pathlib import Path
import string
import time

# region="us-west-2"
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

def generate_lambda_safe_name(length=12):
    """
    Generates a random name that is safe for Lambda functions.
    - Only contains letters, numbers, hyphens, and underscores
    - No periods or other special characters
    """
    if not 3 <= length <= 63:
        raise ValueError("Length must be between 3 and 63 characters.")

    # Characters for Lambda-safe names (no periods)
    body_chars = string.ascii_lowercase + string.digits + '-_'

    # Characters allowed at the end of the name
    end_chars = string.ascii_lowercase + string.digits

    # Generate the first n-1 characters
    main_part = ''.join(random.choices(body_chars, k=length - 1))

    # Generate a valid final character
    last_char = random.choice(end_chars)

    return "q" + main_part + last_char


def generate_rds_safe_name(length=12):
    """
    Generates a random name that is safe for RDS database names.
    - Only contains letters and numbers (no hyphens, underscores, or special characters)
    - Must begin with a letter
    """
    if not 3 <= length <= 63:
        raise ValueError("Length must be between 3 and 63 characters.")

    # Characters for RDS-safe names (only letters and numbers)
    body_chars = string.ascii_lowercase + string.digits

    # Characters allowed at the end of the name
    end_chars = string.ascii_lowercase + string.digits

    # Generate the first n-1 characters
    main_part = ''.join(random.choices(body_chars, k=length - 1))

    # Generate a valid final character
    last_char = random.choice(end_chars)

    return "q" + main_part + last_char


foundry_key = generate_random_alphanumeric(8)  # 8 characters for uniqueness
lambda_safe_key = generate_lambda_safe_name()
rds_safe_key = generate_rds_safe_name()
name_key = foundry_key  # Use shorter name like refer.py
lambda_name_key = "genai-foundry-" + lambda_safe_key
rds_name_key = "genaifoundry" + rds_safe_key
print(f"Resource name: {name_key}")
print(f"Lambda-safe name: {lambda_name_key}")
print(f"RDS-safe name: {rds_name_key}")

# CloudFront Distribution functionality integrated into FinalCdkStack
class LambdaLayerUploader(Construct):
    """Custom construct to upload Lambda layers from ZIP files"""
    
    def __init__(self, scope: Construct, construct_id: str, **kwargs):
        super().__init__(scope, construct_id, **kwargs)
        Tags.of(self).add("Project", "GenAI-Foundry")  
        # Map ZIP files to layer names with path information
        self.layer_mapping = {
            "layers/boto3-9e4ca0fc-be18-4b62-8bb2-40b541fc7de6.zip": {
                "name": "boto3",
                "path": "python/lib/python3.9/site-packages",
                "description": "Boto3 library for AWS SDK"
            },
            "layers/psycopg2-2eed8ff7-665e-4303-9aac-82f315ffdf5f.zip": {
                "name": "psycopg2", 
                "path": "python/lib/python3.9/site-packages",
                "description": "PostgreSQL adapter for Python"
            },
            "layers/requests-0899e8ab-9427-46b4-b6e7-3d3c376139dc.zip": {
                "name": "requests",
                "path": "python/lib/python3.9/site-packages", 
                "description": "HTTP library for Python"
            },
            "layers/opensearchpy.zip": {
                "name": "opensearchpy",
                "path": "python/lib/python3.9/site-packages",
                "description": "OpenSearch Python client"
            },
            "layers/aws4auth.zip": {
                "name": "requests_aws4auth",
                "path": "python/lib/python3.9/site-packages",
                "description": "AWS4Auth for requests library"
            }
        }
        
        self.layers = {}
        self._create_layers()
    
    def _create_layers(self):
        """Create Lambda layers from ZIP files with path information"""
        for zip_file, layer_info in self.layer_mapping.items():
            layer_name = layer_info["name"]
            layer_path = layer_info["path"]
            layer_description = layer_info["description"]
            
            if os.path.exists(zip_file):
                # Create layer using CDK with path information
                layer = lambda_.LayerVersion(
                    self, f"{layer_name.capitalize()}Layer",
                    layer_version_name=layer_name,
                    description=f"{layer_description} - Path: {layer_path}",
                    code=lambda_.Code.from_asset(zip_file),
                    compatible_runtimes=[
                        lambda_.Runtime.PYTHON_3_9,
                        lambda_.Runtime.PYTHON_3_10,
                        lambda_.Runtime.PYTHON_3_11,
                        lambda_.Runtime.PYTHON_3_12
                    ],
                    compatible_architectures=[lambda_.Architecture.X86_64]
                )
                
                # Store layer with path information
                self.layers[layer_name] = {
                    "layer": layer,
                    "path": layer_path,
                    "zip_file": zip_file,
                    "description": layer_description
                }
            else:
                print(f"⚠️  ZIP file not found: {zip_file}")
                # Create empty layer as fallback
                layer = lambda_.LayerVersion(
                    self, f"{layer_name.capitalize()}Layer",
                    layer_version_name=layer_name,
                    description=f"Empty {layer_name} layer (ZIP file not found) - Path: {layer_path}",
                    code=lambda_.Code.from_inline("# Empty layer"),
                    compatible_runtimes=[lambda_.Runtime.PYTHON_3_9],
                    compatible_architectures=[lambda_.Architecture.X86_64]
                )
                
                self.layers[layer_name] = {
                    "layer": layer,
                    "path": layer_path,
                    "zip_file": zip_file,
                    "description": layer_description
                }

s3_name = "genai-foundry-test"
class FinalCdkStack(Stack):

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Create VPC
        vpc = ec2.Vpc(
            self, name_key,
            ip_protocol=ec2.IpProtocol.IPV4_ONLY,
            max_azs=2,
            cidr="10.0.0.0/16",
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    name="PublicSubnet",
                    subnet_type=ec2.SubnetType.PUBLIC,
                    cidr_mask=24,
                ),
                ec2.SubnetConfiguration(
                    name="PrivateSubnet",
                    subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS,
                    cidr_mask=24,
                )
            ]
        )

        # Create security group for EC2
        ec2_security_group = ec2.SecurityGroup(
            self, "MyEC2SecurityGroup",
            vpc=vpc,
            description="Security group for EC2 instance",
            allow_all_outbound=True
        )

        ec2_security_group.add_ingress_rule(
            peer=ec2.Peer.any_ipv4(),
            connection=ec2.Port.tcp(22),
            description="Allow SSH access"
        )

        ec2_security_group.add_ingress_rule(
            peer=ec2.Peer.any_ipv4(),
            connection=ec2.Port.tcp(80),
            description="Allow HTTP access"
        )

        ec2_security_group.add_ingress_rule(
            peer=ec2.Peer.any_ipv4(),
            connection=ec2.Port.tcp(8000),
            description="Allow HTTP access"
        )

         # Create security group for RDS
        rds_security_group = ec2.SecurityGroup(
            self, "RDSSecurityGroup",
            vpc=vpc,
            description="Security group for RDS instance",
            allow_all_outbound=False
        )
        
        # Create Lambda security group
        lambda_security_group = ec2.SecurityGroup(
            self, "LambdaSecurityGroup",
            vpc=vpc,
            description="Security group for Lambda functions",
            allow_all_outbound=True
        )
        
        # Allow Lambda to access RDS
        rds_security_group.add_ingress_rule(
            peer=lambda_security_group,
            connection=ec2.Port.tcp(5432),
            description="Allow PostgreSQL access from Lambda"
        )

        # Also allow Lambda security group to access RDS (explicit rule)
        lambda_security_group.add_egress_rule(
            peer=rds_security_group,
            connection=ec2.Port.tcp(5432),
            description="Allow Lambda to connect to RDS"
        )
        
        key_pair = ec2.KeyPair(
            self, "MyKeyPair",
            key_pair_name=f"keypair-{name_key}",  # Use your random name
            type=ec2.KeyPairType.RSA,
            format=ec2.KeyPairFormat.PEM
        )

        # Allow EC2 to access RDS
        rds_security_group.add_ingress_rule(
            peer=ec2_security_group,
            connection=ec2.Port.tcp(5432),
            description="Allow PostgreSQL access from EC2"
        )

        # Create RDS subnet group
        db_subnet_group = rds.SubnetGroup(
            self, "MyDBSubnetGroup",
            description="Subnet group for RDS database",
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS
            )
        )
        # Create three S3 buckets with different purposes
        s3_bucket_name = "genaifoundry"+name_key
        frontend_bucket_name = "genaifoundry-front"+name_key
        voiceops_bucket_name = "voiceop"+name_key
        
        # Main bucket for knowledge base data
        bucket = s3.Bucket(
            self, 
            "KnowledgeBaseBucket",
            bucket_name=s3_bucket_name,
            versioned=True,
            removal_policy=RemovalPolicy.DESTROY,  # For development only
            auto_delete_objects=True  # For development only
        )
        
        # Voice operations bucket for audio processing
        voiceops_bucket = s3.Bucket(
            self, 
            "VoiceOpsBucket",
            bucket_name=voiceops_bucket_name,
            versioned=True,
            removal_policy=RemovalPolicy.DESTROY,  # For development only
            auto_delete_objects=True  # For development only
        )
        
        # Frontend bucket for static website hosting (public)
        frontend_bucket = s3.Bucket(
            self, 
            "FrontendBucket",
            bucket_name=frontend_bucket_name,
            versioned=True,
            removal_policy=RemovalPolicy.DESTROY,  # For development only
            auto_delete_objects=True,  # For development only
            website_index_document="index.html",
            website_error_document="index.html",
            # public_read_access=True,  # Allow public read access
            # block_public_access=s3.BlockPublicAccess.BLOCK_NONE  # Disable public access blocking
        )
        
        # Upload knowledge base folder contents to the main bucket
        s3deploy.BucketDeployment(
            self,
            "DeployKnowledgeBaseFolder",
            sources=[s3deploy.Source.asset("genaifoundy-usecases")],  # Path to your local folder
            destination_bucket=bucket,
            destination_key_prefix="kb/",  # Optional: prefix for uploaded files
        )
        
        # Upload frontend folder contents to the frontend bucket
        frontend_deploy = s3deploy.BucketDeployment(
            self,
            "DeployFrontendFolder",
            sources=[s3deploy.Source.asset("genaifoundry-front")],  # Path to your frontend folder
            destination_bucket=frontend_bucket,
            destination_key_prefix="",  # Upload to root of bucket
        )
         # Reusable variables
        
        model_arn = f"arn:aws:bedrock:{self.region}::foundation-model/amazon.titan-embed-text-v2:0"

        # Reference existing S3 bucket
        self.data_bucket = s3.Bucket.from_bucket_name(
            self, 
            "ExistingDataBucket",
            bucket_name=s3_bucket_name
        )

        # Create separate OpenSearch Serverless Collections for each KB
        banking_collection_name = f"bank-{name_key}-col"
        insurance_collection_name = f"ins-{name_key}-col"
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
            name=f"bank-{name_key}-encrypt",
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
            name=f"bank-{name_key}-network",
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
            name=f"ins-{name_key}-encrypt",
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
            name=f"ins-{name_key}-network",
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
                                f"arn:aws:aoss:{self.region}:{self.account}:collection/*",
                                f"arn:aws:aoss:{self.region}:{self.account}:index/*",
                                f"arn:aws:aoss:{self.region}:{self.account}:collection/{banking_collection_name}",
                                f"arn:aws:aoss:{self.region}:{self.account}:index/{banking_collection_name}/*",
                                f"arn:aws:aoss:{self.region}:{self.account}:collection/{insurance_collection_name}",
                                f"arn:aws:aoss:{self.region}:{self.account}:index/{insurance_collection_name}/*"
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
                                f"arn:aws:lambda:{self.region}:{self.account}:function:*"
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
                                f"arn:aws:bedrock:{self.region}:{self.account}:knowledge-base/*",
                                f"arn:aws:bedrock:{self.region}:{self.account}:knowledge-base/*/data-source/*"
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
            name=f"bank-{name_key}-access",
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
            name=f"ins-{name_key}-access",
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
        # Point to the layers and lambda directories in the project root (one level up)
        layers_dir = current_dir.parent / "layers"
        lambda_dir = current_dir.parent / "lambda"
        
        # Generate separate index names for each KB
        banking_index_name = f"bank-{name_key}-idx"
        insurance_index_name = f"ins-{name_key}-idx"
        
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
            f"s3://{s3_bucket_name}/kb/bank/",
            model_arn,
            bedrock_kb_role.role_arn,
            "kb/bank",
            banking_index_name,
            banking_index_creator_function,
            banking_index_creator,
            banking_provider,
            banking_collection.attr_arn,
            banking_data_access_policy
        )

        insurance_kb = self.create_kb(
            "genaifoundryinsurance-1",
            f"s3://{s3_bucket_name}/kb/insurance/",
            model_arn,
            bedrock_kb_role.role_arn,
            "kb/insurance",
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
                    s3.NotificationKeyFilter(prefix="kb/bank/")
                )
                
                self.data_bucket.add_event_notification(
                    s3.EventType.OBJECT_CREATED,
                    s3n.LambdaDestination(auto_sync_function),
                    s3.NotificationKeyFilter(prefix="kb/insurance/")
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

        # Create RDS PostgreSQL instance
        db_instance = rds.DatabaseInstance(
            self, "MyPostgreSQLDB",
            engine=rds.DatabaseInstanceEngine.postgres(
                version=rds.PostgresEngineVersion.VER_17_4
            ),
            instance_type=ec2.InstanceType.of(
                ec2.InstanceClass.T3,
                ec2.InstanceSize.MICRO
            ),
            vpc=vpc,
            subnet_group=db_subnet_group,
            security_groups=[rds_security_group],  # Use the correct security group
            credentials=rds.Credentials.from_generated_secret(
                username="postgres",
                secret_name=f"rds-credentials-{rds_name_key}"  # Make it unique
            ),
            allocated_storage=20,
            storage_type=rds.StorageType.GP2,
            deletion_protection=False,
            delete_automated_backups=False,
            backup_retention=Duration.days(7),
            removal_policy=RemovalPolicy.DESTROY,
            database_name=rds_name_key
        )

        # Create IAM role for EC2
        ec2_role = iam.Role(
            self, "EC2Role",
            assumed_by=iam.ServicePrincipal("ec2.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("AmazonSSMManagedInstanceCore"),
                iam.ManagedPolicy.from_aws_managed_policy_name("AmazonS3ReadOnlyAccess"),
                iam.ManagedPolicy.from_aws_managed_policy_name("AdministratorAccess")
            ],
                inline_policies={
        "TranscribePolicy": iam.PolicyDocument(
            statements=[
                iam.PolicyStatement(
                    effect=iam.Effect.ALLOW,
                    actions=[
                        "transcribe:StartTranscriptionJob",
                        "transcribe:GetTranscriptionJob", 
                        "transcribe:DeleteTranscriptionJob"
                    ],
                    resources=["*"]
                ),
                iam.PolicyStatement(
                    effect=iam.Effect.ALLOW,
                    actions=[
                        "s3:GetObject",
                        "s3:PutObject",
                        "s3:DeleteObject"
                    ],
                    resources=[f"arn:aws:s3:::{s3_bucket_name}/*"]
                )
            ]
        )
    }
        )
        instance_profile = iam.CfnInstanceProfile(
    self, "EC2InstanceProfile",
    roles=[ec2_role.role_name]
)

        ec2_role.add_to_policy(iam.PolicyStatement(
            effect=iam.Effect.ALLOW,
            actions=[
                "ec2-instance-connect:SendSSHPublicKey",
                "ec2:DescribeInstances",
                "ec2:DescribeInstanceAttribute"
            ],
            resources=["*"]
        ))

        ec2_role.add_to_policy(iam.PolicyStatement(
            effect=iam.Effect.ALLOW,
            actions=[
                "secretsmanager:GetSecretValue",
                "secretsmanager:DescribeSecret"
            ],
            resources=[
                f"arn:aws:secretsmanager:*:*:secret:rds-credentials-{name_key}-*"
            ]
        ))

        # AdministratorAccess provides wide permissions needed for provisioning and bootstrap tasks
        # IMPORTANT: Grant EC2 access to the RDS secret
        if db_instance.secret:
            db_instance.secret.grant_read(ec2_role)

        # Create EC2 instance
        ec2_instance = ec2.Instance(
            self, "MyEC2Instance",
            role=ec2_role,
            instance_type=ec2.InstanceType.of(
                ec2.InstanceClass.T3,
                ec2.InstanceSize.MEDIUM
            ),
            # machine_image=ec2.MachineImage.latest_amazon_linux2(),
            machine_image=ec2.MachineImage.lookup(
                name="Deep Learning OSS Nvidia Driver AMI GPU PyTorch 2.7 (Ubuntu 22.04)*",
                owners=["amazon"]
            ),
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PUBLIC
            ),
            security_group=ec2_security_group,
            key_pair=key_pair,
            user_data=ec2.UserData.for_linux(),
            block_devices=[
                ec2.BlockDevice(
                    device_name="/dev/sda1",  # Root volume device name for Ubuntu
                    volume=ec2.BlockDeviceVolume.ebs(
                        volume_size=300,  # Size in GB
                        volume_type=ec2.EbsDeviceVolumeType.GP3,  # GP3 is cost-effective and performant
                        delete_on_termination=True,  # Delete when instance terminates
                        encrypted=True  # Optional: encrypt the volume
                    )
                )
            ]
        )
        secret_name = f"rds-credentials-{rds_name_key}"
        ec2_instance.add_user_data(
     "sudo apt update -y",
    "sudo apt install -y apache2 awscli jq postgresql-client-14",
    "systemctl start apache2",
    "systemctl enable apache2", 
    "echo '<h1>Hello from AWSSSSSSSSSSSSSS!</h1>' > /var/www/html/index.html",
    'cd home/ubuntu/',
    'mkdir startingggggg',
    'mkdir final'
    # Create restoration script (note: using /home/ubuntu for Ubuntu AMI)
    'cat << \'EOF\' > /home/ubuntu/restore_db.sh',
    '#!/bin/bash',
    'set -e',
    '',

    'EOF',    
    'mkdir creating_voicebittttttttt',
    'cat << \'EOF\' > /home/ubuntu/voice_bot.sh',
    '#!/bin/bash',
    'set -e',
    '',
    'export DEBIAN_FRONTEND=noninteractive',
    'echo "Getting database credentials from Secrets Manager..."',
    f'SECRET_JSON=$(aws secretsmanager get-secret-value --secret-id "{secret_name}" --query SecretString --output text --region {self.region})',
    'echo "$SECRET_JSON"',
    'DB_HOST=$(echo "$SECRET_JSON" | jq -r .host)',
    'DB_PORT=$(echo "$SECRET_JSON" | jq -r .port)',
    'DB_USERNAME=$(echo "$SECRET_JSON" | jq -r .username)',
    'DB_PASSWORD=$(echo "$SECRET_JSON" | jq -r .password)',
    'DB_NAME=$(echo "$SECRET_JSON" | jq -r .dbname)',
    "export DB_HOST=$(echo \"$SECRET_JSON\" | jq -r .host)",
    "export DB_PORT=$(echo \"$SECRET_JSON\" | jq -r .port)",
    "export DB_USERNAME=$(echo \"$SECRET_JSON\" | jq -r .username)",
    "export DB_PASSWORD=$(echo \"$SECRET_JSON\" | jq -r .password)",
    "export DB_NAME=$(echo \"$SECRET_JSON\" | jq -r .dbname)",
    f"export REGION={self.region}",
    "",
    "echo 'Database connection details:'",
    "echo \"Host: $DB_HOST\"",
    "echo \"Port: $DB_PORT\"",
    "echo \"Database: $DB_NAME\"",
    "echo \"Username: $DB_USERNAME\"",
    "",
    '',
    'echo "Database connection details:"',
    'echo "Host: $DB_HOST"',
    'echo "Port: $DB_PORT"',
    'echo "Database: $DB_NAME"',
    'echo "Username: $DB_USERNAME"',
    '',
    'export PGPASSWORD="$DB_PASSWORD"',
    '',
    '# Test connection',
    'echo "Testing database connection..."',
    'psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USERNAME" -d "$DB_NAME" -c "SELECT version();"',
    '',
    '# Download dump',
    'echo "Downloading database dump file..."',
    'aws s3 cp s3://sql-dumps-bucket/dump-postgres.sql /tmp/dump.sql',
    '',
    '# Restore database',
    'echo "Restoring database from dump file..."',
    'psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USERNAME" -d "$DB_NAME" -f /tmp/dump.sql',
    '',
    '# Verify restoration',
    'echo "Verifying restoration..."',
    'psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USERNAME" -d "$DB_NAME" -c "\\\\dn"',
    'psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USERNAME" -d "$DB_NAME" -c "\\\\dt foundry_app.*"',
    '',
    'echo "Database restoration completed successfully!"',
    "echo 'starting python code implementation'",
    "export DEBIAN_FRONTEND=noninteractive",
    "cd /home/ubuntu",
    "aws s3 sync s3://sql-dumps-bucket/ec2_needs/ ./ec2_needs/",
    "cd ec2_needs",
    "sudo apt install python3.10-venv -y",
    "python3 -m venv eagle",
    "source eagle/bin/activate",
    "pip install -r requirements.txt --no-input",
    "pip install asgiref --no-input",
    "# Set environment variable and run in screen session",
    "screen -dmS run_app bash -c 'source eagle/bin/activate && export S3_PATH=" + s3_name + " && uvicorn sun:asgi_app --host 0.0.0.0 --port 8000'",
    "echo 'DONE!!!!!!!!!!!!!!'",
    'EOF',
    'mkdir adding_permissionssssssss',
    'sudo chmod +x /home/ubuntu/restore_db.sh',
    'sudo chown ubuntu:ubuntu /home/ubuntu/restore_db.sh',

    'sudo chmod +x /home/ubuntu/voice_bot.sh', 
    'sudo chown ubuntu:ubuntu /home/ubuntu/voice_bot.sh',
    'mkdir permissions_addeddddddd',
    # Wait for RDS to be ready and run restoration
    'sleep 20',
    #'sudo su - ubuntu -c "/home/ubuntu/restore_db.sh" > /var/log/db_restore.log 2>&1',
    "sleep 30",
    'sudo su - ubuntu -c "/home/ubuntu/voice_bot.sh" > /var/log/voice_bot.log 2>&1'
        )

        # Get the secret name that will be created (this is available at synthesis time)


        # Add user data with database restoration script

        
        print("📦 Creating Lambda layers from ZIP files...")
        layer_uploader = LambdaLayerUploader(self, "LambdaLayerUploader")
        
        # Get the created layers
        boto3_layer = layer_uploader.layers.get("boto3")["layer"] if layer_uploader.layers.get("boto3") else None
        psycopg2_layer = layer_uploader.layers.get("psycopg2")["layer"] if layer_uploader.layers.get("psycopg2") else None
        requests_layer = layer_uploader.layers.get("requests")["layer"] if layer_uploader.layers.get("requests") else None
        requests_aws4auth_layer = layer_uploader.layers.get("requests_aws4auth")["layer"] if layer_uploader.layers.get("requests_aws4auth") else None
        opensearchpy_layer = layer_uploader.layers.get("opensearchpy")["layer"] if layer_uploader.layers.get("opensearchpy") else None

        # Create IAM role for Lambda
        lambda_role = iam.Role(
            self, "LambdaRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSLambdaBasicExecutionRole"),
                iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSLambdaVPCAccessExecutionRole"),
                iam.ManagedPolicy.from_aws_managed_policy_name("AmazonAPIGatewayAdministrator"),
                iam.ManagedPolicy.from_aws_managed_policy_name("AmazonAPIGatewayInvokeFullAccess"),
                iam.ManagedPolicy.from_aws_managed_policy_name("AmazonBedrockFullAccess"),
                iam.ManagedPolicy.from_aws_managed_policy_name("AmazonEC2FullAccess"),
                iam.ManagedPolicy.from_aws_managed_policy_name("AmazonRDSFullAccess"),
                iam.ManagedPolicy.from_aws_managed_policy_name("AmazonS3FullAccess")
            ]
        )

        # Add custom inline policy for specific permissions
        lambda_role.add_to_policy(iam.PolicyStatement(
            sid="VPCAccessForRDSConnectivity",
            effect=iam.Effect.ALLOW,
            actions=[
                "ec2:CreateNetworkInterface",
                "ec2:DeleteNetworkInterface",
                "ec2:DescribeNetworkInterfaces",
                "ec2:DescribeSecurityGroups",
                "ec2:DescribeSubnets",
                "ec2:DescribeVpcs",
                "ec2:AssignPrivateIpAddresses",
                "ec2:UnassignPrivateIpAddresses",
                "execute-api:ManageConnections",
                "s3:*"
            ],
            resources=["*"]
        ))

        lambda_role.add_to_policy(iam.PolicyStatement(
            sid="FullRDSAccess",
            effect=iam.Effect.ALLOW,
            actions=[
                "rds:*"
            ],
            resources=["*"]
        ))

        lambda_role.add_to_policy(iam.PolicyStatement(
            sid="FullBedrockAccess",
            effect=iam.Effect.ALLOW,
            actions=[
                "bedrock:*"
            ],
            resources=["*"]
        ))

        lambda_role.add_to_policy(iam.PolicyStatement(
            sid="ExplicitBedrockInferenceAccess",
            effect=iam.Effect.ALLOW,
            actions=[
                "bedrock:GetInferenceProfile",
                "bedrock:RetrieveAndGenerateStream",
                "bedrock:InvokeModel",
                "bedrock:InvokeModelWithResponseStream",
                "bedrock:ListInferenceProfiles"
            ],
            resources=[
                "arn:aws:bedrock:*:*:inference-profile/*",
                "arn:aws:bedrock:*:*:model/*"
            ]
        ))

        lambda_role.add_to_policy(iam.PolicyStatement(
            sid="CloudWatchLogsAccess",
            effect=iam.Effect.ALLOW,
            actions=[
                "logs:*"
            ],
            resources=["*"]
        ))

        # Add account-specific log group permissions
        lambda_role.add_to_policy(iam.PolicyStatement(
            sid="AccountSpecificLogGroupAccess",
            effect=iam.Effect.ALLOW,
            actions=[
                "logs:CreateLogGroup"
            ],
            resources=[
                f"arn:aws:logs:{self.region}:{self.account}:*"
            ]
        ))

        lambda_role.add_to_policy(iam.PolicyStatement(
            sid="LambdaLogStreamAccess",
            effect=iam.Effect.ALLOW,
            actions=[
                "logs:CreateLogStream",
                "logs:PutLogEvents"
            ],
            resources=[
                f"arn:aws:logs:{self.region}:{self.account}:log-group:/aws/lambda/*:*"
            ]
        ))

        # Grant Lambda access to the RDS secret
        if db_instance.secret:
            db_instance.secret.grant_read(lambda_role)
            
        # Grant Lambda access to the voice operations S3 bucket
        voiceops_bucket.grant_read_write(lambda_role)

        # Create API Gateway with specific configurations
        api = apigateway.RestApi(
            self, "GenAIFoundryAPI",
            rest_api_name="genaifoundry-api"+name_key,
            description="API Gateway for GenAI Foundry Lambda function",
            binary_media_types=["multipart/form-data"],
            default_cors_preflight_options=apigateway.CorsOptions(
                allow_origins=["*"],
                allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
                allow_headers=["*"],
                allow_credentials=False,
                max_age=Duration.seconds(86400)
            ),
            deploy_options=apigateway.StageOptions(
                stage_name="dev",
                logging_level=apigateway.MethodLoggingLevel.OFF,
                data_trace_enabled=False
            )
        )

        # Create coaching_assist_voiceops API Gateway
        coaching_api = apigateway.RestApi(
            self, "CoachingAssistVoiceopsAPI",
            rest_api_name="coaching_assist_voiceops"+name_key,
            description="API Gateway for Coaching Assist Voice Operations",
            default_cors_preflight_options=apigateway.CorsOptions(
                allow_origins=["*"],
                allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
                allow_headers=["*"],
                allow_credentials=False,
                max_age=Duration.seconds(86400)
            ),
            deploy_options=apigateway.StageOptions(
                stage_name="dev",
                logging_level=apigateway.MethodLoggingLevel.OFF,
                data_trace_enabled=False
            )
        )

        # Now define environment variables after API Gateway is created
        env_vars = {
            "CHAT_LOG_TABLE": "ce_cexp_logs",
            "KB_ID": insurance_kb.attr_knowledge_base_id,  # Insurance KB ID
            "RETAIL_KB_ID": "EPCDJQTW5Q",
            "bank_kb_id": banking_kb.attr_knowledge_base_id,  # Banking KB ID
            "banking_chat_history_table": "banking_chat_history",
            "chat_history_table": "chat_history",
            "db_database": rds_name_key,
            "db_host": db_instance.instance_endpoint.hostname,
            "db_port": "5432",
            "db_user": "postgres",
            "hr_kb_id": "5VLRLLOZWO",
            "model_id": "us.anthropic.claude-3-7-sonnet-20250219-v1:0",
            "perplexity_api_key": "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",
            "product_kb_id": "BLGSVQOACP",
            "prompt_metadata_table": "prompt_metadata",
            "region_used": self.region,
            "region_name": self.region,  # New environment variable for region name
            "retail_chat_history_table": "retail_chat_history",
            "schema": "genaifoundry",
            "voiceops_bucket_name": voiceops_bucket_name,  # New environment variable for voice operations bucket
            "ec2_instance_ip": ec2_instance.instance_public_ip,  # Public IP of the T3 medium instance
            # "socket_endpoint": f"https://{api.rest_api_id}.execute-api.{self.region}.amazonaws.com/dev/",
            "rds_secret_name": f"rds-credentials-{rds_name_key}",
            "rds_secret_arn": db_instance.secret.secret_arn if db_instance.secret else "",
            "rds_endpoint": db_instance.instance_endpoint.hostname,
            "rds_port": str(db_instance.instance_endpoint.port),
            "rds_database": rds_name_key,
            "rds_username": "postgres"
        }

        # Create Lambda function
        lambda_function = lambda_.Function(
            self, "MyLambdaFunction",
            runtime=lambda_.Runtime.PYTHON_3_9,
            handler="working.lambda_handler",
            code=lambda_.Code.from_asset("lambda_code"),
            function_name=lambda_name_key,
            memory_size=128,
            timeout=Duration.seconds(303),  # 5 minutes 3 seconds
            ephemeral_storage_size=Size.mebibytes(512),
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS
            ),
            security_groups=[lambda_security_group],
            role=lambda_role,
            layers=[
                boto3_layer,
                psycopg2_layer,
                requests_layer,
                requests_aws4auth_layer,
                opensearchpy_layer
            ],
            environment=env_vars
        )

        # Create WebSocket Lambda function
        websocket_lambda_function = lambda_.Function(
            self, "WebSocketLambdaFunction",
            runtime=lambda_.Runtime.PYTHON_3_9,
            handler="websocket_handler.lambda_handler",
            code=lambda_.Code.from_asset("lambda_code"),
            function_name="ws_"+lambda_name_key,
            memory_size=128,
            timeout=Duration.seconds(29),  # 29 seconds as specified
            ephemeral_storage_size=Size.mebibytes(512),
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS
            ),
            security_groups=[lambda_security_group],
            role=lambda_role,
            layers=[
                boto3_layer,
                psycopg2_layer,
                requests_layer,
                requests_aws4auth_layer,
                opensearchpy_layer
            ],
            environment=env_vars
        )

        # Create HTTP proxy integration for coaching API
        http_proxy_integration = apigateway.HttpIntegration(
            url=f"http://{ec2_instance.instance_public_ip}:8000",
            proxy=True,
            options=apigateway.IntegrationOptions(
                timeout=Duration.seconds(29),
                integration_responses=[
                    apigateway.IntegrationResponse(
                        status_code="200",
                        response_parameters={
                            "method.response.header.Access-Control-Allow-Origin": "'*'"
                        }
                    )
                ]
            )
        )

        # Add ANY method to root resource
        coaching_api.root.add_method("ANY", http_proxy_integration,
            method_responses=[
                apigateway.MethodResponse(
                    status_code="200",
                    response_parameters={
                        "method.response.header.Access-Control-Allow-Origin": True
                    }
                )
            ]
        )

        # Add /ping resource and GET method
        ping_resource = coaching_api.root.add_resource("ping")
        ping_resource.add_method("GET", 
            apigateway.HttpIntegration(
                url=f"http://{ec2_instance.instance_public_ip}:8000/ping",
                proxy=True,
                options=apigateway.IntegrationOptions(
                    timeout=Duration.seconds(29)
                )
            ),
            method_responses=[
                apigateway.MethodResponse(
                    status_code="200"
                )
            ]
        )

        # OPTIONS method is automatically added by CORS configuration

        # Add /transcribe resource
        transcribe_resource = coaching_api.root.add_resource("transcribe")
       
        # Add POST method to /transcribe
        transcribe_resource.add_method("POST",
            apigateway.HttpIntegration(
                url=f"http://{ec2_instance.instance_public_ip}:8000/transcribe",
                proxy=True,
                options=apigateway.IntegrationOptions(
                    timeout=Duration.seconds(29),
                    integration_responses=[
                        apigateway.IntegrationResponse(
                            status_code="200",
                            response_parameters={
                                "method.response.header.Access-Control-Allow-Origin": "'*'"
                            }
                        )
                    ]
                )
            ),
            method_responses=[
                apigateway.MethodResponse(
                    status_code="200",
                    response_parameters={
                        "method.response.header.Access-Control-Allow-Origin": True
                    }
                )
            ]
        )

        # OPTIONS method is automatically added by CORS configuration

        # Create Lambda integration for regular endpoints (non-proxy)
        lambda_integration = apigateway.LambdaIntegration(
            lambda_function,
            proxy=False,
            request_templates={
                "application/json": ''
            },
            integration_responses=[
                apigateway.IntegrationResponse(
                    status_code="200",
                    response_parameters={
                        "method.response.header.Access-Control-Allow-Headers": "'Content-Type,X-Amz-Date,Authorization,X-Api-Key,X-Amz-Security-Token'",
                        "method.response.header.Access-Control-Allow-Methods": "'DELETE,GET,HEAD,OPTIONS,PATCH,POST,PUT'",
                        "method.response.header.Access-Control-Allow-Origin": "'*'",
                        "method.response.header.X-Requested-With": "'*'"
                    },
                    response_templates={
                        "application/json": ""
                    }
                )
            ]
        )

        # Create Lambda integration for opensearch endpoint (with specific mapping)
        opensearch_lambda_integration = apigateway.LambdaIntegration(
            lambda_function,
            proxy=False,
            request_templates={
                "application/json": '',
                "multipart/form-data": '{\n  "content": "$input.body",\n  "event_type": "$input.params(\'event_type\')",\n  "search_type": "$input.params(\'search_type\')",\n  "image_file": "$input.params(\'image\')"\n}'
            },
            integration_responses=[
                apigateway.IntegrationResponse(
                    status_code="200",
                    response_parameters={
                        "method.response.header.Access-Control-Allow-Headers": "'Content-Type,X-Amz-Date,Authorization,X-Api-Key,X-Amz-Security-Token'",
                        "method.response.header.Access-Control-Allow-Methods": "'DELETE,GET,HEAD,OPTIONS,PATCH,POST,PUT'",
                        "method.response.header.Access-Control-Allow-Origin": "'*'",
                        "method.response.header.X-Requested-With": "'*'"
                    },
                    response_templates={
                        "application/json": ""
                    }
                )
            ]
        )

        # Create resources and methods with CORS enabled
        # /chat_api endpoint
        chat_api_resource = api.root.add_resource("chat_api")
        chat_api_resource.add_method("POST", lambda_integration,
            method_responses=[
                apigateway.MethodResponse(
                    status_code="200",
                    response_parameters={
                        "method.response.header.Access-Control-Allow-Headers": True,
                        "method.response.header.Access-Control-Allow-Methods": True,
                        "method.response.header.Access-Control-Allow-Origin": True,
                        "method.response.header.X-Requested-With": True
                    }
                )
            ]
        )

        # /genai_foundry_misc endpoint
        genai_misc_resource = api.root.add_resource("genai_foundry_misc")
        genai_misc_resource.add_method("POST", lambda_integration,
            method_responses=[
                apigateway.MethodResponse(
                    status_code="200",
                    response_parameters={
                        "method.response.header.Access-Control-Allow-Headers": True,
                        "method.response.header.Access-Control-Allow-Methods": True,
                        "method.response.header.Access-Control-Allow-Origin": True,
                        "method.response.header.X-Requested-With": True
                    }
                )
            ]
        )

        # /opensearch endpoint with specific configuration
        opensearch_resource = api.root.add_resource("opensearch")
        opensearch_resource.add_method("POST", opensearch_lambda_integration,
            request_parameters={
                "method.request.querystring.event_type": True,
                "method.request.querystring.search_type": True,
                "method.request.querystring.search_query": True,
                "method.request.querystring.document_type": False,
                "method.request.querystring.file_type": False,
                "method.request.querystring.input_language": False,
                "method.request.querystring.job_description": False,
                "method.request.querystring.output_language": False,
                "method.request.querystring.session_id": False
            },
            method_responses=[
                apigateway.MethodResponse(
                    status_code="200",
                    response_parameters={
                        "method.response.header.Access-Control-Allow-Headers": True,
                        "method.response.header.Access-Control-Allow-Methods": True,
                        "method.response.header.Access-Control-Allow-Origin": True,
                        "method.response.header.X-Requested-With": True
                    }
                )
            ]
        )

        # /voiceops endpoint
        voiceops_resource = api.root.add_resource("voiceops")
        voiceops_resource.add_method("POST", lambda_integration,
            method_responses=[
                apigateway.MethodResponse(
                    status_code="200",
                    response_parameters={
                        "method.response.header.Access-Control-Allow-Headers": True,
                        "method.response.header.Access-Control-Allow-Methods": True,
                        "method.response.header.Access-Control-Allow-Origin": True,
                        "method.response.header.X-Requested-With": True
                    }
                )
            ]
        )

        # Create WebSocket API Gateway using API Gateway v2
        websocket_api = apigatewayv2.WebSocketApi(
            self, "GenAIFoundryWebSocketAPI"+name_key,
            api_name="GenAIFoundry_ws"+name_key
        )

        # Add routes to the WebSocket API
        websocket_api.add_route(
            "$connect",
            integration=apigatewayv2_integrations.WebSocketLambdaIntegration(
                "ConnectIntegration",
                websocket_lambda_function
            )
        )

        websocket_api.add_route(
            "$disconnect",
            integration=apigatewayv2_integrations.WebSocketLambdaIntegration(
                "DisconnectIntegration",
                websocket_lambda_function
            )
        )

        websocket_api.add_route(
            "$default",
            integration=apigatewayv2_integrations.WebSocketLambdaIntegration(
                "DefaultIntegration",
                websocket_lambda_function
            )
        )

        # Create WebSocket Stage
        websocket_stage = apigatewayv2.WebSocketStage(
            self, "WebSocketStage",
            web_socket_api=websocket_api,
            stage_name="production",
            auto_deploy=True
        )

        # Update WebSocket Lambda environment with the endpoint URL and region
        websocket_url = websocket_stage.url.replace('wss://', 'https://')
        websocket_lambda_function.add_environment("WEBSOCKET_ENDPOINT", websocket_url)
        websocket_lambda_function.add_environment("WEBSOCKET_REGION", self.region)
        
        # Add dynamic RDS database credentials to WebSocket Lambda function
        websocket_lambda_function.add_environment("RDS_SECRET_NAME", f"rds-credentials-{rds_name_key}")
        websocket_lambda_function.add_environment("RDS_SECRET_ARN", db_instance.secret.secret_arn if db_instance.secret else "")
        websocket_lambda_function.add_environment("RDS_ENDPOINT", db_instance.instance_endpoint.hostname)
        websocket_lambda_function.add_environment("db_host", db_instance.instance_endpoint.hostname)
        
        # Add dynamic socket endpoint to WebSocket Lambda function
        websocket_lambda_function.add_environment("SOCKET_ENDPOINT", f"https://{api.rest_api_id}.execute-api.{self.region}.amazonaws.com/dev/")
        
        # Also update the main Lambda function with WebSocket environment variables
        lambda_function.add_environment("WEBSOCKET_ENDPOINT", websocket_url)
        lambda_function.add_environment("WEBSOCKET_REGION", self.region)
        
        # Add dynamic RDS database credentials to main Lambda function
        lambda_function.add_environment("RDS_SECRET_NAME", f"rds-credentials-{rds_name_key}")
        lambda_function.add_environment("RDS_SECRET_ARN", db_instance.secret.secret_arn if db_instance.secret else "")
        lambda_function.add_environment("RDS_ENDPOINT", db_instance.instance_endpoint.hostname)
        lambda_function.add_environment("db_host", db_instance.instance_endpoint.hostname)
        
        # Add dynamic socket endpoint to main Lambda function
        lambda_function.add_environment("socket_endpoint",websocket_url)
        
        # Note: AWS_DEFAULT_REGION and AWS_REGION are reserved by Lambda runtime
        # and cannot be set manually. They are automatically set by AWS.

        #front end ec2 instance 
        ec2_instance_front = ec2.Instance(
            self, "MyEC2InstanceFront",
            role=ec2_role,
            instance_type=ec2.InstanceType.of(
                ec2.InstanceClass.T3,
                ec2.InstanceSize.SMALL
            ),
            machine_image=ec2.MachineImage.latest_amazon_linux2023(),
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PUBLIC
            ),
            security_group=ec2_security_group,
            key_pair=key_pair,
            user_data=ec2.UserData.for_linux(),
            block_devices=[
                ec2.BlockDevice(
                    device_name="/dev/sda1",  # Root volume device name for Ubuntu
                    volume=ec2.BlockDeviceVolume.ebs(
                        volume_size=300,  # Size in GB
                        volume_type=ec2.EbsDeviceVolumeType.GP3,  # GP3 is cost-effective and performant
                        delete_on_termination=True,  # Delete when instance terminates
                        encrypted=True  # Optional: encrypt the volume
                    )
                )
            ]
        )
        # Set the environment variables that will be passed to the EC2 instance
        rest_api_name = f"genaifoundry-api{name_key}"
        websocket_api_name = f"GenAIFoundry_ws{name_key}"
        transcribe_api_name = f"coaching_assist_voiceops{name_key}"
        bucket_name = frontend_bucket_name
        region = self.region

        # Alternative approach: Use hardcoded API IDs or skip API Gateway lookup
        ec2_instance_front.add_user_data(
      "#!/bin/bash",
            "",
            "set -e  # Exit on any error",
            "",
            "echo \"🚀 Starting React deployment from S3...\"",
            "",
            "# Set environment variables from CDK",
            f"export REST_API_NAME=\"{rest_api_name}\"",
            f"export WEBSOCKET_API_NAME=\"{websocket_api_name}\"",
            f"export TRANSCRIBE_API_NAME=\"{transcribe_api_name}\"",
            f"export BUCKET_NAME=\"{bucket_name}\"",
            f"export REGION=\"{region}\"",
            "",
            "# Helper function to check if a command exists",
            "command_exists() {",
            "    command -v \"$1\" &> /dev/null",
            "}",
            "",
            "# Check for required environment variables",
            "check_required_env_vars() {",
            "    local missing_vars=()",
            "   ",
            "    if [[ -z \"${REST_API_NAME:-}\" ]]; then",
            "        missing_vars+=(\"REST_API_NAME\")",
            "    fi",
            "   ",
            "    if [[ -z \"${WEBSOCKET_API_NAME:-}\" ]]; then",
            "        missing_vars+=(\"WEBSOCKET_API_NAME\")",
            "    fi",
            "   ",
            "    if [[ -z \"${TRANSCRIBE_API_NAME:-}\" ]]; then",
            "        missing_vars+=(\"TRANSCRIBE_API_NAME\")",
            "    fi",
            "   ",
            "    if [[ -z \"${BUCKET_NAME:-}\" ]]; then",
            "        missing_vars+=(\"BUCKET_NAME\")",
            "    fi",
            "   ",
            "    if [[ -z \"${REGION:-}\" ]]; then",
            "        missing_vars+=(\"REGION\")",
            "    fi",
            "   ",
            "    if [[ ${#missing_vars[@]} -gt 0 ]]; then",
            "        echo \"❌ Error: The following required environment variables are not set:\"",
            "        printf '   - %s\\n' \"${missing_vars[@]}\"",
            "        echo \"\"",
            "        echo \"Please export these variables before running the script:\"",
            "        echo \"  export REST_API_NAME=\\\"your-rest-api-name\\\"\"",
            "        echo \"  export WEBSOCKET_API_NAME=\\\"your-websocket-api-name\\\"\"",
            "        echo \"  export TRANSCRIBE_API_NAME=\\\"your-transcribe-api-name\\\"\"",
            "        echo \"  export BUCKET_NAME=\\\"your-s3-bucket-name\\\"\"",
            "        echo \"  export REGION=\\\"your-aws-region\\\"\"",
            "        exit 1",
            "    fi",
            "}",
            "",
            "# Check required environment variables",
            "echo \"🔍 Checking required environment variables...\"",
            "check_required_env_vars",
            "",
            "echo \"✅ All required environment variables are set:\"",
            "echo \"  REST_API_NAME:        ${REST_API_NAME}\"",
            "echo \"  WEBSOCKET_API_NAME:   ${WEBSOCKET_API_NAME}\"",
            "echo \"  TRANSCRIBE_API_NAME:  ${TRANSCRIBE_API_NAME}\"",
            "echo \"  BUCKET_NAME:          ${BUCKET_NAME}\"",
            "echo \"  REGION:               ${REGION}\"",
            "",
            "echo \"🔧 Checking and installing prerequisites...\"",
            "",
            "# Install unzip if not present",
            "if ! command_exists unzip; then",
            "    echo \"📦 Installing unzip...\"",
            "    sudo yum install -y unzip --allowerasing",
            "else",
            "    echo \"✅ unzip already installed\"",
            "fi",
            "",
            "# Install curl if not present",
            "if ! command_exists curl; then",
            "    echo \"📦 Installing curl...\"",
            "    sudo yum install -y curl --allowerasing",
            "else",
            "    echo \"✅ curl already installed\"",
            "fi",
            "",
            "# Install Node.js and npm if not present",
            "if ! command_exists node || ! command_exists npm; then",
            "    echo \"📦 Installing Node.js and npm...\"",
            "    curl -fsSL https://rpm.nodesource.com/setup_18.x | sudo bash -",
            "    sudo yum install -y nodejs --allowerasing",
            "else",
            "    echo \"✅ Node.js and npm already installed\"",
            "fi",
            "",
            "# Install AWS CLI v2 if not present",
            "if ! command_exists aws; then",
            "    echo \"📦 Installing AWS CLI v2...\"",
            "    curl \"https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip\" -o \"awscliv2.zip\"",
            "    unzip awscliv2.zip",
            "    sudo ./aws/install",
            "    rm -rf aws awscliv2.zip",
            "else",
            "    echo \"✅ AWS CLI already installed\"",
            "fi",
            "",
            "# Set variables",
            "WORK_DIR=~/react-app",
            "ZIP_FILE=\"src.zip\"",
            "S3_SOURCE_PATH=\"s3://${BUCKET_NAME}/${ZIP_FILE}\"",
            "",
            "echo \"📁 Creating work directory: $WORK_DIR\"",
            "mkdir -p \"$WORK_DIR\"",
            "cd \"$WORK_DIR\"",
            "",
            "echo \"📥 Downloading $ZIP_FILE from S3...\"",
            "aws s3 cp \"$S3_SOURCE_PATH\" . --region \"$REGION\"",
            "",
            "echo \"📂 Unzipping $ZIP_FILE...\"",
            "unzip -o \"$ZIP_FILE\"",
            "rm \"$ZIP_FILE\"",
            "",
            "# 📦 Install dependencies",
            "echo \"📦 Running npm install...\"",
            "npm install",
            "",
            "# 🌐 Extract API Gateway IDs",
            "echo \"🔍 Fetching API Gateway IDs...\"",
            "",
            "# REST APIs (API Gateway v1)",
            "get_rest_api_id_by_name() {",
            "    aws apigateway get-rest-apis \\",
            "      --region \"$REGION\" \\",
            "      --query \"items[?name=='$1'].id\" \\",
            "      --output text",
            "}",
            "",
            "# WebSocket APIs (API Gateway v2)",
            "get_ws_api_id_by_name() {",
            "    aws apigatewayv2 get-apis \\",
            "      --region \"$REGION\" \\",
            "      --query \"Items[?Name=='$1'].ApiId\" \\",
            "      --output text",
            "}",
            "",
            "API_ID_REST=$(get_rest_api_id_by_name \"$REST_API_NAME\")",
            "API_ID_WS=$(get_ws_api_id_by_name \"$WEBSOCKET_API_NAME\")",
            "API_ID_TRANSCRIBE=$(get_rest_api_id_by_name \"$TRANSCRIBE_API_NAME\")",
            "",
            "# Validate that API IDs were found",
            "if [[ -z \"$API_ID_REST\" ]]; then",
            "    echo \"❌ Error: Could not find REST API with name '$REST_API_NAME'\"",
            "    exit 1",
            "fi",
            "",
            "if [[ -z \"$API_ID_WS\" ]]; then",
            "    echo \"❌ Error: Could not find WebSocket API with name '$WEBSOCKET_API_NAME'\"",
            "    exit 1",
            "fi",
            "",
            "if [[ -z \"$API_ID_TRANSCRIBE\" ]]; then",
            "    echo \"❌ Error: Could not find Transcribe API with name '$TRANSCRIBE_API_NAME'\"",
            "    exit 1",
            "fi",
            "",
            "# Debug logging",
            "echo \"✅ Retrieved API IDs:\"",
            "echo \"  REST API (chat):      $API_ID_REST (from $REST_API_NAME)\"",
            "echo \"  WebSocket API:        $API_ID_WS (from $WEBSOCKET_API_NAME)\"",
            "echo \"  Transcribe API:       $API_ID_TRANSCRIBE (from $TRANSCRIBE_API_NAME)\"",
            "",
            "# Construct URLs",
            "VITE_API_BASE_URL=\"https://${API_ID_REST}.execute-api.${REGION}.amazonaws.com/dev/chat_api\"",
            "VITE_WEBSOCKET_URL=\"wss://${API_ID_WS}.execute-api.${REGION}.amazonaws.com/production/\"",
            "VITE_WEBSOCKET_URL_VOICEOPS=\"https://${API_ID_WS}.execute-api.${REGION}.amazonaws.com/production/\"",
            "VITE_TRANSCRIBE_API_URL=\"https://${API_ID_TRANSCRIBE}.execute-api.${REGION}.amazonaws.com/dev/transcribe\"",
            "",
            "# 📄 Update .env file",
            "ENV_FILE=\".env\"",
            "echo \"🛠 Updating environment variables in $ENV_FILE...\"",
            "",
            "update_env_var() {",
            "    local key=\"$1\"",
            "    local value=\"$2\"",
            "    if grep -q \"^$key=\" \"$ENV_FILE\"; then",
            "        sed -i \"s|^$key=.*|$key=$value|\" \"$ENV_FILE\"",
            "    else",
            "        echo \"$key=$value\" >> \"$ENV_FILE\"",
            "    fi",
            "}",
            "",
            "update_env_var \"VITE_API_BASE_URL\" \"$VITE_API_BASE_URL\"",
            "update_env_var \"VITE_WEBSOCKET_URL\" \"$VITE_WEBSOCKET_URL\"",
            "update_env_var \"VITE_WEBSOCKET_URL_VOICEOPS\" \"$VITE_WEBSOCKET_URL_VOICEOPS\"",
            "update_env_var \"VITE_TRANSCRIBE_API_URL\" \"$VITE_TRANSCRIBE_API_URL\"",
            "",
            "echo \"✅ .env updated. Current values:\"",
            "grep -E \"VITE_API_BASE_URL|VITE_WEBSOCKET_URL|VITE_WEBSOCKET_URL_VOICEOPS|VITE_TRANSCRIBE_API_URL\" \"$ENV_FILE\"",
            "",
            "# 🚧 Build the app",
            "echo \"⚙️ Running npm run build...\"",
            "npm run build",
            "",
            "# ☁️ Clean and upload to S3 bucket root",
            "echo \"🧹 Clearing existing files in s3://${BUCKET_NAME}/ ...\"",
            "aws s3 rm \"s3://${BUCKET_NAME}/\" --recursive --region \"$REGION\"",
            "echo \"☁️ Uploading dist/ contents to s3://${BUCKET_NAME}/ ...\"",
            "aws s3 cp dist/ \"s3://${BUCKET_NAME}/\" --recursive --region \"$REGION\"",
            "echo \"✅ Done! React app built and uploaded to s3://${BUCKET_NAME}/\"",
            "TOKEN=$(curl -s -X PUT \"http://169.254.169.254/latest/api/token\" -H \"X-aws-ec2-metadata-token-ttl-seconds: 21600\")",
            "INSTANCE_ID=$(curl -s -H \"X-aws-ec2-metadata-token: $TOKEN\" http://169.254.169.254/latest/meta-data/instance-id)",
            "aws ec2 terminate-instances --instance-ids \"$INSTANCE_ID\" --region \"$REGION\""
            
           
        )

        # Outputs
        CfnOutput(
            self, "VPCId",
            value=vpc.vpc_id,
            description="VPC ID"
        )
        CfnOutput(
            self, "LambdaFunctionARN",
            value=lambda_function.function_arn,
            description="ARN of the Lambda function"
        )
        CfnOutput(
            self, "LambdaFunctionName",
            value=lambda_function.function_name,
            description="Name of the Lambda function"
        )
        CfnOutput(
            self, "APIGatewayURL",
            value=api.url,
            description="API Gateway URL"
        )

        CfnOutput(
            self, "APIGatewayID",
            value=api.rest_api_id,
            description="API Gateway ID"
        )

        CfnOutput(
            self, "WebSocketAPIGatewayURL",
            value=websocket_stage.url,
            description="WebSocket API Gateway URL"
        )

        CfnOutput(
            self, "WebSocketAPIGatewayID",
            value=websocket_api.api_id,
            description="WebSocket API Gateway ID"
        )

        CfnOutput(
            self, "WebSocketLambdaFunctionName",
            value=websocket_lambda_function.function_name,
            description="WebSocket Lambda Function Name"
        )

        CfnOutput(
            self, "WebSocketRegion",
            value=self.region,
            description="WebSocket API Region"
        )

        CfnOutput(
            self, "WebSocketEndpointHTTPS",
            value=websocket_url,
            description="WebSocket API HTTPS Endpoint (for Lambda)"
        )

        CfnOutput(
            self, "CoachingAPIGatewayURL",
            value=coaching_api.url,
            description="Coaching Assist Voiceops API Gateway URL"
        )
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


        CfnOutput(
            self, "CoachingAPIGatewayID",
            value=coaching_api.rest_api_id,
            description="Coaching Assist Voiceops API Gateway ID"
        )

        # Add outputs for the created layers
        for layer_name, layer_info in layer_uploader.layers.items():
            
            CfnOutput(
                self, f"{layer_name.capitalize()}LayerARN",
                value=layer_info["layer"].layer_version_arn,
                description=f"ARN of the {layer_name} Lambda layer"
            )



        CfnOutput(
            self, "InstancePublicIP",
            value=ec2_instance.instance_public_ip,
            description="EC2 Instance Public IP"
        )

        CfnOutput(
            self, "DatabaseEndpoint",
            value=db_instance.instance_endpoint.hostname,
            description="RDS Database Endpoint"
        )

        CfnOutput(
            self, "DatabaseSecretName",
            value=secret_name,
            description="Secret name for database credentials"
        )

        CfnOutput(
            self, "DatabaseSecretArn",
            value=db_instance.secret.secret_arn if db_instance.secret else "No secret created",
            description="ARN of the secret containing database credentials"
        )

        CfnOutput(
            self,
            "KnowledgeBaseBucketName",
            value=bucket.bucket_name,
            description="Name of the S3 bucket for knowledge base data"
        )
        
        CfnOutput(
            self,
            "FrontendBucketName",
            value=frontend_bucket.bucket_name,
            description="Name of the S3 bucket for frontend static files"
        )
        
        CfnOutput(
            self,
            "FrontendBucketWebsiteURL",
            value=frontend_bucket.bucket_website_url,
            description="Website URL of the frontend S3 bucket"
        )
          # EC2 Database Initialization Outputs
        CfnOutput(
            self, "EC2InstanceId",
            value=ec2_instance.instance_id,
            description="EC2 Instance ID for Database Initialization"
        )


        CfnOutput(
            self, "EC2SecurityGroupId",
            value=ec2_security_group.security_group_id,
            description="EC2 Security Group ID"
        )

        CfnOutput(
            self, "ResourceNameKey",
            value=name_key,
            description="Random resource name key used for this deployment"
        )

        CfnOutput(
            self, "KeyPairName",
            value=key_pair.key_pair_name,
            description="Key Pair Name for EC2 Instance"
        )
        CfnOutput(
            self, "PrivateKeyCommand",
            value=f"aws ssm get-parameter --name /ec2/keypair/{key_pair.key_pair_id} --with-decryption --query Parameter.Value --output text",
            description="Command to retrieve private key"
        )
        
        # Create CloudFront Distribution for frontend S3 bucket
        # Create S3 Origin using the new S3BucketOrigin (not deprecated)
        s3_origin = origins.S3BucketOrigin(
            frontend_bucket,
            origin_path=""  # Empty path means root of bucket
        )
 
        # Create CloudFront Distribution
        distribution = cloudfront.Distribution(
            self, "GenAIFoundryDistribution",
            default_behavior=cloudfront.BehaviorOptions(
                origin=s3_origin,
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                cache_policy=cloudfront.CachePolicy.CACHING_OPTIMIZED,
                origin_request_policy=None,
                response_headers_policy=None
            ),
            # General settings matching the console configuration
            default_root_object="index.html",
            price_class=cloudfront.PriceClass.PRICE_CLASS_ALL,
            http_version=cloudfront.HttpVersion.HTTP2,  # Fixed: use HTTP2 instead of HTTP2_AND_HTTP1_1
            enable_logging=False,  # Standard logging: Off
            enable_ipv6=True,
            # Error pages configuration matching the console
            error_responses=[
                cloudfront.ErrorResponse(
                    http_status=403,
                    response_http_status=200,
                    response_page_path="/index.html",
                    ttl=Duration.seconds(10)
                ),
                cloudfront.ErrorResponse(
                    http_status=404,
                    response_http_status=200,
                    response_page_path="/index.html",
                    ttl=Duration.seconds(10)
                )
            ]
        )

        # Switch to Origin Access Control (OAC) so S3 policy can use CloudFront service principal + AWS:SourceArn
        oac = cloudfront.CfnOriginAccessControl(
            self,
            "FrontendOAC",
            origin_access_control_config=cloudfront.CfnOriginAccessControl.OriginAccessControlConfigProperty(
                name=f"{name_key}-frontend-oac",
                description="OAC for frontend S3 origin",
                origin_access_control_origin_type="s3",
                signing_behavior="always",
                signing_protocol="sigv4",
            ),
        )
        cfn_dist = distribution.node.default_child  # type: ignore
        # Attach OAC to first origin and remove OAI reference
        cfn_dist.add_property_override(
            "DistributionConfig.Origins.0.OriginAccessControlId", oac.attr_id
        )
        cfn_dist.add_property_deletion_override(
            "DistributionConfig.Origins.0.S3OriginConfig.OriginAccessIdentity"
        )
        cfn_dist.add_dependency(oac)

        # Explicit CloudFront invalidation via AWS SDK (since L1 Invalidations are not available in this CDK version)
        invalidation = cr.AwsCustomResource(
            self,
            "GenAIFoundryInvalidation",
            on_update=cr.AwsSdkCall(
                service="CloudFront",
                action="createInvalidation",
                parameters={
                    "DistributionId": distribution.distribution_id,
                    "InvalidationBatch": {
                        "CallerReference": str(int(time.time())),
                        "Paths": {"Quantity": 1, "Items": ["/*"]},
                    },
                },
                physical_resource_id=cr.PhysicalResourceId.of(
                    f"InvalidateFrontend-{int(time.time())}"
                ),
            ),
            policy=cr.AwsCustomResourcePolicy.from_statements([
                iam.PolicyStatement(
                    actions=[
                        "cloudfront:CreateInvalidation",
                        "cloudfront:GetInvalidation",
                        "cloudfront:ListInvalidations",
                    ],
                    resources=["*"],
                )
            ]),
        )
        # Ensure invalidation runs after upload and distribution exist
        invalidation.node.add_dependency(frontend_deploy)
        invalidation.node.add_dependency(distribution)
        # Replace frontend bucket policy with the previously working policy
        # 1) Grant required S3 actions to account root
        frontend_bucket.add_to_resource_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                principals=[
                    # Allow the entire account (root) to perform required actions
                    iam.ArnPrincipal(f"arn:aws:iam::{self.account}:root"),
                ],
                actions=[
                    "s3:DeleteObject*",
                    "s3:GetBucket*",
                    "s3:GetObject",
                    "s3:List*",
                    "s3:PutBucketPolicy"
                ],
                resources=[
                    frontend_bucket.bucket_arn,
                    f"{frontend_bucket.bucket_arn}/*"
                ]
            )
        )

        # 2) Allow CloudFront access to objects in the frontend bucket
        frontend_bucket.add_to_resource_policy(
            iam.PolicyStatement(
                sid="AllowCloudFrontAccess",
                effect=iam.Effect.ALLOW,
                principals=[iam.ServicePrincipal("cloudfront.amazonaws.com")],
                actions=["s3:GetObject"],
                resources=[f"{frontend_bucket.bucket_arn}/*"],
                conditions={
                    "StringEquals": {
                        "AWS:SourceArn": distribution.distribution_arn
                    }
                }
            )
        )

        # Add bucket policy to main bucket to allow CloudFront access only
        bucket.add_to_resource_policy(
            iam.PolicyStatement(
                sid="AllowCloudFrontAccessOnly",
                effect=iam.Effect.ALLOW,
                principals=[iam.ServicePrincipal("cloudfront.amazonaws.com")],
                actions=["s3:GetObject"],
                resources=[f"{bucket.bucket_arn}/*"],
                conditions={
                    "StringEquals": {
                        "AWS:SourceArn": distribution.distribution_arn
                    }
                }
            )
        )
 
        # Outputs for easy access to distribution information
        CfnOutput(
            self, "DistributionDomainName",
            value=distribution.distribution_domain_name,
            description="CloudFront Distribution Domain Name"
        )
       
        CfnOutput(
            self, "DistributionId",
            value=distribution.distribution_id,
            description="CloudFront Distribution ID"
        )
       
        CfnOutput(
            self, "DistributionArn",
            value=distribution.distribution_arn,
            description="CloudFront Distribution ARN"
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
