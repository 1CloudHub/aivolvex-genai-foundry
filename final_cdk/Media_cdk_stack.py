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
)
from constructs import Construct
import random
import string


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

        frontend_bucket = s3.Bucket(
            self,
            "MediaFrontendBucket",
            bucket_name=frontend_bucket_name,
            versioned=True,
            website_index_document="index.html",
            website_error_document="index.html",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
        )

        # Upload frontend package as-is. Existing deployment flow can build from src.zip if needed.
        s3deploy.BucketDeployment(
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
                iam.ManagedPolicy.from_aws_managed_policy_name("AmazonBedrockFullAccess"),
            ],
        )
        media_bucket.grant_read_write(ec2_role)

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

        ec2_instance.add_user_data(
            "set -euxo pipefail",
            "dnf update -y",
            "dnf install -y git python3.11 python3.11-pip ffmpeg",
            "cd /home/ec2-user",
            "git clone https://github.com/1CloudHub/aivolvex-genai-foundry.git || true",
            "cd aivolvex-genai-foundry/media_ec2_needs",
            "python3.11 -m venv .venv",
            "source .venv/bin/activate",
            "pip install --upgrade pip setuptools wheel",
            "pip install -r requirements.txt",
            "export SOURCE_BUCKET=public-media-sandbox",
            f"export OUTPUT_BUCKET={media_bucket_name}",
            f"export AWS_REGION={self.region}",
            f"export REGION={self.region}",
            f"export STACK_SELECTION={stack_selection}",
            f"export CHAT_TOOL_MODEL={chat_tool_model}",
            (
                "nohup bash -c 'source .venv/bin/activate && "
                f"uvicorn {ec2_media_entry_file.replace('.py', '')}:app --host 0.0.0.0 --port 8000' "
                "> /home/ec2-user/media-api.log 2>&1 &"
            ),
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

        media_lambda = lambda_.Function(
            self,
            "MediaStreamingLambda",
            runtime=lambda_.Runtime.PYTHON_3_11,
            handler="media.lambda_handler",
            code=lambda_.Code.from_asset("lambda_code"),
            timeout=Duration.seconds(60),
            memory_size=256,
            role=lambda_role,
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

        ec2_chat_integration = apigateway.HttpIntegration(
            url=f"http://{ec2_instance.instance_public_ip}:8000/chat_api",
            proxy=True,
            options=apigateway.IntegrationOptions(timeout=Duration.seconds(29)),
        )
        ec2_edit_video_integration = apigateway.HttpIntegration(
            url=f"http://{ec2_instance.instance_public_ip}:8000/edit-video",
            proxy=True,
            options=apigateway.IntegrationOptions(timeout=Duration.seconds(29)),
        )
        ec2_root_proxy_integration = apigateway.HttpIntegration(
            url=f"http://{ec2_instance.instance_public_ip}:8000",
            proxy=True,
            options=apigateway.IntegrationOptions(timeout=Duration.seconds(29)),
        )

        for path_name, integration in {
            "chat_api": ec2_chat_integration,
            "edit_video": ec2_edit_video_integration,
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
        media_streaming_api_url = f"https://{media_api.rest_api_id}.execute-api.{self.region}.amazonaws.com/dev/edit_video"
        media_misc_api_url = f"https://{media_api.rest_api_id}.execute-api.{self.region}.amazonaws.com/dev/genai_foundry_misc"
        media_api_name = f"genaifoundry-media-api-{suffix}"

        # Build and deploy frontend with Media-specific environment values.
        ec2_instance.add_user_data(
            "set -euxo pipefail",
            "dnf install -y unzip jq",
            "if ! command -v aws >/dev/null 2>&1; then dnf install -y awscli; fi",
            "if ! command -v node >/dev/null 2>&1; then curl -fsSL https://rpm.nodesource.com/setup_18.x | bash -; dnf install -y nodejs; fi",
            "cd /home/ec2-user/aivolvex-genai-foundry/genaifoundry-front",
            "rm -rf /home/ec2-user/media-front-build && mkdir -p /home/ec2-user/media-front-build",
            "unzip -o src.zip -d /home/ec2-user/media-front-build",
            "APP_DIR=/home/ec2-user/media-front-build",
            "if [ ! -f \"$APP_DIR/package.json\" ]; then PKG_PATH=$(find /home/ec2-user/media-front-build -name package.json | head -n 1 || true); if [ -n \"$PKG_PATH\" ]; then APP_DIR=$(dirname \"$PKG_PATH\"); fi; fi",
            "cd \"$APP_DIR\"",
            "touch .env",
            "update_env_var(){ key=\"$1\"; val=\"$2\"; if grep -q \"^${key}=\" .env; then sed -i \"s|^${key}=.*|${key}=${val}|\" .env; else echo \"${key}=${val}\" >> .env; fi; }",
            f"export REST_API_NAME=\"{media_api_name}\"",
            "API_ID_REST=$(aws apigateway get-rest-apis --region \"$REGION\" --query \"items[?name=='$REST_API_NAME'].id\" --output text)",
            "VITE_API_BASE_URL=\"https://${API_ID_REST}.execute-api.${REGION}.amazonaws.com/dev/chat_api\"",
            "VITE_MEDIA_STREAMING_API_URL=\"https://${API_ID_REST}.execute-api.${REGION}.amazonaws.com/dev/edit_video\"",
            "VITE_GENAI_FOUNDRY_MISC_URL=\"https://${API_ID_REST}.execute-api.${REGION}.amazonaws.com/dev/genai_foundry_misc\"",
            "VITE_EC2_BASE_URL=\"http://127.0.0.1:8000\"",
            "update_env_var VITE_API_BASE_URL \"$VITE_API_BASE_URL\"",
            "update_env_var VITE_MEDIA_STREAMING_API_URL \"$VITE_MEDIA_STREAMING_API_URL\"",
            "update_env_var VITE_GENAI_FOUNDRY_MISC_URL \"$VITE_GENAI_FOUNDRY_MISC_URL\"",
            "update_env_var VITE_EC2_BASE_URL \"$VITE_EC2_BASE_URL\"",
            f"update_env_var VITE_EC2_IP \"{ec2_instance.instance_public_ip}\"",
            f"update_env_var VITE_STACK_SELECTION \"{stack_selection}\"",
            "npm install",
            "npm run build",
            f"aws s3 rm s3://{frontend_bucket_name}/ --recursive --region {self.region} || true",
            f"aws s3 cp dist/ s3://{frontend_bucket_name}/ --recursive --region {self.region}",
        )

        distribution = cloudfront.Distribution(
            self,
            "MediaFrontendDistribution",
            default_behavior=cloudfront.BehaviorOptions(
                origin=origins.S3BucketOrigin(frontend_bucket),
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
            ),
            default_root_object="index.html",
            error_responses=[
                cloudfront.ErrorResponse(http_status=403, response_http_status=200, response_page_path="/index.html"),
                cloudfront.ErrorResponse(http_status=404, response_http_status=200, response_page_path="/index.html"),
            ],
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
