# PIM on AWS — Solution Blueprint

**Version:** 1.0
**Last Updated:** March 2026

---

## 1. Purpose & Intent

This project is a **reference architecture and working blueprint** for small-to-medium retail businesses to build and host their own Product Information Management (PIM) system on AWS using serverless technologies.

It is **not** a SaaS product. It is a deployable, extensible codebase that demonstrates how to:

- Store and manage product data at scale using a serverless data lake
- Process incoming product feeds through automated ETL pipelines with data quality validation
- Serve product data through secure REST APIs with query cost optimisation
- Provide a web UI for non-technical staff (data stewards, merchandisers) to manage products
- Adapt to any retail vertical (books, electronics, fashion) through YAML configuration — no code changes required

The goal is to give retailers a production-grade starting point that follows AWS best practices, rather than building from scratch or paying for expensive commercial PIM platforms.

---

## 2. Who Is This For?

| Audience | What They Get |
|----------|---------------|
| **Retail businesses (10K–1M products)** | A deployable PIM system at a fraction of commercial PIM cost |
| **Solutions architects** | A reference architecture for serverless data lake + EAV pattern on AWS |
| **Developers** | Working CDK infrastructure, Lambda APIs, Glue ETL jobs, and React frontend |
| **Data engineers** | Iceberg table design, partitioning strategies, and Athena query optimisation patterns |

---

## 3. Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                         Frontend (React + Amplify)                   │
│   Dashboard │ Products │ Work Queues │ DQ Dashboard │ Product Form  │
└──────────────────────────────┬──────────────────────────────────────┘
                               │ JWT (Cognito)
                               ▼
┌──────────────────────────────────────────────────────────────────────┐
│                    API Gateway + Lambda (Single Function)             │
│   Products CRUD │ Search │ Queues │ Analytics │ DQ │ CSV Export      │
└──────────────────────────────┬───────────────────────────────────────┘
                               │
              ┌────────────────┼────────────────┐
              ▼                ▼                ▼
     ┌──────────────┐  ┌────────────┐  ┌──────────────┐
     │ Amazon Athena │  │ DynamoDB   │  │ S3 Data Lake │
     │ (Query)       │  │ (Cache)    │  │ (Storage)    │
     └──────┬───────┘  └────────────┘  └──────┬───────┘
            │                                  │
            └──────────┬───────────────────────┘
                       ▼
              ┌──────────────────┐
              │  Apache Iceberg  │
              │  Tables (Glue    │
              │  Data Catalog)   │
              └──────────────────┘
                       ▲
              ┌────────┴────────┐
              ▼                 ▼
     ┌──────────────┐  ┌──────────────┐
     │ Glue ETL Job │  │ Glue DQ Job  │
     └──────┬───────┘  └──────┬───────┘
            │                  │
            └────────┬─────────┘
                     ▼
           ┌───────────────────┐
           │  Step Functions    │
           │  (Orchestration)   │
           └───────────────────┘
                     ▲
                     │
           ┌───────────────────┐
           │  S3 Raw Zone      │◄── Kinesis Firehose (real-time)
           │  (JSON feeds)     │◄── Manual upload / CSV
           └───────────────────┘
