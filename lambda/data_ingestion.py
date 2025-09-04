import os
import boto3
import json
import base64
from opensearchpy import OpenSearch, RequestsHttpConnection
from requests_aws4auth import AWS4Auth

region = os.environ["AWS_REGION"]
service = "aoss"
HOST = os.environ["OPENSEARCH_ENDPOINT"].replace("https://", "").replace("http://", "")
index_name = os.environ["INDEX_NAME"]
bucket_name = os.environ["BUCKET_NAME"]
prefix = os.environ["S3_PREFIX"]

session = boto3.Session()
credentials = session.get_credentials()
awsauth = AWS4Auth(
    credentials.access_key,
    credentials.secret_key,
    region,
    service,
    session_token=credentials.token
)

# OpenSearch client - Fixed: use HOST variable
client = OpenSearch(
    hosts=[{"host": HOST, "port": 443}],  # ✅ Fixed: use HOST instead of collection_endpoint
    http_auth=awsauth,
    use_ssl=True,
    verify_certs=True,
    connection_class=RequestsHttpConnection,
)

s3 = boto3.client("s3")

def lambda_handler(event, context):
    print(f"Scanning s3://{bucket_name}/{prefix}")
    print(f"OpenSearch Host: {HOST}")  # ✅ Log the parsed host
    print(f"Index Name: {index_name}")

    # list objects in the given prefix
    response = s3.list_objects_v2(Bucket=bucket_name, Prefix=prefix)
    if "Contents" not in response:
        return {"status": "no files found"}

    for obj in response["Contents"]:
        key = obj["Key"]
        if key.endswith("/"):  # skip folders
            continue

        # download file
        file_obj = s3.get_object(Bucket=bucket_name, Key=key)
        content = file_obj["Body"].read()

        # prepare document (simple example)
        doc = {
            "file_name": key,
            "content_b64": base64.b64encode(content).decode("utf-8")
        }

        # index into OpenSearch
        client.index(index=index_name, body=doc)

        print(f"Ingested {key}")

    return {"status": "success", "indexed": len(response['Contents'])}
