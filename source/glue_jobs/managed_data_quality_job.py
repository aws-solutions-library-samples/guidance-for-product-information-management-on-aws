"""
Tier 1: Managed Data Quality Job using AWS Glue Data Quality (DQDL)
Evaluates standard rules (completeness, format, range) with row-level outcomes.
Passes results to Tier 2 custom job via S3.
"""
import sys
import re
from awsglue.transforms import *
from awsglue.utils import getResolvedOptions
from pyspark.context import SparkContext
from awsglue.context import GlueContext
from awsglue.job import Job
from awsglue.dynamicframe import DynamicFrame
from pyspark.sql.functions import *
from pyspark.sql.types import *
from datetime import datetime

from awsgluedq.transforms import EvaluateDataQuality

args = getResolvedOptions(sys.argv, ['JOB_NAME', 'data_lake_bucket', 'glue_database'])

sc = SparkContext()
glueContext = GlueContext(sc)
spark = glueContext.spark_session
job = Job(glueContext)
job.init(args['JOB_NAME'], args)

DATA_LAKE_BUCKET = args['data_lake_bucket']
GLUE_DATABASE = args['glue_database']

# Validate identifiers to prevent SQL injection
if not re.match(r'^[a-zA-Z0-9_-]+$', GLUE_DATABASE):
    raise ValueError(f"Invalid glue_database name: {GLUE_DATABASE}")

JOB_RUN_ID = args.get('JOB_RUN_ID', None)
if not JOB_RUN_ID:
    for i, arg in enumerate(sys.argv):
        if arg == '--JOB_RUN_ID' and i + 1 < len(sys.argv):
            JOB_RUN_ID = sys.argv[i + 1]
            break
    else:
        JOB_RUN_ID = f"dq-t1-{int(datetime.now().timestamp())}"
TIMESTAMP = datetime.now().isoformat()

# Iceberg catalog config
spark.conf.set("spark.sql.catalog.glue_catalog", "org.apache.iceberg.spark.SparkCatalog")
spark.conf.set("spark.sql.catalog.glue_catalog.warehouse", f"s3://{DATA_LAKE_BUCKET}/iceberg/")
spark.conf.set("spark.sql.catalog.glue_catalog.catalog-impl", "org.apache.iceberg.aws.glue.GlueCatalog")
spark.conf.set("spark.sql.catalog.glue_catalog.io-impl", "org.apache.iceberg.aws.s3.S3FileIO")

# --- DQDL Ruleset: Standard declarative rules ---
DQDL_RULESET = """
Rules = [
    Completeness "upc_ean" = 1.0,
    Completeness "base_name" = 1.0,
    Completeness "currency_code" = 1.0,
    Completeness "base_price" = 1.0,
    ColumnLength "upc_ean" >= 3,
    ColumnValues "base_price" > 0,
    ColumnValues "currency_code" in ["USD","AUD","GBP","EUR","NZD"],
    ColumnLength "base_name" between 2 and 500,
    Uniqueness "upc_ean" = 1.0
]
"""


def iceberg_table(table_name):
    """Read an Iceberg table via DataFrame API — no SQL string construction."""
    return spark.table(f"glue_catalog.{GLUE_DATABASE}.{table_name}")


def merge_update_products(product_ids_list, dq_status, status):
    """Update product dq_status/status for a list of IDs using temp view + MERGE."""
    if not product_ids_list:
        return
    updates_df = spark.createDataFrame(
        [(pid, dq_status, status) for pid in product_ids_list],
        ["product_id", "new_dq_status", "new_status"]
    )
    updates_df.createOrReplaceTempView("_dq_updates")
    spark.sql(f"""
        MERGE INTO glue_catalog.{GLUE_DATABASE}.product AS target
        USING _dq_updates AS source
        ON target.product_id = source.product_id
        WHEN MATCHED THEN UPDATE SET
            target.dq_status = source.new_dq_status,
            target.status = source.new_status
    """)


def delete_failed_records_for(product_ids_list):
    """Delete dq_failed_records for a list of product IDs using temp view."""
    if not product_ids_list:
        return
    ids_df = spark.createDataFrame([(pid,) for pid in product_ids_list], ["product_id"])
    ids_df.createOrReplaceTempView("_ids_to_delete")
    spark.sql(f"""
        DELETE FROM glue_catalog.{GLUE_DATABASE}.dq_failed_records
        WHERE product_id IN (SELECT product_id FROM _ids_to_delete)
    """)