```

### Core AWS Services Used

| Service | Role | Why This Choice |
|---------|------|-----------------|
| **S3 + Apache Iceberg** | Data lake storage | ACID transactions, schema evolution, time travel, partition pruning — all serverless |
| **AWS Glue** | ETL + Data Quality | Serverless Spark, job bookmarks for incremental processing, no cluster management |
| **Step Functions** | Workflow orchestration | Visual workflow, error handling, retry logic for ETL → DQ pipeline |
| **Amazon Athena** | SQL query engine | Pay-per-query, no infrastructure, direct query on Iceberg tables |
| **API Gateway + Lambda** | REST API | Single Lambda function handles all endpoints, pay-per-request |
| **Amazon Cognito** | Authentication | User pools, groups (Admin/Manager/Editor/Viewer), JWT tokens |
| **DynamoDB** | Query result cache | TTL-based cache for Athena execution IDs, sub-dollar monthly cost |
| **Kinesis Firehose** | Real-time ingestion | Buffer and deliver product feeds to S3 raw zone |
| **CloudFront** | CDN for media assets | Global delivery of product images |
| **Amplify** | Frontend hosting | CI/CD for React app, custom domain support |

---

## 4. Data Model

### Design Philosophy: Entity-Attribute-Value (EAV)

The system uses a **hybrid fixed-schema + EAV** approach:

- **Fixed columns** on the `product` table for fields used in 80%+ of queries (SKU, name, price, status)
- **EAV pattern** via `product_attribute_value` for industry-specific fields (author, ISBN, warranty period, fabric type)

This means a bookstore and an electronics retailer use the **same codebase and infrastructure** — only the YAML configuration file changes.

### Tables

| Table | Purpose | Partition Strategy |
|-------|---------|-------------------|
| `product` | Core product data | `(status, dq_status)` — optimised for work queue queries |
| `product_attribute_value` | Flexible attributes (EAV) | `(attribute_id)` |
| `product_category` | Product ↔ category mapping | `(category_id)` |
| `category` | Hierarchical categories with materialised path | None (small table) |
| `brand` | Brand/manufacturer data | None (small table) |
| `attribute_definition` | Attribute metadata (code, type, required) | None (small table) |
| `media_asset` | Product images and files | `bucket(16, product_id)` |
| `dq_failed_records` | One row per validation error per product | `(correction_status)` |
| `dq_run_summary` | DQ job run metrics | None (small table) |

### Configuration-Driven Customisation

```yaml
# source/config/bookstore-production-config.yaml
categories:
  - category_id: 'cat_fiction'
    name: 'Fiction'
    parent_category_id: 'cat_book_root'
    level: 2

attributes:
  - attribute_id: 1
    code: 'author'
    name: 'Author'
    data_type: 'string'
    is_required: true
    is_searchable: true
```

To adapt for a different industry:
1. Create a new YAML file (e.g., `PIM-Customisation-Electronics.yml`)
2. Define categories and attributes
3. Run table recreation and data population scripts
4. The entire system adapts — ETL, API, UI — no code changes

---

## 5. What's Implemented

### API Endpoints (Single Lambda)

**Product Management:**
- `GET /api/v1/products` — List with filters (status, category, brand, search, sort, pagination)
- `GET /api/v1/products/{id}` — Full product detail with attributes, categories, media
- `POST /api/v1/products` — Create product
- `PUT /api/v1/products/{id}` — Update product (MERGE on Iceberg)
- `DELETE /api/v1/products/{id}` — Soft delete

**Search:**
- `GET /api/v1/products/search?q=` — Quick search (SKU, name, description)
- `POST /api/v1/products/search/advanced` — Advanced search with attribute filters, category, brand

**Work Queues (for data stewards):**
- `GET /api/v1/queues/dq-failed` — Products that failed data quality (with failure reasons)
- `GET /api/v1/queues/drafts` — Draft products awaiting review
- `GET /api/v1/queues/incomplete` — Active products missing required fields
- `GET /api/v1/queues/low-stock` — Low inventory alerts
- `GET /api/v1/queues/recent` — Recently modified products

**Data Quality:**
- `GET /api/v1/stats` — Dashboard metrics (total, active, draft, failed counts)
- `GET /api/v1/data-quality/dashboard` — DQ success rate and metrics
- `GET /api/v1/data-quality/export-failed` — Export failed records as CSV (presigned S3 URL)
- `POST /api/v1/data-quality/upload-corrections` — Upload corrected CSV
- `POST /api/v1/data-quality/reprocess` — Trigger DQ revalidation

**Analytics:**
- `GET /api/v1/analytics/inventory-report` — Stock levels by brand
- `GET /api/v1/analytics/price-analysis` — Pricing distribution
- `GET /api/v1/analytics/books-by-genre` — Category distribution
- `GET /api/v1/analytics/top-authors` — Top contributors

**Categories:**
- `GET /api/v1/categories` — Hierarchical category tree with level/parent filtering

### ETL Pipeline

- **Glue ETL Job**: Reads JSON from S3 raw zone → extracts products, attributes, categories, media → writes to Iceberg tables using MERGE on SKU (idempotent, no duplicates)
- **Glue DQ Job**: Picks up `dq_status='pending'` products → validates (SKU, price, name, currency) → marks as `passed`/`failed` → writes error details to `dq_failed_records` (one row per error with `failure_reason` and `failed_field`)
- **Step Functions**: Orchestrates ETL → DQ as a workflow with error handling

### Data Quality Correction Workflow

```
ETL loads products (status=draft, dq_status=pending)
    ↓
