# GenAI Foundry CDK Deployment Guide

## Overview

GenAI Foundry is an advanced AWS CDK stack that provisions infrastructure for a multi-knowledge base generative AI platform.  
It integrates Amazon Bedrock with OpenSearch Serverless, Amazon RDS, AWS Lambda, and API Gateway to enable intelligent data ingestion, search, and conversational AI capabilities.

Two AI models are central to this deployment:

```
- amazon.titan-embed-text-v2 (for text embeddings)
- anthropic.claude-3-7-sonnet-20250219-v1:0 (for generative reasoning)
```

> **Important:** Both models are only available in `us-east-1` and `us-west-2`. This stack is designed and tested for deployment in `us-west-2`. Deploying in other regions may result in failures.

---

## Prerequisites

Before deploying GenAI Foundry, ensure the following:

* AWS account access with administrator permissions.
* Target region can be set to **US West (Oregon) – `us-west-2`** or  **US East (Virginia) – `us-east-1`**.
* AWS CDK installed locally or use AWS CloudShell.
* Python 3.9+ installed with required dependencies.

---

## Deployment Steps

Run the following commands in order:

```bash
# 1. Clone the repository
git clone <repository-url>
cd aivolvex-genai-foundry/

# 2. Create and activate a Python virtual environment
python -m venv .env
source .env/bin/activate

# 3. Install Python dependencies
pip install -r requirements.txt

# 4. Install AWS CDK globally
sudo npm install -g aws-cdk

# 5. Bootstrap the CDK environment (only required for first-time setup)
cdk bootstrap

# 6. Deploy the FinalCdkStack
cdk deploy FinalCdkStack
```

---

## Post Deployment

### Request Bedrock Model Access

Go to **Amazon Bedrock → Model Access** and request access to:

```
- amazon.titan-embed-text-v2
- anthropic.claude-3-7-sonnet-20250219-v1:0
```

---

### Retrieve API and Frontend URLs

After deployment, check the CloudFormation stack outputs for the **CloudFront Distribution Domain**.

---

## Key Components Deployed

* **Amazon VPC** with public/private subnets.
* **Amazon EC2** instance for application hosting and database initialization.
* **Amazon RDS (PostgreSQL)** for data persistence.
* **Amazon OpenSearch Serverless** collections for vector search.
* **Amazon S3** for knowledge base storage and frontend hosting.
* **AWS Lambda** functions for ingestion, search, and APIs.
* **Amazon API Gateway** (REST and WebSocket) for client interaction.
* **Amazon CloudFront** for frontend content delivery.
* **IAM Roles and Policies** for secure service integration.

---

## About

GenAI Foundry combines retrieval-augmented generation (RAG) with multi-knowledge base support, enabling domain-specific AI-powered search and reasoning.
## Legal Notice

© 1CloudHub. All rights reserved.

This project is developed for internal demo or POC purposes and is not production-ready without proper security, scalability, and compliance review.

---

## Important Notes

- After the CDK deployment is over, make sure to visit the CloudFront URL to see the latest domain. Kindly use it to view the fully deployed current stack in terms of UI.
- Do not terminate or temporarily stop the EC2 instance at any cost.
- Whatever the region you are supposed to deploy in, make sure to have access to `amazon.titan-embed-text-v2` and `anthropic.claude-3-7-sonnet` (Claude 3.7) in Amazon Bedrock.
