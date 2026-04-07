"""
Tier 2: Custom Data Quality Job - Cross-table and business rule validation
Reads Tier 1 passed records from staging, applies rules that DQDL cannot express.
"""
import sys
import re
from awsglue.utils import getResolvedOptions
from pyspark.context import SparkContext
from awsglue.context import GlueContext
from awsglue.job import Job
import builtins
from pyspark.sql.functions import *
from pyspark.sql.types import *
from datetime import datetime

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
        JOB_RUN_ID = f"dq-t2-{int(datetime.now().timestamp())}"
TIMESTAMP = datetime.now().isoformat()

# Iceberg catalog config
spark.conf.set("spark.sql.catalog.glue_catalog", "org.apache.iceberg.spark.SparkCatalog")
spark.conf.set("spark.sql.catalog.glue_catalog.warehouse", f"s3://{DATA_LAKE_BUCKET}/iceberg/")
spark.conf.set("spark.sql.catalog.glue_catalog.catalog-impl", "org.apache.iceberg.aws.glue.GlueCatalog")
spark.conf.set("spark.sql.catalog.glue_catalog.io-impl", "org.apache.iceberg.aws.s3.S3FileIO")


def iceberg_table(table_name):
    """Read an Iceberg table via DataFrame API — no SQL string construction."""
    return spark.table(f"glue_catalog.{GLUE_DATABASE}.{table_name}")


def validate_cross_table(products_df):
    """Cross-table validations: category assignment, required attributes"""

    errors_df = spark.createDataFrame([], StructType([
        StructField("product_id", StringType()),
        StructField("failure_reason", StringType()),
        StructField("failed_field", StringType())
    ]))

    product_ids = products_df.select("product_id")

    # 1. Every product must have at least one category
    cat_counts = iceberg_table("product_category") \
        .groupBy("product_id").agg(count("*").alias("cat_count"))
    missing_cats = product_ids.join(cat_counts, "product_id", "left") \
        .filter(col("cat_count").isNull()) \
        .select(
            col("product_id"),
            lit("Product has no category assignment").alias("failure_reason"),
            lit("category").alias("failed_field")
        )
    errors_df = errors_df.union(missing_cats)

    # 2. Required attributes must be populated (from attribute_definition.is_required)
    required_attrs = iceberg_table("attribute_definition") \
        .filter(col("is_required") == True)
    required_list = required_attrs.collect()

    if required_list:
        attr_values = iceberg_table("product_attribute_value") \
            .filter((col("value").isNotNull()) & (col("value") != "")) \
            .select("product_id", "attribute_id")

        for req in required_list:
            has_attr = attr_values.filter(col("attribute_id") == req.attribute_id) \
                .select("product_id")
            missing = product_ids.subtract(has_attr).select(
                col("product_id"),
                lit(f"Missing required attribute: {req.name}").alias("failure_reason"),
                lit(req.code).alias("failed_field")
            )
            errors_df = errors_df.union(missing)

    return errors_df


def validate_cross_field(products_df):
    """Cross-field validations: business logic across multiple columns"""

    errors_df = spark.createDataFrame([], StructType([
        StructField("product_id", StringType()),
        StructField("failure_reason", StringType()),
        StructField("failed_field", StringType())
    ]))

    # 1. Active products must have price > 0 and stock > 0
    active_invalid = products_df.filter(
        (col("status") == "active") &
        ((col("base_price").cast("double") <= lit(0)) | (col("stock_quantity").cast("int") <= lit(0)))
    ).select(
        col("product_id"),
        lit("Active product must have price > 0 and stock > 0").alias("failure_reason"),
        lit("status/price/stock").alias("failed_field")
    )
    errors_df = errors_df.union(active_invalid)

    return errors_df


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
    """Delete dq_failed_records for a list of product IDs using temp view + MERGE/DELETE."""
    if not product_ids_list:
        return
    ids_df = spark.createDataFrame([(pid,) for pid in product_ids_list], ["product_id"])
    ids_df.createOrReplaceTempView("_ids_to_delete")
    spark.sql(f"""
        DELETE FROM glue_catalog.{GLUE_DATABASE}.dq_failed_records
        WHERE product_id IN (SELECT product_id FROM _ids_to_delete)
    """)


