# PIM System — As-Built Technical Reference

> Single source of truth for the current implementation. Supersedes all other docs in this folder.
> Last updated: 2026-04-07

---

## 1. Architecture Overview

```
┌─────────────┐     ┌──────────────┐     ┌─────────────────────────────────────┐
│  React UI   │────▶│ API Gateway  │────▶│  Lambda (products_api/app.py)       │
│  (Amplify)  │     │  + Cognito   │     │  - CRUD, DQ dashboard, corrections  │
└─────────────┘     └──────────────┘     │  - Athena query + result caching    │
                                         └──────────┬──────────────────────────┘
                                                    │
                    ┌───────────────────────────────┼───────────────────────┐
                    ▼                               ▼                       ▼
            ┌──────────────┐              ┌──────────────┐        ┌──────────────┐
            │   Athena     │              │  DynamoDB    │        │  S3 Data     │
            │  (Iceberg)   │              │  (Cache)     │        │  Lake        │
            └──────────────┘              └──────────────┘        └──────────────┘

┌──────────────────────────────────────────────────────────────────────────────┐
│  Step Functions Orchestration                                                │
│                                                                              │
│  Input ──▶ [SkipETL Choice]                                                  │
│              ├── skip_etl present ──▶ Tier 1 DQ ──▶ Tier 2 DQ ──▶ Success   │
│              └── otherwise ──▶ ETL ──▶ Tier 1 DQ ──▶ Tier 2 DQ ──▶ Success  │
└──────────────────────────────────────────────────────────────────────────────┘
```

### Key Files

| Component | File | Purpose |
|-----------|------|---------|
| CDK Entry | `source/app.py` | CDK application entry point |
| CDK Stack | `source/pim_system/infrastructure/core_stack.py` | All infrastructure |
| Deployment Config | `source/pim_system/config/deployment_config.py` | Environment configuration |
| API Lambda | `source/lambda_functions/products_api/app.py` | REST API (~2500+ lines) |
| ETL Trigger Lambda | `source/lambda_functions/etl_trigger/app.py` | Step Functions trigger |
| ETL Job | `source/glue_jobs/sample_product_etl_job.py` | Raw JSON → Iceberg tables |
| Tier 1 DQ | `source/glue_jobs/managed_data_quality_job.py` | DQDL managed rules |
| Tier 2 DQ | `source/glue_jobs/custom_data_quality_job.py` | Cross-table/field rules |
| Table Mgmt | `source/scripts/manage_iceberg_tables.py` | Iceberg DDL |
| Ref Data | `source/scripts/populate_base_tables.py` | Categories, attributes, brands |
| Industry Config | `source/config/bookstore-production-config.yaml` | Bookstore vertical configuration |
| Mock Data | `source/mock-data/test_data1.json` | Sample product data |
| Frontend | `source/frontend/src/` | React SPA |
| API Tests | `source/tests/test_products_api.py` | Integration tests |
| Deploy Script | `deployment/deploy.sh` | One-click deployment |

---

## 2. Data Model — Iceberg Tables

### 2.1 Why Self-Managed Iceberg on S3 (not S3 Tables)

S3 Tables (GA 2024) provides fully managed Iceberg with automatic compaction and snapshot cleanup. We evaluated it and chose self-managed Iceberg for this PIM workload. The rationale:

| Factor | Self-Managed Iceberg | S3 Tables |
|--------|---------------------|-----------|
| **Athena result reuse** | ✅ Supported — core cost optimisation | ❌ Not supported — breaks caching layer |
| **Write pattern** | Batch ETL + bulk corrections (infrequent, large writes) | Designed for high-frequency writes with small-file problem |
| **Compaction need** | Low — few large writes, not many small files | Auto-compaction is the headline feature |
| **DDL flexibility** | Full ALTER TABLE, partition evolution | No ALTER TABLE RENAME, limited DDL |
| **Catalog model** | Standard Glue Data Catalog | Requires Lake Formation integration + child catalog |
| **Glue ETL compatibility** | Native `glue_catalog.*` paths | Different catalog config (`account:s3tablescatalog/bucket`) |
| **Partition control** | Full control over partition spec | Same Iceberg partitioning, but less DDL flexibility |
| **Operational overhead** | Periodic `OPTIMIZE` (one scheduled query) | Zero maintenance |

