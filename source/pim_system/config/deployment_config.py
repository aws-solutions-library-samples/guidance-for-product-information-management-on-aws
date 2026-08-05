"""
Deployment Configuration Management
"""
import os
from typing import Dict, Any, Optional
from dataclasses import dataclass
from aws_cdk import App


@dataclass
class DeploymentConfig:
    """Configuration for CDK deployment"""
    environment: str
    region: str
    project_prefix: str = "pim"
    enable_multi_az: bool = True
    lambda_memory_size: int = 512
    athena_workgroup_name: str = "pim_analytics_wg"
    vpc_cidr: str = "10.0.0.0/16"
    
    # Core PIM Blueprint Services (Always Enabled - Minimal Essential Set)
    # Storage & Data Layer
    enable_s3_data_lake: bool = True
    enable_iceberg_tables: bool = True
    
    # Processing & ETL Layer  
    enable_glue_etl: bool = True
    enable_glue_data_quality: bool = True
    enable_step_functions: bool = True
    
    # Analytics & Query Layer
    enable_athena: bool = True
    
    # Ingestion Layer
    enable_kinesis_firehose: bool = False
    
    # API & Authentication Layer
    enable_api_gateway: bool = True
    enable_lambda_functions: bool = True
    enable_cognito: bool = True
    
    # User Interface Layer
    enable_amplify_studio: bool = True
    
    # Note: Add-on services (OpenSearch, EMR, QuickSight) are deployed 
    # as separate stacks and integrate via well-defined interfaces
    
    @classmethod
    def load_from_context(cls, app: App, env: Optional[str] = None) -> 'DeploymentConfig':
        """Load deployment configuration from CDK context"""
        # Get environment from context or default to development
        environment = env or app.node.try_get_context("environment") or "development"
        print(f"DEBUG: Loading config for environment: {environment}")
        
        # Get deployment configurations from cdk.json
        deployment_configs = app.node.try_get_context("deployment_config")
        print(f"DEBUG: Available deployment configs: {list(deployment_configs.keys()) if deployment_configs else 'None'}")
        
        # If not found in context, provide default configuration
        if not deployment_configs:
            print(f"WARNING: No deployment_config found in CDK context, using defaults")
            deployment_configs = {
                "development": {
                    "environment": "development",
                    "region": "us-east-1",
                    "enable_multi_az": False,
                    "lambda_memory_size": 256,
                    "athena_workgroup_name": "pim_analytics_wg"
                },
                "production": {
                    "environment": "production",
                    "region": "us-east-1",
                    "enable_multi_az": True,
                    "lambda_memory_size": 512,
                    "athena_workgroup_name": "pim_analytics_wg"
                }
            }
        
        if environment not in deployment_configs:
            raise ValueError(f"No configuration found for environment: {environment}")
        
        config_data = deployment_configs[environment]
        
        # AWS_REGION env var overrides cdk.json region if set
        region = os.environ.get("AWS_REGION") or os.environ.get("CDK_DEFAULT_REGION") or config_data["region"]
        print(f"DEBUG: Config data loaded: {config_data}")
        print(f"DEBUG: Resolved region: {region}")
        
        return cls(
            environment=config_data["environment"],
            region=region,
            project_prefix=config_data.get("project_prefix", "pim"),
            enable_multi_az=config_data.get("enable_multi_az", True),
            lambda_memory_size=config_data.get("lambda_memory_size", 512),
            athena_workgroup_name=config_data.get("athena_workgroup_name", "pim_analytics_wg"),
            vpc_cidr=config_data.get("vpc_cidr", "10.0.0.0/16"),
            # Core PIM Blueprint Services (all enabled by default)
            enable_s3_data_lake=config_data.get("enable_s3_data_lake", True),
            enable_iceberg_tables=config_data.get("enable_iceberg_tables", True),
            enable_glue_etl=config_data.get("enable_glue_etl", True),
            enable_glue_data_quality=config_data.get("enable_glue_data_quality", True),
            enable_step_functions=config_data.get("enable_step_functions", True),
            enable_athena=config_data.get("enable_athena", True),
            enable_kinesis_firehose=config_data.get("enable_kinesis_firehose", True),
            enable_api_gateway=config_data.get("enable_api_gateway", True),
            enable_lambda_functions=config_data.get("enable_lambda_functions", True),
            enable_cognito=config_data.get("enable_cognito", True),
            enable_amplify_studio=config_data.get("enable_amplify_studio", True)
        )
    
    def get_resource_name(self, resource_type: str, account_id: str = None) -> str:
        """Generate standardized resource names using configurable prefix"""
        prefix = self.project_prefix
        # For Cognito domains, use only lowercase letters and numbers with account ID for uniqueness
        if resource_type == "auth" and account_id:
            return f"{prefix}{resource_type}{account_id}"
        elif resource_type == "auth":
            return f"{prefix}{resource_type}"
        # For Athena/Glue resources, use underscores
        elif resource_type in ["catalog"]:
            return f"{prefix}_{resource_type}"
        # For S3 buckets, add account ID for global uniqueness
        elif resource_type in ["data-lake", "assets", "config", "quarantine", "athena-results"] and account_id:
            return f"{prefix}-{resource_type}-{account_id}"
        # For other resources, use hyphens
        else:
            return f"{prefix}-{resource_type}"
    
    def get_tags(self) -> Dict[str, str]:
        """Get standard tags for all resources"""
        return {
            "Project": "AWS-PIM-System",
            "Environment": self.environment,
            "ManagedBy": "CDK"
        }