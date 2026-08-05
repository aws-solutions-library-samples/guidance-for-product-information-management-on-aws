#!/usr/bin/env python3
"""
Populate Base Tables Script

Reads business-specific YAML configuration and populates the Category and AttributeDefinition
tables using CloudFormation stack outputs.

Usage:
    python scripts/populate_base_tables.py --config PIM-Customisation-BookStore.yml --stack-name pim-on-aws
"""

import argparse
import yaml
import boto3
from datetime import datetime, timezone

def load_config(config_path):
    """Load YAML configuration file"""
    with open(config_path, 'r') as file:
        return yaml.safe_load(file)

def get_stack_outputs(cfn_client, stack_name):
    """Get CloudFormation stack outputs"""
    try:
        response = cfn_client.describe_stacks(StackName=stack_name)
        outputs = {}
        for output in response['Stacks'][0].get('Outputs', []):
            outputs[output['OutputKey']] = output['OutputValue']
        return outputs
    except cfn_client.exceptions.ClientError as e:
        if 'does not exist' in str(e):
            print(f"❌ Stack '{stack_name}' not found. Please deploy the CDK stack first:")
            print(f"   ./deploy.sh")
            exit(1)
        else:
            raise

def populate_categories(athena_client, config, database_name, table_name, bucket_name):
    """Populate Category table with data from config and build materialized paths"""
    categories = config.get('categories', [])
    if not categories:
        print("No categories found in config")
        return
    
    # Build category hierarchy and paths
    category_map = {}
    for cat in categories:
        category_map[cat['category_id']] = cat
    
    def build_path(category_id):
        """Recursively build materialized path"""
        cat = category_map.get(category_id)
        if not cat:
            return '/'
        
        parent_id = cat.get('parent_category_id')
        if not parent_id:
            # Root category
            return f"/{category_id}/"
        else:
            # Child category
            parent_path = build_path(parent_id)
            return f"{parent_path}{category_id}/"
    
    # Create VALUES clause for all categories
    values_list = []
    for idx, category in enumerate(categories):
        category_id = category['category_id']
        name = category['name']
        parent_id = category.get('parent_category_id')
        path = build_path(category_id)
        level = category.get('level', 1)
        display_order = category.get('display_order', idx + 1)  # Default to insertion order
        is_active = 'true'  # All categories active by default
        created_date = 'CURRENT_TIMESTAMP'
        modified_date = 'CURRENT_TIMESTAMP'
        
        values = (
            f"'{category_id}'",
            f"'{name}'", 
            f"'{parent_id}'" if parent_id else 'NULL',
            f"'{path}'",
            str(level),
            str(display_order),
            is_active,
            created_date,
            modified_date
        )
        values_list.append(f"({', '.join(values)})")
    
    # Use INSERT INTO with VALUES
    stmt = f"""INSERT INTO {database_name}.{table_name} 
               (category_id, name, parent_category_id, path, level, display_order, 
                is_active, created_date, modified_date) 
               VALUES {', '.join(values_list)}"""
    
    print(f"Executing: {stmt}")
    response = athena_client.start_query_execution(
        QueryString=stmt,
        ResultConfiguration={'OutputLocation': f's3://{bucket_name}/athena-results/'}
    )
    print(f"Query execution ID: {response['QueryExecutionId']}")
    print(f"Populated {len(categories)} categories with materialized paths")


def run_athena_query(athena_client, query, bucket_name, description="query"):
    """Execute an Athena query and wait for completion"""
    import time
    resp = athena_client.start_query_execution(
        QueryString=query,
        ResultConfiguration={'OutputLocation': f's3://{bucket_name}/athena-results/'}
    )
    qid = resp['QueryExecutionId']
    while True:
        status = athena_client.get_query_execution(QueryExecutionId=qid)['QueryExecution']['Status']
        state = status['State']
        if state in ('SUCCEEDED', 'FAILED', 'CANCELLED'):
            break
        time.sleep(1)
    if state != 'SUCCEEDED':
        reason = status.get('StateChangeReason', 'unknown')
        print(f"⚠️  {description} failed: {reason}")
    return state


