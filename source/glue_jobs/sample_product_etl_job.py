#!/usr/bin/env python3
"""
Production-Grade Product ETL Job - Optimized for Large-Scale Processing

Best Practices Applied:
- No .collect() on large datasets
- Broadcast joins for small lookup tables
- Single-pass processing with stack/melt operations
- Minimal count() operations
- Proper caching strategy
- Partition-aware writes
"""

import sys
import re
from awsglue.transforms import *
from awsglue.utils import getResolvedOptions
from pyspark.context import SparkContext
from awsglue.context import GlueContext
from awsglue.job import Job
from pyspark.sql.functions import *
from pyspark.sql.types import *
import boto3
from datetime import datetime
import uuid as uuid_lib

# Initialize Glue context
args = getResolvedOptions(sys.argv, [
    'JOB_NAME',
    'data_lake_bucket',
    'glue_database',
    'quarantine_bucket'
])

sc = SparkContext()
glueContext = GlueContext(sc)
spark = glueContext.spark_session
job = Job(glueContext)
job.init(args['JOB_NAME'], args)

# Validate identifiers to prevent SQL injection
for _key in ['glue_database', 'data_lake_bucket', 'quarantine_bucket']:
    if not re.match(r'^[a-zA-Z0-9_.-]+$', args[_key]):
        raise ValueError(f"Invalid {_key}: {args[_key]}")

GLUE_DATABASE = args['glue_database']

# Create UUID UDF for Spark versions without native uuid()
def generate_uuid():
    return str(uuid_lib.uuid4())

uuid_udf = udf(generate_uuid, StringType())

# Configure Spark for Iceberg
spark.conf.set("spark.sql.catalog.glue_catalog", "org.apache.iceberg.spark.SparkCatalog")
spark.conf.set("spark.sql.catalog.glue_catalog.warehouse", f"s3://{args['data_lake_bucket']}/iceberg/")
spark.conf.set("spark.sql.catalog.glue_catalog.catalog-impl", "org.apache.iceberg.aws.glue.GlueCatalog")
spark.conf.set("spark.sql.catalog.glue_catalog.io-impl", "org.apache.iceberg.aws.s3.S3FileIO")

# Optimize for large files
spark.conf.set("spark.sql.files.maxPartitionBytes", "134217728")  # 128MB
spark.conf.set("spark.sql.adaptive.enabled", "true")
spark.conf.set("spark.sql.adaptive.coalescePartitions.enabled", "true")


def iceberg_table(table_name):
    """Read an Iceberg table via DataFrame API — no SQL string construction."""
    return spark.table(f"glue_catalog.{GLUE_DATABASE}.{table_name}")


def write_to_iceberg(df, table_name, merge_key=None):
    """Write DataFrame to Iceberg table. Uses MERGE if merge_key provided, else append."""
    # Validate identifiers used in SQL
    if not re.match(r'^[a-zA-Z0-9_]+$', table_name):
        raise ValueError(f"Invalid table_name: {table_name}")
    if merge_key and not re.match(r'^[a-zA-Z0-9_]+$', merge_key):
        raise ValueError(f"Invalid merge_key: {merge_key}")
    try:
        if merge_key:
            # Deduplicate source on merge_key — keep first occurrence
            deduped = df.dropDuplicates([merge_key])
            view_name = f"source_{table_name}"
            deduped.createOrReplaceTempView(view_name)

            # Build SET clause from all columns except the merge key
            columns = [c for c in df.columns if c != merge_key]
            set_clause = ", ".join([f"target.{c} = source.{c}" for c in columns])
            insert_cols = ", ".join(df.columns)
            insert_vals = ", ".join([f"source.{c}" for c in df.columns])

            # Table name and merge_key are validated above; column names come from df.columns (safe).
            merge_sql = f"""
            MERGE INTO glue_catalog.{GLUE_DATABASE}.{table_name} AS target
            USING {view_name} AS source
            ON target.{merge_key} = source.{merge_key}
            WHEN MATCHED THEN UPDATE SET {set_clause}
            WHEN NOT MATCHED THEN INSERT ({insert_cols}) VALUES ({insert_vals})
            """
            spark.sql(merge_sql)
            print(f"Successfully merged into {table_name} on {merge_key}")
        else:
            df.writeTo(f"glue_catalog.{GLUE_DATABASE}.{table_name}").append()
            print(f"Successfully wrote to {table_name}")
    except Exception as e:
        print(f"Error writing to {table_name}: {str(e)}")
        raise