**The deciding factor is Athena result reuse.** Our three-layer caching strategy (client → DynamoDB execution cache → Athena native `ResultReuseByAgeConfiguration`) is the primary cost control for a read-heavy PIM workload with 100+ staff users. S3 Tables does not support Athena result reuse, which would force every identical query to re-scan data.

**Write pattern doesn't justify S3 Tables.** A medium-scale retail PIM (10K–100K products) has:
- 1–2 ETL loads per day (batch, large writes → few large Parquet files)
- Occasional bulk corrections (tens to hundreds of rows via MERGE)
- Rare individual row updates

This pattern produces well-sized files naturally. The small-file problem that S3 Tables auto-compaction solves doesn't materialise here. A single scheduled `OPTIMIZE table REWRITE DATA USING BIN_PACK` query handles any residual fragmentation.

**Migration path exists.** Both use Parquet/Iceberg format. If write patterns change (e.g., real-time ingestion via Firehose creating many small files), migrating to S3 Tables is a catalog configuration change, not a data rewrite.

### 2.2 Table Schemas & Partitioning

Designed for medium-scale retail: 10K–100K products, ~1M attribute rows, 100+ staff users.

**Transactional tables** — partitioned by the most common WHERE clause filter:

| Table | Partition | Why This Key | Query Pattern |
|-------|-----------|-------------|---------------|
| `product` | `(status, dq_status)` | 90% of queries filter by status (`active`, `draft`) or dq_status (`pending`, `passed`, `failed`). Dashboard, product list, DQ workflow all use these filters. At 100K products with 3 statuses × 3 dq_statuses = ~9 partitions, each ~11K rows — good partition size. | `WHERE status = 'active'`, `WHERE dq_status = 'pending'` |
| `product_attribute_value` | `(attribute_id)` | With ~15 attributes × 100K products = 1.5M rows. Partitioning by `attribute_id` means "get all authors" scans 1/15th of data. Attribute-specific queries (search, filter, DQ checks) are the dominant pattern. | `WHERE attribute_id = 3` (author) |
| `product_category` | `(category_id)` | Category browsing ("show all Fiction books") is a primary navigation pattern. With ~30 categories, each partition holds ~3K product mappings. | `WHERE category_id = 'cat_fiction'` |
| `media_asset` | `bucket(16, product_id)` | Always accessed per-product ("get images for product X"). Bucket hash on `product_id` co-locates all assets for a product. 16 buckets = ~6K products per bucket at 100K scale. | `WHERE product_id = 'uuid-123'` |
| `dq_failed_records` | `(correction_status)` | Correction workflow queries: "show pending corrections" vs "show corrected". 3 values: `pending`, `corrected`, `ignored`. | `WHERE correction_status = 'pending'` |

**Reference tables** — no partitioning (small, broadcast-joined):

| Table | Rows (typical) | Why No Partition |
|-------|----------------|-----------------|
| `category` | 30–200 | Broadcast in JOINs, full scan is <1KB |
| `brand` | 10–50 | Broadcast in JOINs |
| `attribute_definition` | 10–30 | Broadcast in JOINs |
| `dq_run_summary` | 1 row per DQ run (~365/year) | Tiny table, `ORDER BY timestamp DESC LIMIT 1` |

**Why not bucket(16, product_id) on all tables (co-location)?**

The co-location strategy (bucket all tables by `product_id`) eliminates shuffle in JOINs — ideal for single-product lookups. However, our dominant query pattern is filtered listings (`WHERE status = 'active' LIMIT 50`), not single-product fetches. Partitioning by `status` prunes 60–80% of data on every listing query. The single-product `GET /products/{id}` query is fast regardless because it filters on a specific `product_id` value and Iceberg's file-level min/max statistics handle pruning.

If the workload shifts to heavy multi-table JOINs (e.g., analytics dashboards joining product + attributes + categories), partition evolution to `bucket(16, product_id)` can be done without rewriting existing data — Iceberg reads both old and new partition layouts transparently.

### 2.2 Key Column Types

