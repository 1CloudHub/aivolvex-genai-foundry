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
    aws_s3_notifications as s3n,
    aws_rds as rds,
    aws_apigateway as apigateway,
    aws_apigatewayv2 as apigatewayv2,
    aws_apigatewayv2_integrations as apigatewayv2_integrations,
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

    body_chars = string.ascii_lowercase + string.digits
    end_chars = string.ascii_lowercase + string.digits
    first_char = random.choice(string.ascii_lowercase)

    if length > 2:
        middle_chars = ''.join(random.choices(body_chars + '-', k=length - 2))
        middle_chars = middle_chars.replace('--', '-')
        if middle_chars.endswith('-'):
            middle_chars = middle_chars[:-1] + random.choice(string.ascii_lowercase + string.digits)
    else:
        middle_chars = ''

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

    body_chars = string.ascii_lowercase + string.digits + '-_'
    end_chars = string.ascii_lowercase + string.digits
    main_part = ''.join(random.choices(body_chars, k=length - 1))
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

    body_chars = string.ascii_lowercase + string.digits
    end_chars = string.ascii_lowercase + string.digits
    main_part = ''.join(random.choices(body_chars, k=length - 1))
    last_char = random.choice(end_chars)

    return "q" + main_part + last_char


foundry_key = generate_random_alphanumeric(8)
lambda_safe_key = generate_lambda_safe_name()
rds_safe_key = generate_rds_safe_name()
name_key = foundry_key
lambda_name_key = "genai-foundry-" + lambda_safe_key
rds_name_key = "genaifoundry" + rds_safe_key
print(f"Resource name: {name_key}")
print(f"Lambda-safe name: {lambda_name_key}")
print(f"RDS-safe name: {rds_name_key}")

s3_name = "genai-foundry-test"


class LambdaLayerUploader(Construct):
    """Custom construct to upload Lambda layers from ZIP files"""

    def __init__(self, scope: Construct, construct_id: str, **kwargs):
        super().__init__(scope, construct_id, **kwargs)
        Tags.of(self).add("Project", "GenAI-Foundry")
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

                self.layers[layer_name] = {
                    "layer": layer,
                    "path": layer_path,
                    "zip_file": zip_file,
                    "description": layer_description
                }
            else:
                print(f"ZIP file not found: {zip_file}")
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