def process_product_data():
    """Process incoming product data - optimized for scale"""

    raw_data_path = f"s3://{args['data_lake_bucket']}/raw/products/"

    try:
        # Read raw data
        raw_dynamic_frame = glueContext.create_dynamic_frame.from_options(
            connection_type="s3",
            connection_options={
                "paths": [raw_data_path],
                "recurse": True
            },
            format="json",
            format_options={"multiline": True},
            transformation_ctx="raw_products_source"
        )

        raw_json = raw_dynamic_frame.toDF()

        # Early exit if no data - avoid count()
        if raw_json.rdd.isEmpty():
            print("No data to process")
            return

        # Extract and cache products - single pass
        products_df = raw_json.select(explode(col("products")).alias("product")).cache()

        # Process in optimal order
        products_with_uuid = process_products(products_df)

        # All downstream processing uses the same cached DataFrame with UUIDs
        process_product_attributes_optimized(products_with_uuid)
        process_categories_and_assignments(products_with_uuid)
        process_media_assets(products_with_uuid)

        products_df.unpersist()

        print("ETL completed successfully")

    except Exception as e:
        print(f"Error processing product data: {str(e)}")
        import traceback
        traceback.print_exc()
        raise


def process_products(products_df):
    """Process products with MERGE on upc_ean to prevent duplicates"""
    try:
        raw_products_base = products_df.select("product.*")

        price_field = raw_products_base.schema["base_price"]
        if hasattr(price_field.dataType, 'fields'):
            price_col = coalesce(
                col("base_price.double"),
                col("base_price.int").cast("double")
            ).cast("decimal(10,2)")
        else:
            price_col = col("base_price").cast("decimal(10,2)")

        raw_products = raw_products_base.select(
            col("upc_ean"),
            col("brand"),
            col("base_name"),
            col("short_description"),
            col("long_description"),
            price_col.alias("base_price"),
            col("currency_code"),
            coalesce(col("stock_quantity"), lit(0)).alias("stock_quantity"),
            col("attributes"),
            col("categories"),
            col("primary_category"),
            col("media_assets"),
        ).filter(col("upc_ean").isNotNull() & col("base_name").isNotNull())

        # Lookup existing product_ids by upc_ean via DataFrame API
        existing_products = iceberg_table("product").select("product_id", "upc_ean")

        # Join: reuse existing product_id or generate new UUID
        products_with_uuid = raw_products.join(
            broadcast(existing_products), "upc_ean", "left"
        ).withColumn(
            "product_id", coalesce(col("product_id"), uuid_udf())
        ).withColumn("status", lit("draft")) \
         .withColumn("dq_status", lit("pending")) \
         .withColumn("completeness_score", lit(0)) \
         .withColumn("low_stock", (col("stock_quantity") < 10)) \
         .withColumn("assigned_to", lit(None).cast("string")) \
         .withColumn("assigned_date", lit(None).cast("timestamp")) \
         .withColumn("created_date", current_timestamp()) \
         .withColumn("modified_date", current_timestamp())

        products_with_uuid.cache()

        # Write products using MERGE on upc_ean
        product_records = products_with_uuid.select(
            "product_id", "upc_ean", "brand", "base_name",
            "short_description", "base_price",
            "currency_code", "stock_quantity", "status", "dq_status",
            "completeness_score", "low_stock", "assigned_to", "assigned_date",
            "created_date", "modified_date"
        )

        write_to_iceberg(product_records, "product", merge_key="upc_ean")

        return products_with_uuid

    except Exception as e:
        print(f"Error processing products: {str(e)}")
        raise