- `base_price`: `decimal(10,2)` — changed from string to support numeric DQ rules
- `stock_quantity`: `int`
- `attribute_id`: `int` in `attribute_definition` and `product_attribute_value`
- `attribute_definition.code`: `string` — human-readable key for API/ETL mapping
- `completeness_score`: `int` (0-4) — for incomplete product queue
- `low_stock`: `boolean` — flag for low stock queue

### 2.3 Iceberg Table Properties

```sql
TBLPROPERTIES (
    'table_type'='ICEBERG',
    'format'='parquet',
    'write_compression'='snappy',
    'optimize_rewrite_delete_file_threshold'='10'
)
```

---

## 3. ETL Pipeline

### 3.1 Flow

```
S3 raw/products/*.json
  → GlueDynamicFrame (JSON multiline)
  → Spark DataFrame
  → process_brands()          — MERGE into brand table
  → process_products()        — MERGE on SKU (upsert), assigns UUID
  → process_attributes()      — stack() unpivot, broadcast join to attr_definition
  → process_categories()      — explode + join
  → process_media_assets()    — explode + write
```

### 3.2 Best Practices Applied

| Practice | Status | Implementation |
|----------|--------|----------------|
| No `.collect()` on large data | ✅ | All processing distributed |
| Broadcast joins for small tables | ✅ | `broadcast(existing_products)`, `broadcast(attr_def_df)` |
| Single-pass attribute unpivot | ✅ | `stack()` instead of loop+union |
| `.rdd.isEmpty()` instead of `.count()` | ✅ | Early exit check |
| Cache + reuse DataFrames | ✅ | `products_with_uuid.cache()` |
| Adaptive Query Execution | ✅ | `spark.sql.adaptive.enabled=true` |
| MERGE on SKU (idempotent) | ✅ | `write_to_iceberg(df, "product", merge_key="sku")` |
| `base_price` struct handling | ✅ | `coalesce(col("product.base_price.double"), col("product.base_price.int").cast("double")).cast("decimal(10,2)")` |

### 3.3 Glue Job Config

- Glue Version: 5.0
- Workers: 2 × G.1X
- Datalake formats: iceberg
- Timeout: 15 minutes

---

## 4. Data Quality — Two-Tier Architecture

### 4.1 Tier 1: Managed DQ (DQDL)

**File**: `managed_data_quality_job.py`
**Method**: `EvaluateDataQuality().process_rows()` + `SelectFromCollection`

```
DQDL Rules:
  Completeness "sku" = 1.0
  Completeness "base_name" = 1.0
  Completeness "currency_code" = 1.0
  Completeness "base_price" = 1.0
  ColumnLength "sku" >= 3
  ColumnValues "base_price" > 0
  ColumnValues "currency_code" in ["USD","AUD","GBP","EUR","NZD"]
  ColumnLength "base_name" between 2 and 500
  Uniqueness "sku" = 1.0
```

**Key decisions**:
- `process_rows()` not `.apply()` — row-level tagging (Passed/Failed per row)
- CloudWatch metrics publishing: disabled (cost)
- Results publishing: disabled
- Casts `base_price` to double and `stock_quantity` to int before evaluation
- Writes passed records to `s3://bucket/dq-staging/{JOB_RUN_ID}/tier1-passed/` as parquet
- Writes failures to `dq_failed_records` Iceberg table
- Updates product `dq_status` to `passed`/`failed` and `status` to `active`/`draft`

### 4.2 Tier 2: Custom DQ (PySpark)

**File**: `custom_data_quality_job.py`

**Cross-table validations** (`validate_cross_table`):
1. Every product must have ≥1 category assignment
2. Required attributes (from `attribute_definition.is_required`) must be populated
3. `brand_id` must exist in brand table (if set)

**Cross-field validations** (`validate_cross_field`):
1. Active products must have `base_price > 0` AND `stock_quantity > 0`

**Key fixes applied**:
- `builtins.round()` instead of PySpark's `round()` (namespace collision from `import *`)
- `lit(0)` in comparisons instead of raw Python `0`
- `JOB_RUN_ID` parsed from `sys.argv` (not available via `getResolvedOptions`)

### 4.3 JOB_RUN_ID Coordination

Both DQ jobs receive `--JOB_RUN_ID` from Step Functions (`$$.Execution.Name`).
Parsed via `sys.argv` fallback since `getResolvedOptions` doesn't include it:

