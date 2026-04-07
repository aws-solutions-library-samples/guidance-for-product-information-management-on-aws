#!/usr/bin/env python3
"""
Iceberg Table Management Script for PIM System

This script manages the creation, update, and deletion of Iceberg tables
for the PIM system using Glue/Athena. Table schemas are driven by configuration files.
"""

import boto3
import json
import time
import yaml
from typing import Dict, List

# Base table schemas with partition specifications
# Partition Strategy: Co-locate related data by product_id for efficient JOINs
BASE_TABLE_SCHEMAS = {
    "product": {
        "columns": [
            "product_id string",
            "upc_ean string",
            "brand string",
            "base_name string",
            "short_description string",
            "base_price decimal(10,2)",
            "currency_code string",
            "stock_quantity int",
            "status string",
            "dq_status string",
            "completeness_score int",  # NEW: 0-4 score for incomplete queue
            "low_stock boolean",  # NEW: Flag for low stock queue
            "assigned_to string",  # NEW: Future task assignment
            "assigned_date timestamp",  # NEW: Future task assignment
            "created_date timestamp",
            "modified_date timestamp"
        ],
        "partition": "PARTITIONED BY (status, dq_status)"  # NEW: Optimized for queue queries
    },
    "category": {
        "columns": [
            "category_id string",
            "name string",
            "parent_category_id string",
            "path string",  # NEW: Materialized path (/books/fiction/scifi/)
            "level int",
            "display_order int",
            "is_active boolean",
            "created_date timestamp",
            "modified_date timestamp"
        ],
        "partition": None  # Small reference table, no partition needed
    },
    "attribute_definition": {
        "columns": [
            "attribute_id int",
            "code string",  # Human-readable code (e.g., 'author', 'publisher')
            "name string",  # Display name
            "data_type string",  # string, int, decimal, date, boolean
            "is_required boolean",
            "is_searchable boolean",
            "category_id string",  # NULL = applies to all categories
            "valid_from date",  # NEW: Temporal validity
            "valid_to date",  # NEW: NULL = currently active
            "created_by string",
            "created_date timestamp"
        ],
        "partition": None  # Small reference table
    },
    "product_attribute_value": {
        "columns": [
            "product_id string",
            "attribute_id int",
            "value string",  # All values stored as string, cast on read
            "created_date timestamp",
            "modified_date timestamp"
        ],
        "partition": "PARTITIONED BY (attribute_id)"  # NEW: Optimized for attribute queries
    },
    "product_category": {
        "columns": [
            "product_id string",
            "category_id string",
            "is_primary boolean",
            "created_date timestamp"
        ],
        "partition": "PARTITIONED BY (category_id)"  # NEW: Optimized for category queries
    },
    "media_asset": {
        "columns": [
            "asset_id string",
            "product_id string",
            "file_name string",
            "url string",
            "type string",
            "usage_code string",
            "alt_text string",
            "display_order int",
            "created_date timestamp"
        ],
        "partition": "PARTITIONED BY (bucket(16, product_id))"  # Co-located with product
    },
    "dq_run_summary": {
        "columns": [
            "job_run_id string",
            "timestamp string",
            "total_records bigint",
            "failed_records bigint",
            "valid_records bigint",
            "success_rate double",
            "total_products_in_system bigint"
        ],
        "partition": ""
    },
    "dq_failed_records": {
        "columns": [
            "product_id string",
            "run_id string",
            "failure_reason string",
            "failed_field string",
            "failed_value string",
            "correction_status string",  # pending, corrected, ignored
            "corrected_by string",
            "corrected_date timestamp",
            "created_date timestamp"
        ],
        "partition": "PARTITIONED BY (correction_status)"  # Optimized for correction workflow
    }
}