def populate_attributes(athena_client, config, database_name, table_name, bucket_name):
    """Populate AttributeDefinition table with data from config (idempotent — clears first)"""
    attributes = config.get('attributes', [])
    if not attributes:
        print("No attributes found in config")
        return
    
    # Clear existing rows to prevent duplicates on re-run
    print("Clearing existing attribute definitions...")
    run_athena_query(athena_client, f"DELETE FROM {database_name}.{table_name} WHERE 1=1", bucket_name, "attribute clear")
    
    # Create VALUES clause for all attributes
    values_list = []
    for attr in attributes:
        # Map old config fields to new schema
        attr_id = attr['attribute_id']
        code = attr['code']
        name = attr['name']
        data_type = attr['data_type'].lower()  # string, int, decimal, date, boolean
        is_required = 'true' if attr.get('is_required', False) else 'false'
        is_searchable = 'true' if attr.get('is_searchable', True) else 'false'  # Default true
        
        # Category-specific attributes
        category_id = 'NULL'
        if attr.get('scope') == 'Category-Specific':
            # For now, set to NULL (applies to all categories)
            # In future, can map to specific category_id
            category_id = 'NULL'
        
        # Temporal validity - default to current date and NULL (always active)
        valid_from = f"DATE '{datetime.now().strftime('%Y-%m-%d')}'"
        valid_to = 'NULL'
        
        # Audit fields
        created_by = "'system'"
        created_date = 'CURRENT_TIMESTAMP'
        
        values = (
            f"{attr_id}",  # attribute_id (int)
            f"'{code}'",  # code (string)
            f"'{name}'",  # name (string)
            f"'{data_type}'",  # data_type (string)
            is_required,  # is_required (boolean)
            is_searchable,  # is_searchable (boolean)
            category_id,  # category_id (string or NULL)
            valid_from,  # valid_from (date)
            valid_to,  # valid_to (date or NULL)
            created_by,  # created_by (string)
            created_date  # created_date (timestamp)
        )
        values_list.append(f"({', '.join(values)})")
    
    # Use INSERT INTO with VALUES
    stmt = f"""INSERT INTO {database_name}.{table_name} 
               (attribute_id, code, name, data_type, is_required, is_searchable, 
                category_id, valid_from, valid_to, created_by, created_date) 
               VALUES {', '.join(values_list)}"""
    
    print(f"Executing: {stmt}")
    response = athena_client.start_query_execution(
        QueryString=stmt,
        ResultConfiguration={'OutputLocation': f's3://{bucket_name}/athena-results/'}
    )
    print(f"Query execution ID: {response['QueryExecutionId']}")

def main():
    parser = argparse.ArgumentParser(description='Populate base PIM tables from YAML config')
    parser.add_argument('--config', required=True, help='Path to YAML configuration file')
    parser.add_argument('--stack-name', required=True, help='CloudFormation stack name')
    parser.add_argument('--region', default='us-east-1', help='AWS region')
    parser.add_argument('--profile', help='AWS profile to use')
    
    args = parser.parse_args()
    
    # Load configuration
    config = load_config(args.config)
    
    # Initialize AWS session with profile
    session = boto3.Session(profile_name=args.profile) if args.profile else boto3.Session()
    
    # Initialize AWS clients
    cfn_client = session.client('cloudformation', region_name=args.region)
    athena_client = session.client('athena', region_name=args.region)
    
    # Get stack outputs
    outputs = get_stack_outputs(cfn_client, args.stack_name)
    
    database_name = outputs['GlueCatalogName']
    category_table = outputs['CategoryTableName']
    attribute_table = outputs['AttributeDefinitionTableName']
    bucket_name = outputs['DataLakeBucketName']
    
    print(f"Using database: {database_name}")
    print(f"Using bucket: {bucket_name}")
    
    # Populate tables
    populate_categories(athena_client, config, database_name, category_table, bucket_name)
    populate_attributes(athena_client, config, database_name, attribute_table, bucket_name)
    
    print("Base table population completed!")

if __name__ == "__main__":
    main()