```python
JOB_RUN_ID = args.get('JOB_RUN_ID', None)
if not JOB_RUN_ID:
    for i, arg in enumerate(sys.argv):
        if arg == '--JOB_RUN_ID' and i + 1 < len(sys.argv):
            JOB_RUN_ID = sys.argv[i + 1]
            break
```

This ensures Tier 1 staging path matches what Tier 2 reads.

### 4.4 Revalidation (Skip ETL)

- Step Functions Choice state: `is_present("$.skip_etl")`
- Lambda `trigger_reprocessing()` passes `{"skip_etl": true}`
- Skips ETL, runs Tier 1 → Tier 2 only
- Returns `console_url` for Step Functions execution link

---

## 5. Athena Query Strategy & Caching

### 5.1 Three-Layer Caching

| Layer | Where | TTL | Scope | Status |
|-------|-------|-----|-------|--------|
| Client-side | `source/frontend/src/services/api.js` | 5 min | Per browser tab | ✅ Implemented |
| Lambda/DynamoDB | `execute_athena_query()` | 5 min (300s) | Shared across all users | ✅ Implemented |
| Athena native reuse | `ResultReuseByAgeConfiguration` | 5 min | Athena-level | ✅ Implemented |

### 5.2 Lambda Cache Flow

```
Query → MD5 hash → Check DynamoDB (cache_key = "query_{hash}")
  ├── HIT + not expired → Reuse Athena execution_id → GetQueryResults (500ms)
  └── MISS → StartQueryExecution (with ResultReuse) → Cache execution_id → Return (3-5s)
```

### 5.3 Cache Invalidation

- **Write operations** (create/update/delete product): calls `invalidate_query_cache()` which scans DynamoDB for all `query_*` keys and deletes them
- **Cache version**: `update_cache_version('products_list')` updates a version timestamp; frontend fetches this to know when to refresh
- **DynamoDB TTL**: Automatic expiry at `current_time + 300`

### 5.4 Query Patterns

**Product listing** — single query with aggregated attributes and media:
```sql
WITH product_data AS (
    SELECT p.*, b.name as brand_name,
           map_agg(ad.code, pav.value) as attributes,
           array_agg(...) as media_assets,
           ROW_NUMBER() OVER (ORDER BY ...) as row_num
    FROM product p
    LEFT JOIN brand b ON p.brand_id = b.brand_id
    LEFT JOIN product_attribute_value pav ON p.product_id = pav.product_id
    LEFT JOIN attribute_definition ad ON pav.attribute_id = ad.attribute_id
    LEFT JOIN media_asset ma ON p.product_id = ma.product_id
    WHERE p.status = 'active'   -- ← partition pruning on status
    GROUP BY ...
)
SELECT * FROM product_data WHERE row_num BETWEEN {offset+1} AND {offset+limit}
```

**DQ Dashboard** — single query, latest run:
```sql
SELECT total_records, failed_records, success_rate, timestamp
FROM dq_run_summary ORDER BY timestamp DESC LIMIT 1
```

---

## 6. Correction Workflow

### 6.1 Single Record

`PUT /api/v1/data-quality/correct-record/{product_id}` — updates product fields via Athena MERGE

### 6.2 Bulk Upload

`POST /api/v1/data-quality/upload-corrections` — base64-encoded CSV

**CSV format**:
```
product_id,sku,name,price,status,stock_quantity,brand_id,attributes,categories,validation_errors
prod_001,SKU123,My Book,19.99,active,100,brand_penguin,author:John|pages:350,cat_fiction,
```

- `attributes`: `code:value|code:value` format, resolved via JOIN to `attribute_definition.code`
- `categories`: `cat_id|cat_id` format
- `brand_id`: direct value from brand table
- `validation_errors`: ignored on upload (reference only)

**Processing**: 3 batched MERGE queries (products, attributes, categories) — single Athena query each.

### 6.3 Export

`GET /api/v1/data-quality/export-failed` — generates CSV with all failed records including brand_id, attributes, categories

---

## 7. Reference Data Population

**Script**: `source/scripts/populate_base_tables.py`
**Config**: `source/config/bookstore-production-config.yaml`

Populates via Athena INSERT:
1. Categories (with materialized paths)
2. Brands (10 book publishers)
3. Attribute definitions (with codes, types, required flags)