class IcebergTableManager:
    def __init__(self, region='ap-southeast-2', profile=None, config_file=None):
        session = boto3.Session(profile_name=profile) if profile else boto3.Session()
        self.athena = session.client('athena', region_name=region)
        self.glue = session.client('glue', region_name=region)
        self.table_schemas = BASE_TABLE_SCHEMAS.copy()
        
        # Load configuration if provided
        if config_file:
            self.load_config(config_file)
    
    def load_config(self, config_file: str):
        """Load configuration from YAML file to understand business context"""
        try:
            with open(config_file, 'r') as f:
                config = yaml.safe_load(f)
            
            print(f"Loaded configuration from: {config_file}")
            print(f"Found {len(config.get('categories', []))} categories")
            print(f"Found {len(config.get('attributes', []))} attributes")
            
            # Configuration validates the business model but doesn't change base schemas
            # The base schemas are designed to be generic and extensible
            
        except Exception as e:
            print(f"Warning: Could not load config file {config_file}: {str(e)}")
            print("Using base table schemas only")
        
    def wait_for_query(self, query_id: str, timeout: int = 60) -> bool:
        """Wait for Athena query to complete"""
        start_time = time.time()
        while time.time() - start_time < timeout:
            response = self.athena.get_query_execution(QueryExecutionId=query_id)
            status = response['QueryExecution']['Status']['State']
            
            if status == 'SUCCEEDED':
                return True
            elif status in ['FAILED', 'CANCELLED']:
                error = response['QueryExecution']['Status'].get('StateChangeReason', 'Unknown error')
                print(f"Query failed: {error}")
                return False
                
            time.sleep(2)
        
        print(f"Query timed out after {timeout} seconds")
        return False
    
    def execute_query(self, query: str, database: str, workgroup: str = 'pim_analytics_wg') -> bool:
        """Execute Athena query and wait for completion"""
        try:
            response = self.athena.start_query_execution(
                QueryString=query,
                QueryExecutionContext={'Database': database},
                WorkGroup=workgroup
            )
            
            query_id = response['QueryExecutionId']
            print(f"Executing query: {query_id}")
            
            return self.wait_for_query(query_id)
            
        except Exception as e:
            print(f"Error executing query: {str(e)}")
            return False
    
    def drop_table(self, table_name: str, database: str) -> bool:
        """Drop an Iceberg table"""
        query = f"DROP TABLE IF EXISTS {database}.{table_name}"
        print(f"Dropping table: {table_name}")
        return self.execute_query(query, database)
    
    def create_table(self, table_name: str, database: str, data_lake_bucket: str) -> bool:
        """Create an Iceberg table with optional partitioning"""
        if table_name not in self.table_schemas:
            print(f"Unknown table: {table_name}")
            return False
        
        schema = self.table_schemas[table_name]
        columns = schema["columns"]
        partition_spec = schema.get("partition")
        
        column_defs = ",\n        ".join(columns)
        
        # Build partition clause if specified
        partition_clause = f"\n    {partition_spec}" if partition_spec else ""
        
        query = f"""
        CREATE TABLE {database}.{table_name} (
            {column_defs}
        ){partition_clause}
        LOCATION 's3://{data_lake_bucket}/iceberg/{table_name}/'
        TBLPROPERTIES (
            'table_type'='ICEBERG',
            'format'='parquet',
            'write_compression'='snappy',
            'optimize_rewrite_delete_file_threshold'='10'
        )
        """
        
        print(f"Creating table: {table_name}")
        if partition_spec:
            print(f"  With partitioning: {partition_spec}")
        return self.execute_query(query, database)
    
    def recreate_table(self, table_name: str, database: str, data_lake_bucket: str) -> bool:
        """Drop and recreate a table"""
        if not self.drop_table(table_name, database):
            return False
        time.sleep(5)  # Wait a bit between drop and create
        return self.create_table(table_name, database, data_lake_bucket)
    
    def recreate_all_tables(self, database: str, data_lake_bucket: str) -> bool:
        """Recreate all tables"""
        success = True
        for table_name in self.table_schemas.keys():
            if not self.recreate_table(table_name, database, data_lake_bucket):
                success = False
                print(f"Failed to recreate table: {table_name}")
            else:
                print(f"Successfully recreated table: {table_name}")
        return success
    
    def clear_table_data(self, table_name: str, database: str) -> bool:
        """Clear all data from a table"""
        query = f"DELETE FROM {database}.{table_name}"
        print(f"Clearing data from table: {table_name}")
        return self.execute_query(query, database)

def main():
    import argparse
    
    parser = argparse.ArgumentParser(description='Manage PIM Iceberg tables')
    parser.add_argument('--action', choices=['create', 'drop', 'recreate', 'recreate-all', 'clear'], 
                       required=True, help='Action to perform')
    parser.add_argument('--table', help='Table name (required for create/drop/recreate/clear)')
    parser.add_argument('--database', default='pim_catalog', help='Glue database name')
    parser.add_argument('--bucket', required=True, help='Data lake S3 bucket name')
    parser.add_argument('--config', help='Configuration YAML file (e.g., PIM-Customisation-BookStore.yml)')
    parser.add_argument('--profile', help='AWS profile name')
    parser.add_argument('--region', default='ap-southeast-2', help='AWS region')
    
    args = parser.parse_args()
    
    if args.action in ['create', 'drop', 'recreate', 'clear'] and not args.table:
        parser.error(f"--table is required for action: {args.action}")
    
    manager = IcebergTableManager(region=args.region, profile=args.profile, config_file=args.config)
    
    if args.action == 'create':
        success = manager.create_table(args.table, args.database, args.bucket)
    elif args.action == 'drop':
        success = manager.drop_table(args.table, args.database)
    elif args.action == 'recreate':
        success = manager.recreate_table(args.table, args.database, args.bucket)
    elif args.action == 'recreate-all':
        success = manager.recreate_all_tables(args.database, args.bucket)
    elif args.action == 'clear':
        success = manager.clear_table_data(args.table, args.database)
    
    if success:
        print(f"Action '{args.action}' completed successfully")
    else:
        print(f"Action '{args.action}' failed")
        exit(1)

if __name__ == "__main__":
    main()
