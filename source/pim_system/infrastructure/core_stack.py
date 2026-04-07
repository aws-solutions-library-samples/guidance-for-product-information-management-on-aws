"""
)Core PIM System Stack - The Blueprint Foundation
Contains essential services that every PIM deployment needs
"""
import json
from aws_cdk import (
    Stack,
    Tags,
    CfnOutput,
    RemovalPolicy,
    Duration,
    CustomResource,
    SecretValue,
)
from aws_cdk import aws_s3 as s3

from aws_cdk import aws_glue as glue
from aws_cdk import aws_iam as iam
from aws_cdk import aws_s3_assets as assets
from aws_cdk import aws_stepfunctions as sfn
from aws_cdk import aws_stepfunctions_tasks as tasks
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_dynamodb as dynamodb
from aws_cdk import aws_athena as athena


from aws_cdk import aws_apigateway as apigateway
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_cognito as cognito
from aws_cdk import aws_secretsmanager as secretsmanager
from aws_cdk import custom_resources as cr
try:
    import aws_cdk.aws_amplify_alpha as amplify
except ImportError:
    try:
        from aws_cdk import aws_amplify_alpha as amplify
    except ImportError:
        # If Amplify alpha is not available, we'll handle this in the stack
        amplify = None
from constructs import Construct
from pim_system.config.deployment_config import DeploymentConfig


