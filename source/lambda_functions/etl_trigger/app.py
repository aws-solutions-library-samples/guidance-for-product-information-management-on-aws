"""
ETL Trigger Lambda Function
Triggers the Step Functions ETL workflow via API Gateway endpoint
"""
import json
import boto3
import os
from datetime import datetime
from typing import Dict, Any

# Initialize AWS clients
stepfunctions = boto3.client('stepfunctions')

# Environment variables
STATE_MACHINE_ARN = os.environ.get('STATE_MACHINE_ARN')
DATA_LAKE_BUCKET = os.environ.get('DATA_LAKE_BUCKET')


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Trigger ETL workflow via API Gateway
    
    POST /api/v1/etl/trigger
    Body (optional):
    {
        "bucket": "custom-bucket",  // Optional, defaults to DATA_LAKE_BUCKET
        "prefix": "raw/products/",  // Optional, defaults to raw/products/
        "description": "Manual trigger from UI"  // Optional description
    }
    """
    
    try:
        # Parse request body
        body = {}
        if event.get('body'):
            try:
                body = json.loads(event['body'])
            except json.JSONDecodeError:
                return create_response(400, {'error': 'Invalid JSON in request body'})
        
        # Get parameters from body or use defaults
        bucket = body.get('bucket', DATA_LAKE_BUCKET)
        prefix = body.get('prefix', 'raw/products/')
        description = body.get('description', 'Manual trigger from API')
        
        # Get user info from Cognito authorizer context
        user_email = 'unknown'
        if 'requestContext' in event and 'authorizer' in event['requestContext']:
            claims = event['requestContext']['authorizer'].get('claims', {})
            user_email = claims.get('email', 'unknown')
        
        # Create execution name with timestamp
        execution_name = f"api-trigger-{int(datetime.now().timestamp())}"
        
        # Start Step Functions execution
        response = stepfunctions.start_execution(
            stateMachineArn=STATE_MACHINE_ARN,
            name=execution_name,
            input=json.dumps({
                'bucket': bucket,
                'prefix': prefix,
                'manual_trigger': True,
                'triggered_by': user_email,
                'description': description,
                'timestamp': datetime.now().isoformat()
            })
        )
        
        # Return success response
        return create_response(200, {
            'message': 'ETL workflow started successfully',
            'execution_arn': response['executionArn'],
            'execution_name': execution_name,
            'started_at': response['startDate'].isoformat(),
            'state_machine_arn': STATE_MACHINE_ARN,
            'console_url': f"https://{os.environ.get('AWS_DEFAULT_REGION', 'ap-southeast-2')}.console.aws.amazon.com/states/home?region={os.environ.get('AWS_DEFAULT_REGION', 'ap-southeast-2')}#/executions/details/{response['executionArn']}"
        })
        
    except stepfunctions.exceptions.ExecutionAlreadyExists:
        return create_response(409, {
            'error': 'An ETL execution with this name already exists',
            'message': 'Please wait a moment and try again'
        })
        
    except stepfunctions.exceptions.StateMachineDoesNotExist:
        return create_response(500, {
            'error': 'State machine not found',
            'state_machine_arn': STATE_MACHINE_ARN
        })
        
    except Exception as e:
        print(f"Error triggering ETL workflow: {str(e)}")
        return create_response(500, {
            'error': 'Failed to trigger ETL workflow',
            'details': str(e)
        })


def create_response(status_code: int, body: Dict) -> Dict:
    """Create API Gateway response"""
    return {
        'statusCode': status_code,
        'headers': {
            'Content-Type': 'application/json',
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Headers': 'Content-Type,X-Amz-Date,Authorization,X-Api-Key,X-Amz-Security-Token',
            'Access-Control-Allow-Methods': 'OPTIONS,POST'
        },
        'body': json.dumps(body)
    }
