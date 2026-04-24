from aws_cdk import (
    Stack,
    Duration,
    CfnOutput,
    RemovalPolicy,
    aws_ec2 as ec2,
    aws_iam as iam,
    aws_lambda as lambda_,
    aws_s3 as s3,
    aws_s3_deployment as s3deploy,
    aws_apigateway as apigateway,
    aws_cloudfront as cloudfront,
    aws_cloudfront_origins as origins,
    custom_resources as cr,
)
from constructs import Construct
import random
import string
import time


def generate_random_suffix(length: int = 8) -> str:
    chars = string.ascii_lowercase + string.digits
    return "".join(random.choice(chars) for _ in range(length))


class MediaCdkStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        stack_selection: str = "unknown",
        chat_tool_model: str = "us.amazon.nova-pro-v1:0",
        model_selection: str = "amazon",
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        suffix = generate_random_suffix()
        is_amazon_selected = model_selection == "amazon"
        ec2_media_entry_file = "nova_main.py" if is_amazon_selected else "main.py"

        media_bucket_name = f"genaifoundryc-{suffix}"
        frontend_bucket_name = f"genaifoundry-front-{suffix}"

        vpc = ec2.Vpc(
            self,
            "MediaVpc",
            max_azs=2,
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
                ),
            ],
        )

        ec2_sg = ec2.SecurityGroup(
            self,
            "MediaEc2SecurityGroup",
            vpc=vpc,
            description="Security group for media EC2 instance",
            allow_all_outbound=True,
        )
        ec2_sg.add_ingress_rule(ec2.Peer.any_ipv4(), ec2.Port.tcp(22), "SSH")
        ec2_sg.add_ingress_rule(ec2.Peer.any_ipv4(), ec2.Port.tcp(8000), "Media API")

        media_bucket = s3.Bucket(
            self,
            "MediaOutputBucket",
            bucket_name=media_bucket_name,
            versioned=True,
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
        )

        # Frontend bucket for static website hosting (public)
        frontend_bucket = s3.Bucket(
            self,
            "MediaFrontendBucket",
            bucket_name=frontend_bucket_name,
            versioned=True,
            removal_policy=RemovalPolicy.DESTROY,  # For development only
            auto_delete_objects=True,  # For development only
            website_index_document="index.html",
            website_error_document="index.html",
        )

        # Upload frontend package as-is. Existing deployment flow can build from src.zip if needed.
        frontend_deploy = s3deploy.BucketDeployment(
            self,
            "DeployMediaFrontend",
            sources=[s3deploy.Source.asset("genaifoundry-front")],
            destination_bucket=frontend_bucket,
        )

        ec2_role = iam.Role(
            self,
            "MediaEc2Role",
            assumed_by=iam.ServicePrincipal("ec2.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("AmazonSSMManagedInstanceCore"),
                iam.ManagedPolicy.from_aws_managed_policy_name("AmazonS3FullAccess"),
                iam.ManagedPolicy.from_aws_managed_policy_name("AdministratorAccess"),
            ],
        )

        ec2_instance = ec2.Instance(
            self,
            "MediaApiEc2",
            vpc=vpc,
            role=ec2_role,
            security_group=ec2_sg,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
            instance_type=ec2.InstanceType.of(ec2.InstanceClass.T3, ec2.InstanceSize.MEDIUM),
            machine_image=ec2.MachineImage.latest_amazon_linux2023(),
        )
        ec2_instance_front = ec2.Instance(
            self,
            "MediaFrontendEc2",
            vpc=vpc,
            role=ec2_role,
            security_group=ec2_sg,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
            instance_type=ec2.InstanceType.of(ec2.InstanceClass.T3, ec2.InstanceSize.MEDIUM),
            machine_image=ec2.MachineImage.latest_amazon_linux2023(),
        )

        ec2_instance.add_user_data(
            "set -euxo pipefail",
            "dnf update -y",
            "dnf install -y git python3.11 python3.11-pip jq screen",
            "dnf install -y ffmpeg || true",
            "if ! command -v aws >/dev/null 2>&1; then dnf install -y awscli; fi",
            "cd /home/ec2-user",
            "git clone https://github.com/1CloudHub/aivolvex-genai-foundry.git || true",
            "cd aivolvex-genai-foundry/media_ec2_needs",
            "python3.11 -m venv .venv",
            "source .venv/bin/activate",
            "pip install --upgrade pip setuptools wheel",
            "pip install -r requirements.txt",
            "cat > /home/ec2-user/start_media_api.sh << 'SCRIPT'",
            "#!/bin/bash",
            f"export SOURCE_BUCKET=public-media-sandbox",
            f"export OUTPUT_BUCKET={media_bucket_name}",
            f"export REGION={self.region}",
            f"export STACK_SELECTION={stack_selection}",
            f"export CHAT_TOOL_MODEL={chat_tool_model}",
            "cd /home/ec2-user/aivolvex-genai-foundry/media_ec2_needs",
            "source .venv/bin/activate",
            f"screen -dmS media_api bash -c 'source .venv/bin/activate && uvicorn {ec2_media_entry_file.replace('.py', '')}:app --host 0.0.0.0 --port 8000 > /home/ec2-user/media-api.log 2>&1'",
            "SCRIPT",
            "chmod +x /home/ec2-user/start_media_api.sh",
            "chown ec2-user:ec2-user /home/ec2-user/start_media_api.sh",
            "sudo su - ec2-user -c '/home/ec2-user/start_media_api.sh'",
            f"export REGION={self.region}",
            f"export COACHING_API_NAME=\"coaching_assist_media-{suffix}\"",
            "TOKEN=$(curl -s -X PUT \"http://169.254.169.254/latest/api/token\" -H \"X-aws-ec2-metadata-token-ttl-seconds: 21600\")",
            "PUBLIC_IP=$(curl -s -H \"X-aws-ec2-metadata-token: $TOKEN\" http://169.254.169.254/latest/meta-data/public-ipv4)",
            "COACHING_API_ID=$(aws apigateway get-rest-apis --region \"$REGION\" --query \"items[?name=='$COACHING_API_NAME'].id\" --output text)",
            "ROOT_RESOURCE_ID=$(aws apigateway get-resources --rest-api-id \"$COACHING_API_ID\" --region \"$REGION\" --query \"items[?path=='/'].id\" --output text)",
            "EDIT_RESOURCE_ID=$(aws apigateway get-resources --rest-api-id \"$COACHING_API_ID\" --region \"$REGION\" --query \"items[?path=='/edit_video'].id\" --output text)",
            "aws apigateway update-integration --rest-api-id \"$COACHING_API_ID\" --resource-id \"$ROOT_RESOURCE_ID\" --http-method ANY --region \"$REGION\" --patch-operations op=replace,path=/uri,value=\"http://${PUBLIC_IP}:8000\"",
            "aws apigateway update-integration --rest-api-id \"$COACHING_API_ID\" --resource-id \"$EDIT_RESOURCE_ID\" --http-method POST --region \"$REGION\" --patch-operations op=replace,path=/uri,value=\"http://${PUBLIC_IP}:8000/edit-video\"",
        )

        lambda_role = iam.Role(
            self,
            "MediaLambdaRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSLambdaBasicExecutionRole"),
                iam.ManagedPolicy.from_aws_managed_policy_name("AmazonS3ReadOnlyAccess"),
            ],
        )
        lambda_role.add_to_policy(
            iam.PolicyStatement(
                actions=["s3:GetObject", "s3:ListBucket"],
                resources=[media_bucket.bucket_arn, f"{media_bucket.bucket_arn}/*"],
            )
        )

        media_boto3_layer = lambda_.LayerVersion(
            self,
            "MediaBoto3Layer",
            code=lambda_.Code.from_asset("layers/boto3-9e4ca0fc-be18-4b62-8bb2-40b541fc7de6.zip"),
            compatible_runtimes=[lambda_.Runtime.PYTHON_3_11],
            description="Boto3 layer for Media Lambda",
        )
        media_requests_layer = lambda_.LayerVersion(
            self,
            "MediaRequestsLayer",
            code=lambda_.Code.from_asset("layers/requests-0899e8ab-9427-46b4-b6e7-3d3c376139dc.zip"),
            compatible_runtimes=[lambda_.Runtime.PYTHON_3_11],
            description="Requests layer for Media Lambda",
        )

        media_lambda = lambda_.Function(
            self,
            "MediaStreamingLambda",
            runtime=lambda_.Runtime.PYTHON_3_11,
            handler="media.lambda_handler",
            code=lambda_.Code.from_asset("lambda_code"),
            timeout=Duration.seconds(60),
            memory_size=256,
            role=lambda_role,
            layers=[media_boto3_layer, media_requests_layer],
            environment={
                "PUBLIC_MEDIA_BASE_URL": "https://public-media-sandbox.s3-us-west-2.amazonaws.com",
                "region_used": self.region,
            },
        )

        media_api = apigateway.RestApi(
            self,
            "MediaRestApi",
            rest_api_name=f"genaifoundry-media-api-{suffix}",
            description="Media API Gateway for Lambda + EC2 integrations",
            binary_media_types=["multipart/form-data"],
            default_cors_preflight_options=apigateway.CorsOptions(
                allow_origins=["*"],
                allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
                allow_headers=["*"],
            ),
            deploy_options=apigateway.StageOptions(
                stage_name="dev",
                logging_level=apigateway.MethodLoggingLevel.OFF,
                data_trace_enabled=False,
            ),
        )
        coaching_api = apigateway.RestApi(
            self,
            "MediaCoachingApi",
            rest_api_name=f"coaching_assist_media-{suffix}",
            description="Media EC2 proxy API",
            default_cors_preflight_options=apigateway.CorsOptions(
                allow_origins=["*"],
                allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
                allow_headers=["*"],
            ),
            deploy_options=apigateway.StageOptions(
                stage_name="dev",
                logging_level=apigateway.MethodLoggingLevel.OFF,
                data_trace_enabled=False,
            ),
        )

        lambda_integration = apigateway.LambdaIntegration(
            media_lambda,
            proxy=False,
            request_templates={"application/json": "$input.json('$')"},
            integration_responses=[
                apigateway.IntegrationResponse(
                    status_code="200",
                    response_parameters={
                        "method.response.header.Access-Control-Allow-Origin": "'*'",
                        "method.response.header.Access-Control-Allow-Headers": "'*'",
                        "method.response.header.Access-Control-Allow-Methods": "'*'",
                    },
                )
            ],
        )
        media_streaming_lambda_integration = apigateway.LambdaIntegration(
            media_lambda,
            proxy=False,
            request_templates={
                "application/json": '{"event_type":"media_streaming","media_id":$input.json(\'$.media_id\')}'
            },
            integration_responses=[
                apigateway.IntegrationResponse(
                    status_code="200",
                    response_parameters={
                        "method.response.header.Access-Control-Allow-Origin": "'*'",
                        "method.response.header.Access-Control-Allow-Headers": "'*'",
                        "method.response.header.Access-Control-Allow-Methods": "'*'",
                    },
                )
            ],
        )

        ec2_edit_video_integration = apigateway.HttpIntegration(
            url="http://127.0.0.1:8000/edit-video",
            proxy=True,
            options=apigateway.IntegrationOptions(timeout=Duration.seconds(29)),
        )
        ec2_root_proxy_integration = apigateway.HttpIntegration(
            url="http://127.0.0.1:8000",
            proxy=True,
            options=apigateway.IntegrationOptions(timeout=Duration.seconds(29)),
        )

        for path_name, integration in {
            "chat_api": lambda_integration,
            "edit_video": lambda_integration,
            "genai_foundry_misc": lambda_integration,
            "media_streaming": media_streaming_lambda_integration,
        }.items():
            res = media_api.root.add_resource(path_name)
            res.add_method(
                "POST",
                integration,
                method_responses=[
                    apigateway.MethodResponse(
                        status_code="200",
                        response_parameters={
                            "method.response.header.Access-Control-Allow-Origin": True,
                            "method.response.header.Access-Control-Allow-Headers": True,
                            "method.response.header.Access-Control-Allow-Methods": True,
                        },
                    )
                ],
            )

        coaching_api.root.add_method(
            "ANY",
            ec2_root_proxy_integration,
            method_responses=[
                apigateway.MethodResponse(
                    status_code="200",
                    response_parameters={"method.response.header.Access-Control-Allow-Origin": True},
                )
            ],
        )
        coaching_edit_video_resource = coaching_api.root.add_resource("edit_video")
        coaching_edit_video_resource.add_method(
            "POST",
            ec2_edit_video_integration,
            method_responses=[
                apigateway.MethodResponse(
                    status_code="200",
                    response_parameters={"method.response.header.Access-Control-Allow-Origin": True},
                )
            ],
        )

        media_api_base_url = f"https://{media_api.rest_api_id}.execute-api.{self.region}.amazonaws.com/dev/chat_api"
        media_streaming_api_url = f"https://{coaching_api.rest_api_id}.execute-api.{self.region}.amazonaws.com/dev/edit_video"
        media_misc_api_url = f"https://{media_api.rest_api_id}.execute-api.{self.region}.amazonaws.com/dev/genai_foundry_misc"
        media_api_name = f"genaifoundry-media-api-{suffix}"

        # Build and deploy frontend with Media-specific environment values.
        ec2_instance_front.add_user_data(
            "#!/bin/bash",
            "set -e",
            f"export REST_API_NAME=\"{media_api_name}\"",
            f"export COACHING_API_NAME=\"coaching_assist_media-{suffix}\"",
            f"export BUCKET_NAME=\"{frontend_bucket_name}\"",
            f"export REGION=\"{self.region}\"",
            "command -v git >/dev/null 2>&1 || sudo yum install -y git --allowerasing",
            "command -v unzip >/dev/null 2>&1 || sudo yum install -y unzip --allowerasing",
            "command -v curl >/dev/null 2>&1 || sudo yum install -y curl --allowerasing",
            "if ! command -v node >/dev/null 2>&1 || ! command -v npm >/dev/null 2>&1; then curl -fsSL https://rpm.nodesource.com/setup_18.x | sudo bash - && sudo yum install -y nodejs --allowerasing; fi",
            "if ! command -v aws >/dev/null 2>&1; then curl \"https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip\" -o \"awscliv2.zip\" && unzip awscliv2.zip && sudo ./aws/install && rm -rf aws awscliv2.zip; fi",
            "cd /home/ec2-user",
            "git clone https://github.com/1CloudHub/aivolvex-genai-foundry.git || true",
            "SRC_ZIP=$(find /home/ec2-user/aivolvex-genai-foundry -name 'src.zip' | head -1 || true)",
            "if [ -n \"$SRC_ZIP\" ]; then",
            "    APP_DIR=$(dirname \"$SRC_ZIP\")",
            "    cd \"$APP_DIR\"",
            "    unzip -o src.zip",
            "    PKG_PATH=$(find . -name package.json | head -1 || true)",
            "    if [ -n \"$PKG_PATH\" ]; then APP_DIR=$(dirname \"$PKG_PATH\"); fi",
            "    cd \"$APP_DIR\"",
            "else",
            "    PKG_FILE=$(find /home/ec2-user/aivolvex-genai-foundry -name package.json 2>/dev/null | head -1 || true)",
            "    if [ -n \"$PKG_FILE\" ]; then APP_DIR=$(dirname \"$PKG_FILE\"); else echo 'Could not locate src.zip or package.json under cloned repository'; exit 1; fi",
            "    cd \"$APP_DIR\"",
            "fi",
            "touch .env",
            "update_env_var(){ key=\"$1\"; val=\"$2\"; if grep -q \"^${key}=\" .env; then sed -i \"s|^${key}=.*|${key}=${val}|\" .env; else echo \"${key}=${val}\" >> .env; fi; }",
            "API_ID_REST=$(aws apigateway get-rest-apis --region \"$REGION\" --query \"items[?name=='$REST_API_NAME'].id\" --output text)",
            "API_ID_COACHING=$(aws apigateway get-rest-apis --region \"$REGION\" --query \"items[?name=='$COACHING_API_NAME'].id\" --output text)",
            "VITE_API_BASE_URL=\"https://${API_ID_REST}.execute-api.${REGION}.amazonaws.com/dev/chat_api\"",
            "VITE_MEDIA_STREAMING_API_URL=\"https://${API_ID_COACHING}.execute-api.${REGION}.amazonaws.com/dev/edit_video\"",
            "VITE_GENAI_FOUNDRY_MISC_URL=\"https://${API_ID_REST}.execute-api.${REGION}.amazonaws.com/dev/genai_foundry_misc\"",
            "VITE_EC2_BASE_URL=\"http://127.0.0.1:8000\"",
            "update_env_var VITE_API_BASE_URL \"$VITE_API_BASE_URL\"",
            "update_env_var VITE_MEDIA_STREAMING_API_URL \"$VITE_MEDIA_STREAMING_API_URL\"",
            "update_env_var VITE_GENAI_FOUNDRY_MISC_URL \"$VITE_GENAI_FOUNDRY_MISC_URL\"",
            "update_env_var VITE_EC2_BASE_URL \"$VITE_EC2_BASE_URL\"",
            f"export MEDIA_STACK_SUFFIX=\"{suffix}\"",
            "MEDIA_EC2_IP=$(aws ec2 describe-instances --region \"$REGION\" --filters \"Name=tag:aws:cloudformation:stack-name,Values=GenAiFoundryMediaStack\" \"Name=instance-state-name,Values=running\" \"Name=tag:Name,Values=*MediaApiEc2*\" --query 'Reservations[0].Instances[0].PublicIpAddress' --output text 2>/dev/null || echo '')",
            "update_env_var VITE_EC2_IP \"$MEDIA_EC2_IP\"",
            f"update_env_var VITE_STACK_SELECTION \"{stack_selection}\"",
            "npm install",
            "npm run build",
            f"aws s3 rm s3://{frontend_bucket_name}/ --recursive --region {self.region} || true",
            f"aws s3 cp dist/ s3://{frontend_bucket_name}/ --recursive --region {self.region}",
            "TOKEN=$(curl -s -X PUT \"http://169.254.169.254/latest/api/token\" -H \"X-aws-ec2-metadata-token-ttl-seconds: 21600\")",
            "INSTANCE_ID=$(curl -s -H \"X-aws-ec2-metadata-token: $TOKEN\" http://169.254.169.254/latest/meta-data/instance-id)",
            f"aws ec2 terminate-instances --instance-ids \"$INSTANCE_ID\" --region {self.region}",
        )

        s3_origin = origins.S3BucketOrigin(
            frontend_bucket,
            origin_path="",
        )

        distribution = cloudfront.Distribution(
            self,
            "MediaFrontendDistribution",
            default_behavior=cloudfront.BehaviorOptions(
                origin=s3_origin,
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                cache_policy=cloudfront.CachePolicy.CACHING_OPTIMIZED,
                origin_request_policy=None,
                response_headers_policy=None,
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
                    ttl=Duration.seconds(10),
                ),
                cloudfront.ErrorResponse(
                    http_status=404,
                    response_http_status=200,
                    response_page_path="/index.html",
                    ttl=Duration.seconds(10),
                ),
            ],
        )

        oac = cloudfront.CfnOriginAccessControl(
            self,
            "MediaFrontendOAC",
            origin_access_control_config=cloudfront.CfnOriginAccessControl.OriginAccessControlConfigProperty(
                name=f"{suffix}-media-frontend-oac",
                description="OAC for media frontend S3 origin",
                origin_access_control_origin_type="s3",
                signing_behavior="always",
                signing_protocol="sigv4",
            ),
        )
        cfn_dist = distribution.node.default_child  # type: ignore
        cfn_dist.add_property_override(
            "DistributionConfig.Origins.0.OriginAccessControlId",
            oac.attr_id,
        )
        cfn_dist.add_property_deletion_override(
            "DistributionConfig.Origins.0.S3OriginConfig.OriginAccessIdentity"
        )
        cfn_dist.add_dependency(oac)

        invalidation = cr.AwsCustomResource(
            self,
            "MediaFrontendInvalidation",
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
                    f"InvalidateMediaFrontend-{int(time.time())}"
                ),
            ),
            policy=cr.AwsCustomResourcePolicy.from_statements(
                [
                    iam.PolicyStatement(
                        actions=[
                            "cloudfront:CreateInvalidation",
                            "cloudfront:GetInvalidation",
                            "cloudfront:ListInvalidations",
                        ],
                        resources=["*"],
                    )
                ]
            ),
        )
        invalidation.node.add_dependency(frontend_deploy)
        invalidation.node.add_dependency(distribution)

        frontend_bucket.add_to_resource_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                principals=[iam.ArnPrincipal(f"arn:aws:iam::{self.account}:root")],
                actions=[
                    "s3:DeleteObject*",
                    "s3:GetBucket*",
                    "s3:GetObject",
                    "s3:List*",
                    "s3:PutBucketPolicy",
                ],
                resources=[
                    frontend_bucket.bucket_arn,
                    f"{frontend_bucket.bucket_arn}/*",
                ],
            )
        )
        frontend_bucket.add_to_resource_policy(
            iam.PolicyStatement(
                sid="AllowCloudFrontAccess",
                effect=iam.Effect.ALLOW,
                principals=[iam.ServicePrincipal("cloudfront.amazonaws.com")],
                actions=["s3:GetObject"],
                resources=[f"{frontend_bucket.bucket_arn}/*"],
                conditions={"StringEquals": {"AWS:SourceArn": distribution.distribution_arn}},
            )
        )

        CfnOutput(self, "MediaEc2Ip", value=ec2_instance.instance_public_ip)
        CfnOutput(self, "MediaStreamingBucketName", value=media_bucket_name)
        CfnOutput(self, "MediaApiBaseUrl", value=media_api_base_url)
        CfnOutput(self, "MediaStreamingApiUrl", value=media_streaming_api_url)
        CfnOutput(self, "MediaMiscApiUrl", value=media_misc_api_url)
        CfnOutput(self, "MediaEc2ProxyApiUrl", value=coaching_api.url)
        CfnOutput(self, "ViteApiBaseUrl", value=media_api_base_url)
        CfnOutput(self, "ViteMediaStreamingApiUrl", value=media_streaming_api_url)
        CfnOutput(self, "MediaCloudFrontUrl", value=f"https://{distribution.distribution_domain_name}")