def process_product_attributes_optimized(products_with_uuid):
    """
    Optimized attribute processing - NO LOOP, NO COLLECT!
    Uses stack() to unpivot in a single operation
    """
    try:
        # Load attribute definitions and broadcast (small table) via DataFrame API
        attr_def_df = iceberg_table("attribute_definition").select("attribute_id", "code")

        # Create broadcast map for lookup
        attr_broadcast = broadcast(attr_def_df)

        # Get products with attributes
        products_with_attrs = products_with_uuid.filter(col("attributes").isNotNull())

        # Dynamically build stack expression from schema
        attr_schema = products_with_attrs.select("attributes").schema.fields[0].dataType
        attr_fields = [f.name for f in attr_schema.fields]

        # Filter out non-attribute fields
        exclude_fields = {'stock_quantity', 'inventory_status', 'status', 'base_name', 'base_price', 'currency_code'}
        attr_fields = [f for f in attr_fields if f not in exclude_fields]

        if not attr_fields:
            print("No attributes to process")
            return

        stack_expr = f"stack({len(attr_fields)}, " + \
                     ", ".join([f"'{field}', CAST(attributes.{field} AS STRING)" for field in attr_fields]) + \
                     ") as (raw_attr_name, attr_value)"

        # Single operation to unpivot all attributes
        unpivoted = products_with_attrs.select(
            "product_id",
            expr(stack_expr)
        ).filter(col("attr_value").isNotNull())

        # Map raw attribute names to numeric attribute_ids
        attributes_with_ids = unpivoted.join(
            broadcast(attr_def_df.select(col("code").alias("raw_attr_name"), col("attribute_id"))),
            "raw_attr_name",
            "left"
        ).filter(col("attribute_id").isNotNull())

        # Create final attribute records with timestamps
        attribute_records = attributes_with_ids.select(
            col("product_id"),
            col("attribute_id"),
            col("attr_value").cast("string").alias("value"),
            current_timestamp().alias("created_date"),
            current_timestamp().alias("modified_date")
        )

        # Add long_description as 'synopsis' attribute if present (attribute_id=2)
        synopsis_records = products_with_uuid.filter(col("long_description").isNotNull()).select(
            col("product_id"),
            lit(2).alias("attribute_id"),
            col("long_description").cast("string").alias("value"),
            current_timestamp().alias("created_date"),
            current_timestamp().alias("modified_date")
        )

        attribute_records = attribute_records.union(synopsis_records)

        write_to_iceberg(attribute_records, "product_attribute_value")
        print("Processed attributes successfully")

    except Exception as e:
        print(f"Error processing attributes: {str(e)}")
        import traceback
        traceback.print_exc()
        raise


def process_categories_and_assignments(products_with_uuid):
    """Process category assignments - uses cached DataFrame"""
    try:
        products_with_categories = products_with_uuid.filter(col("categories").isNotNull())

        # Explode categories
        category_assignments = products_with_categories.select(
            col("product_id"),
            col("primary_category"),
            explode(col("categories")).alias("category_id")
        )

        # Create records with is_primary flag and timestamps
        category_records = category_assignments.select(
            col("product_id"),
            col("category_id"),
            when(col("category_id") == col("primary_category"), True).otherwise(False).alias("is_primary"),
            current_timestamp().alias("created_date")
        ).filter(col("category_id").isNotNull())

        write_to_iceberg(category_records, "product_category")
        print("Processed categories successfully")

    except Exception as e:
        print(f"Error processing categories: {str(e)}")
        raise


def process_media_assets(products_with_uuid):
    """Process media assets - uses cached DataFrame"""
    try:
        products_with_media = products_with_uuid.filter(col("media_assets").isNotNull())

        # Explode media assets
        media_exploded = products_with_media.select(
            col("product_id"),
            explode(col("media_assets")).alias("asset")
        )

        # Create media records with all required fields
        media_records = media_exploded.select(
            concat(lit("asset-"), col("product_id"), lit("-"), monotonically_increasing_id().cast("string")).alias("asset_id"),
            col("product_id"),
            col("asset.file_name").alias("file_name"),
            concat(lit("https://cdn.example.com/"), col("asset.file_name")).alias("url"),
            coalesce(col("asset.type"), lit("Image")).alias("type"),
            coalesce(col("asset.usage_code"), lit("HERO")).alias("usage_code"),
            coalesce(col("asset.alt_text"), lit("")).alias("alt_text"),
            lit(0).alias("display_order"),
            current_timestamp().alias("created_date")
        ).filter(col("file_name").isNotNull())

        write_to_iceberg(media_records, "media_asset")
        print("Processed media assets successfully")

    except Exception as e:
        print(f"Error processing media: {str(e)}")
        raise


# Main execution
if __name__ == "__main__":
    print("Starting Production-Grade Product ETL Job")
    print(f"Database: {args['glue_database']}")

    process_product_data()

    print("ETL Job completed successfully")
    job.commit()