class LogisticsCdkStack(Stack):

    def __init__(self, scope: Construct, construct_id: str, stack_selection: str = "unknown", chat_tool_model: str = "us.amazon.nova-pro-v1:0", **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self.chat_tool_model = chat_tool_model
        self.stack_selection = stack_selection
        print(f"Building Logistics Stack with selection: {self.stack_selection}")

        # ── VPC ───────────────────────────────────────────────────────────────
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

        # ── Security Groups ───────────────────────────────────────────────────
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

        lambda_security_group = ec2.SecurityGroup(
            self, "LambdaSecurityGroup",
            vpc=vpc,
            description="Security group for Lambda functions",
            allow_all_outbound=True
        )

        rds_security_group.add_ingress_rule(
            peer=lambda_security_group,
            connection=ec2.Port.tcp(5432),
            description="Allow PostgreSQL access from Lambda"
        )

        lambda_security_group.add_egress_rule(
            peer=rds_security_group,
            connection=ec2.Port.tcp(5432),
            description="Allow Lambda to connect to RDS"
        )

        key_pair = ec2.KeyPair(
            self, "MyKeyPair",
            key_pair_name=f"keypair-{name_key}",
            type=ec2.KeyPairType.RSA,
            format=ec2.KeyPairFormat.PEM
        )

        rds_security_group.add_ingress_rule(
            peer=ec2_security_group,
            connection=ec2.Port.tcp(5432),
            description="Allow PostgreSQL access from EC2"
        )

        # ── RDS Subnet Group ──────────────────────────────────────────────────
        db_subnet_group = rds.SubnetGroup(
            self, "MyDBSubnetGroup",
            description="Subnet group for RDS database",
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS
            )
        )

        # ── S3 Buckets ────────────────────────────────────────────────────────
        s3_bucket_name = "genaifoundry" + name_key
        frontend_bucket_name = "genaifoundry-front" + name_key

        bucket = s3.Bucket(
            self,
            "KnowledgeBaseBucket",
            bucket_name=s3_bucket_name,
            versioned=True,
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True
        )

        frontend_bucket = s3.Bucket(
            self,
            "FrontendBucket",
            bucket_name=frontend_bucket_name,
            versioned=True,
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
            website_index_document="index.html",
            website_error_document="index.html",
        )

        s3deploy.BucketDeployment(
            self,
            "DeployKnowledgeBaseFolder",
            sources=[s3deploy.Source.asset("genaifoundy-usecases")],
            destination_bucket=bucket,
            destination_key_prefix="kb/",
        )

        frontend_deploy = s3deploy.BucketDeployment(
            self,
            "DeployFrontendFolder",
            sources=[s3deploy.Source.asset("genaifoundry-front")],
            destination_bucket=frontend_bucket,
            destination_key_prefix="",
        )

        # ── Model ARN and data bucket reference ───────────────────────────────
        model_arn = f"arn:aws:bedrock:{self.region}::foundation-model/amazon.titan-embed-text-v2:0"

        self.data_bucket = s3.Bucket.from_bucket_name(
            self,
            "ExistingDataBucket",
            bucket_name=s3_bucket_name
        )

        # ── OpenSearch Serverless Collection (Logistics) ──────────────────────
        logistics_collection_name = f"lg-{name_key}-col"

        logistics_collection = opensearch.CfnCollection(
            self, "LogisticsKBCollection",
            name=logistics_collection_name,
            type="VECTORSEARCH"
        )

        logistics_encryption_policy = opensearch.CfnSecurityPolicy(
            self, "LogisticsKBSecurityPolicy",
            name=f"lg-{name_key}-encrypt",
            type="encryption",
            policy=json.dumps({
                "Rules": [{
                    "ResourceType": "collection",
                    "Resource": [f"collection/{logistics_collection_name}"]
                }],
                "AWSOwnedKey": True
            })
        )

        logistics_network_policy = opensearch.CfnSecurityPolicy(
            self, "LogisticsKBNetworkPolicy",
            name=f"lg-{name_key}-network",
            type="network",
            policy=json.dumps([{
                "Rules": [{
                    "ResourceType": "collection",
                    "Resource": [f"collection/{logistics_collection_name}"]
                }],
                "AllowFromPublic": True
            }])
        )

        logistics_collection.add_dependency(logistics_encryption_policy)
        logistics_collection.add_dependency(logistics_network_policy)

        # ── IAM Role for Bedrock Knowledge Base ───────────────────────────────
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

        # ── IAM Role for Index Creator Lambda ─────────────────────────────────
        lambda_role_index = iam.Role(
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
                                f"arn:aws:aoss:{self.region}:{self.account}:collection/{logistics_collection_name}",
                                f"arn:aws:aoss:{self.region}:{self.account}:index/{logistics_collection_name}/*"
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

        # ── IAM Role for Auto-Sync Lambda ─────────────────────────────────────
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

        # ── OpenSearch Data Access Policy ─────────────────────────────────────
        logistics_data_access_policy = opensearch.CfnAccessPolicy(
            self, "LogisticsKBDataAccessPolicy",
            name=f"lg-{name_key}-access",
            type="data",
            policy=json.dumps([{
                "Rules": [{
                    "ResourceType": "collection",
                    "Resource": [f"collection/{logistics_collection_name}"],
                    "Permission": [
                        "aoss:CreateCollectionItems",
                        "aoss:DeleteCollectionItems",
                        "aoss:UpdateCollectionItems",
                        "aoss:DescribeCollectionItems"
                    ]
                }, {
                    "ResourceType": "index",
                    "Resource": [f"index/{logistics_collection_name}/*"],
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
                    f"arn:aws:iam::{self.account}:role/{lambda_role_index.role_name}",
                    f"arn:aws:iam::{self.account}:role/{auto_sync_lambda_role.role_name}"
                ],
                "Description": f"Data access policy for {logistics_collection_name}"
            }])
        )

        logistics_collection.add_dependency(logistics_data_access_policy)

        # ── Lambda layer / source paths ───────────────────────────────────────
        current_dir = Path(__file__).parent
        layers_dir = current_dir.parent / "layers"
        lambda_dir = current_dir.parent / "lambda"

        # ── Index names ───────────────────────────────────────────────────────
        logistics_index_name = f"lg-{name_key}-idx"

        # ── Index Creator Function ────────────────────────────────────────────
        logistics_index_creator_function = lambda_.Function(
            self, "LogisticsIndexCreatorFunction",
            runtime=lambda_.Runtime.PYTHON_3_9,
            handler="index_creator.lambda_handler",
            role=lambda_role_index,
            timeout=Duration.minutes(10),
            environment={
                "OPENSEARCH_ENDPOINT": logistics_collection.attr_collection_endpoint,
                "COLLECTION_NAME": logistics_collection_name,
                "INDEX_NAME": logistics_index_name,
                "chat_tool_model": self.chat_tool_model
            },
            layers=[
                lambda_.LayerVersion(
                    self, "LogisticsOpenSearchPyLayer",
                    code=lambda_.Code.from_asset(str(layers_dir / "opensearchpy.zip")),
                    compatible_runtimes=[lambda_.Runtime.PYTHON_3_9],
                    description="OpenSearch Python client layer for Logistics"
                ),
                lambda_.LayerVersion(
                    self, "LogisticsAWS4AuthLayer",
                    code=lambda_.Code.from_asset(str(layers_dir / "aws4auth.zip")),
                    compatible_runtimes=[lambda_.Runtime.PYTHON_3_9],
                    description="AWS4Auth layer for Logistics"
                )
            ],
            code=lambda_.Code.from_asset(str(lambda_dir))
        )

        # ── Index Waiter Function ─────────────────────────────────────────────
        logistics_index_waiter_function = lambda_.Function(
            self, "LogisticsIndexWaiterFunction",
            runtime=lambda_.Runtime.PYTHON_3_9,
            handler="index_waiter.lambda_handler",
            role=lambda_role_index,
            timeout=Duration.minutes(15),
            environment={
                "OPENSEARCH_ENDPOINT": logistics_collection.attr_collection_endpoint,
                "COLLECTION_NAME": logistics_collection_name,
                "INDEX_NAME": logistics_index_name,
                "chat_tool_model": self.chat_tool_model
            },
            layers=[
                lambda_.LayerVersion(
                    self, "LogisticsWaiterOpenSearchPyLayer",
                    code=lambda_.Code.from_asset(str(layers_dir / "opensearchpy.zip")),
                    compatible_runtimes=[lambda_.Runtime.PYTHON_3_9],
                    description="OpenSearch Python client layer for Logistics Index Waiter"
                ),
                lambda_.LayerVersion(
                    self, "LogisticsWaiterAWS4AuthLayer",
                    code=lambda_.Code.from_asset(str(layers_dir / "aws4auth.zip")),
                    compatible_runtimes=[lambda_.Runtime.PYTHON_3_9],
                    description="AWS4Auth layer for Logistics Index Waiter"
                )
            ],
            code=lambda_.Code.from_asset(str(lambda_dir))
        )

        logistics_index_creator_function.node.add_dependency(logistics_collection)
        logistics_index_creator_function.node.add_dependency(logistics_data_access_policy)

        logistics_index_waiter_function.node.add_dependency(logistics_collection)
        logistics_index_waiter_function.node.add_dependency(logistics_data_access_policy)

        # ── Custom Resource Providers ─────────────────────────────────────────
        logistics_provider = cr.Provider(
            self, "LogisticsInitProvider",
            on_event_handler=logistics_index_creator_function
        )

        logistics_waiter_provider = cr.Provider(
            self, "LogisticsWaiterProvider",
            on_event_handler=logistics_index_waiter_function
        )

        # ── Custom Resources for index creation and waiting ───────────────────
        logistics_index_creator = CustomResource(
            self, "LogisticsIndexCreator",
            service_token=logistics_provider.service_token,
            properties={
                "index_name": logistics_index_name,
                "dimension": 1024,
                "method": "hnsw",
                "engine": "faiss",
                "space_type": "l2"
            }
        )

        logistics_index_waiter = CustomResource(
            self, "LogisticsIndexWaiter",
            service_token=logistics_waiter_provider.service_token,
            properties={
                "index_name": logistics_index_name,
                "max_retries": 60,
                "retry_delay": 5
            }
        )

        logistics_index_creator.node.add_dependency(logistics_collection)
        logistics_index_creator.node.add_dependency(logistics_index_creator_function)

        logistics_index_waiter.node.add_dependency(logistics_index_creator)
        logistics_index_waiter.node.add_dependency(logistics_index_waiter_function)
        logistics_index_waiter.node.add_dependency(logistics_waiter_provider)

        # ── Create Logistics Knowledge Base ───────────────────────────────────
        logistics_kb = self.create_kb(
            f"genaifoundrylogistics-{name_key}",
            f"s3://{s3_bucket_name}/kb/logistics/",
            model_arn,
            bedrock_kb_role.role_arn,
            "kb/logistics",
            logistics_index_name,
            logistics_index_creator_function,
            logistics_index_creator,
            logistics_provider,
            logistics_collection.attr_arn,
            logistics_data_access_policy,
            logistics_index_waiter,
            logistics_index_waiter_function,
            logistics_waiter_provider
        )

        # ── Auto-Sync Lambda ──────────────────────────────────────────────────
        auto_sync_function = lambda_.Function(
            self, "AutoSyncFunction",
            runtime=lambda_.Runtime.PYTHON_3_9,
            handler="auto_sync_function.handler",
            role=auto_sync_lambda_role,
            timeout=Duration.minutes(15),
            environment={
                "LOGISTICS_KB_ID": logistics_kb.attr_knowledge_base_id,
                "LOGISTICS_DS_ID": logistics_kb.data_source_id,
                "chat_tool_model": self.chat_tool_model
            },
            code=lambda_.Code.from_asset(str(lambda_dir))
        )

        auto_sync_function.node.add_dependency(logistics_kb)

        # ── Initial Sync Lambda ───────────────────────────────────────────────
        initial_sync_function = lambda_.Function(
            self, "InitialSyncFunction",
            runtime=lambda_.Runtime.PYTHON_3_9,
            handler="initial_sync_function.handler",
            role=auto_sync_lambda_role,
            timeout=Duration.minutes(15),
            environment={
                "LOGISTICS_KB_ID": logistics_kb.attr_knowledge_base_id,
                "LOGISTICS_DS_ID": logistics_kb.data_source_id,
                "chat_tool_model": self.chat_tool_model
            },
            code=lambda_.Code.from_asset(str(lambda_dir))
        )

        initial_sync_function.node.add_dependency(logistics_kb)
        initial_sync_function.node.add_dependency(s3deploy.BucketDeployment(
            self, "LogisticsKBDeployment",
            sources=[s3deploy.Source.asset("genaifoundy-usecases/logistics")],
            destination_bucket=self.data_bucket,
            destination_key_prefix="kb/logistics/"
        ))

        # ── Custom Resource to trigger initial sync ───────────────────────────
        initial_sync_provider = cr.Provider(
            self, "InitialSyncProvider",
            on_event_handler=initial_sync_function
        )

        initial_sync = CustomResource(
            self, "InitialSync",
            service_token=initial_sync_provider.service_token,
            properties={
                "logistics_kb_id": logistics_kb.attr_knowledge_base_id,
                "logistics_ds_id": logistics_kb.data_source_id
            }
        )

        initial_sync.node.add_dependency(logistics_kb)
        initial_sync.node.add_dependency(initial_sync_function)

        # ── S3 Event Notification for auto-sync ───────────────────────────────
        try:
            s3_client = boto3.client('s3')
            try:
                s3_client.head_bucket(Bucket=s3_bucket_name)
                print(f"Bucket {s3_bucket_name} exists, adding event notifications...")

                self.data_bucket.add_event_notification(
                    s3.EventType.OBJECT_CREATED,
                    s3n.LambdaDestination(auto_sync_function),
                    s3.NotificationKeyFilter(prefix="kb/logistics/")
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

        # ── RDS PostgreSQL Instance ───────────────────────────────────────────
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
            security_groups=[rds_security_group],
            credentials=rds.Credentials.from_generated_secret(
                username="postgres",
                secret_name=f"rds-credentials-{rds_name_key}"
            ),
            allocated_storage=20,
            storage_type=rds.StorageType.GP2,
            deletion_protection=False,
            delete_automated_backups=False,
            backup_retention=Duration.days(7),
            removal_policy=RemovalPolicy.DESTROY,
            database_name=rds_name_key
        )

        # ── IAM Role for EC2 ──────────────────────────────────────────────────
        ec2_role = iam.Role(
            self, "EC2Role",
            assumed_by=iam.ServicePrincipal("ec2.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("AmazonSSMManagedInstanceCore"),
                iam.ManagedPolicy.from_aws_managed_policy_name("AmazonS3ReadOnlyAccess"),
                iam.ManagedPolicy.from_aws_managed_policy_name("AdministratorAccess")
            ],
            inline_policies={
                "S3AccessPolicy": iam.PolicyDocument(
                    statements=[
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

        if db_instance.secret:
            db_instance.secret.grant_read(ec2_role)

        # ── EC2 Instance (backend/DB setup) ───────────────────────────────────
        secret_name = f"rds-credentials-{rds_name_key}"

        ec2_instance = ec2.Instance(
            self, "MyEC2Instance",
            role=ec2_role,
            instance_type=ec2.InstanceType.of(
                ec2.InstanceClass.T3,
                ec2.InstanceSize.MEDIUM
            ),
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
                    device_name="/dev/sda1",
                    volume=ec2.BlockDeviceVolume.ebs(
                        volume_size=300,
                        volume_type=ec2.EbsDeviceVolumeType.GP3,
                        delete_on_termination=True,
                        encrypted=True
                    )
                )
            ]
        )

        ec2_instance.add_user_data(
            "sudo apt update -y",
            "sudo apt install -y apache2 awscli jq postgresql-client-14",
            "systemctl start apache2",
            "systemctl enable apache2",
            "echo '<h1>Hello from AWS!</h1>' > /var/www/html/index.html",
            'cd home/ubuntu/',
            'mkdir startingggggg',
            'mkdir final',
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
            'sudo apt-get update -y',
            'sudo apt-get install -y postgresql postgresql-contrib',
            'sudo systemctl enable postgresql',
            'sudo systemctl start postgresql',
            "sudo systemctl restart postgresql || echo 'PostgreSQL restart failed'",
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
            f"export STACK_SELECTION={self.stack_selection}",
            "",
            'export PGPASSWORD="$DB_PASSWORD"',
            '',
            'echo "Testing database connection..."',
            'psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USERNAME" -d "$DB_NAME" -c "SELECT version();"',
            '',
            'echo "Downloading database dump file..."',
            "git clone https://github.com/1CloudHub/aivolvex-genai-foundry.git",
            '',
            'echo "Restoring database from dump file..."',
            'psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USERNAME" -d "$DB_NAME" -f ~/aivolvex-genai-foundry/dump-postgres.sql',
            '',
            'echo "Verifying restoration..."',
            'psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USERNAME" -d "$DB_NAME" -c "\\\\dn"',
            'psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USERNAME" -d "$DB_NAME" -c "\\\\dt foundry_app.*"',
            '',
            'echo "Database restoration completed successfully!"',
            "echo 'starting python code implementation'",
            "export DEBIAN_FRONTEND=noninteractive",
            "cd /home/ubuntu",
            "cd aivolvex-genai-foundry/ec2_needs",
            "sudo apt install python3.10-venv -y",
            "python3 -m venv eagle",
            "source eagle/bin/activate",
            "pip install -r requirements.txt --no-input",
            "pip install asgiref --no-input",
            "screen -dmS run_app bash -c 'source eagle/bin/activate && export S3_PATH=" + s3_name + " && uvicorn sun:asgi_app --host 0.0.0.0 --port 8000'",
            "echo 'DONE!!!!!!!!!!!!!!'",
            'EOF',
            'mkdir adding_permissionssssssss',
            'sudo chmod +x /home/ubuntu/restore_db.sh',
            'sudo chown ubuntu:ubuntu /home/ubuntu/restore_db.sh',
            'sudo chmod +x /home/ubuntu/voice_bot.sh',
            'sudo chown ubuntu:ubuntu /home/ubuntu/voice_bot.sh',
            'mkdir permissions_addeddddddd',
            'sleep 20',
            "sleep 30",
            'sudo su - ubuntu -c "/home/ubuntu/voice_bot.sh" > /var/log/voice_bot.log 2>&1'
        )

        # ── Lambda Layers ─────────────────────────────────────────────────────
        print("Creating Lambda layers from ZIP files...")
        layer_uploader = LambdaLayerUploader(self, "LambdaLayerUploader")

        boto3_layer = layer_uploader.layers.get("boto3")["layer"] if layer_uploader.layers.get("boto3") else None
        psycopg2_layer = layer_uploader.layers.get("psycopg2")["layer"] if layer_uploader.layers.get("psycopg2") else None
        requests_layer = layer_uploader.layers.get("requests")["layer"] if layer_uploader.layers.get("requests") else None
        requests_aws4auth_layer = layer_uploader.layers.get("requests_aws4auth")["layer"] if layer_uploader.layers.get("requests_aws4auth") else None
        opensearchpy_layer = layer_uploader.layers.get("opensearchpy")["layer"] if layer_uploader.layers.get("opensearchpy") else None

        # ── IAM Role for Main Lambda ──────────────────────────────────────────
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
            actions=["rds:*"],
            resources=["*"]
        ))

        lambda_role.add_to_policy(iam.PolicyStatement(
            sid="FullBedrockAccess",
            effect=iam.Effect.ALLOW,
            actions=["bedrock:*"],
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
            actions=["logs:*"],
            resources=["*"]
        ))

        lambda_role.add_to_policy(iam.PolicyStatement(
            sid="AccountSpecificLogGroupAccess",
            effect=iam.Effect.ALLOW,
            actions=["logs:CreateLogGroup"],
            resources=[f"arn:aws:logs:{self.region}:{self.account}:*"]
        ))

        lambda_role.add_to_policy(iam.PolicyStatement(
            sid="LambdaLogStreamAccess",
            effect=iam.Effect.ALLOW,
            actions=[
                "logs:CreateLogStream",
                "logs:PutLogEvents"
            ],
            resources=[f"arn:aws:logs:{self.region}:{self.account}:log-group:/aws/lambda/*:*"]
        ))

        if db_instance.secret:
            db_instance.secret.grant_read(lambda_role)

        # ── REST API Gateway ──────────────────────────────────────────────────
        api = apigateway.RestApi(
            self, "GenAIFoundryAPI",
            rest_api_name="genaifoundry-api" + name_key,
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

        # ── Environment variables for main and WebSocket Lambda ───────────────
        env_vars = {
            "CHAT_LOG_TABLE": "ce_cexp_logs",
            "KB_ID": logistics_kb.attr_knowledge_base_id,
            "LOGISTICS_KB_ID": logistics_kb.attr_knowledge_base_id,
            "chat_history_table": "chat_history",
            "banking_chat_history_table": "banking_chat_history",
            "db_database": rds_name_key,
            "db_host": db_instance.instance_endpoint.hostname,
            "db_port": "5432",
            "db_user": "postgres",
            "model_id": "us.anthropic.claude-3-7-sonnet-20250219-v1:0",
            "prompt_metadata_table": "prompt_metadata",
            "region_used": self.region,
            "region_name": self.region,
            "schema": "genaifoundry",
            "rds_secret_name": f"rds-credentials-{rds_name_key}",
            "rds_secret_arn": db_instance.secret.secret_arn if db_instance.secret else "",
            "rds_endpoint": db_instance.instance_endpoint.hostname,
            "rds_port": str(db_instance.instance_endpoint.port),
            "rds_database": rds_name_key,
            "rds_username": "postgres",
            "chat_tool_model": self.chat_tool_model
        }

        # ── Main Lambda Function ──────────────────────────────────────────────
        lambda_function = lambda_.Function(
            self, "MyLambdaFunction",
            runtime=lambda_.Runtime.PYTHON_3_9,
            handler="logistics.lambda_handler",
            code=lambda_.Code.from_asset("lambda_code"),
            function_name=lambda_name_key,
            memory_size=128,
            timeout=Duration.seconds(303),
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

        # ── WebSocket Lambda Function ─────────────────────────────────────────
        websocket_lambda_function = lambda_.Function(
            self, "WebSocketLambdaFunction",
            runtime=lambda_.Runtime.PYTHON_3_9,
            handler="websocket_handler.lambda_handler",
            code=lambda_.Code.from_asset("lambda_code"),
            function_name="ws_" + lambda_name_key,
            memory_size=128,
            timeout=Duration.seconds(29),
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

        # ── Lambda Integration for REST API ───────────────────────────────────
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

        # ── REST API Resources ────────────────────────────────────────────────
        # /chat_api
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

        # /genai_foundry_misc
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

        # /opensearch
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

        # ── WebSocket API Gateway ─────────────────────────────────────────────
        websocket_api = apigatewayv2.WebSocketApi(
            self, "GenAIFoundryWebSocketAPI" + name_key,
            api_name="GenAIFoundry_ws" + name_key
        )

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

        websocket_stage = apigatewayv2.WebSocketStage(
            self, "WebSocketStage",
            web_socket_api=websocket_api,
            stage_name="production",
            auto_deploy=True
        )

        # ── Update Lambda environment with runtime-known values ───────────────
        websocket_url = websocket_stage.url.replace('wss://', 'https://')

        websocket_lambda_function.add_environment("WEBSOCKET_ENDPOINT", websocket_url)
        websocket_lambda_function.add_environment("WEBSOCKET_REGION", self.region)
        websocket_lambda_function.add_environment("RDS_SECRET_NAME", f"rds-credentials-{rds_name_key}")
        websocket_lambda_function.add_environment("RDS_SECRET_ARN", db_instance.secret.secret_arn if db_instance.secret else "")
        websocket_lambda_function.add_environment("RDS_ENDPOINT", db_instance.instance_endpoint.hostname)
        websocket_lambda_function.add_environment("db_host", db_instance.instance_endpoint.hostname)
        websocket_lambda_function.add_environment("SOCKET_ENDPOINT", f"https://{api.rest_api_id}.execute-api.{self.region}.amazonaws.com/dev/")
        websocket_lambda_function.add_environment("socket_endpoint", websocket_url)

        lambda_function.add_environment("WEBSOCKET_ENDPOINT", websocket_url)
        lambda_function.add_environment("WEBSOCKET_REGION", self.region)
        lambda_function.add_environment("RDS_SECRET_NAME", f"rds-credentials-{rds_name_key}")
        lambda_function.add_environment("RDS_SECRET_ARN", db_instance.secret.secret_arn if db_instance.secret else "")
        lambda_function.add_environment("RDS_ENDPOINT", db_instance.instance_endpoint.hostname)
        lambda_function.add_environment("db_host", db_instance.instance_endpoint.hostname)
        lambda_function.add_environment("socket_endpoint", websocket_url)

        # ── Frontend EC2 Instance ─────────────────────────────────────────────
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
                    device_name="/dev/xvda",
                    volume=ec2.BlockDeviceVolume.ebs(
                        volume_size=300,
                        volume_type=ec2.EbsDeviceVolumeType.GP3,
                        delete_on_termination=True,
                        encrypted=True
                    )
                )
            ]
        )

        rest_api_name = f"genaifoundry-api{name_key}"
        websocket_api_name = f"GenAIFoundry_ws{name_key}"
        bucket_name = frontend_bucket_name
        region = self.region

        ec2_instance_front.add_user_data(
            "#!/bin/bash",
            "",
            "set -e  # Exit on any error",
            "",
            "echo \"Starting React deployment from S3...\"",
            "",
            f"export REST_API_NAME=\"{rest_api_name}\"",
            f"export WEBSOCKET_API_NAME=\"{websocket_api_name}\"",
            f"export BUCKET_NAME=\"{bucket_name}\"",
            f"export REGION=\"{region}\"",
            f"export STACK_SELECTION=\"{self.stack_selection}\"",
            "",
            "command_exists() {",
            "    command -v \"$1\" &> /dev/null",
            "}",
            "",
            "if ! command_exists unzip; then",
            "    sudo yum install -y unzip --allowerasing",
            "fi",
            "",
            "if ! command_exists curl; then",
            "    sudo yum install -y curl --allowerasing",
            "fi",
            "",
            "if ! command_exists node || ! command_exists npm; then",
            "    curl -fsSL https://rpm.nodesource.com/setup_18.x | sudo bash -",
            "    sudo yum install -y nodejs --allowerasing",
            "fi",
            "",
            "if ! command_exists aws; then",
            "    curl \"https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip\" -o \"awscliv2.zip\"",
            "    unzip awscliv2.zip",
            "    sudo ./aws/install",
            "    rm -rf aws awscliv2.zip",
            "fi",
            "",
            "WORK_DIR=~/react-app",
            "ZIP_FILE=\"src.zip\"",
            "S3_SOURCE_PATH=\"s3://${BUCKET_NAME}/${ZIP_FILE}\"",
            "",
            "mkdir -p \"$WORK_DIR\"",
            "cd \"$WORK_DIR\"",
            "",
            "aws s3 cp \"$S3_SOURCE_PATH\" . --region \"$REGION\"",
            "",
            "unzip -o \"$ZIP_FILE\"",
            "rm \"$ZIP_FILE\"",
            "",
            "npm install",
            "",
            "get_rest_api_id_by_name() {",
            "    aws apigateway get-rest-apis \\",
            "      --region \"$REGION\" \\",
            "      --query \"items[?name=='$1'].id\" \\",
            "      --output text",
            "}",
            "",
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
            "if [[ -z \"$API_ID_REST\" ]]; then",
            "    echo \"Error: Could not find REST API with name '$REST_API_NAME'\"",
            "    exit 1",
            "fi",
            "",
            "if [[ -z \"$API_ID_WS\" ]]; then",
            "    echo \"Error: Could not find WebSocket API with name '$WEBSOCKET_API_NAME'\"",
            "    exit 1",
            "fi",
            "",
            "VITE_API_BASE_URL=\"https://${API_ID_REST}.execute-api.${REGION}.amazonaws.com/dev/chat_api\"",
            "VITE_WEBSOCKET_URL=\"wss://${API_ID_WS}.execute-api.${REGION}.amazonaws.com/production/\"",
            "VITE_WEBSOCKET_URL_VOICEOPS=\"https://${API_ID_WS}.execute-api.${REGION}.amazonaws.com/production/\"",
            "",
            "ENV_FILE=\".env\"",
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
            "update_env_var \"VITE_STACK_SELECTION\" \"$STACK_SELECTION\"",
            "",
            "npm run build",
            "",
            "aws s3 rm \"s3://${BUCKET_NAME}/\" --recursive --region \"$REGION\"",
            "aws s3 cp dist/ \"s3://${BUCKET_NAME}/\" --recursive --region \"$REGION\"",
            "echo \"Done! React app built and uploaded to s3://${BUCKET_NAME}/\"",
            "TOKEN=$(curl -s -X PUT \"http://169.254.169.254/latest/api/token\" -H \"X-aws-ec2-metadata-token-ttl-seconds: 21600\")",
            "INSTANCE_ID=$(curl -s -H \"X-aws-ec2-metadata-token: $TOKEN\" http://169.254.169.254/latest/meta-data/instance-id)",
            "aws ec2 terminate-instances --instance-ids \"$INSTANCE_ID\" --region \"$REGION\""
        )

        # ── CloudFront Distribution ───────────────────────────────────────────
        s3_origin = origins.S3BucketOrigin(
            frontend_bucket,
            origin_path=""
        )

        distribution = cloudfront.Distribution(
            self, "GenAIFoundryDistribution",
            default_behavior=cloudfront.BehaviorOptions(
                origin=s3_origin,
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                cache_policy=cloudfront.CachePolicy.CACHING_OPTIMIZED,
                origin_request_policy=None,
                response_headers_policy=None
            ),
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

        cfn_dist = distribution.node.default_child
        cfn_dist.add_property_override(
            "DistributionConfig.Origins.0.OriginAccessControlId", oac.attr_id
        )
        cfn_dist.add_property_deletion_override(
            "DistributionConfig.Origins.0.S3OriginConfig.OriginAccessIdentity"
        )
        cfn_dist.add_dependency(oac)

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

        invalidation.node.add_dependency(frontend_deploy)
        invalidation.node.add_dependency(distribution)

        frontend_bucket.add_to_resource_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                principals=[
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

        CfnOutput(
            self, "CloudFrontDistributionUrl",
            value=f"https://{distribution.distribution_domain_name}",
            description="CloudFront Distribution URL for the frontend application"
        )

    def create_kb(self, name: str, s3_uri: str, model_arn: str, role_arn: str, data_prefix: str, index_name: str, index_creator_function: lambda_.Function, index_creator: CustomResource, provider: cr.Provider, collection_arn: str, data_access_policy: opensearch.CfnAccessPolicy, index_waiter: CustomResource = None, index_waiter_function: lambda_.Function = None, index_waiter_provider: cr.Provider = None):
        """Create a Bedrock Knowledge Base with data source"""

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

        if index_waiter:
            kb.node.add_dependency(index_waiter)
        if index_waiter_function:
            kb.node.add_dependency(index_waiter_function)
        if index_waiter_provider:
            kb.node.add_dependency(index_waiter_provider)

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

        kb.data_source_id = data_source.attr_data_source_id

        return kb