```bash
python3 source/scripts/populate_base_tables.py \
  --config source/config/bookstore-production-config.yaml \
  --stack-name pim-on-aws --region us-east-1
```

---

## 8. Implementation vs Design — Gap Analysis

### 8.1 Partitioning Gaps

| Design Doc Says | Actual Implementation | Gap | Impact |
|-----------------|----------------------|-----|--------|
| `product`: `PARTITIONED BY (status, month(modified_date))` | `PARTITIONED BY (status, dq_status)` | No time-based partition | Low — table is small now; revisit at >100K products. `dq_status` is more useful for current queue-based queries |
| `product_attribute_value`: `bucket(16, product_id)` (co-location doc) | `PARTITIONED BY (attribute_id)` | No co-location with product | **Medium** — JOINs between product and attributes require shuffle. At scale, bucket partitioning on `product_id` across all tables would eliminate shuffle |
| `product_category`: `bucket(16, product_id)` | `PARTITIONED BY (category_id)` | No co-location | **Medium** — same as above |
| `media_asset`: `bucket(16, product_id)` | `PARTITIONED BY (bucket(16, product_id))` | ✅ Matches | None |
| `dq_run_summary`: `days(timestamp)` | No partition (empty string) | No time partition | **Low** — table has few rows (one per DQ run) |
| `dq_failed_records`: `days(failed_at)` | `PARTITIONED BY (correction_status)` | Different partition key | Low — `correction_status` is better for the correction workflow |

**Recommendation**: The current partitioning is pragmatic for the current scale. The co-location strategy (bucket by `product_id`) is the right long-term move but requires recreating all tables and re-running ETL. Defer until data volume justifies it (>100K products).

### 8.2 Athena Query Gaps

| Best Practice | Status | Detail |
|---------------|--------|--------|
| Partition pruning in WHERE clauses | ⚠️ Partial | `list_products` filters on `status` (partition column) ✅ but JOINs to `product_attribute_value` don't filter on `attribute_id` partition |
| Avoid `SELECT *` | ⚠️ Partial | `list_products` selects specific columns ✅ but some internal queries use broad selects |
| Limit data scanned | ✅ | Pagination with `ROW_NUMBER()` + `BETWEEN` |
| Athena result reuse | ✅ | `ResultReuseByAgeConfiguration` enabled with 5-min window |
| DynamoDB cache layer | ✅ | MD5 hash → execution_id caching |
| Cache invalidation on writes | ✅ | `invalidate_query_cache()` on all mutations |

**Key gap**: The `list_products` query JOINs `product_attribute_value` without filtering on `attribute_id`. This means Athena scans ALL attribute partitions for every product listing. At scale, this is expensive.

**Fix**: For list views, consider fetching attributes in a separate query only for the paginated product_ids, or use a subquery that limits the JOIN scope.

### 8.3 ETL Gaps

| Best Practice | Status | Detail |
|---------------|--------|--------|
| No `.collect()` on large data | ✅ | Fully distributed |
| Broadcast small tables | ✅ | `broadcast(existing_products)` |
| `stack()` for unpivot | ✅ | Single-pass attribute processing |
| MERGE for idempotency | ✅ | `write_to_iceberg(df, table, merge_key="sku")` |
| Adaptive Query Execution | ✅ | Enabled in Spark config |
| Error handling | ✅ | Try/catch with traceback |
| Quarantine bad records | ⚠️ | `quarantine_bucket` arg exists but no quarantine logic implemented |

### 8.4 DQ Gaps

| Feature | Status | Detail |
|---------|--------|--------|
| Tier 1 DQDL managed rules | ✅ | 9 rules, row-level outcomes |
| Tier 2 cross-table rules | ✅ | Category, attribute, brand checks |
| Tier 2 cross-field rules | ✅ | Active product price/stock check |
| Failed records to Iceberg | ✅ | With correction_status partition |
| Run summary to Iceberg | ✅ | Both tiers write summaries |
| Revalidation (skip ETL) | ✅ | Step Functions Choice state |
| CloudWatch metrics | ❌ Disabled | By design — cost saving |
| Dataset-level rules (RowCount, ColumnCount) | ❌ Not in ruleset | Could add as guardrails |
| Duplicate detection across runs | ⚠️ | `dq_failed_records` accumulates across runs; no cleanup of old run failures |