DQ job validates → passed → status=active
                 → failed → status=draft, errors written to dq_failed_records
    ↓
Data steward sees failures in UI (Work Queues > DQ Failed)
    ↓
Option A: Edit individual record in UI → saves → dq_status=pending
Option B: Export CSV → fix in Excel → upload corrections → trigger revalidation
    ↓
DQ job re-runs → validates corrected records
```

### Query Optimisation

- **Athena Result Caching**: Lambda checks DynamoDB for cached Athena execution IDs. Cache hit = 500ms (reuse S3 result), cache miss = 3–5s (new Athena query). TTL = 5 minutes. Write operations invalidate cache. Estimated saving: ~$600/month for 100 staff.
- **Partition Pruning**: Product table partitioned by `(status, dq_status)` — queue queries scan only relevant partitions (e.g., DQ failed queue scans only `status=draft/dq_status=failed`)
- **Pagination**: ROW_NUMBER windowing for efficient offset-based pagination without scanning full table
- **MAP_AGG**: Attributes returned as a map for easy frontend consumption, avoiding N+1 queries

### Frontend (React)

- **Dashboard**: Product counts, DQ metrics, queue summaries
- **Products Page**: Filterable, sortable product list with status chips
- **Product Form**: Edit product details, attributes, categories
- **Work Queues**: DQ Failed, Drafts, Incomplete, Low Stock, Recent — each with relevant columns
- **DQ Failed Queue**: Shows failure reasons, failed fields as chips, with Export CSV / Upload Corrections / Revalidate buttons
- **Data Quality Dashboard**: High-level DQ metrics with click-through to failed records
- **Sample Data Loader**: Upload sample/custom JSON data for testing
- **Authentication**: Cognito login with Amplify integration

### Infrastructure as Code (CDK)

Single CDK stack (`PimCoreStack`) deploys everything:
- S3 buckets (data lake, assets, athena results, quarantine)
- Glue database, ETL job, DQ job, IAM roles
- Step Functions state machine
- Lambda function with API Gateway (Cognito authoriser)
- Cognito user pool with groups
- DynamoDB cache table with TTL
- Kinesis Firehose delivery stream
- CloudFront distribution
- Amplify app

---

## 6. Design Decisions & Trade-offs

| Decision | Rationale | Trade-off |
|----------|-----------|-----------|
| **Iceberg over plain Parquet** | ACID transactions, schema evolution, UPDATE/DELETE support | Slightly more complex setup |
| **EAV over wide tables** | Supports any industry without schema changes | JOINs required for attribute queries |
| **Single Lambda** | Simpler deployment, shared connection pooling, lower cold starts | Larger deployment package |
| **Athena over DynamoDB for reads** | Handles complex queries (JOINs, aggregations, search) on millions of rows | Higher latency (500ms–5s) — acceptable for internal staff tool |
| **DynamoDB for cache only** | Cheap, fast TTL-based cache for Athena execution IDs | Not used for primary data storage |
| **Partition by (status, dq_status)** | Optimises work queue queries (90% of daily usage) | Full table scan for cross-status queries |
| **MERGE on SKU in ETL** | Idempotent — re-running ETL on same data doesn't create duplicates | Slightly slower than blind append |
| **YAML config over database config** | Simple, version-controlled, no UI needed for industry setup | Requires redeploy for config changes |

---

## 7. Known Gaps & Current Limitations

### Functional Gaps

| Gap | Impact | Effort |
|-----|--------|--------|
| **No DQ rules configuration UI** | Data stewards can't add/modify validation rules without code changes | Medium (2–3 weeks) |
| **Category dropdown not wired in Product Form** | Users must type category IDs manually | Low (1–2 days) |
| **No audit trail** | No history of who changed what and when | Medium (1–2 weeks) |
| **No bulk status updates** | Can't mark 10 products as discontinued in one action | Low (2–3 days) |
| **No product relationships** | Cross-sell/upsell table exists but not used in API or UI | Low (1 week) |
| **No media upload to S3** | Media asset records exist but no actual file upload flow | Medium (1 week) |
| **No user management UI** | Cognito users/groups managed via CLI only | Medium (1–2 weeks) |
| **Search is LIKE-based** | No fuzzy matching, faceted search, or relevance ranking | High (OpenSearch extension) |

### Operational Gaps

| Gap | Impact | Effort |
|-----|--------|--------|
| **No CloudWatch dashboards** | No visual monitoring of system health | Low (1–2 days) |
| **No alarms** | No alerts for DQ failures, ETL errors, or API errors | Low (1–2 days) |
| **No automated tests** | No unit, integration, or E2E tests | Medium (1–2 weeks) |
| **No CI/CD pipeline** | Manual `cdk deploy` — no automated build/test/deploy | Medium (1 week) |
| **No backup/restore procedure** | Relies on Iceberg time travel and S3 versioning but no documented procedure | Low (1–2 days) |

### Data & Performance Gaps

| Gap | Impact | Effort |
|-----|--------|--------|
| **No partition compaction job** | Iceberg small files accumulate over time, degrading query performance | Low (1 day) |
| **No completeness score calculation** | `completeness_score` column exists but never computed | Low (1 day) |
| **Co-location strategy not implemented** | Designed (bucket partitioning on product_id) but not deployed — would improve JOIN performance 10x | Medium (1 week) |
| **Performance benchmarks not recorded** | Target latencies defined but no actual measurements | Low (1–2 days) |

---

## 8. Future Enhancements

### Phase 1: Operational Maturity
- **Configurable DQ Rules Dashboard** — Store rules in DynamoDB, DQ job reads at runtime, data stewards self-service via UI
- **CloudWatch dashboards and alarms** — System health visibility
- **CI/CD pipeline** — Automated testing and deployment
- **Partition compaction** — Scheduled Glue job to optimise Iceberg file sizes

### Phase 2: Feature Completeness
- **Multi-language support** — Product names/descriptions in multiple languages (attribute-level localisation)
- **Multi-currency support** — Price lists per currency/region
- **Product relationships** — Cross-sell, upsell, accessories, bundles
- **Media upload workflow** — Direct S3 upload with CloudFront delivery
- **Audit trail** — Change history with user attribution
- **Bulk operations** — Multi-select status changes, bulk attribute updates

### Phase 3: Advanced Capabilities
- **OpenSearch integration** — Fuzzy search, faceted filtering, relevance ranking
- **AI-powered data entry** — Upload supplier PDFs/spreadsheets, AI extracts product data (Amazon Bedrock)
- **AI data quality suggestions** — Intelligent correction recommendations for failed records
- **QuickSight dashboards** — Business intelligence and reporting
- **EMR Serverless** — Complex analytics and ML on product data
- **Multi-tenant support** — Shared infrastructure for multiple retailers

---

## 9. Cost Profile

### Estimated Monthly Cost (10K products, 50 staff users)

| Service | Estimated Cost | Notes |
|---------|---------------|-------|
| S3 | $2–5 | Storage + requests |
| Athena | $5–15 | With caching (80% hit rate) |
| Glue | $5–10 | 2 jobs, daily runs |
| Lambda | $1–3 | API requests |
| API Gateway | $3–5 | REST API calls |
| DynamoDB | $1 | Cache table, on-demand |
| Cognito | $0 | Free tier (50K MAU) |
| Step Functions | $1 | State transitions |
| CloudFront | $1–5 | Asset delivery |
| **Total** | **~$20–45/month** | |

Compare this to commercial PIM platforms: $500–$5,000+/month.

### Cost at Scale (1M products, 200 staff)

| Service | Estimated Cost |
|---------|---------------|
| S3 | $20–50 |
| Athena (with caching) | $50–150 |
| Glue | $30–80 |
| Lambda + API Gateway | $20–40 |
| Other | $10–20 |
| **Total** | **~$130–340/month** |

---

## 10. Getting Started

### Prerequisites
- Python 3.9+, Node.js 16+
- AWS CLI configured
- AWS CDK CLI (`npm install -g aws-cdk`)

### Deploy

```bash
export AWS_PROFILE=your-profile
export AWS_REGION=ap-southeast-2

