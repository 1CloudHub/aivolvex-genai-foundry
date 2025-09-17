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
    aws_opensearchserverless as aoss,
    aws_s3_notifications as s3n,
    aws_rds as rds,
    aws_apigateway as apigateway,
    aws_apigatewayv2 as apigatewayv2,
    aws_apigatewayv2_integrations as apigatewayv2_integrations,
    # aws_lambda_event_sources as lambda_event_sources,  # unused
    aws_opensearchservice as opensearch,
    Size
)
import aws_cdk as cdk
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
s3_name = "genai-foundry-test"
print(f"Resource name: {name_key}")
print(f"Lambda-safe name: {lambda_name_key}")
print(f"RDS-safe name: {rds_name_key}")


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


class RetailCdkStack(Stack):

    def __init__(self, scope: Construct, construct_id: str, stack_selection: str = "unknown", **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
        
        # Store the selection for use throughout the stack
        self.stack_selection = stack_selection
        print(f"🏗️ Building Retail Stack with selection: {self.stack_selection}")

        # Create VPC
        vpc = ec2.Vpc(
            self, name_key,
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

        s3_bucket_name = "genaifoundry"+name_key
        frontend_bucket_name = "genaifoundry-front"+name_key

        # Main bucket for knowledge base data
        bucket = s3.Bucket(
            self, 
            "KnowledgeBaseBucket",
            bucket_name=s3_bucket_name,
            versioned=True,
            removal_policy=RemovalPolicy.DESTROY,  # For development only
            auto_delete_objects=True,  # For development only
            lifecycle_rules=[
                s3.LifecycleRule(
                    id="VideoCleanup",
                    expiration=Duration.days(31),  # Delete videos after 30 days
                    transitions=[
                        s3.Transition(
                            storage_class=s3.StorageClass.INFREQUENT_ACCESS,
                            transition_after=Duration.days(30)
                        )
                    ],
                    # Apply only to video files
                    noncurrent_version_expiration=Duration.days(30),
                    prefix="videos/"
                )
            ]
        )

         # Frontend bucket for static website hosting (public)
        frontend_bucket = s3.Bucket(
            self, 
            "FrontendBucket",
            bucket_name=frontend_bucket_name,
            versioned=True,
            removal_policy=RemovalPolicy.DESTROY,  # For development only
            auto_delete_objects=True,  # For development only
            # Note: Website hosting is disabled to use CloudFront OAC
            # public_read_access=False,
            # block_public_access=s3.BlockPublicAccess.BLOCK_ALL
        )

        frontend_deploy = s3deploy.BucketDeployment(
            self,
            "DeployFrontendFolder",
            sources=[s3deploy.Source.asset("genaifoundry-front")],  # Path to your frontend folder
            destination_bucket=frontend_bucket,
            destination_key_prefix="",  # Upload to root of bucket
        )




        # Upload retail knowledge base folder
        retail_kb_deploy = s3deploy.BucketDeployment(
            self,
            "DeployRetailKnowledgeBase",
            sources=[s3deploy.Source.asset("genaifoundy-usecases/retail")],  # Path to retail KB folder
            destination_bucket=bucket,
            destination_key_prefix="kb/retail/",  # Prefix for retail KB files
        )



        model_arn = f"arn:aws:bedrock:{self.region}::foundation-model/amazon.titan-embed-text-v2:0"

        self.data_bucket = s3.Bucket.from_bucket_name(
            self, 
            "ExistingDataBucket",
            bucket_name=s3_bucket_name
        )


        video_deploy_virtualtryon = s3deploy.BucketDeployment(
            self,
            "DeployVirtualTryOnAssets",
            sources=[s3deploy.Source.asset("assets/virtualtryon")],
            destination_bucket=bucket,
            destination_key_prefix="virtualtryon/",
        )

        video_deploy_visualproductsearch = s3deploy.BucketDeployment(
            self,
            "DeployVisualProductSearchAssets",
            sources=[s3deploy.Source.asset("assets/visualproductsearch")],
            destination_bucket=bucket,
            destination_key_prefix="visualproductsearch/",
        )


        retail_collection_name = f"retail-{name_key}-col"
        retail_collection = aoss.CfnCollection(
            self, "RetailKBCollection",
            name=retail_collection_name,
            type="VECTORSEARCH"
        )

        retail_encryption_policy = aoss.CfnSecurityPolicy(
            self, "RetailKBSecurityPolicy",
            name=f"retail-{name_key}-encrypt",
            type="encryption",
            policy=json.dumps({
                "Rules": [{
                    "ResourceType": "collection",
                    "Resource": [f"collection/{retail_collection_name}"]
                }],
                "AWSOwnedKey": True
            })
        )

        retail_network_policy = aoss.CfnSecurityPolicy(
            self, "RetailKBNetworkPolicy",
            name=f"retail-{name_key}-network",
            type="network",
            policy=json.dumps([{
                "Rules": [{
                    "ResourceType": "collection",
                    "Resource": [f"collection/{retail_collection_name}"]
                }],
                "AllowFromPublic": True
            }])
        )

        retail_collection.add_dependency(retail_encryption_policy)
        retail_collection.add_dependency(retail_network_policy)
        retail_collection.node.add_dependency(retail_kb_deploy)


         # 1) Create the serverless collection
        visual_search_collection = aoss.CfnCollection(
            self,
            "VisualProductSearchCollection",
            name=f"visualproductsearch-{name_key}",
            type="VECTORSEARCH",  # Matches your screenshot
            description="Vector search collection for visual product search"
        )

        # 2) Encryption policy (AWS-owned key)
        visual_search_encryption = aoss.CfnSecurityPolicy(
            self,
            "VisualSearchEncryptionPolicy",
            name=f"vps-enc-{name_key}",  # unique per stack
            type="encryption",
            policy=json.dumps({
                "Rules": [
                    {
                        "ResourceType": "collection",
                        "Resource": [f"collection/visualproductsearch-{name_key}"]
                    }
                ],
                "AWSOwnedKey": True
            })
        )


        # 3) Network policy (allow from public)
        visual_search_network_policy = aoss.CfnSecurityPolicy(
            self,
            "VisualSearchNetworkPolicy",
            name=f"vps-net-{name_key}",  # unique per stack
            type="network",
            policy=json.dumps([{
                "Rules": [{
                    "ResourceType": "collection",
                    "Resource": [f"collection/visualproductsearch-{name_key}"]
                }],
                "AllowFromPublic": True
            }])
        )
        # Add dependencies
        visual_search_collection.add_dependency(visual_search_encryption)
        visual_search_collection.add_dependency(visual_search_network_policy)
        

        bedrock_kb_role = iam.Role(

            self, "BedrockKBRole",
            assumed_by=iam.ServicePrincipal("bedrock.amazonaws.com"),
            description="Role for Bedrock Knowledge Base to access S3 and OpenSearch"
        )
        
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


        lambda_role = iam.Role(
            self, "IndexCreatorLambdaRole",
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
                                f"arn:aws:aoss:{self.region}:{self.account}:collection/{retail_collection_name}",
                                f"arn:aws:aoss:{self.region}:{self.account}:index/{retail_collection_name}/*",
                                f"arn:aws:aoss:{self.region}:{self.account}:collection/visualproductsearch-{name_key}",
                                f"arn:aws:aoss:{self.region}:{self.account}:index/visualproductsearchmod-{name_key}"
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

        # Add this additional policy statement to grant search permissions
        lambda_role.add_to_policy(iam.PolicyStatement(
            sid="OpenSearchSearchAccess",
            effect=iam.Effect.ALLOW,
            actions=[
                "aoss:APIAccessAll",
                "aoss:ReadDocument",
                "aoss:DescribeCollectionItems",
                "aoss:DescribeIndex"
            ],
            resources=[
                f"arn:aws:aoss:{self.region}:{self.account}:collection/*",
                f"arn:aws:aoss:{self.region}:{self.account}:index/*"
            ]
        ))

       
        
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
        # Get current AWS identity dynamically
        try:
            import boto3
            sts = boto3.client('sts')
            current_identity = sts.get_caller_identity()
            current_user_arn = current_identity.get('Arn', '')
            print(f"Current AWS Identity: {current_user_arn}")
        except Exception as e:
            print(f"Warning: Could not get current AWS identity: {e}")
            current_user_arn = f"arn:aws:iam::{self.account}:root"  # Fallback to account root
        
        retail_data_access_policy = aoss.CfnAccessPolicy(
            self, "RetailKBDataAccessPolicy",
            name=f"retail-{name_key}-access",
            type="data",
            policy=json.dumps([{
                "Rules": [{
                    "ResourceType": "collection",
                    "Resource": [f"collection/{retail_collection_name}"],
                    "Permission": [
                        "aoss:CreateCollectionItems",
                        "aoss:DeleteCollectionItems",
                        "aoss:UpdateCollectionItems",
                        "aoss:DescribeCollectionItems"
                    ]
                }, {
                    "ResourceType": "index",
                    "Resource": [f"index/{retail_collection_name}/*"],
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
                    f"arn:aws:iam::{self.account}:role/{auto_sync_lambda_role.role_name}",
                    current_user_arn
                ],
                "Description": f"Data access policy for {retail_collection_name}"
            }])
        )
        # Add dependencies for data access policy
        retail_collection.add_dependency(retail_data_access_policy)


         # 4) Data access policy for collection and index permissions (Rule 1)
        visual_search_collection_index_policy = aoss.CfnAccessPolicy(
            self,
            "VisualSearchCollectionIndexPolicy",
            name=f"vs-col-idx-policy-{name_key}",
            type="data",
            policy=json.dumps([{
                "Rules": [
                    {
                        "ResourceType": "collection",
                        "Resource": [f"collection/visualproductsearch-{name_key}"],
                        "Permission": [
                            "aoss:CreateCollectionItems",
                            "aoss:DeleteCollectionItems",
                            "aoss:UpdateCollectionItems",
                            "aoss:DescribeCollectionItems"
                        ]
                    },
                    {
                        "ResourceType": "index",
                        "Resource": [f"index/visualproductsearch-{name_key}/visualproductsearchmod-{name_key}"],
                        "Permission": [
                            "aoss:CreateIndex",
                            "aoss:DeleteIndex",
                            "aoss:UpdateIndex",
                            "aoss:DescribeIndex",
                            "aoss:ReadDocument",
                            "aoss:WriteDocument"
                        ]
                    }
                ],
                "Principal": [
                    f"arn:aws:iam::{self.account}:role/{bedrock_kb_role.role_name}",
                    f"arn:aws:iam::{self.account}:role/{lambda_role.role_name}",
                    f"arn:aws:iam::{self.account}:role/{auto_sync_lambda_role.role_name}",
                    current_user_arn
                ]
            }])
        )

        # 5) Data access policy for model permissions (Rule 2)
        visual_search_model_policy = aoss.CfnAccessPolicy(
            self,
            "VisualSearchModelPolicy",
            name=f"vs-model-policy-{name_key}",
            type="data",
            policy=json.dumps([{
                "Rules": [
                    {
                        "ResourceType": "model",
                        "Resource": [f"model/visualproductsearch-{name_key}/*"],
                        "Permission": [
                            "aoss:CreateMLResource",
                            "aoss:DeleteMLResource",
                            "aoss:UpdateMLResource",
                            "aoss:DescribeMLResource",
                            "aoss:ExecuteMLResource"
                        ]
                    }
                ],
                "Principal": [
                    f"arn:aws:iam::{self.account}:role/{lambda_role.role_name}",
                    current_user_arn,
                    f"arn:aws:iam::{self.account}:role/{auto_sync_lambda_role.role_name}",
                    f"arn:aws:iam::{self.account}:role/{bedrock_kb_role.role_name}"
                ]
            }])
        )

        # Add dependencies for both policies
        visual_search_collection.add_dependency(visual_search_collection_index_policy)
        visual_search_collection.add_dependency(visual_search_model_policy)

        # Create Lambda function to create OpenSearch indices
        # Get the current directory where this file is located
        current_dir = Path(__file__).parent
        # Point to the layers and lambda directories in the project root (one level up)
        layers_dir = current_dir.parent / "layers"
        lambda_dir = current_dir.parent / "lambda"


        retail_index_name = f"retail-{name_key}-idx"

        # Create RDS PostgreSQL instance
        db_instance = rds.DatabaseInstance(
            self, "MyPostgreSQLDB",
            engine=rds.DatabaseInstanceEngine.postgres(
                version=rds.PostgresEngineVersion.VER_16_3
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
                iam.ManagedPolicy.from_aws_managed_policy_name("AdministratorAccess"),
                iam.ManagedPolicy.from_aws_managed_policy_name("CloudWatchLogsFullAccess")
            ],
                inline_policies={}
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

        # Add dependency for EC2 instance to run after RDS instance
        ec2_instance.node.add_dependency(db_instance)
        secret_name = f"rds-credentials-{rds_name_key}"
        ec2_instance.add_user_data(
            "#!/bin/bash",
            "",
            "set -e  # Exit on any error",
            "",
            "echo \"🚀 Starting database initialization for retail use case...\"",
            "",
            "# Install required packages",
            "sudo apt update -y",
            "sudo apt install -y apache2 awscli jq postgresql-client-14 git",
            "systemctl start apache2",
            "systemctl enable apache2",
            "",
            "# Create restoration script",
            "cat << 'EOF' > /home/ubuntu/restore_db.sh",
            "#!/bin/bash",
            "set -e",
            "",
            "export DEBIAN_FRONTEND=noninteractive",
            "echo \"Getting database credentials from Secrets Manager...\"",
            "",
            "# Get database credentials from Secrets Manager",
            f"SECRET_JSON=$(aws secretsmanager get-secret-value --secret-id \"{secret_name}\" --query SecretString --output text --region {self.region})",
            "echo \"$SECRET_JSON\"",
            "",
            "# Extract database connection details",
            "DB_HOST=$(echo \"$SECRET_JSON\" | jq -r .host)",
            "DB_PORT=$(echo \"$SECRET_JSON\" | jq -r .port)",
            "DB_USERNAME=$(echo \"$SECRET_JSON\" | jq -r .username)",
            "DB_PASSWORD=$(echo \"$SECRET_JSON\" | jq -r .password)",
            "DB_NAME=$(echo \"$SECRET_JSON\" | jq -r .dbname)",
            "",
            "# Set environment variables",
            "export DB_HOST=\"$DB_HOST\"",
            "export DB_PORT=\"$DB_PORT\"",
            "export DB_USERNAME=\"$DB_USERNAME\"",
            "export DB_PASSWORD=\"$DB_PASSWORD\"",
            "export DB_NAME=\"$DB_NAME\"",
            f"export REGION=\"{self.region}\"",
            f"export STACK_SELECTION=\"{self.stack_selection}\"",
            "",
            "echo \"Database connection details:\"",
            "echo \"Host: $DB_HOST\"",
            "echo \"Port: $DB_PORT\"",
            "echo \"Database: $DB_NAME\"",
            "echo \"Username: $DB_USERNAME\"",
            "",
            "# Test connection",
            "echo \"Testing database connection...\"",
            "export PGPASSWORD=\"$DB_PASSWORD\"",
            "psql -h \"$DB_HOST\" -p \"$DB_PORT\" -U \"$DB_USERNAME\" -d \"$DB_NAME\" -c \"SELECT version();\"",
            "",
            "# Download dump file",
            "echo \"Downloading database dump file...\"",
            "git clone https://github.com/1CloudHub/aivolvex-genai-foundry.git",
            "",
            "# Restore database",
            "echo \"Restoring database from dump file...\"",
            "psql -h \"$DB_HOST\" -p \"$DB_PORT\" -U \"$DB_USERNAME\" -d \"$DB_NAME\" -f ~/aivolvex-genai-foundry/dump-postgres.sql",
            "",
            "# Create additional tables for retail use case",
            "echo \"Creating retail-specific tables...\"",
            "psql -h \"$DB_HOST\" -p \"$DB_PORT\" -U \"$DB_USERNAME\" -d \"$DB_NAME\" << 'SQL_EOF'",
            "CREATE TABLE IF NOT EXISTS genaifoundry.retail_chat_history (",
            "    id SERIAL PRIMARY KEY,",
            "    session_id VARCHAR(255) NOT NULL,",
            "    question TEXT NOT NULL,",
            "    answer TEXT NOT NULL,",
            "    input_tokens INTEGER DEFAULT 0,",
            "    output_tokens INTEGER DEFAULT 0,",
            "    created_on TIMESTAMP DEFAULT CURRENT_TIMESTAMP,",
            "    updated_on TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
            ");",
            "",
            "CREATE TABLE IF NOT EXISTS genaifoundry.vid_gen_link (",
            "    id SERIAL PRIMARY KEY,",
            "    session_id VARCHAR(255) NOT NULL,",
            "    s3_link TEXT NOT NULL,",
            "    created_on TIMESTAMP DEFAULT CURRENT_TIMESTAMP,",
            "    updated_on TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
            ");",
            "",
            "# Create indexes for better performance",
            "CREATE INDEX IF NOT EXISTS idx_retail_chat_session_id ON genaifoundry.retail_chat_history(session_id);",
            "CREATE INDEX IF NOT EXISTS idx_video_gen_session_id ON genaifoundry.vid_gen_link(session_id);",
            "SQL_EOF",
            "",
            "# Verify restoration",
            "echo \"Verifying restoration...\"",
            "psql -h \"$DB_HOST\" -p \"$DB_PORT\" -U \"$DB_USERNAME\" -d \"$DB_NAME\" -c \"\\dn\"",
            "psql -h \"$DB_HOST\" -p \"$DB_PORT\" -U \"$DB_USERNAME\" -d \"$DB_NAME\" -c \"\\dt genaifoundry.*\"",
            "",
            "echo \"Database initialization completed successfully!\"",
            "EOF",
            "",
            "# Set permissions and run database initialization",
            "sudo chmod +x /home/ubuntu/restore_db.sh",
            "sudo chown ubuntu:ubuntu /home/ubuntu/restore_db.sh",
            "",
            "# Wait for RDS to be ready and run initialization",
            "echo \"Waiting for RDS to be ready...\"",
            "sleep 30",
            "",
            "# Run database initialization",
            "echo \"Running database initialization...\"",
            "sudo su - ubuntu -c \"/home/ubuntu/restore_db.sh\" > /var/log/db_init.log 2>&1",
            "",
            "echo \"Database initialization completed! Check /var/log/db_init.log for details.\""
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

        # Add Bedrock model-specific permissions for retail use case
        lambda_role.add_to_policy(iam.PolicyStatement(
            sid="BedrockModelAccess",
            effect=iam.Effect.ALLOW,
            actions=[
                "bedrock:InvokeModel",
                "bedrock:InvokeModelWithResponseStream"
            ],
            resources=[
                "arn:aws:bedrock:*:*:foundation-model/amazon.titan-embed-image-v1:*",
                "arn:aws:bedrock:*:*:foundation-model/amazon.titan-embed-text-v1:*",
                "arn:aws:bedrock:*:*:foundation-model/amazon.nova-canvas-v1:*",
                "arn:aws:bedrock:*:*:foundation-model/amazon.nova-reel-v1:*",
                "arn:aws:bedrock:*:*:foundation-model/us.meta.llama3-3-70b-instruct-v1:*",
                "arn:aws:bedrock:*:*:foundation-model/us.anthropic.claude-3-7-sonnet-20250219-v1:*"
            ]
        ))

        retail_index_creator_function = lambda_.Function(
            self, "RetailIndexCreatorFunction",
            runtime=lambda_.Runtime.PYTHON_3_9,
            handler="index_creator.lambda_handler",
            role=lambda_role,
            layers=[l for l in [boto3_layer, psycopg2_layer, requests_layer, requests_aws4auth_layer, opensearchpy_layer] if l is not None],  # ADD THIS LINE
            timeout=Duration.minutes(10),
            code=lambda_.Code.from_asset(str(lambda_dir)),
            environment={
                "OPENSEARCH_ENDPOINT": retail_collection.attr_collection_endpoint,
                "COLLECTION_NAME": retail_collection_name,
                "INDEX_NAME": retail_index_name
            },
        )

        # Create Lambda function to wait for OpenSearch index readiness
        retail_index_waiter_function = lambda_.Function(
            self, "RetailIndexWaiterFunction",
            runtime=lambda_.Runtime.PYTHON_3_9,
            handler="index_waiter.lambda_handler",
            role=lambda_role,
            layers=[l for l in [boto3_layer, psycopg2_layer, requests_layer, requests_aws4auth_layer, opensearchpy_layer] if l is not None],
            timeout=Duration.minutes(15),  # Longer timeout for waiting
            code=lambda_.Code.from_asset(str(lambda_dir)),
            environment={
                "OPENSEARCH_ENDPOINT": retail_collection.attr_collection_endpoint,
                "COLLECTION_NAME": retail_collection_name,
                "INDEX_NAME": retail_index_name
            },
        )
        # Add dependency to ensure collection and name generation is complete before Lambda runs
        retail_index_creator_function.node.add_dependency(retail_collection)
        
        # Add dependencies for index waiter function
        retail_index_waiter_function.node.add_dependency(retail_collection)
        
        # Create separate providers for each collection
        retail_provider = cr.Provider(
            self, "RetailInitProvider",
            on_event_handler=retail_index_creator_function
        )

        # Create provider for index waiter
        retail_waiter_provider = cr.Provider(
            self, "RetailWaiterProvider",
            on_event_handler=retail_index_waiter_function
        )

        # Add dependency for retail provider to run after retail index creator function
        retail_provider.node.add_dependency(retail_index_creator_function)

        # Create custom resource to create retail index
        retail_index_creator = CustomResource(
            self, "RetailIndexCreator",
            service_token=retail_provider.service_token,
            properties={
                "index_name": retail_index_name,
                "dimension": 1024,
                "method": "hnsw",
                "engine": "faiss",
                "space_type": "l2"
            }
        )

        # Create custom resource to wait for retail index readiness
        retail_index_waiter = CustomResource(
            self, "RetailIndexWaiter",
            service_token=retail_waiter_provider.service_token,
            properties={
                "index_name": retail_index_name,
                "max_retries": 60,  # 5 minutes with 5-second intervals
                "retry_delay": 5    # 5 seconds between retries
            }
        )

        # Add dependency to ensure Lambda function is ready before index creation
        retail_index_creator.node.add_dependency(retail_index_creator_function)

        # Add dependencies for index waiter
        retail_index_waiter.node.add_dependency(retail_index_creator)
        retail_index_waiter.node.add_dependency(retail_index_waiter_function)
        retail_index_waiter.node.add_dependency(retail_waiter_provider)


         # Create Lambda function for visual search index creation
        visual_search_index_creator_function = lambda_.Function(
            self, "VisualSearchIndexCreatorFunction",
            runtime=lambda_.Runtime.PYTHON_3_9,
            handler="visual_product_search_index_creator.lambda_handler",
            role=lambda_role,
            code=lambda_.Code.from_asset(str(lambda_dir)),
            timeout=Duration.minutes(5),
            layers=[l for l in [boto3_layer, psycopg2_layer, requests_layer, requests_aws4auth_layer, opensearchpy_layer] if l is not None],
            environment={
                "OPENSEARCH_ENDPOINT": visual_search_collection.attr_collection_endpoint,
                "COLLECTION_NAME": f"visualproductsearch-{name_key}",
                "INDEX_NAME": f"visualproductsearchmod-{name_key}"
            },
        )
        
        # Add dependency to ensure collection and policies are ready before Lambda runs
        visual_search_index_creator_function.node.add_dependency(visual_search_collection)
        
        # Create provider for visual search index creation
        visual_search_provider = cr.Provider(
            self, "VisualSearchInitProvider",
            on_event_handler=visual_search_index_creator_function
        )

        # Add dependency for visual search provider
        visual_search_provider.node.add_dependency(visual_search_index_creator_function)

        # Create custom resource to create visual search index
        visual_search_index_creator = CustomResource(
            self, "VisualSearchIndexCreator",
            service_token=visual_search_provider.service_token,
            properties={
                "index_name": f"visualproductsearchmod-{name_key}"
            }
        )


        # Grant Lambda access to the RDS secret
        if db_instance.secret:
            db_instance.secret.grant_read(lambda_role)

            


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

        

        # Create resources and methods with CORS enabled
        # /chat_api endpoint
        chat_api_resource = api.root.add_resource("chat_api")
       
        


        retail_kb = self.create_kb(
            f"genaifoundryretail-{name_key}",
            f"s3://{s3_bucket_name}/kb/retail/",
            model_arn,
            bedrock_kb_role.role_arn,
            "kb/retail",
            retail_index_name,
            retail_index_creator_function,
            retail_index_creator,
            retail_provider,
            retail_collection.attr_arn,
            retail_data_access_policy,
            retail_index_waiter,
            retail_index_waiter_function,
            retail_waiter_provider
        )
        # Dependencies are now handled inside the create_kb function
        retail_kb.node.add_dependency(bedrock_kb_role)

        # Add dependency for retail KB deployment
        retail_kb.node.add_dependency(retail_kb_deploy)
        
        # # Add dynamic socket endpoint to main Lambda function
        # lambda_function.add_environment("socket_endpoint",websocket_url)
        
        # Note: AWS_DEFAULT_REGION and AWS_REGION are reserved by Lambda runtime
        # and cannot be set manually. They are automatically set by AWS.

        #front end ec2 instance 

        env_vars = {
            "CHAT_LOG_TABLE": "ce_cexp_logs",
            "KB_ID": retail_kb.attr_knowledge_base_id,  # Retail KB ID
            "RETAIL_KB_ID": retail_kb.attr_knowledge_base_id,
            "chat_history_table": "chat_history",
            "retail_chat_history_table": "retail_chat_history",
            "db_database": rds_name_key,
            "db_host": db_instance.instance_endpoint.hostname,
            "db_port": "5432",
            "db_user": "postgres",
            "model_id": "us.anthropic.claude-3-7-sonnet-20250219-v1:0",
            "prompt_metadata_table": "prompt_metadata",
            "region_used": self.region,
            "region_name": self.region,  # New environment variable for region name
            "schema": "genaifoundry",

            # OpenSearch configuration for visual product search
            "OPENSEARCH_REGION": self.region,
            "OPENSEARCH_HOST": visual_search_collection.attr_collection_endpoint,
            "OPENSEARCH_INDEX": f"visualproductsearchmod-{name_key}",

            # Bedrock model configuration
            "LLAMA3_MODEL_ID": "us.meta.llama3-3-70b-instruct-v1:0",
            "NOVA_MODEL_ID": "amazon.nova-canvas-v1:0",
            "NOVA_REEL_MODEL_ID": "amazon.nova-reel-v1:1",
            "CLAUDE_MODEL_ID": "us.anthropic.claude-3-7-sonnet-20250219-v1:0",

            # S3 configuration
            "S3_BUCKET": s3_bucket_name,
            "S3_REGION": self.region,
            "VIDEO_GENERATION_BUCKET": f"{s3_bucket_name}/videos/",

            "ec2_instance_ip": ec2_instance.instance_public_ip,  # Public IP of the T3 medium instance
            # "socket_endpoint": f"https://{api.rest_api_id}.execute-api.{self.region}.amazonaws.com/dev/",
            "rds_secret_name": f"rds-credentials-{rds_name_key}",
            "rds_secret_arn": db_instance.secret.secret_arn if db_instance.secret else "",
            "rds_endpoint": db_instance.instance_endpoint.hostname,
            "rds_port": str(db_instance.instance_endpoint.port),
            "rds_database": rds_name_key,
            "rds_username": "postgres"
        }

        # Dependencies will be added after Lambda functions are defined

        # Create Lambda function
        lambda_function = lambda_.Function(
            self, "MyLambdaFunction",
            runtime=lambda_.Runtime.PYTHON_3_9,
            handler="retail.lambda_handler",
            code=lambda_.Code.from_asset("lambda_code"),
            function_name=lambda_name_key,
            memory_size=512,  # Increased for image processing
            timeout=Duration.seconds(303),  # 5 minutes 3 seconds
            ephemeral_storage_size=Size.mebibytes(1024),  # Increased for image processing
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS
            ),
            security_groups=[lambda_security_group],
            role=lambda_role,
            layers=[l for l in [boto3_layer, psycopg2_layer, requests_layer, requests_aws4auth_layer, opensearchpy_layer] if l is not None],
            environment=env_vars
        )

        # Add dependency for Lambda function to run after EC2 instance
        lambda_function.node.add_dependency(ec2_instance)



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

        data_ingestion_function = lambda_.Function(
            self, "DataIngestionFunction",
            runtime=lambda_.Runtime.PYTHON_3_9,
            handler="data_ingestion.lambda_handler",
            code=lambda_.Code.from_asset("lambda"),
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS
            ),
            security_groups=[lambda_security_group],
            role=lambda_role,
            function_name="data_ingestion_"+lambda_name_key,
            memory_size=256,
            timeout=Duration.minutes(10),
            ephemeral_storage_size=Size.mebibytes(512),
            layers=[l for l in [opensearchpy_layer, requests_aws4auth_layer, boto3_layer] if l is not None]
        )
        data_ingestion_function.add_environment("OPENSEARCH_ENDPOINT", visual_search_collection.attr_collection_endpoint)
        data_ingestion_function.add_environment("INDEX_NAME", f"visualproductsearchmod-{name_key}")
        data_ingestion_function.add_environment("BUCKET_NAME", s3_bucket_name)
        data_ingestion_function.add_environment("S3_PREFIX", "visualproductsearch")
        data_ingestion_function.add_environment("CLAUDE_MODEL_ID", "us.anthropic.claude-3-7-sonnet-20250219-v1:0")


        bucket.grant_read(data_ingestion_function)

        data_ingestion_function.node.add_dependency(visual_search_collection)
        data_ingestion_function.node.add_dependency(bucket)

        data_ingestion_provider = cr.Provider(
            self, "DataIngestionProvider",
            on_event_handler=data_ingestion_function
        )
        data_ingestion_provider.node.add_dependency(data_ingestion_function)
        
        data_ingestion_resource = CustomResource(
            self, "DataIngestionResource",
            service_token=data_ingestion_provider.service_token,
            properties={
                "index_name": f"visualproductsearchmod-{name_key}"
            }
        )








        websocket_lambda_function = lambda_.Function(
            self, "WebSocketLambdaFunction",
            runtime=lambda_.Runtime.PYTHON_3_9,
            handler="websocket_handler.lambda_handler",
            code=lambda_.Code.from_asset("lambda_code"),
            function_name="ws_"+lambda_name_key,
            memory_size=256,  # Increased for retail operations
            timeout=Duration.seconds(29),  # 29 seconds as specified
            ephemeral_storage_size=Size.mebibytes(512),
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS
            ),
            security_groups=[lambda_security_group],
            role=lambda_role,
            layers=[l for l in [boto3_layer, psycopg2_layer, requests_layer, requests_aws4auth_layer, opensearchpy_layer] if l is not None],
            environment=env_vars
        )

        
        # Add dynamic RDS database credentials to WebSocket Lambda function
        websocket_lambda_function.add_environment("RDS_SECRET_NAME", f"rds-credentials-{rds_name_key}")
        websocket_lambda_function.add_environment("RDS_SECRET_ARN", db_instance.secret.secret_arn if db_instance.secret else "")
        websocket_lambda_function.add_environment("RDS_ENDPOINT", db_instance.instance_endpoint.hostname)
        websocket_lambda_function.add_environment("db_host", db_instance.instance_endpoint.hostname)
        
        lambda_function.add_environment("WEBSOCKET_REGION", self.region)
        # Add dynamic RDS database credentials to main Lambda function
        lambda_function.add_environment("RDS_SECRET_NAME", f"rds-credentials-{rds_name_key}")
        lambda_function.add_environment("RDS_SECRET_ARN", db_instance.secret.secret_arn if db_instance.secret else "")
        lambda_function.add_environment("RDS_ENDPOINT", db_instance.instance_endpoint.hostname)
        lambda_function.add_environment("db_host", db_instance.instance_endpoint.hostname)
         # Add dynamic socket endpoint to main Lambda function
        




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

        # Add dependency for WebSocket stage to run after WebSocket API
        websocket_stage.node.add_dependency(websocket_api)

        # Update WebSocket Lambda environment with the endpoint URL and region
        websocket_url = websocket_stage.url.replace('wss://', 'https://')

        websocket_lambda_function.add_environment("WEBSOCKET_REGION", self.region)
        websocket_lambda_function.add_environment("WEBSOCKET_ENDPOINT", websocket_url)
        websocket_lambda_function.add_environment("SOCKET_ENDPOINT", f"https://{api.rest_api_id}.execute-api.{self.region}.amazonaws.com/dev/")
        lambda_function.add_environment("WEBSOCKET_ENDPOINT", websocket_url)
        

        # ✅ ADD THIS: Add socket endpoint to main Lambda function environment
        lambda_function.add_environment("socket_endpoint", websocket_url)

        # ✅ ADD THIS: Also add to env_vars dictionary for consistency  
        env_vars["socket_endpoint"] = websocket_url



        ec2_instance_front = ec2.Instance(
            self, "MyEC2InstanceFront",
            role=ec2_role,
            instance_type=ec2.InstanceType.of(
                ec2.InstanceClass.T3,
                ec2.InstanceSize.MEDIUM
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


         # Create Auto-Sync Lambda function AFTER knowledge bases are created
        auto_sync_function = lambda_.Function(
            self, "AutoSyncFunction",
            runtime=lambda_.Runtime.PYTHON_3_9,
            handler="auto_sync_function.handler",
            role=auto_sync_lambda_role,
            timeout=Duration.minutes(15),
            environment={
                "RETAIL_KB_ID": retail_kb.attr_knowledge_base_id,
                "RETAIL_DS_ID": retail_kb.data_source_id,
            },
            code=lambda_.Code.from_asset(str(lambda_dir))
        )

        # Add dependencies to ensure Knowledge Bases are created before Lambda
        auto_sync_function.node.add_dependency(retail_kb)
        auto_sync_function.node.add_dependency(retail_kb_deploy)

        # Create a custom resource to trigger initial sync after Knowledge Base creation
        initial_sync_function = lambda_.Function(
            self, "InitialSyncFunction",
            runtime=lambda_.Runtime.PYTHON_3_9,
            handler="initial_sync_function.handler",
            role=auto_sync_lambda_role,
            timeout=Duration.minutes(15),
            environment={
                "RETAIL_KB_ID": retail_kb.attr_knowledge_base_id,
                "RETAIL_DS_ID": retail_kb.data_source_id,
            },
            code=lambda_.Code.from_asset(str(lambda_dir))
        )   
        
        # Add dependencies to ensure Knowledge Bases are created before initial sync
        initial_sync_function.node.add_dependency(retail_kb)
        initial_sync_function.node.add_dependency(retail_kb_deploy)

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
                "retail_kb_id": retail_kb.attr_knowledge_base_id,
                "retail_ds_id": retail_kb.data_source_id
            }
        )
        
        # Add dependencies for initial sync
        initial_sync_function.node.add_dependency(retail_kb)
        initial_sync_function.node.add_dependency(retail_kb_deploy)




        # Add S3 event notification to trigger auto-sync Lambda
        bucket.add_event_notification(  # Use 'bucket' instead of 'self.data_bucket'
            s3.EventType.OBJECT_CREATED,
            s3n.LambdaDestination(auto_sync_function),
            s3.NotificationKeyFilter(prefix="kb/retail/")
        )

        # Set the environment variables that will be passed to the EC2 instance
        rest_api_name = f"genaifoundry-api{name_key}"
        websocket_api_name = f"GenAIFoundry_ws{name_key}"
        bucket_name = frontend_bucket_name
        kb_bucket_name = s3_bucket_name  # Knowledge base bucket name for S3_BUCKET_NAME
        
        region = self.region

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
            f"export BUCKET_NAME=\"{bucket_name}\"",
            f"export S3_BUCKET_NAME=\"{kb_bucket_name}\"",
            f"export REGION=\"{region}\"",
            f"export STACK_SELECTION=\"{self.stack_selection}\"",
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

            "    if [[ -z \"${BUCKET_NAME:-}\" ]]; then",
            "        missing_vars+=(\"BUCKET_NAME\")",
            "    fi",
            "   ",
            "    if [[ -z \"${S3_BUCKET_NAME:-}\" ]]; then",
            "        missing_vars+=(\"S3_BUCKET_NAME\")",
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
            "        echo \"  export BUCKET_NAME=\\\"your-frontend-s3-bucket-name\\\"\"",
            "        echo \"  export S3_BUCKET_NAME=\\\"your-knowledge-base-s3-bucket-name\\\"\"",
            "        echo \"  export REGION=\\\"your-aws-region\\\"\"",
            "        echo \"  export STACK_SELECTION=\\\"your-stack-selection\\\"\"",
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
            "echo \"  BUCKET_NAME:          ${BUCKET_NAME}\"",
            "echo \"  S3_BUCKET_NAME:       ${S3_BUCKET_NAME}\"",
            "echo \"  REGION:               ${REGION}\"",
            "echo \"  STACK_SELECTION:      ${STACK_SELECTION}\"",
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
            
            "# Debug logging",
            "echo \"✅ Retrieved API IDs:\"",
            "echo \"  REST API (chat):      $API_ID_REST (from $REST_API_NAME)\"",
            "echo \"  WebSocket API:        $API_ID_WS (from $WEBSOCKET_API_NAME)\"",
    
            "",
            "# Construct URLs",
            "VITE_API_BASE_URL=\"https://${API_ID_REST}.execute-api.${REGION}.amazonaws.com/dev/chat_api\"",
            "VITE_WEBSOCKET_URL=\"wss://${API_ID_WS}.execute-api.${REGION}.amazonaws.com/production/\"",
    
    
            "",
            "# 📄 Update .env file",
            "ENV_FILE=\".env\"",
            "LOG_FILE=\"/var/log/env-update.log\"",
            "echo \"🛠 Updating environment variables in $ENV_FILE...\"",
            "echo \"📝 Logging to: $LOG_FILE\"",
            "",
            "# Create log file with timestamp",
            "echo \"=== Environment Variable Update Log - $(date) ===\" > \"$LOG_FILE\"",
            "echo \"Starting environment variable updates...\" >> \"$LOG_FILE\"",
            "",
            "update_env_var() {",
            "    local key=\"$1\"",
            "    local value=\"$2\"",
            "    echo \"[$(date)] Updating $key...\" | tee -a \"$LOG_FILE\"",
            "    echo \"[$(date)] Old value: $(grep \"^$key=\" \"$ENV_FILE\" 2>/dev/null || echo 'Not found')\" | tee -a \"$LOG_FILE\"",
            "    if grep -q \"^$key=\" \"$ENV_FILE\"; then",
            "        sed -i \"s|^$key=.*|$key=$value|\" \"$ENV_FILE\"",
            "        echo \"[$(date)] Updated existing $key\" | tee -a \"$LOG_FILE\"",
            "    else",
            "        echo \"$key=$value\" >> \"$ENV_FILE\"",
            "        echo \"[$(date)] Added new $key\" | tee -a \"$LOG_FILE\"",
            "    fi",
            "    echo \"[$(date)] New value: $key=$value\" | tee -a \"$LOG_FILE\"",
            "    echo \"---\" | tee -a \"$LOG_FILE\"",
            "}",
            "",
            "echo \"[$(date)] Starting environment variable updates...\" | tee -a \"$LOG_FILE\"",
            "update_env_var \"VITE_API_BASE_URL\" \"$VITE_API_BASE_URL\"",
            "update_env_var \"VITE_WEBSOCKET_URL\" \"$VITE_WEBSOCKET_URL\"",
            "update_env_var \"S3_BUCKET_NAME\" \"$S3_BUCKET_NAME\"",
            "update_env_var \"VITE_STACK_SELECTION\" \"$STACK_SELECTION\"",
    
    
            "",
            "echo \"✅ .env updated. Current values:\"",
            "grep -E \"VITE_API_BASE_URL|VITE_WEBSOCKET_URL|S3_BUCKET_NAME|VITE_STACK_SELECTION\" \"$ENV_FILE\"",
            "",
            "# 📋 Environment Variable Validation",
            "echo \"[$(date)] Starting environment variable validation...\" | tee -a \"$LOG_FILE\"",
            "validate_env_var() {",
            "    local key=\"$1\"",
            "    local expected_pattern=\"$2\"",
            "    local actual_value=$(grep \"^$key=\" \"$ENV_FILE\" 2>/dev/null | cut -d'=' -f2-)",
            "    ",
            "    if [[ -n \"$actual_value\" ]]; then",
            "        if [[ \"$actual_value\" =~ $expected_pattern ]]; then",
            "            echo \"[$(date)] ✅ $key validation PASSED: $actual_value\" | tee -a \"$LOG_FILE\"",
            "            return 0",
            "        else",
            "            echo \"[$(date)] ❌ $key validation FAILED: Expected pattern '$expected_pattern', got '$actual_value'\" | tee -a \"$LOG_FILE\"",
            "            return 1",
            "        fi",
            "    else",
            "        echo \"[$(date)] ❌ $key validation FAILED: Variable not found\" | tee -a \"$LOG_FILE\"",
            "            return 1",
            "    fi",
            "}",
            "",
            "# Validate each environment variable",
            "validation_failed=0",
            "validate_env_var \"VITE_API_BASE_URL\" \"^https://.*\\.execute-api\\..*\\.amazonaws\\.com/.*\" || validation_failed=1",
            "validate_env_var \"VITE_WEBSOCKET_URL\" \"^wss://.*\\.execute-api\\..*\\.amazonaws\\.com/.*\" || validation_failed=1",
            "validate_env_var \"S3_BUCKET_NAME\" \"^genaifoundry.*\" || validation_failed=1",
            "validate_env_var \"VITE_STACK_SELECTION\" \"^(retail|banking|insurance|healthcare)$\" || validation_failed=1",
            "",
            "if [[ $validation_failed -eq 0 ]]; then",
            "    echo \"[$(date)] ✅ All environment variables validated successfully!\" | tee -a \"$LOG_FILE\"",
            "else",
            "    echo \"[$(date)] ❌ Environment variable validation failed! Check the log above.\" | tee -a \"$LOG_FILE\"",
            "fi",
            "",
            "# 📄 Final .env file content",
            "echo \"[$(date)] Final .env file content:\" | tee -a \"$LOG_FILE\"",
            "cat \"$ENV_FILE\" | tee -a \"$LOG_FILE\"",
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
            "",
            "# 📤 Upload logs to S3 for persistence",
            "echo \"[$(date)] Uploading logs to S3 for persistence...\" | tee -a \"$LOG_FILE\"",
            "TIMESTAMP=$(date +%Y%m%d_%H%M%S)",
            "INSTANCE_ID=$(curl -s http://169.254.169.254/latest/meta-data/instance-id)",
            "",
            "# Upload environment update log",
            "aws s3 cp \"$LOG_FILE\" \"s3://${S3_BUCKET_NAME}/logs/frontend-deployment/${TIMESTAMP}_${INSTANCE_ID}_env-update.log\" --region \"$REGION\"",
            "echo \"[$(date)] Environment update log uploaded to S3\" | tee -a \"$LOG_FILE\"",
            "",
            "# Upload .env file for verification",
            "aws s3 cp \"$ENV_FILE\" \"s3://${S3_BUCKET_NAME}/logs/frontend-deployment/${TIMESTAMP}_${INSTANCE_ID}_env-file.txt\" --region \"$REGION\"",
            "echo \"[$(date)] .env file uploaded to S3 for verification\" | tee -a \"$LOG_FILE\"",
            "",
            "# Upload build logs if they exist",
            "if [[ -f \"npm-debug.log\" ]]; then",
            "    aws s3 cp \"npm-debug.log\" \"s3://${S3_BUCKET_NAME}/logs/frontend-deployment/${TIMESTAMP}_${INSTANCE_ID}_npm-debug.log\" --region \"$REGION\"",
            "    echo \"[$(date)] npm debug log uploaded to S3\" | tee -a \"$LOG_FILE\"",
            "fi",
            "",
            "# Create deployment summary",
            "DEPLOYMENT_SUMMARY=\"/tmp/deployment-summary.txt\"",
            "cat > \"$DEPLOYMENT_SUMMARY\" << EOF",
            "=== Frontend Deployment Summary ===",
            "Timestamp: $(date)",
            "Instance ID: $INSTANCE_ID",
            "Stack Selection: $STACK_SELECTION",
            "Region: $REGION",
            "REST API Name: $REST_API_NAME",
            "WebSocket API Name: $WEBSOCKET_API_NAME",
            "Frontend Bucket: $BUCKET_NAME",
            "Knowledge Base Bucket: $S3_BUCKET_NAME",
            "",
            "=== Environment Variables ===",
            "VITE_API_BASE_URL: $VITE_API_BASE_URL",
            "VITE_WEBSOCKET_URL: $VITE_WEBSOCKET_URL",
            "S3_BUCKET_NAME: $S3_BUCKET_NAME",
            "VITE_STACK_SELECTION: $STACK_SELECTION",
            "",
            "=== Validation Results ===",
            "EOF",
            "",
            "# Append validation results to summary",
            "grep \"validation\" \"$LOG_FILE\" >> \"$DEPLOYMENT_SUMMARY\"",
            "",
            "# Upload deployment summary",
            "aws s3 cp \"$DEPLOYMENT_SUMMARY\" \"s3://${S3_BUCKET_NAME}/logs/frontend-deployment/${TIMESTAMP}_${INSTANCE_ID}_deployment-summary.txt\" --region \"$REGION\"",
            "echo \"[$(date)] Deployment summary uploaded to S3\" | tee -a \"$LOG_FILE\"",
            "",
            "echo \"[$(date)] All logs uploaded to s3://${S3_BUCKET_NAME}/logs/frontend-deployment/\" | tee -a \"$LOG_FILE\"",
            "",
            "# 📊 Send logs to CloudWatch",
            "echo \"[$(date)] Sending logs to CloudWatch...\" | tee -a \"$LOG_FILE\"",
            "LOG_GROUP_NAME=\"/aws/ec2/frontend-deployment\"",
            "LOG_STREAM_NAME=\"${INSTANCE_ID}-$(date +%Y%m%d_%H%M%S)\"",
            "",
            "# Create log group if it doesn't exist",
            "aws logs create-log-group --log-group-name \"$LOG_GROUP_NAME\" --region \"$REGION\" 2>/dev/null || true",
            "",
            "# Create log stream",
            "aws logs create-log-stream --log-group-name \"$LOG_GROUP_NAME\" --log-stream-name \"$LOG_STREAM_NAME\" --region \"$REGION\" 2>/dev/null || true",
            "",
            "# Upload log file to CloudWatch",
            "aws logs put-log-events \\",
            "    --log-group-name \"$LOG_GROUP_NAME\" \\",
            "    --log-stream-name \"$LOG_STREAM_NAME\" \\",
            "    --log-events timestamp=$(date +%s)000,message=\"$(cat $LOG_FILE | base64 -w 0)\" \\",
            "    --region \"$REGION\" 2>/dev/null || echo \"[$(date)] CloudWatch upload failed, but continuing...\" | tee -a \"$LOG_FILE\"",
            "",
            "echo \"[$(date)] Logs sent to CloudWatch: $LOG_GROUP_NAME/$LOG_STREAM_NAME\" | tee -a \"$LOG_FILE\"",
            "",
            "# 🏁 Final completion message",
            "echo \"[$(date)] ==========================================\" | tee -a \"$LOG_FILE\"",
            "echo \"[$(date)] Frontend deployment completed successfully!\" | tee -a \"$LOG_FILE\"",
            "echo \"[$(date)] Check S3 logs at: s3://${S3_BUCKET_NAME}/logs/frontend-deployment/\" | tee -a \"$LOG_FILE\"",
            "echo \"[$(date)] Check CloudWatch logs at: $LOG_GROUP_NAME\" | tee -a \"$LOG_FILE\"",
            "echo \"[$(date)] ==========================================\" | tee -a \"$LOG_FILE\"",
            "",
            "TOKEN=$(curl -s -X PUT \"http://169.254.169.254/latest/api/token\" -H \"X-aws-ec2-metadata-token-ttl-seconds: 21600\")",
            "INSTANCE_ID=$(curl -s -H \"X-aws-ec2-metadata-token: $TOKEN\" http://169.254.169.254/latest/meta-data/instance-id)",
            "aws ec2 terminate-instances --instance-ids \"$INSTANCE_ID\" --region \"$REGION\""
            
           
        )


        # Create CloudFront Distribution for frontend S3 bucket
        # Create S3 Origin
        s3_origin = origins.S3Origin(frontend_bucket, origin_path="")

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
            additional_behaviors={
                "/virtualtryon/*": cloudfront.BehaviorOptions(
                    origin=s3_origin,
                    viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                    cache_policy=cloudfront.CachePolicy.CACHING_DISABLED,
                    origin_request_policy=None,
                    response_headers_policy=None
                ),
                "/visualproductsearch/*": cloudfront.BehaviorOptions(
                    origin=s3_origin,
                    viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                    cache_policy=cloudfront.CachePolicy.CACHING_DISABLED,
                    origin_request_policy=None,
                    response_headers_policy=None
                )
            },
            default_root_object="index.html",
            price_class=cloudfront.PriceClass.PRICE_CLASS_ALL,
            http_version=cloudfront.HttpVersion.HTTP2,
            enable_logging=False,
            enable_ipv6=True,
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
        cfn_dist = distribution.node.default_child  # type: ignore
        # Attach OAC to first origin and remove OAI reference
        cfn_dist.add_property_override(
            "DistributionConfig.Origins.0.OriginAccessControlId", oac.attr_id
        )
        cfn_dist.add_property_deletion_override(
            "DistributionConfig.Origins.0.S3OriginConfig.OriginAccessIdentity"
        )





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
                        "Paths": {"Quantity": 3, "Items": ["/*", "/virtualtryon/*", "/visualproductsearch/*"]},
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

        # Add dependency for invalidation to run after distribution
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

        # Add bucket policy to allow CloudFront access to virtualtryon and visualproductsearch folders
        bucket.add_to_resource_policy(
            iam.PolicyStatement(
                sid="AllowCloudFrontVirtualTryOnAccess",
                effect=iam.Effect.ALLOW,
                principals=[iam.ServicePrincipal("cloudfront.amazonaws.com")],
                actions=["s3:GetObject"],
                resources=[f"{bucket.bucket_arn}/virtualtryon/*"],
                conditions={
                    "StringEquals": {
                        "AWS:SourceArn": distribution.distribution_arn
                    }
                }
            )
        )
        
        bucket.add_to_resource_policy(
            iam.PolicyStatement(
                sid="AllowCloudFrontVisualProductSearchAccess",
                effect=iam.Effect.ALLOW,
                principals=[iam.ServicePrincipal("cloudfront.amazonaws.com")],
                actions=["s3:GetObject"],
                resources=[f"{bucket.bucket_arn}/visualproductsearch/*"],
                conditions={
                    "StringEquals": {
                        "AWS:SourceArn": distribution.distribution_arn
                    }
                }
            )
        )
        
        CfnOutput(
            self, "CloudFrontDistributionUrl",
            value=f"https://{distribution.distribution_domain_name}",
            description="CloudFront Distribution URL for the frontend application"
        )
    
    def create_kb(self, name: str, s3_uri: str, model_arn: str, role_arn: str, data_prefix: str, index_name: str, index_creator_function: lambda_.Function, index_creator: CustomResource, provider: cr.Provider, collection_arn: str, data_access_policy: aoss.CfnAccessPolicy, index_waiter: CustomResource = None, index_waiter_function: lambda_.Function = None, index_waiter_provider: cr.Provider = None):
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
        
        # Add index waiter dependencies if provided
        if index_waiter:
            kb.node.add_dependency(index_waiter)
        if index_waiter_function:
            kb.node.add_dependency(index_waiter_function)
        if index_waiter_provider:
            kb.node.add_dependency(index_waiter_provider)

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