### 8.5 Caching Gaps

| Feature | Status | Detail |
|---------|--------|--------|
| Lambda-level query cache | ✅ | DynamoDB + Athena execution reuse |
| Client-side cache | ✅ | In-memory Map with 5-min TTL |
| Athena native result reuse | ✅ | `ResultReuseByAgeConfiguration` |
| Selective cache invalidation | ⚠️ | Currently invalidates ALL query caches on any write. Could be smarter (e.g., only invalidate product queries on product update) |
| Cache warming | ❌ | Not implemented |
| `execute_athena_query` pagination | ⚠️ | Only fetches first page of `GetQueryResults` (1000 rows max). No `NextToken` handling |

### 8.6 Other Gaps

| Area | Gap | Impact | Fix Effort |
|------|-----|--------|------------|
| SQL injection | `list_products` interpolates `search_term` directly into SQL | **High** — security risk | Low — use parameterized queries or sanitize input |
| Athena result pagination | `GetQueryResults` returns max 1000 rows; no `NextToken` loop | **Medium** — large result sets truncated silently | Low |
| `dq_failed_records` cleanup | Old run failures accumulate; no DELETE of previous run's records before new run | **Low** — dashboard uses latest run_id, but table grows | Low |
| Quarantine bucket | Arg passed to ETL but never used | **Low** — no bad record isolation | Medium |
| `long_description` | Still in product table (co-location doc says move to attributes) | **Low** — not in current schema, but design doc mentions it | N/A — not applicable to current BookStore config |
| Iceberg table maintenance | No `OPTIMIZE` / compaction scheduled | **Medium** — small files accumulate over time | Low — add periodic OPTIMIZE |
| Monitoring | No CloudWatch alarms on DQ failure rate or ETL failures | **Medium** — silent failures | Low |

---

## 9. Cost Optimization Summary

| Technique | Implemented | Estimated Savings |
|-----------|-------------|-------------------|
| Partition pruning (status) | ✅ | 60-80% on product queries |
| Athena result reuse (native) | ✅ | Avoids re-scan for identical queries |
| DynamoDB execution cache | ✅ | ~80% cache hit rate → $600/mo at scale |
| Client-side cache | ✅ | Eliminates redundant API calls |
| Pagination (LIMIT/OFFSET) | ✅ | Bounds result size |
| Snappy compression | ✅ | ~50% storage reduction |
| Glue 2 workers (minimal) | ✅ | $0.44/hr per DPU |
| DQ metrics publishing off | ✅ | Avoids CloudWatch costs |

---

## 10. Operational Runbook

### Deploy (Automated)
```bash
./deployment/deploy.sh
```

### Deploy (Manual CDK Only)
```bash
cdk deploy pim-on-aws --require-approval never
```

### Recreate Tables
```bash
python3 source/scripts/manage_iceberg_tables.py --action recreate-all \
  --bucket "$BUCKET" --database "$GLUE_DATABASE" --region us-east-1
```

### Populate Reference Data
```bash
python3 source/scripts/populate_base_tables.py \
  --config source/config/bookstore-production-config.yaml \
  --stack-name pim-on-aws --region us-east-1
```

### Load Sample Data
```bash
for f in source/mock-data/*.json; do
  aws s3 cp "$f" "s3://$BUCKET/raw/products/"
done
```

### Trigger ETL + DQ
From UI Dashboard, or via Step Functions CLI:
```bash
aws stepfunctions start-execution --state-machine-arn "$ETL_WORKFLOW_ARN"
```

### Trigger Revalidation Only (Skip ETL)
From UI Data Quality page → "Revalidate" button

### Monitor Glue Jobs
```bash
aws logs tail /aws/glue/jobs/output --follow
aws logs tail /aws/glue/jobs/error --follow
```

### Monitor API
```bash
aws logs tail /aws/lambda/pim-products-api-dev --follow
```

### Compact Iceberg Tables (Periodic Maintenance)
```sql
OPTIMIZE pim_catalog.product REWRITE DATA USING BIN_PACK;
OPTIMIZE pim_catalog.product_attribute_value REWRITE DATA USING BIN_PACK;
```

---

