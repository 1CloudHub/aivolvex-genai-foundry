from aws_cdk import (
    Stack,
    aws_ec2 as ec2,
    aws_s3 as s3,
    aws_iam as iam,
    aws_rds as rds,
    RemovalPolicy,
    aws_s3_deployment as s3deploy,
    CfnOutput,
    Duration
)
from constructs import Construct
import random
import string

def generate_random_alphanumeric(length=12):
    if not 3 <= length <= 63:
        raise ValueError("Length must be between 3 and 63 characters.")

    # First character must be a letter
    first_char = random.choice(string.ascii_letters)

    # Remaining characters can be letters, numbers, or hyphens  
    remaining_chars = string.ascii_letters + string.digits + '-'

    # Generate the remaining characters
    remaining_part = ''.join(random.choices(remaining_chars, k=length - 1))

    return first_char + remaining_part

name_key = generate_random_alphanumeric()
print(name_key)
s3_name = "genai-foundry-test"
class DeployStack(Stack):

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Create VPC
        vpc = ec2.Vpc(
            self, "MyVPC",
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

        key_pair = ec2.KeyPair(
            self, "MyKeyPair",
            key_pair_name=f"keypair-{name_key}",  # Use your random name
            type=ec2.KeyPairType.RSA,
            format=ec2.KeyPairFormat.PEM
        )


        # Create security group for RDS
        rds_security_group = ec2.SecurityGroup(
            self, "MyRDSSecurityGroup",
            vpc=vpc,
            description="Security group for RDS instance",
            allow_all_outbound=False
        )

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
        bucket = s3.Bucket(
            self, 
            "MyBucket",
            bucket_name=s3_name,
            versioned=True,
            removal_policy=RemovalPolicy.DESTROY,  # For development only
            auto_delete_objects=True  # For development only
        )
        
        # Upload folder contents to the bucket
        s3deploy.BucketDeployment(
            self,
            "DeployFolder",
            sources=[s3deploy.Source.asset("genaifoundy-usecases")],  # Path to your local folder
            destination_bucket=bucket,
            destination_key_prefix="root/",  # Optional: prefix for uploaded files
        )

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
            security_groups=[rds_security_group],
            credentials=rds.Credentials.from_generated_secret(
                username="postgres",
                secret_name=f"rds-credentials-{name_key}"  # Make it unique
            ),
            allocated_storage=20,
            storage_type=rds.StorageType.GP2,
            deletion_protection=False,
            delete_automated_backups=False,
            backup_retention=Duration.days(7),
            removal_policy=RemovalPolicy.DESTROY,
            database_name="myapp"
        )
        
        # Create IAM role for EC2
        ec2_role = iam.Role(
            self, "EC2Role",
            assumed_by=iam.ServicePrincipal("ec2.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("AmazonSSMManagedInstanceCore"),
                iam.ManagedPolicy.from_aws_managed_policy_name("AmazonS3ReadOnlyAccess")
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
                    resources=[f"arn:aws:s3:::{s3_name}/*"]
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

        # Get the secret name that will be created (this is available at synthesis time)
        secret_name = f"rds-credentials-{name_key}"

        # Add user data with database restoration script
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
    f'SECRET_JSON=$(aws secretsmanager get-secret-value --secret-id "{secret_name}" --query SecretString --output text --region ap-southeast-1)',
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
        
        # Outputs
        CfnOutput(
            self, "VPCId",
            value=vpc.vpc_id,
            description="VPC ID"
        )

        CfnOutput(
            self, "InstanceId",
            value=ec2_instance.instance_id,
            description="EC2 Instance ID"
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
            "BucketName",
            value=bucket.bucket_name,
            description="Name of the S3 bucket"
        )

        CfnOutput(
            self, "KeyPairName",
            value=key_pair.key_pair_name,
            description="Key pair name for SSH access"
        )
        CfnOutput(
            self, "PrivateKeyCommand",
            value=f"aws ssm get-parameter --name /ec2/keypair/{key_pair.key_pair_id} --with-decryption --query Parameter.Value --output text",
            description="Command to retrieve private key"
        )