class PimCoreStack(Stack):
    """
    Core stack containing the PIM blueprint foundation:
    - S3 Data Lake + Iceberg Tables
    - Lambda Functions + API Gateway
    - Athena for Analytics
    - Glue ETL + Data Quality

    - Step Functions for ETL Orchestration
    - Amplify Studio UI
    - Cognito Authentication
    """
    
    def __init__(
        self, 
        scope: Construct, 
        construct_id: str, 
        config: DeploymentConfig,
        **kwargs
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
        
        self.config = config
        
        # Apply standard tags
        for key, value in config.get_tags().items():
            Tags.of(self).add(key, value)
        
        # Initialize storage for other components to reference
        self.data_lake_bucket = None
        self.assets_bucket = None
        self.config_bucket = None
        self.quarantine_bucket = None

        self.glue_catalog = None
        self.glue_role = None
        self.etl_job = None
        self.step_functions_role = None
        self.etl_workflow = None
        self.athena_workgroup = None
        self.athena_results_bucket = None

        self.api_gateway = None
        self.lambda_role = None
        self.lambda_functions = {}
        self.user_pool = None
        self.user_pool_client = None
        self.identity_pool = None
        self.amplify_app = None
        
        # Core infrastructure components (always deployed)
        self._create_storage_layer()
        self._create_data_quality_layer()  # Create DynamoDB table first
        self._create_processing_layer()    # Then create Glue jobs that use the table
        self._create_orchestration_layer()  # Then create Step Functions that use the jobs
        self._create_analytics_layer()
        self._create_authentication_layer()
        self._create_api_layer()
        self._create_ui_layer()
        self._create_outputs()
    
    def _create_storage_layer(self) -> None:
        """Create S3 buckets and storage infrastructure"""
        self._create_s3_buckets()
        self._create_iceberg_catalog()
    
    def _create_processing_layer(self) -> None:
        """Create core processing services"""
        self._create_glue_role()
        self._create_glue_etl_job()
        self._create_glue_dq_job()
        self._create_data_quality_rules()
    
    def _create_orchestration_layer(self) -> None:
        """Create Step Functions for ETL workflow orchestration"""
        self._create_step_functions_role()
        self._create_etl_workflow()
    
    def _create_authentication_layer(self) -> None:
        """Create Cognito authentication infrastructure"""
        self._create_user_pool()
        self._create_identity_pool()
    
    def _create_api_layer(self) -> None:
        """Create API Gateway and Lambda functions"""
        self._create_lambda_role()
        self._create_lambda_functions()
        self._create_api_gateway()
    
    def _create_data_quality_layer(self) -> None:
        """Create data quality management infrastructure"""
        # DQ tables are now Iceberg tables created via Athena
        pass
    
    def _create_analytics_layer(self) -> None:
        """Create Athena workgroup and analytics infrastructure"""
        self._create_athena_workgroup()
        # Sample queries are business-specific and created by application configuration
    
    def _create_ui_layer(self) -> None:
        """Create Amplify Studio UI"""
        self._create_amplify_app()
    
    def _create_s3_buckets(self) -> None:
        """Create S3 buckets for data lake, assets, config, and quarantine"""
        
        # Data Lake Bucket - Main storage for Iceberg tables
        self.data_lake_bucket = s3.Bucket(
            self,
            "DataLakeBucket",
            bucket_name=self.config.get_resource_name("data-lake", self.account),
            versioned=True,
            encryption=s3.BucketEncryption.S3_MANAGED,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            lifecycle_rules=[
                s3.LifecycleRule(
                    id="TransitionToIA",
                    enabled=True,
                    transitions=[
                        s3.Transition(
                            storage_class=s3.StorageClass.INFREQUENT_ACCESS,
                            transition_after=Duration.days(30)
                        ),
                        s3.Transition(
                            storage_class=s3.StorageClass.GLACIER,
                            transition_after=Duration.days(90)
                        )
                    ]
                )
            ],
            removal_policy=RemovalPolicy.RETAIN if self.config.environment == "production" else RemovalPolicy.DESTROY
        )
        
        # Assets Bucket - Digital assets (book covers, documents)
        self.assets_bucket = s3.Bucket(
            self,
            "AssetsBucket",
            bucket_name=self.config.get_resource_name("assets", self.account),
            versioned=True,
            encryption=s3.BucketEncryption.S3_MANAGED,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            cors=[
                s3.CorsRule(
                    allowed_methods=[s3.HttpMethods.GET, s3.HttpMethods.PUT, s3.HttpMethods.POST],
                    allowed_origins=["*"],  # Will be restricted to Amplify domain in production
                    allowed_headers=["*"],
                    max_age=3000
                )
            ],
            removal_policy=RemovalPolicy.RETAIN if self.config.environment == "production" else RemovalPolicy.DESTROY
        )
        
        # Configuration Bucket - Business configurations and schemas
        self.config_bucket = s3.Bucket(
            self,
            "ConfigBucket",
            bucket_name=self.config.get_resource_name("config", self.account),
            versioned=True,
            encryption=s3.BucketEncryption.S3_MANAGED,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            removal_policy=RemovalPolicy.RETAIN
        )
        
        # Quarantine Bucket - Failed data quality records
        self.quarantine_bucket = s3.Bucket(
            self,
            "QuarantineBucket",
            bucket_name=self.config.get_resource_name("quarantine", self.account),
            versioned=True,
            encryption=s3.BucketEncryption.S3_MANAGED,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            lifecycle_rules=[
                s3.LifecycleRule(
                    id="CleanupOldQuarantineData",
                    enabled=True,
                    expiration=Duration.days(365)  # Keep quarantine data for 1 year
                )
            ],
            removal_policy=RemovalPolicy.DESTROY
        )
    
    def _create_iceberg_catalog(self) -> None:
        """Create Glue catalog for Iceberg tables"""
        
        # Glue Database for Iceberg catalog
        self.glue_catalog = glue.CfnDatabase(
            self,
            "IcebergCatalog",
            catalog_id=self.account,
            database_input=glue.CfnDatabase.DatabaseInputProperty(
                name=self.config.get_resource_name("catalog"),
                description=f"Iceberg catalog for PIM system - {self.config.environment}",
                parameters={
                    "classification": "iceberg",
                    "location": f"s3://{self.data_lake_bucket.bucket_name}/iceberg/"
                }
            )
        )
        

        
    

    
    def _create_outputs(self) -> None:
        """Create CloudFormation outputs"""
        CfnOutput(
            self,
            "DataLakeBucketName",
            value=self.data_lake_bucket.bucket_name,
            description="S3 bucket for data lake storage",
            export_name=f"{self.stack_name}-DataLakeBucket"
        )
        
        CfnOutput(
            self,
            "AssetsBucketName",
            value=self.assets_bucket.bucket_name,
            description="S3 bucket for digital assets",
            export_name=f"{self.stack_name}-AssetsBucket"
        )
        
        CfnOutput(
            self,
            "ConfigBucketName",
            value=self.config_bucket.bucket_name,
            description="S3 bucket for configuration files",
            export_name=f"{self.stack_name}-ConfigBucket"
        )
        
        CfnOutput(
            self,
            "QuarantineBucketName",
            value=self.quarantine_bucket.bucket_name,
            description="S3 bucket for quarantined data",
            export_name=f"{self.stack_name}-QuarantineBucket"
        )
        
        CfnOutput(
            self,
            "GlueCatalogName",
            value=self.glue_catalog.ref,
            description="Glue catalog database name for Iceberg tables",
            export_name=f"{self.stack_name}-GlueCatalog"
        )
        
        # Table name outputs for populate_base_tables.py
        CfnOutput(
            self,
            "CategoryTableName",
            value="category",
            description="Category table name",
            export_name=f"{self.stack_name}-CategoryTable"
        )
        
        CfnOutput(
            self,
            "AttributeDefinitionTableName", 
            value="attribute_definition",
            description="AttributeDefinition table name",
            export_name=f"{self.stack_name}-AttributeDefinitionTable"
        )
        
    def _create_glue_role(self) -> None:
        """Create IAM role for Glue jobs"""
        
        self.glue_role = iam.Role(
            self,
            "GlueServiceRole",
            assumed_by=iam.ServicePrincipal("glue.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSGlueServiceRole")
            ],
            inline_policies={
                "S3Access": iam.PolicyDocument(
                    statements=[
                        iam.PolicyStatement(
                            effect=iam.Effect.ALLOW,
                            actions=[
                                "s3:GetObject",
                                "s3:PutObject",
                                "s3:DeleteObject",
                                "s3:ListBucket"
                            ],
                            resources=[
                                self.data_lake_bucket.bucket_arn,
                                f"{self.data_lake_bucket.bucket_arn}/*",
                                self.config_bucket.bucket_arn,
                                f"{self.config_bucket.bucket_arn}/*",
                                self.quarantine_bucket.bucket_arn,
                                f"{self.quarantine_bucket.bucket_arn}/*"
                            ]
                        ),
                        iam.PolicyStatement(
                            effect=iam.Effect.ALLOW,
                            actions=[
                                "glue:GetDatabase",
                                "glue:GetTable",
                                "glue:GetTables",
                                "glue:CreateTable",
                                "glue:UpdateTable",
                                "glue:GetPartition",
                                "glue:GetPartitions",
                                "glue:CreatePartition",
                                "glue:UpdatePartition",
                                "glue:GetDataQualityRuleset",
                                "glue:GetDataQualityResult",
                                "glue:StartDataQualityRulesetEvaluationRun",
                                "glue:PublishDataQuality"
                            ],
                            resources=[
                                f"arn:aws:glue:{self.region}:{self.account}:catalog",
                                f"arn:aws:glue:{self.region}:{self.account}:database/{self.config.get_resource_name('catalog')}",
                                f"arn:aws:glue:{self.region}:{self.account}:table/{self.config.get_resource_name('catalog')}/*"
                            ]
                        ),
                        iam.PolicyStatement(
                            effect=iam.Effect.ALLOW,
                            actions=[
                                "dynamodb:PutItem",
                                "dynamodb:GetItem",
                                "dynamodb:UpdateItem",
                                "dynamodb:Query",
                                "dynamodb:Scan"
                            ],
                            resources=[
                                f"arn:aws:dynamodb:{self.region}:{self.account}:table/*pim-dq-tracking*"
                            ]
                        )
                    ]
                )
            }
        )
    
    def _create_glue_etl_job(self) -> None:
        """Create generic Glue ETL job for product processing"""
        
        # Upload generic ETL job script to S3
        job_script = assets.Asset(
            self,
            "GlueJobScript",
            path="source/glue_jobs/sample_product_etl_job.py"
        )
        
        # Grant Glue role access to the script
        job_script.grant_read(self.glue_role)
        
        # Create generic ETL job
        self.etl_job = glue.CfnJob(
            self,
            "ProductETLJob",
            name=self.config.get_resource_name("product-etl"),
            role=self.glue_role.role_arn,
            command=glue.CfnJob.JobCommandProperty(
                name="glueetl",
                script_location=job_script.s3_object_url,
                python_version="3"
            ),
            default_arguments={
                "--job-language": "python",
                "--job-bookmark-option": "job-bookmark-enable",
                "--enable-metrics": "true",
                "--enable-spark-ui": "true",
                "--spark-event-logs-path": f"s3://{self.data_lake_bucket.bucket_name}/spark-logs/",
                "--enable-continuous-cloudwatch-log": "true",
                "--datalake-formats": "iceberg",
                "--data_lake_bucket": self.data_lake_bucket.bucket_name,
                "--config_bucket": self.config_bucket.bucket_name,
                "--quarantine_bucket": self.quarantine_bucket.bucket_name,
                "--glue_database": self.glue_catalog.ref
            },
            glue_version="5.0",
            max_retries=0,
            timeout=30,  # 60 minutes
            worker_type="G.1X",
            number_of_workers=2,
            description=f"ETL job for processing product data - {self.config.environment}"
        )
    
    def _create_glue_dq_job(self) -> None:
        """Create Glue Data Quality jobs - Tier 1 (managed DQDL) + Tier 2 (custom business rules)"""
        
        # --- Tier 1: Managed DQ with DQDL rules ---
        tier1_script = assets.Asset(
            self, "GlueManagedDQScript",
            path="source/glue_jobs/managed_data_quality_job.py"
        )
        tier1_script.grant_read(self.glue_role)
        
        self.managed_dq_job = glue.CfnJob(
            self, "ManagedDataQualityJob",
            name=self.config.get_resource_name("managed-dq"),
            role=self.glue_role.role_arn,
            command=glue.CfnJob.JobCommandProperty(
                name="glueetl",
                script_location=tier1_script.s3_object_url,
                python_version="3"
            ),
            default_arguments={
                "--job-language": "python",
                "--enable-metrics": "true",
                "--enable-continuous-cloudwatch-log": "true",
                "--datalake-formats": "iceberg",
                "--data_lake_bucket": self.data_lake_bucket.bucket_name,
                "--glue_database": self.glue_catalog.ref,
            },
            max_retries=0,
            timeout=15,
            worker_type="G.1X",
            number_of_workers=2,
            glue_version="5.0",
            description=f"Tier 1: Managed DQ (DQDL rules) - {self.config.environment}"
        )
        
        # --- Tier 2: Custom DQ with business rules ---
        tier2_script = assets.Asset(
            self, "GlueCustomDQScript",
            path="source/glue_jobs/custom_data_quality_job.py"
        )
        tier2_script.grant_read(self.glue_role)
        
        self.dq_job = glue.CfnJob(
            self, "CustomDataQualityJob",
            name=self.config.get_resource_name("custom-dq"),
            role=self.glue_role.role_arn,
            command=glue.CfnJob.JobCommandProperty(
                name="glueetl",
                script_location=tier2_script.s3_object_url,
                python_version="3"
            ),
            default_arguments={
                "--job-language": "python",
                "--enable-metrics": "true",
                "--enable-continuous-cloudwatch-log": "true",
                "--datalake-formats": "iceberg",
                "--data_lake_bucket": self.data_lake_bucket.bucket_name,
                "--glue_database": self.glue_catalog.ref,
            },
            glue_version="5.0",
            max_retries=0,
            timeout=15,
            worker_type="G.1X",
            number_of_workers=2,
            description=f"Tier 2: Custom DQ (business rules) - {self.config.environment}"
        )
    
    def _create_data_quality_rules(self) -> None:
        """Create Glue Data Quality outputs"""
        
        CfnOutput(
            self,
            "GlueETLJobName",
            value=self.etl_job.ref,
            description="Glue ETL job name for product processing",
            export_name=f"{self.stack_name}-GlueETLJob"
        )
        
        CfnOutput(
            self,
            "GlueManagedDQJobName", 
            value=self.managed_dq_job.ref,
            description="Tier 1: Managed DQ job (DQDL rules)",
            export_name=f"{self.stack_name}-ManagedDQJob"
        )
        
        CfnOutput(
            self,
            "GlueCustomDQJobName", 
            value=self.dq_job.ref,
            description="Tier 2: Custom DQ job (business rules)",
            export_name=f"{self.stack_name}-CustomDQJob"
        )
        
        CfnOutput(
            self,
            "GlueRoleName",
            value=self.glue_role.role_name,
            description="IAM role for Glue jobs",
            export_name=f"{self.stack_name}-GlueRole"
        )
        
    def _create_step_functions_role(self) -> None:
        """Create IAM role for Step Functions"""
        
        self.step_functions_role = iam.Role(
            self,
            "StepFunctionsRole",
            assumed_by=iam.ServicePrincipal("states.amazonaws.com"),
            inline_policies={
                "GlueJobExecution": iam.PolicyDocument(
                    statements=[
                        iam.PolicyStatement(
                            effect=iam.Effect.ALLOW,
                            actions=[
                                "glue:StartJobRun",
                                "glue:GetJobRun",
                                "glue:GetJobRuns",
                                "glue:BatchStopJobRun"
                            ],
                            resources=[
                                f"arn:aws:glue:{self.region}:{self.account}:job/{self.config.get_resource_name('product-etl')}",
                                f"arn:aws:glue:{self.region}:{self.account}:job/{self.config.get_resource_name('managed-dq')}",
                                f"arn:aws:glue:{self.region}:{self.account}:job/{self.config.get_resource_name('custom-dq')}"
                            ]
                        ),
                        iam.PolicyStatement(
                            effect=iam.Effect.ALLOW,
                            actions=[
                                "glue:StartDataQualityRulesetEvaluationRun",
                                "glue:GetDataQualityRulesetEvaluationRun"
                            ],
                            resources=[
                                f"arn:aws:glue:{self.region}:{self.account}:dataQualityRuleset/*"
                            ]
                        )
                    ]
                )
            }
        )
    
    def _create_etl_workflow(self) -> None:
        """Create Step Functions workflow for ETL orchestration with Data Quality"""
        
        # ETL Job
        start_etl_job = tasks.GlueStartJobRun(
            self,
            "StartProductETLJob",
            glue_job_name=self.etl_job.ref,
            integration_pattern=sfn.IntegrationPattern.RUN_JOB,
            result_path="$.etl_result"
        )
        
        # Tier 1 - Managed DQ (DQDL rules)
        start_managed_dq = tasks.GlueStartJobRun(
            self,
            "StartManagedDQJob",
            glue_job_name=self.managed_dq_job.ref,
            integration_pattern=sfn.IntegrationPattern.RUN_JOB,
            arguments=sfn.TaskInput.from_object({
                "--JOB_RUN_ID.$": "$$.Execution.Name"
            }),
            result_path="$.managed_dq_result"
        )
        
        # Tier 2 - Custom DQ (business rules)
        start_custom_dq = tasks.GlueStartJobRun(
            self,
            "StartCustomDQJob",
            glue_job_name=self.dq_job.ref,
            integration_pattern=sfn.IntegrationPattern.RUN_JOB,
            arguments=sfn.TaskInput.from_object({
                "--JOB_RUN_ID.$": "$$.Execution.Name"
            }),
            result_path="$.custom_dq_result"
        )
        
        pipeline_success = sfn.Succeed(
            self,
            "PipelineSuccess",
            comment="Pipeline completed successfully"
        )
        
        # DQ chain: Tier 1 → Tier 2 → Success
        dq_chain = start_managed_dq.next(start_custom_dq).next(pipeline_success)
        
        # Choice: skip ETL when input has skip_etl=true (revalidation mode)
        skip_etl_choice = sfn.Choice(self, "SkipETL")
        definition = skip_etl_choice \
            .when(sfn.Condition.is_present("$.skip_etl"), dq_chain) \
            .otherwise(start_etl_job.next(dq_chain))
        
        # Create Step Functions state machine
        self.etl_workflow = sfn.StateMachine(
            self,
            "ProductETLWorkflow",
            state_machine_name=self.config.get_resource_name("product-etl-workflow"),
            definition=definition,
            role=self.step_functions_role,
            timeout=Duration.hours(4),
            comment=f"Product ETL + Data Quality workflow - {self.config.environment}"
        )
        
        CfnOutput(
            self,
            "StepFunctionsWorkflowArn",
            value=self.etl_workflow.state_machine_arn,
            description="Step Functions workflow ARN for ETL orchestration",
            export_name=f"{self.stack_name}-ETLWorkflow"
        )
    
    def _create_athena_workgroup(self) -> None:
        """Create Athena workgroup for analytics queries"""
        
        # Create S3 bucket for Athena query results
        self.athena_results_bucket = s3.Bucket(
            self,
            "AthenaResultsBucket",
            bucket_name=self.config.get_resource_name("athena-results", self.account),
            encryption=s3.BucketEncryption.S3_MANAGED,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            lifecycle_rules=[
                s3.LifecycleRule(
                    id="DeleteOldQueryResults",
                    enabled=True,
                    expiration=Duration.days(30)  # Clean up query results after 30 days
                )
            ],
            removal_policy=RemovalPolicy.DESTROY
        )
        
        # Create Athena workgroup
        self.athena_workgroup = athena.CfnWorkGroup(
            self,
            "BookstoreAnalyticsWorkgroup",
            name=self.config.athena_workgroup_name,
            description=f"Athena workgroup for bookstore analytics - {self.config.environment}",
            state="ENABLED",
            work_group_configuration=athena.CfnWorkGroup.WorkGroupConfigurationProperty(
                result_configuration=athena.CfnWorkGroup.ResultConfigurationProperty(
                    output_location=f"s3://{self.athena_results_bucket.bucket_name}/query-results/",
                    encryption_configuration=athena.CfnWorkGroup.EncryptionConfigurationProperty(
                        encryption_option="SSE_S3"
                    )
                ),
                enforce_work_group_configuration=True,
                publish_cloud_watch_metrics_enabled=True,
                bytes_scanned_cutoff_per_query=1000000000,  # 1GB limit per query
                requester_pays_enabled=False
            )
        )
    
        CfnOutput(
            self,
            "AthenaWorkgroupName",
            value=self.athena_workgroup.ref,
            description="Athena workgroup for analytics",
            export_name=f"{self.stack_name}-AthenaWorkgroup"
        )
        
        CfnOutput(
            self,
            "AthenaResultsBucketName",
            value=self.athena_results_bucket.bucket_name,
            description="S3 bucket for Athena query results",
            export_name=f"{self.stack_name}-AthenaResultsBucket"
        )
        
        
    def _create_lambda_role(self) -> None:
        """Create IAM role for Lambda functions"""
        
        self.lambda_role = iam.Role(
            self,
            "LambdaExecutionRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSLambdaBasicExecutionRole")
            ],
            inline_policies={
                "PimApiPolicy": iam.PolicyDocument(
                    statements=[
                        # Athena permissions
                        iam.PolicyStatement(
                            effect=iam.Effect.ALLOW,
                            actions=[
                                "athena:StartQueryExecution",
                                "athena:GetQueryExecution",
                                "athena:GetQueryResults",
                                "athena:StopQueryExecution",
                                "athena:GetWorkGroup"
                            ],
                            resources=[
                                f"arn:aws:athena:{self.region}:{self.account}:workgroup/{self.config.athena_workgroup_name}"
                            ]
                        ),
                        # S3 permissions for Athena results
                        iam.PolicyStatement(
                            effect=iam.Effect.ALLOW,
                            actions=[
                                "s3:GetObject",
                                "s3:PutObject",
                                "s3:DeleteObject",
                                "s3:ListBucket",
                                "s3:GetBucketLocation"
                            ],
                            resources=[
                                self.athena_results_bucket.bucket_arn,
                                f"{self.athena_results_bucket.bucket_arn}/*"
                            ]
                        ),
                        # Comprehensive Iceberg permissions for data lake
                        iam.PolicyStatement(
                            effect=iam.Effect.ALLOW,
                            actions=[
                                # S3 permissions for Iceberg data operations
                                "s3:GetObject",
                                "s3:PutObject", 
                                "s3:DeleteObject",
                                "s3:ListBucket",
                                "s3:GetBucketLocation",
                                "s3:GetBucketVersioning",
                                "s3:PutObjectAcl",
                                "s3:GetObjectAcl",
                                "s3:GetObjectVersion",
                                "s3:DeleteObjectVersion"
                            ],
                            resources=[
                                self.data_lake_bucket.bucket_arn,
                                f"{self.data_lake_bucket.bucket_arn}/*"
                            ]
                        ),
                        # Comprehensive Glue permissions for Iceberg operations
                        iam.PolicyStatement(
                            effect=iam.Effect.ALLOW,
                            actions=[
                                "glue:GetDatabase",
                                "glue:GetDatabases", 
                                "glue:GetTable",
                                "glue:GetTables",
                                "glue:UpdateTable",
                                "glue:CreateTable",
                                "glue:DeleteTable",
                                "glue:GetPartition",
                                "glue:GetPartitions",
                                "glue:BatchCreatePartition",
                                "glue:BatchDeletePartition",
                                "glue:BatchUpdatePartition"
                            ],
                            resources=[
                                f"arn:aws:glue:{self.region}:{self.account}:catalog",
                                f"arn:aws:glue:{self.region}:{self.account}:database/{self.config.get_resource_name('catalog')}",
                                f"arn:aws:glue:{self.region}:{self.account}:table/{self.config.get_resource_name('catalog')}/*"
                            ]
                        ),
                        # Glue Job permissions for triggering DQ
                        iam.PolicyStatement(
                            effect=iam.Effect.ALLOW,
                            actions=[
                                "glue:StartJobRun",
                                "glue:GetJobRun"
                            ],
                            resources=[
                                f"arn:aws:glue:{self.region}:{self.account}:job/*"
                            ]
                        ),
                        # Step Functions permissions
                        iam.PolicyStatement(
                            effect=iam.Effect.ALLOW,
                            actions=[
                                "states:StartExecution"
                            ],
                            resources=[
                                f"arn:aws:states:{self.region}:{self.account}:stateMachine:{self.config.get_resource_name('product-etl-workflow')}"
                            ]
                        )
                    ]
                )
            }
        )
    
    def _create_lambda_functions(self) -> None:
        """Create Lambda functions for the API"""
        
        # Create DynamoDB table for cache versions
        self.cache_version_table = dynamodb.Table(
            self,
            "CacheVersionTable",
            table_name=self.config.get_resource_name("cache-versions"),
            partition_key=dynamodb.Attribute(
                name="cache_key",
                type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY,
            time_to_live_attribute="ttl"  # Enable TTL for automatic cache expiration
        )
        
        # Generic Products API Lambda function
        self.lambda_functions['products_api'] = lambda_.Function(
            self,
            "ProductsApiFunction",
            function_name=self.config.get_resource_name("products-api"),
            runtime=lambda_.Runtime.PYTHON_3_9,
            handler="app.lambda_handler",
            code=lambda_.Code.from_asset("source/lambda_functions/products_api"),
            role=self.lambda_role,
            timeout=Duration.seconds(30),
            memory_size=self.config.lambda_memory_size,
            environment={
                "GLUE_DATABASE": self.glue_catalog.ref,
                "ATHENA_WORKGROUP": self.athena_workgroup.ref,
                "ATHENA_RESULTS_BUCKET": self.athena_results_bucket.bucket_name,
                "DATA_LAKE_BUCKET": self.data_lake_bucket.bucket_name,
                "ETL_WORKFLOW_ARN": self.etl_workflow.state_machine_arn,
                "CACHE_VERSION_TABLE": self.cache_version_table.table_name
            },
            description=f"Generic Products API Lambda function - {self.config.environment}"
        )
        
        # Grant Lambda access to cache version table
        self.cache_version_table.grant_read_write_data(self.lambda_functions['products_api'])
        
        # ETL Trigger Lambda function
        self.lambda_functions['etl_trigger'] = lambda_.Function(
            self,
            "EtlTriggerFunction",
            function_name=self.config.get_resource_name("etl-trigger"),
            runtime=lambda_.Runtime.PYTHON_3_9,
            handler="app.lambda_handler",
            code=lambda_.Code.from_asset("source/lambda_functions/etl_trigger"),
            role=self.lambda_role,
            timeout=Duration.seconds(10),
            memory_size=256,
            environment={
                "STATE_MACHINE_ARN": self.etl_workflow.state_machine_arn,
                "DATA_LAKE_BUCKET": self.data_lake_bucket.bucket_name
                # AWS_REGION is automatically available as AWS_DEFAULT_REGION
            },
            description=f"ETL Trigger Lambda function - {self.config.environment}"
        )
    
    def _create_api_gateway(self) -> None:
        """Create API Gateway for the PIM system"""
        
        # Create Cognito authorizer
        cognito_authorizer = apigateway.CognitoUserPoolsAuthorizer(
            self,
            "PimApiAuthorizer",
            cognito_user_pools=[self.user_pool],
            authorizer_name=self.config.get_resource_name("api-authorizer"),
            identity_source="method.request.header.Authorization"
        )
        
        # Create API Gateway
        self.api_gateway = apigateway.RestApi(
            self,
            "PimApi",
            rest_api_name=self.config.get_resource_name("api"),
            description=f"PIM System API - {self.config.environment}",
            default_cors_preflight_options=apigateway.CorsOptions(
                allow_origins=apigateway.Cors.ALL_ORIGINS,
                allow_methods=apigateway.Cors.ALL_METHODS,
                allow_headers=["Content-Type", "X-Amz-Date", "Authorization", "X-Api-Key", "X-Amz-Security-Token"]
            ),
            deploy_options=apigateway.StageOptions(
                stage_name=self.config.environment,
                throttling_rate_limit=1000,
                throttling_burst_limit=2000,
                logging_level=apigateway.MethodLoggingLevel.INFO,
                data_trace_enabled=True,
                metrics_enabled=True
                # Caching disabled - using Lambda-level Athena result reuse instead
            )
        )
        
        # Create Lambda integrations
        products_integration = apigateway.LambdaIntegration(
            self.lambda_functions['products_api'],
            proxy=True
        )
        
        etl_trigger_integration = apigateway.LambdaIntegration(
            self.lambda_functions['etl_trigger'],
            proxy=True
        )
        

        
        # Create API resources and methods
        
        # /api/v1
        api_v1 = self.api_gateway.root.add_resource("api").add_resource("v1")
        
        # /api/v1/etl
        etl_resource = api_v1.add_resource("etl")
        
        # /api/v1/etl/trigger - POST only, requires Cognito authentication
        etl_trigger_resource = etl_resource.add_resource("trigger")
        etl_trigger_resource.add_method(
            "POST",
            etl_trigger_integration,
            authorizer=cognito_authorizer,
            authorization_type=apigateway.AuthorizationType.COGNITO
        )
        
        # /api/v1/{proxy+} - Generic proxy for any product type
        # Caching enabled at stage level, query params included in cache key automatically
        proxy_resource = api_v1.add_resource("{proxy+}")
        proxy_resource.add_method(
            "ANY",
            products_integration,
            authorizer=cognito_authorizer,
            request_parameters={
                "method.request.querystring.limit": False,
                "method.request.querystring.offset": False,
                "method.request.querystring.status": False,
                "method.request.querystring.search": False
            }
        )
        
        # Create API key for external access
        api_key = self.api_gateway.add_api_key(
            "PimApiKey",
            api_key_name=self.config.get_resource_name("api-key"),
            description=f"API key for PIM system - {self.config.environment}"
        )
        
        # Create usage plan
        usage_plan = self.api_gateway.add_usage_plan(
            "PimUsagePlan",
            name=self.config.get_resource_name("usage-plan"),
            description=f"Usage plan for PIM API - {self.config.environment}",
            throttle=apigateway.ThrottleSettings(
                rate_limit=1000,
                burst_limit=2000
            ),
            quota=apigateway.QuotaSettings(
                limit=100000,
                period=apigateway.Period.DAY
            )
        )
        
        usage_plan.add_api_key(api_key)
        usage_plan.add_api_stage(
            stage=self.api_gateway.deployment_stage
        )
        
        CfnOutput(
            self,
            "ApiGatewayUrl",
            value=self.api_gateway.url,
            description="API Gateway URL for PIM system",
            export_name=f"{self.stack_name}-ApiUrl"
        )
        
        CfnOutput(
            self,
            "ApiKeyId",
            value=api_key.key_id,
            description="API Key ID for external access",
            export_name=f"{self.stack_name}-ApiKeyId"
        )
        
        CfnOutput(
            self,
            "ProductsApiLambdaArn",
            value=self.lambda_functions['products_api'].function_arn,
            description="Generic Products API Lambda function ARN",
            export_name=f"{self.stack_name}-ProductsApiLambda"
        )
        
        CfnOutput(
            self,
            "EtlTriggerEndpoint",
            value=f"{self.api_gateway.url}api/v1/etl/trigger",
            description="ETL Trigger API endpoint (POST with Cognito auth)",
            export_name=f"{self.stack_name}-EtlTriggerEndpoint"
        )
        
        CfnOutput(
            self,
            "EtlWorkflowArn",
            value=self.etl_workflow.state_machine_arn,
            description="Step Functions ETL workflow ARN",
            export_name=f"{self.stack_name}-EtlWorkflowArn"
        )
        

        
    def _create_user_pool(self) -> None:
        """Create Cognito User Pool for authentication"""
        
        self.user_pool = cognito.UserPool(
            self,
            "PimUserPool",
            user_pool_name=self.config.get_resource_name("user-pool"),
            sign_in_aliases=cognito.SignInAliases(
                email=True,
                username=True
            ),
            auto_verify=cognito.AutoVerifiedAttrs(email=True),
            password_policy=cognito.PasswordPolicy(
                min_length=8,
                require_lowercase=True,
                require_uppercase=True,
                require_digits=True,
                require_symbols=True
            ),
            account_recovery=cognito.AccountRecovery.EMAIL_ONLY,
            removal_policy=RemovalPolicy.DESTROY,
            mfa=cognito.Mfa.OPTIONAL,
            mfa_second_factor=cognito.MfaSecondFactor(
                sms=True,
                otp=True
            ),
            standard_attributes=cognito.StandardAttributes(
                email=cognito.StandardAttribute(required=True, mutable=True),
                given_name=cognito.StandardAttribute(required=True, mutable=True),
                family_name=cognito.StandardAttribute(required=True, mutable=True)
            ),
            custom_attributes={
                "role": cognito.StringAttribute(min_len=1, max_len=50, mutable=True),
                "department": cognito.StringAttribute(min_len=1, max_len=100, mutable=True)
            }
        )
        
        # Create user groups for role-based access control
        # Two effective roles for the blueprint:
        #   - Editors (read + write): can create/update/delete products, trigger ETL, correct DQ
        #   - Viewers (read only): can browse products, view dashboards, export data
        # Extend with more granular groups (Managers, Administrators) as needed.
        
        editor_group = cognito.CfnUserPoolGroup(
            self,
            "EditorGroup",
            user_pool_id=self.user_pool.user_pool_id,
            group_name="Editors",
            description="Read + write access — create, update, delete products and trigger ETL",
            precedence=1
        )
        
        viewer_group = cognito.CfnUserPoolGroup(
            self,
            "ViewerGroup",
            user_pool_id=self.user_pool.user_pool_id,
            group_name="Viewers",
            description="Read-only access — browse products, view dashboards",
            precedence=2
        )
        
        # --- Blueprint demo users (created via Custom Resource) ---
        # Creates two sample users so the blueprint works out of the box.
        # In production, replace with your identity provider or manual user management.
        
        seed_users_fn = lambda_.Function(
            self,
            "SeedUsersFunction",
            runtime=lambda_.Runtime.PYTHON_3_9,
            handler="index.handler",
            timeout=Duration.seconds(30),
            code=lambda_.Code.from_inline(
                "import boto3, os, json, secrets, string\n"
                "def generate_password():\n"
                "    alpha = string.ascii_letters + string.digits\n"
                "    special = '!@#$%^&*'\n"
                "    pwd = [secrets.choice(string.ascii_uppercase), secrets.choice(string.ascii_lowercase),\n"
                "           secrets.choice(string.digits), secrets.choice(special)]\n"
                "    pwd += [secrets.choice(alpha + special) for _ in range(12)]\n"
                "    secrets.SystemRandom().shuffle(pwd)\n"
                "    return ''.join(pwd)\n"
                "def handler(event, context):\n"
                "    if event.get('RequestType') == 'Delete':\n"
                "        return {'PhysicalResourceId': 'seed-users'}\n"
                "    cognito_client = boto3.client('cognito-idp')\n"
                "    sm_client = boto3.client('secretsmanager')\n"
                "    pool = os.environ['USER_POOL_ID']\n"
                "    secret_arn = os.environ['SECRET_ARN']\n"
                "    users = [\n"
                "        {'username':'admin','email':'admin@example.com','given':'Admin','family':'User','group':'Editors'},\n"
                "        {'username':'viewer','email':'viewer@example.com','given':'Viewer','family':'User','group':'Viewers'},\n"
                "    ]\n"
                "    creds = {}\n"
                "    for u in users:\n"
                "        pwd = generate_password()\n"
                "        try:\n"
                "            cognito_client.admin_create_user(UserPoolId=pool,Username=u['username'],\n"
                "                UserAttributes=[{'Name':'email','Value':u['email']},{'Name':'email_verified','Value':'true'},\n"
                "                    {'Name':'given_name','Value':u['given']},{'Name':'family_name','Value':u['family']}],\n"
                "                MessageAction='SUPPRESS',TemporaryPassword=pwd)\n"
                "        except cognito_client.exceptions.UsernameExistsException:\n"
                "            pwd = generate_password()\n"
                "        try: cognito_client.admin_set_user_password(UserPoolId=pool,Username=u['username'],Password=pwd,Permanent=True)\n"
                "        except Exception: pass\n"
                "        try: cognito_client.admin_add_user_to_group(UserPoolId=pool,Username=u['username'],GroupName=u['group'])\n"
                "        except Exception: pass\n"
                "        creds[u['username']] = {'password': pwd, 'email': u['email'], 'group': u['group']}\n"
                "    sm_client.put_secret_value(SecretId=secret_arn, SecretString=json.dumps(creds))\n"
                "    return {'PhysicalResourceId': 'seed-users'}\n"
            ),
            environment={
                "USER_POOL_ID": self.user_pool.user_pool_id,
            },
        )
        
        # Create Secrets Manager secret for seed user credentials
        seed_users_secret = secretsmanager.Secret(
            self, "SeedUsersSecret",
            secret_name=f"{self.config.get_resource_name('seed-user-credentials')}",
            description="Auto-generated credentials for PIM demo seed users",
            secret_string_value=SecretValue.unsafe_plain_text("{}"),
        )
        seed_users_fn.add_environment("SECRET_ARN", seed_users_secret.secret_arn)
        
        seed_users_fn.add_to_role_policy(iam.PolicyStatement(
            actions=["cognito-idp:AdminCreateUser", "cognito-idp:AdminSetUserPassword", "cognito-idp:AdminAddUserToGroup"],
            resources=[self.user_pool.user_pool_arn],
        ))
        seed_users_secret.grant_write(seed_users_fn)
        
        seed_provider = cr.Provider(self, "SeedUsersProvider", on_event_handler=seed_users_fn)
        CustomResource(self, "SeedUsersResource", service_token=seed_provider.service_token)
        
        # Create User Pool Client for web application
        self.user_pool_client = self.user_pool.add_client(
            "PimWebClient",
            user_pool_client_name=self.config.get_resource_name("web-client"),
            generate_secret=False,  # For web apps, don't use client secret
            auth_flows=cognito.AuthFlow(
                user_password=True,
                user_srp=True,
                admin_user_password=True
            ),
            o_auth=cognito.OAuthSettings(
                flows=cognito.OAuthFlows(
                    authorization_code_grant=True,
                    implicit_code_grant=True
                ),
                scopes=[
                    cognito.OAuthScope.EMAIL,
                    cognito.OAuthScope.OPENID,
                    cognito.OAuthScope.PROFILE
                ],
                callback_urls=["http://localhost:3000/callback"],  # Will be updated for Amplify
                logout_urls=["http://localhost:3000/logout"]
            ),
            supported_identity_providers=[
                cognito.UserPoolClientIdentityProvider.COGNITO
            ]
        )
        
        # Create User Pool Domain
        user_pool_domain = self.user_pool.add_domain(
            "PimUserPoolDomain",
            cognito_domain=cognito.CognitoDomainOptions(
                domain_prefix=self.config.get_resource_name("auth", self.account).lower()
            )
        )
    
    def _create_identity_pool(self) -> None:
        """Create Cognito Identity Pool for AWS resource access"""
        
        self.identity_pool = cognito.CfnIdentityPool(
            self,
            "PimIdentityPool",
            identity_pool_name=self.config.get_resource_name("identity-pool"),
            allow_unauthenticated_identities=False,
            cognito_identity_providers=[
                cognito.CfnIdentityPool.CognitoIdentityProviderProperty(
                    client_id=self.user_pool_client.user_pool_client_id,
                    provider_name=self.user_pool.user_pool_provider_name
                )
            ]
        )
        
        # Create IAM roles for authenticated users
        authenticated_role = iam.Role(
            self,
            "CognitoAuthenticatedRole",
            assumed_by=iam.FederatedPrincipal(
                "cognito-identity.amazonaws.com",
                {
                    "StringEquals": {
                        "cognito-identity.amazonaws.com:aud": self.identity_pool.ref
                    },
                    "ForAnyValue:StringLike": {
                        "cognito-identity.amazonaws.com:amr": "authenticated"
                    }
                },
                "sts:AssumeRoleWithWebIdentity"
            ),
            inline_policies={
                "PimUserPolicy": iam.PolicyDocument(
                    statements=[
                        # Allow users to access their own data
                        iam.PolicyStatement(
                            effect=iam.Effect.ALLOW,
                            actions=[
                                "s3:GetObject",
                                "s3:PutObject"
                            ],
                            resources=[
                                f"{self.assets_bucket.bucket_arn}/users/${{cognito-identity.amazonaws.com:sub}}/*"
                            ]
                        ),
                        # Allow API Gateway access — scoped to this stack's API
                        iam.PolicyStatement(
                            effect=iam.Effect.ALLOW,
                            actions=[
                                "execute-api:Invoke"
                            ],
                            resources=[
                                f"arn:aws:execute-api:{self.region}:{self.account}:*/{self.config.environment}/*/api/v1/*"
                            ]
                        )
                    ]
                )
            }
        )
        
        # Attach the role to the identity pool
        cognito.CfnIdentityPoolRoleAttachment(
            self,
            "IdentityPoolRoleAttachment",
            identity_pool_id=self.identity_pool.ref,
            roles={
                "authenticated": authenticated_role.role_arn
            }
        )
        
        CfnOutput(
            self,
            "UserPoolId",
            value=self.user_pool.user_pool_id,
            description="Cognito User Pool ID",
            export_name=f"{self.stack_name}-UserPoolId"
        )
        
        CfnOutput(
            self,
            "UserPoolClientId",
            value=self.user_pool_client.user_pool_client_id,
            description="Cognito User Pool Client ID",
            export_name=f"{self.stack_name}-UserPoolClientId"
        )
        
        CfnOutput(
            self,
            "IdentityPoolId",
            value=self.identity_pool.ref,
            description="Cognito Identity Pool ID",
            export_name=f"{self.stack_name}-IdentityPoolId"
        )
        
    def _create_amplify_app(self) -> None:
        """Create Amplify application for PIM UI"""
        
        if amplify is None:
            print("⚠️  Amplify alpha module not available. Skipping Amplify app creation.")
            print("   You can deploy the frontend manually using the frontend/ directory.")
            self.amplify_app = None
            return
        
        # Create Amplify app (without source code provider - will be connected manually)
        self.amplify_app = amplify.App(
            self,
            "PimAmplifyApp",
            app_name=self.config.get_resource_name("pim-ui"),
            description=f"PIM System UI - {self.config.environment}",
            environment_variables={
                "REACT_APP_API_URL": self.api_gateway.url,
                "REACT_APP_USER_POOL_ID": self.user_pool.user_pool_id,
                "REACT_APP_USER_POOL_CLIENT_ID": self.user_pool_client.user_pool_client_id,
                "REACT_APP_IDENTITY_POOL_ID": self.identity_pool.ref,
                "REACT_APP_REGION": self.config.region,
                "REACT_APP_ENVIRONMENT": self.config.environment,
                "REACT_APP_ASSETS_BUCKET": self.assets_bucket.bucket_name
            }
        )
        
        # Create main branch for manual deployment (zip upload)
        self.amplify_branch = self.amplify_app.add_branch(
            "main",
            branch_name="main",
            auto_build=False,
        )
        
        if self.amplify_app is not None:
            CfnOutput(
                self,
                "AmplifyAppId",
                value=self.amplify_app.app_id,
                description="Amplify App ID for PIM UI",
                export_name=f"{self.stack_name}-AmplifyAppId"
            )
            
            CfnOutput(
                self,
                "AmplifyAppUrl",
                value=f"https://main.{self.amplify_app.app_id}.amplifyapp.com",
                description="Amplify App URL for PIM UI",
                export_name=f"{self.stack_name}-AmplifyAppUrl"
            )
        
        CfnOutput(
            self,
            "PimCoreStackDeployed",
            value="true",
            description="PIM Core Stack (Blueprint Foundation) deployed successfully"
        )