## 11. Frontend Hosting — Amplify Gen 1 (Manual Deployment)

### 11.1 Why Amplify Gen 1, Not Gen 2

The PIM frontend is hosted on AWS Amplify Hosting using the Gen 1 model with manual zip deployments. We evaluated Gen 2 and chose Gen 1 for the following reasons:

| Factor | Amplify Gen 1 | Amplify Gen 2 |
|--------|---------------|---------------|
| **Backend ownership** | None — CDK owns all backend resources | Amplify owns auth, data, storage via `defineBackend()` |
| **Conflict with existing CDK stack** | No conflict — Amplify is purely a hosting service | Conflicts — Gen 2 wants to manage Cognito, API, and storage that CDK already provisions |
| **Deployment model** | Manual zip upload via CLI (`create-deployment` + `start-deployment`) | Git-connected CI/CD pipeline or Amplify CLI |
| **Infrastructure coupling** | Decoupled — frontend deploys independently of CDK | Tightly coupled — `amplify/` directory defines backend + frontend together |
| **CDK construct support** | `aws_cdk.aws_amplify_alpha` — mature, well-documented | No CDK construct — Gen 2 uses its own CLI and TypeScript config |
| **Complexity** | Minimal — just a static hosting target | Requires Amplify project structure (`amplify/`, `amplifyconfiguration.json`, etc.) |
| **Fit for this project** | ✅ Perfect — we just need static hosting with env vars | ❌ Overkill — we don't need Amplify to manage our backend |

**The core reason**: Our backend (API Gateway, Cognito, Lambda, Glue, Athena, S3, DynamoDB, Step Functions) is fully managed by CDK in `core_stack.py`. Amplify Gen 2 would try to own the auth and data layers, creating a dual-management problem. Gen 1 treats Amplify purely as a hosting service — which is exactly what we need.

### 11.2 How It Works

**CDK creates the Amplify infrastructure** (`_create_amplify_app` in `core_stack.py`):
- Amplify App resource with environment variables (API URL, Cognito IDs, region, etc.)
- `main` branch with `auto_build=False` (manual deployment target)
- Outputs `AmplifyAppId` and `AmplifyAppUrl`

**deploy.sh handles code deployment** (runs in both `fresh` and `update` modes):
1. Retrieves stack outputs (API URL, Cognito IDs, Identity Pool ID, Assets Bucket)
2. Generates `source/frontend/.env` with correct `REACT_APP_*` values
3. Builds the React app (`npm ci && npm run build`)
4. Zips the `build/` directory
5. Calls `aws amplify create-deployment` to get a pre-signed upload URL
6. Uploads the zip via `curl`
7. Calls `aws amplify start-deployment` to trigger the deployment
8. Polls `aws amplify get-job` until deployment completes
9. Prints the live Amplify URL

**This separation is intentional**: CDK manages infrastructure state, `deploy.sh` manages code deployment. Frontend code changes don't require a full `cdk deploy` — just rebuild and push the zip.

### 11.3 Frontend Environment Variables

The React app (CRA) uses `REACT_APP_*` env vars baked in at build time. These are auto-generated by `deploy.sh` from CloudFormation stack outputs:

| Variable | Source (Stack Output) | Purpose |
|----------|----------------------|---------|
| `REACT_APP_API_URL` | `ApiGatewayUrl` | Backend API endpoint |
| `REACT_APP_USER_POOL_ID` | `UserPoolId` | Cognito User Pool for auth |
| `REACT_APP_USER_POOL_CLIENT_ID` | `UserPoolClientId` | Cognito App Client |
| `REACT_APP_IDENTITY_POOL_ID` | `IdentityPoolId` | Cognito Identity Pool for AWS credentials |
| `REACT_APP_REGION` | Deploy script `$REGION` | AWS region |
| `REACT_APP_ENVIRONMENT` | `development` / `production` | Environment label |
| `REACT_APP_ASSETS_BUCKET` | `AssetsBucketName` | S3 bucket for digital assets |

### 11.4 Local Development

The `source/frontend/.env` file also supports local development via `npm start`. After a deployment, the `.env` contains the correct values for the deployed stack, so local dev points at the same backend:

```bash
cd source/frontend
npm install
npm start
# Opens http://localhost:3000 pointing at the deployed API + Cognito
```