try:
    print(f"=== Tier 1 Managed DQ: {JOB_RUN_ID} ===")

    # Read only pending records via DataFrame API
    pending_df = iceberg_table("product") \
        .filter(col("dq_status") == "pending") \
        .select(
            col("product_id"), col("upc_ean"), col("brand"), col("base_name"),
            col("short_description"),
            col("base_price").cast("double").alias("base_price"),
            col("currency_code"),
            col("stock_quantity").cast("int").alias("stock_quantity"),
            col("status"), col("dq_status")
        )

    pending_count = pending_df.count()
    print(f"Pending records: {pending_count}")

    if pending_count == 0:
        print("No pending records. Writing empty summary.")
        spark.createDataFrame([], StructType([
            StructField("product_id", StringType())
        ])).write.mode("overwrite").parquet(
            f"s3://{DATA_LAKE_BUCKET}/dq-staging/{JOB_RUN_ID}/tier1-passed/"
        )
    else:
        # Convert to DynamicFrame for Glue DQ
        pending_dyf = DynamicFrame.fromDF(pending_df, glueContext, "pending_products")

        # Evaluate with DQDL — row-level outcomes via process_rows()
        from awsglue.transforms import SelectFromCollection

        dq_results = EvaluateDataQuality().process_rows(
            frame=pending_dyf,
            ruleset=DQDL_RULESET,
            publishing_options={
                "dataQualityEvaluationContext": f"pim-tier1-{JOB_RUN_ID}",
                "enableDataQualityCloudWatchMetrics": False,
                "enableDataQualityResultsPublishing": False,
            },
            additional_options={"performanceTuning.caching": "CACHE_NOTHING"},
        )

        # Row-level outcomes (original data + DQ columns per row)
        row_level_dyf = SelectFromCollection.apply(
            dfc=dq_results, key="rowLevelOutcomes"
        )
        row_outcomes_df = row_level_dyf.toDF()

        print(f"Row outcomes columns: {row_outcomes_df.columns}")
        row_outcomes_df.select("product_id", "DataQualityEvaluationResult").show(10, truncate=False)

        # Split passed vs failed
        tier1_passed = row_outcomes_df.filter(col("DataQualityEvaluationResult") == "Passed")
        tier1_failed = row_outcomes_df.filter(col("DataQualityEvaluationResult") == "Failed")

        passed_count = tier1_passed.count()
        failed_count = tier1_failed.count()
        print(f"Tier 1 results: {passed_count} passed, {failed_count} failed")

        # --- Handle Tier 1 failures ---
        if failed_count > 0:
            failed_ids = [r.product_id for r in tier1_failed.select("product_id").collect()]

            # Update product table: failed → draft
            merge_update_products(failed_ids, "failed", "draft")

            # Clear old failures for these products
            try:
                delete_failed_records_for(failed_ids)
            except Exception as e:
                print(f"Note: Could not clear old failures: {e}")

            # Write to dq_failed_records — explode DataQualityRulesFail array
            failed_records = tier1_failed.select(
                col("product_id"),
                lit(JOB_RUN_ID).alias("run_id"),
                explode(col("DataQualityRulesFail")).alias("failure_reason")
            ).withColumn("failed_field",
                when(col("failure_reason").contains("upc_ean"), lit("upc_ean"))
                .when(col("failure_reason").contains("base_price"), lit("base_price"))
                .when(col("failure_reason").contains("base_name"), lit("base_name"))
                .when(col("failure_reason").contains("currency_code"), lit("currency_code"))
                .otherwise(lit("standard_rule"))
            ).withColumn("failed_value", lit(None).cast("string")) \
             .withColumn("correction_status", lit("pending")) \
             .withColumn("corrected_by", lit(None).cast("string")) \
             .withColumn("corrected_date", lit(None).cast("timestamp")) \
             .withColumn("created_date", current_timestamp())

            failed_records.writeTo(f"glue_catalog.{GLUE_DATABASE}.dq_failed_records").append()
            print(f"Wrote {failed_records.count()} Tier 1 failure records")

        # --- Write Tier 1 passed records to staging for Tier 2 ---
        if passed_count > 0:
            tier1_clean = tier1_passed.drop(
                "DataQualityRulesPass", "DataQualityRulesFail",
                "DataQualityRulesSkip", "DataQualityEvaluationResult"
            )
            tier1_clean.write.mode("overwrite").parquet(
                f"s3://{DATA_LAKE_BUCKET}/dq-staging/{JOB_RUN_ID}/tier1-passed/"
            )
            print(f"Staged {passed_count} passed records for Tier 2")
        else:
            spark.createDataFrame([], pending_df.schema).write.mode("overwrite").parquet(
                f"s3://{DATA_LAKE_BUCKET}/dq-staging/{JOB_RUN_ID}/tier1-passed/"
            )

    print("Tier 1 Managed DQ complete")

except Exception as e:
    print(f"Tier 1 DQ failed: {e}")
    import traceback
    traceback.print_exc()
    raise

finally:
    job.commit()