try:
    print(f"=== Tier 2 Custom DQ: {JOB_RUN_ID} ===")

    # Read Tier 1 passed records from staging
    staging_path = f"s3://{DATA_LAKE_BUCKET}/dq-staging/{JOB_RUN_ID}/tier1-passed/"
    tier1_passed = spark.read.parquet(staging_path)
    tier1_count = tier1_passed.count()
    print(f"Tier 1 passed records to validate: {tier1_count}")

    # Count Tier 1 failures (already written by Tier 1 job)
    tier1_failed_count = iceberg_table("dq_failed_records") \
        .filter(col("run_id") == JOB_RUN_ID) \
        .count()

    tier2_failed_count = 0
    tier2_passed_count = 0

    if tier1_count > 0:
        # Run cross-table validations
        cross_table_errors = validate_cross_table(tier1_passed)
        # Run cross-field validations
        cross_field_errors = validate_cross_field(tier1_passed)

        # Combine all Tier 2 errors
        all_errors = cross_table_errors.union(cross_field_errors)
        failed_product_ids = all_errors.select("product_id").distinct()
        tier2_failed_count = failed_product_ids.count()

        # Products that passed both tiers
        tier2_passed = tier1_passed.join(failed_product_ids, "product_id", "left_anti")
        tier2_passed_count = tier2_passed.count()

        print(f"Tier 2 results: {tier2_passed_count} passed, {tier2_failed_count} failed")

        # --- Update passed products ---
        if tier2_passed_count > 0:
            passed_ids = [r.product_id for r in tier2_passed.select("product_id").collect()]
            merge_update_products(passed_ids, "passed", "active")
            # Remove from failed records if previously failed
            try:
                delete_failed_records_for(passed_ids)
            except Exception:
                print("Exception at failed records delete")
            print(f"Updated {tier2_passed_count} products to passed/active")

        # --- Update failed products ---
        if tier2_failed_count > 0:
            failed_ids = [r.product_id for r in failed_product_ids.collect()]
            merge_update_products(failed_ids, "failed", "draft")

            # Write Tier 2 failures to dq_failed_records
            tier2_failures = all_errors.withColumn("run_id", lit(JOB_RUN_ID)) \
                .withColumn("failed_value", lit(None).cast("string")) \
                .withColumn("correction_status", lit("pending")) \
                .withColumn("corrected_by", lit(None).cast("string")) \
                .withColumn("corrected_date", lit(None).cast("timestamp")) \
                .withColumn("created_date", current_timestamp()) \
                .select("product_id", "run_id", "failure_reason", "failed_field",
                        "failed_value", "correction_status", "corrected_by",
                        "corrected_date", "created_date")

            tier2_failures.writeTo(f"glue_catalog.{GLUE_DATABASE}.dq_failed_records").append()
            print(f"Wrote {all_errors.count()} Tier 2 failure records")

    # --- Write combined DQ run summary ---
    total_processed = tier1_count + tier1_failed_count
    total_failed = tier1_failed_count + tier2_failed_count
    total_valid = tier2_passed_count

    # Overall system success rate
    product_table = iceberg_table("product").filter(col("status") != "deleted")
    total_count = product_table.count()
    passed_count = product_table.filter(col("dq_status") == "passed").count()
    success_rate = (passed_count / total_count * 100) if total_count > 0 else 0.0

    summary_df = spark.createDataFrame([{
        'job_run_id': JOB_RUN_ID,
        'timestamp': TIMESTAMP,
        'total_records': int(total_processed),
        'failed_records': int(total_failed),
        'valid_records': int(total_valid),
        'success_rate': float(builtins.round(success_rate, 1)),
        'total_products_in_system': int(total_count)
    }], StructType([
        StructField("job_run_id", StringType()),
        StructField("timestamp", StringType()),
        StructField("total_records", LongType()),
        StructField("failed_records", LongType()),
        StructField("valid_records", LongType()),
        StructField("success_rate", DoubleType()),
        StructField("total_products_in_system", LongType())
    ]))

    summary_df.writeTo(f"glue_catalog.{GLUE_DATABASE}.dq_run_summary").append()
    print(f"Summary: {total_processed} processed, {total_valid} passed, {total_failed} failed, {success_rate:.1f}%")

    # Cleanup staging
    import boto3
    s3 = boto3.resource('s3')
    bucket = s3.Bucket(DATA_LAKE_BUCKET)
    bucket.objects.filter(Prefix=f"dq-staging/{JOB_RUN_ID}/").delete()
    print("Cleaned up staging data")

    print("Tier 2 Custom DQ complete")

except Exception as e:
    print(f"Tier 2 DQ failed: {e}")
    import traceback
    traceback.print_exc()
    raise

finally:
    job.commit()