pip install -r requirements.txt
cdk bootstrap
./deployment/deploy.sh
```

The deploy script handles: CDK infrastructure → Iceberg table creation → reference data population → sample data upload.

### Customise for Your Industry

```bash
# 1. Copy and edit the config
cp source/config/bookstore-production-config.yaml source/config/your-industry-config.yaml

# 2. Define your categories and attributes in the YAML

# 3. Recreate tables with your config
python3 source/scripts/manage_iceberg_tables.py \
  --action recreate-all \
  --bucket "$BUCKET" --database "$GLUE_DATABASE" --region us-east-1

# 4. Populate reference data
python3 source/scripts/populate_base_tables.py \
  --config source/config/your-industry-config.yaml \
  --stack-name pim-on-aws --region us-east-1

# 5. Upload your product data (JSON) to S3 and trigger ETL
```

---

## 11. Repository Structure

```
├── cdk.json                            # CDK configuration
├── setup.py                            # Python package setup
├── pyproject.toml                      # Python tooling config
├── requirements.txt                    # Python dependencies
├── amplify.yml                         # Amplify build spec
├── deployment/
│   └── deploy.sh                       # One-click deployment
├── assets/
│   └── docs/                           # Design and architecture documents
└── source/
    ├── app.py                          # CDK entry point
    ├── pim_system/
    │   ├── config/deployment_config.py  # Environment configuration
    │   └── infrastructure/core_stack.py # All AWS infrastructure (single stack)
    ├── lambda_functions/
    │   ├── products_api/app.py         # All API endpoints (single Lambda)
    │   └── etl_trigger/app.py          # Step Functions trigger Lambda
    ├── glue_jobs/
    │   ├── sample_product_etl_job.py   # ETL: JSON → Iceberg (MERGE on SKU)
    │   ├── managed_data_quality_job.py # Tier 1 DQ: DQDL managed rules
    │   └── custom_data_quality_job.py  # Tier 2 DQ: cross-table/field rules
    ├── frontend/src/                   # React UI
    ├── scripts/
    │   ├── manage_iceberg_tables.py    # Create/drop Iceberg tables
    │   └── populate_base_tables.py     # Load categories, attributes, brands
    ├── config/
    │   └── bookstore-production-config.yaml  # Industry config (bookstore)
    ├── mock-data/
    │   └── test_data1.json             # Sample product data
    └── tests/
        └── test_products_api.py        # API integration tests
```

---

## 12. Principles

1. **Lean Core** — Only essential PIM services. No bloat. Extensions deployed separately.
2. **Configuration over Code** — Change industry by editing YAML, not source code.
3. **Serverless First** — No servers to manage. Pay only for what you use.
4. **Data Lake Native** — Iceberg tables with ACID, schema evolution, and partition pruning.
5. **Cost Conscious** — Every design decision considers cost at scale. Caching, partitioning, and efficient queries.
6. **Extensible** — Clear integration points for OpenSearch, AI, QuickSight, and multi-tenant.
