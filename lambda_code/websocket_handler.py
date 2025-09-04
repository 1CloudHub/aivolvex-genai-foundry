import json
import logging
import os
from pprint import pprint
import boto3

# Set up logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Get region and endpoint URL from environment variables
WEBSOCKET_REGION = os.environ.get('WEBSOCKET_REGION', 'ap-southeast-1')
WEBSOCKET_ENDPOINT = os.environ.get('WEBSOCKET_ENDPOINT', '')

# Create client with environment variables
client_apigateway = boto3.client('apigatewaymanagementapi', region_name=WEBSOCKET_REGION)

def send_private_message(connectionId, body):
    print("SENDING PRIVATE MESSAGE")
    print(f"Connection ID: {connectionId}")
    print(f"Message Body: {body}")
    print(f"Using region: {WEBSOCKET_REGION}")
    print(f"Using endpoint: {WEBSOCKET_ENDPOINT}")
    
    try:
        # Create client with endpoint URL if available
        if WEBSOCKET_ENDPOINT:
            print(f"Creating client with endpoint: {WEBSOCKET_ENDPOINT}")
            client = boto3.client('apigatewaymanagementapi', region_name=WEBSOCKET_REGION,
                                endpoint_url=WEBSOCKET_ENDPOINT)
        else:
            print("Using default client without endpoint URL")
            client = client_apigateway
            
        json_data = json.dumps(body)
        print(f"JSON Data: {json_data}")
        
        response = client.post_to_connection(
            ConnectionId=connectionId, 
            Data=json_data.encode('utf-8')
        )
        print(f"Send Response: {response}")
        
    except client_apigateway.exceptions.GoneException:
        print(f"Connection {connectionId} is closed")
    except Exception as e:
        print(f"Error sending message: {str(e)}")
        print(f"Error type: {type(e)}")
        import traceback
        print(f"Full traceback: {traceback.format_exc()}")
    
    return True
 


def lambda_handler(event, context):

    connection_id = event['requestContext']['connectionId']
    route_key = event['requestContext']['routeKey']
    
    logger.info(f"Received event on route: {route_key}, jjjjjjjjjjjjjjjjjjjj")
    logger.info(f"Connection ID: {connection_id}")
    route_key = event['requestContext']['routeKey']
    
    if route_key == '$connect':
        # pprint("eventttttttttttttttttttttt", event)
        send_private_message(connection_id, {"connectionid":connection_id,"message":"connected"})
        return {'statusCode': 200}
    
    elif route_key == 'sendMessage':
        body = json.loads(event['body'])
        message = body.get('message', '')
        logger.info(f"Received message on sendMessage route: {message}")
        # Process the message here
        return {'statusCode': 200, 'body': json.dumps('Message received')}
    
    
    elif route_key == '$disconnect':
        logger.info("Connection closed")
        return {'statusCode': 200}
    
    elif route_key == '$default':
        # This is where we'll handle the incoming message
        try:
            body = event['body']
            print("SSSSSSSSSSSSSSSS", body)
            logger.info(f"Received message: {json.dumps(body, indent=2)}")
            message = {"connectionid":connection_id,"message":"connected"}

            send_private_message(connection_id, message )
            # If you want to log specific parts of teventtttthe message:
            if 'action' in body:
                logger.info(f"Action: {body['action']}")
            if 'records' in body:
                logger.info(f"Number of records received: {len(body['records'])}")
                
        except:
            logger.error("Failed to parse message body as JSON")
            logger.info(f"Raw message body: {event['body']}")
        
        return {'statusCode': 200}

    return {'statusCode': 200}