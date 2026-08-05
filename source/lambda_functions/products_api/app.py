"""
Products API Lambda Function
Handles CRUD operations for products in the PIM system
"""
import json
import boto3
import os
import time
import hashlib
import re
import base64
from datetime import datetime
from typing import Dict, Any, Optional, List
import uuid


# Initialize AWS clients
athena_client = boto3.client('athena')
s3_client = boto3.client('s3')
stepfunctions_client = boto3.client('stepfunctions')
glue_client = boto3.client('glue')
dynamodb = boto3.resource('dynamodb')

# Environment variables
GLUE_DATABASE = os.environ.get('GLUE_DATABASE')
if GLUE_DATABASE and not re.match(r'^[a-zA-Z0-9_-]+$', GLUE_DATABASE):
    raise ValueError(f"Invalid GLUE_DATABASE name: {GLUE_DATABASE}")
ATHENA_WORKGROUP = os.environ.get('ATHENA_WORKGROUP')
ATHENA_RESULTS_BUCKET = os.environ.get('ATHENA_RESULTS_BUCKET')
DATA_LAKE_BUCKET = os.environ.get('DATA_LAKE_BUCKET')
ETL_WORKFLOW_ARN = os.environ.get('ETL_WORKFLOW_ARN')
CACHE_VERSION_TABLE = os.environ.get('CACHE_VERSION_TABLE')

# Cache version table
cache_table = dynamodb.Table(CACHE_VERSION_TABLE) if CACHE_VERSION_TABLE else None

# Query result cache settings
QUERY_CACHE_TTL = 300  # 5 minutes


# =============================================================================
# SQL Injection Prevention
# =============================================================================
# Athena supports parameterised queries via ExecutionParameters for SELECT/DML.
# For read queries we use ? placeholders + ExecutionParameters list.
# For write queries (UPDATE/MERGE/DELETE on Iceberg) ExecutionParameters is not
# supported, so we sanitise values with the helpers below.
# GLUE_DATABASE is an identifier validated at module init with regex.
#
# Helpers:
#   sanitize_sql_string() — for values in SQL string literals (write queries)
#   sanitize_sql_identifier() — for identifiers (column/table names)
#   validate_uuid() — for product_id / record_id path parameters
#   validate_int() — for numeric parameters (limit, offset, level)
#   validate_enum() — for status/sort fields against an allowlist

def sanitize_sql_string(value: str) -> str:
    """Escape a value for use inside SQL single quotes.
    Replaces single quotes with doubled quotes (standard SQL escaping)
    and strips any semicolons or SQL comment markers to prevent injection."""
    if value is None:
        return ''
    s = str(value)
    s = s.replace("'", "''")       # Standard SQL quote escaping
    s = s.replace(';', '')          # No statement terminators
    s = s.replace('--', '')         # No SQL line comments
    s = s.replace('/*', '')         # No SQL block comments
    s = s.replace('*/', '')
    return s


def sanitize_sql_identifier(value: str) -> str:
    """Sanitise a value used as a SQL identifier (column name, table name).
    Only allows alphanumeric characters and underscores."""
    if value is None:
        return ''
    return re.sub(r'[^a-zA-Z0-9_]', '', str(value))


def validate_uuid(value: str) -> str:
    """Validate that a value looks like a product_id (prod_XXXXXXXX or UUID format).
    Returns the value if valid, raises ValueError otherwise."""
    if not value:
        raise ValueError('ID is required')
    s = str(value).strip()
    # Allow prod_XXXX format, standard UUIDs, and attr-XXXX format
    if re.match(r'^(prod_[a-f0-9]{8}|[a-f0-9\-]{36}|attr-[a-f0-9\-]+)$', s):
        return s
    # Also allow simple alphanumeric + underscore + hyphen (for category_id etc.)
    if re.match(r'^[a-zA-Z0-9_\-]{1,100}$', s):
        return s
    raise ValueError(f'Invalid ID format: {s}')


def validate_int(value, default: int = 0, min_val: int = 0, max_val: int = 10000) -> int:
    """Validate and clamp an integer parameter."""
    try:
        n = int(value)
        return max(min_val, min(n, max_val))
    except (TypeError, ValueError):
        return default


def validate_enum(value: str, allowed: List[str], default: str = '') -> str:
    """Validate a value against an allowlist."""
    if value and str(value).lower() in [a.lower() for a in allowed]:
        return str(value).lower()
    return default


# =============================================================================
# RBAC — Role-Based Access Control
# =============================================================================
# Extract Cognito groups from the JWT claims passed by API Gateway.
# Two roles in the blueprint:
#   - WRITE: Editors — can create/update/delete/trigger ETL
#   - READ:  Viewers (and any authenticated user) — can only GET
# Administrators and Managers kept in the set for easy extensibility.

WRITE_GROUPS = {'Administrators', 'Managers', 'Editors'}

def get_user_role(event: Dict) -> str:
    """Extract the effective role (read/write) from Cognito JWT claims.
    API Gateway Cognito authorizer puts claims in requestContext.authorizer.claims."""
    try:
        claims = event.get('requestContext', {}).get('authorizer', {}).get('claims', {})
        groups_str = claims.get('cognito:groups', '')
        if groups_str:
            # Groups come as a comma-separated string or a single value
            groups = {g.strip() for g in groups_str.split(',')}
            if groups & WRITE_GROUPS:
                return 'write'
    except Exception as e:
        print(f"⚠️  Could not extract user groups: {e}")
    return 'read'


def get_user_info(event: Dict) -> Dict[str, str]:
    """Extract user info from JWT claims for audit logging."""
    try:
        claims = event.get('requestContext', {}).get('authorizer', {}).get('claims', {})
        return {
            'username': claims.get('cognito:username', 'unknown'),
            'email': claims.get('email', ''),
            'groups': claims.get('cognito:groups', '')
        }
    except Exception:
        return {'username': 'unknown', 'email': '', 'groups': ''}


def generate_query_hash(query: str) -> str:
    """Generate deterministic hash for query caching"""
    return hashlib.md5(query.encode('utf-8'), usedforsecurity=False).hexdigest()


def get_cached_execution_id(query_hash: str) -> Optional[str]:
    """Check if we have a recent Athena execution for this query"""
    if not cache_table:
        return None
    
    try:
        response = cache_table.get_item(Key={'cache_key': f'query_{query_hash}'})
        if 'Item' in response:
            item = response['Item']
            timestamp = int(item.get('timestamp', 0))
            execution_id = item.get('execution_id')
            
            # Check if cache is still valid
            if time.time() - timestamp < QUERY_CACHE_TTL:
                print(f"✅ Cache HIT for query hash {query_hash[:8]}... (age: {int(time.time() - timestamp)}s)")
                return execution_id
            else:
                print(f"⏰ Cache EXPIRED for query hash {query_hash[:8]}...")
        else:
            print(f"❌ Cache MISS for query hash {query_hash[:8]}...")
        return None
    except Exception as e:
        print(f"⚠️  Failed to check cache: {e}")
        return None


def cache_execution_id(query_hash: str, execution_id: str):
    """Store Athena execution ID for query result reuse"""
    if not cache_table:
        return
    
    try:
        current_time = int(time.time())
        cache_table.put_item(
            Item={
                'cache_key': f'query_{query_hash}',
                'execution_id': execution_id,
                'timestamp': current_time,
                'ttl': current_time + QUERY_CACHE_TTL  # DynamoDB TTL
            }
        )
        print(f"💾 Cached execution ID {execution_id} for query hash {query_hash[:8]}...")
    except Exception as e:
        print(f"⚠️  Failed to cache execution ID: {e}")


def update_cache_version(cache_key: str = 'products_list'):
    """Update cache version in DynamoDB to invalidate API Gateway cache"""
    if not cache_table:
        return
    
    try:
        version = int(time.time() * 1000)  # Millisecond timestamp
        cache_table.put_item(
            Item={
                'cache_key': cache_key,
                'version': version
            }
        )
        print(f"✅ Updated cache version for '{cache_key}': {version}")
    except Exception as e:
        print(f"⚠️  Failed to update cache version: {e}")


def invalidate_query_cache(pattern: str = 'query_'):
    """Invalidate all cached query results matching pattern"""
    if not cache_table:
        return
    
    try:
        # Scan for all query cache entries
        response = cache_table.scan(
            FilterExpression='begins_with(cache_key, :prefix)',
            ExpressionAttributeValues={':prefix': pattern}
        )
        
        # Delete all matching entries
        deleted_count = 0
        with cache_table.batch_writer() as batch:
            for item in response.get('Items', []):
                batch.delete_item(Key={'cache_key': item['cache_key']})
                deleted_count += 1
        
        print(f"🗑️  Invalidated {deleted_count} cached query results")
    except Exception as e:
        print(f"⚠️  Failed to invalidate query cache: {e}")


def get_product_stats() -> Dict[str, Any]:
    """Get lightweight product statistics for dashboard - no joins, just counts"""
    
    query = f"""
    SELECT 
        COUNT(*) as total_products,
        COUNT(CASE WHEN status = 'active' THEN 1 END) as active_products,
        COUNT(CASE WHEN status = 'draft' THEN 1 END) as draft_products,
        COUNT(CASE WHEN status = 'draft' AND dq_status = 'failed' THEN 1 END) as dq_failed_products,
        COUNT(CASE WHEN status = 'inactive' THEN 1 END) as inactive_products,
        COUNT(CASE WHEN dq_status = 'failed' THEN 1 END) as failed_dq,
        COUNT(CASE WHEN dq_status = 'passed' THEN 1 END) as passed_dq,
        
        -- Queue counts
        COUNT(CASE WHEN status = 'draft' AND dq_status = 'failed' THEN 1 END) as queue_dq_failed,
        COUNT(CASE WHEN status = 'draft' AND dq_status != 'failed' THEN 1 END) as queue_drafts,
        COUNT(CASE WHEN status = 'active' AND low_stock = true THEN 1 END) as queue_low_stock
    FROM "{GLUE_DATABASE}".product
    WHERE status != 'deleted'
    """
    
    print(f"=== PRODUCT STATS QUERY (Lightweight) ===")
    print(query)
    
    results = execute_athena_query(query)
    
    if results:
        stats = results[0]
        return create_response(200, {
            'total_products': int(stats.get('total_products', 0)),
            'active_products': int(stats.get('active_products', 0)),
            'draft_products': int(stats.get('draft_products', 0)),
            'dq_failed_products': int(stats.get('dq_failed_products', 0)),
            'inactive_products': int(stats.get('inactive_products', 0)),
            'failed_dq': int(stats.get('failed_dq', 0)),
            'passed_dq': int(stats.get('passed_dq', 0)),
            'queue_dq_failed': int(stats.get('queue_dq_failed', 0)),
            'queue_drafts': int(stats.get('queue_drafts', 0)),
            'queue_low_stock': int(stats.get('queue_low_stock', 0))
        })
    
    return create_response(200, {
        'total_products': 0,
        'active_products': 0,
        'draft_products': 0,
        'dq_failed_products': 0,
        'inactive_products': 0,
        'failed_dq': 0,
        'passed_dq': 0,
        'queue_dq_failed': 0,
        'queue_drafts': 0,
        'queue_low_stock': 0
    })


def handle_queues_api(method: str, path: str, query_params: Dict) -> Dict[str, Any]:
    """Handle work queue requests"""
    
    if method != 'GET':
        return create_response(405, {'error': 'Method not allowed'})
    
    # Extract queue type from path: /api/v1/queues/dq-failed
    queue_type = path.split('/')[-1]
    
    if queue_type == 'dq-failed':
        return get_dq_failed_queue(query_params)
    elif queue_type == 'drafts':
        return get_drafts_queue(query_params)
    elif queue_type == 'low-stock':
        return get_low_stock_queue(query_params)
    elif queue_type == 'recent':
        return get_recently_modified_queue(query_params)
    else:
        return create_response(404, {'error': f'Unknown queue type: {queue_type}'})


def get_dq_failed_queue(query_params: Dict) -> Dict[str, Any]:
    """Queue 1: Products that failed data quality checks"""
    
    limit = validate_int(query_params.get('limit', 50), default=50, min_val=1, max_val=100)
    offset = validate_int(query_params.get('offset', 0), default=0, min_val=0, max_val=100000)
    
    query = f"""
    WITH failed_agg AS (
      SELECT 
        product_id,
        ARRAY_JOIN(ARRAY_AGG(failure_reason), ' | ') as failure_reason,
        ARRAY_JOIN(ARRAY_AGG(DISTINCT failed_field), ', ') as failed_field
      FROM "{GLUE_DATABASE}".dq_failed_records
      WHERE correction_status = 'pending'
      GROUP BY product_id
    ),
    ranked_products AS (
      SELECT 
        p.product_id,
        p.base_name,
        p.status,
        p.dq_status,
        p.modified_date,
        fa.failure_reason,
        fa.failed_field,
        ROW_NUMBER() OVER (ORDER BY p.modified_date DESC) as row_num
      FROM "{GLUE_DATABASE}".product p
      LEFT JOIN failed_agg fa ON p.product_id = fa.product_id
      WHERE p.status = 'draft' 
        AND p.dq_status = 'failed'
    )
    SELECT product_id, base_name, status, dq_status, modified_date, failure_reason, failed_field
    FROM ranked_products
    WHERE row_num > ? AND row_num <= ?
    ORDER BY modified_date DESC
    """
    
    print(f"=== DQ FAILED QUEUE ===")
    results = execute_athena_query(query, params=[str(offset), str(offset + limit)])
    
    return create_response(200, {'products': results, 'total': len(results)})


def get_drafts_queue(query_params: Dict) -> Dict[str, Any]:
    """Queue 2: Draft products (not DQ failures)"""
    
    limit = validate_int(query_params.get('limit', 50), default=50, min_val=1, max_val=100)
    offset = validate_int(query_params.get('offset', 0), default=0, min_val=0, max_val=100000)
    
    query = f"""
    WITH ranked_products AS (
      SELECT 
        p.product_id,
        p.base_name,
        p.status,
        p.dq_status,
        p.modified_date,
        ROW_NUMBER() OVER (ORDER BY p.modified_date DESC) as row_num
      FROM "{GLUE_DATABASE}".product p
      WHERE p.status = 'draft' 
        AND p.dq_status != 'failed'
    )
    SELECT product_id, base_name, status, dq_status, modified_date
    FROM ranked_products
    WHERE row_num > ? AND row_num <= ?
    """
    
    print(f"=== DRAFTS QUEUE ===")
    results = execute_athena_query(query, params=[str(offset), str(offset + limit)])
    
    return create_response(200, {'products': results, 'total': len(results)})


def get_low_stock_queue(query_params: Dict) -> Dict[str, Any]:
    """Queue 4: Products with low stock"""
    
    limit = validate_int(query_params.get('limit', 50), default=50, min_val=1, max_val=100)
    offset = validate_int(query_params.get('offset', 0), default=0, min_val=0, max_val=100000)
    
    query = f"""
    WITH ranked_products AS (
      SELECT 
        p.product_id,
        p.base_name,
        p.stock_quantity,
        p.status,
        ROW_NUMBER() OVER (ORDER BY p.stock_quantity ASC) as row_num
      FROM "{GLUE_DATABASE}".product p
      WHERE p.status = 'active'
        AND p.low_stock = true
    )
    SELECT product_id, base_name, stock_quantity, status
    FROM ranked_products
    WHERE row_num > ? AND row_num <= ?
    """
    
    print(f"=== LOW STOCK QUEUE ===")
    results = execute_athena_query(query, params=[str(offset), str(offset + limit)])
    
    return create_response(200, {'products': results, 'total': len(results)})


def get_recently_modified_queue(query_params: Dict) -> Dict[str, Any]:
    """Queue 5: Recently modified products"""
    
    limit = validate_int(query_params.get('limit', 50), default=50, min_val=1, max_val=100)
    offset = validate_int(query_params.get('offset', 0), default=0, min_val=0, max_val=100000)
    days = validate_int(query_params.get('days', 7), default=7, min_val=1, max_val=365)
    
    query = f"""
    WITH ranked_products AS (
      SELECT 
        p.product_id,
        p.base_name,
        p.status,
        p.modified_date,
        ROW_NUMBER() OVER (ORDER BY p.modified_date DESC) as row_num
      FROM "{GLUE_DATABASE}".product p
      WHERE p.modified_date > CURRENT_TIMESTAMP - INTERVAL ? DAY
        AND p.status != 'deleted'
    )
    SELECT product_id, base_name, status, modified_date
    FROM ranked_products
    WHERE row_num > ? AND row_num <= ?
    """
    
    print(f"=== RECENTLY MODIFIED QUEUE ===")
    results = execute_athena_query(query, params=[str(days), str(offset), str(offset + limit)])
    
    return create_response(200, {'products': results, 'total': len(results)})


def handle_quick_search(method: str, query_params: Dict) -> Dict[str, Any]:
    """Quick search on base fields (ISBN/EAN, name, description)"""
    
    if method != 'GET':
        return create_response(405, {'error': 'Method not allowed'})
    
    search_term = query_params.get('q', '').lower().strip()
    if not search_term:
        return create_response(400, {'error': 'Search term required (q parameter)'})
    
    status = validate_enum(query_params.get('status', 'active'), 
                          ['active', 'draft', 'inactive', 'deleted'], 'active')
    limit = validate_int(query_params.get('limit', 50), default=50, min_val=1, max_val=100)
    
    # Use ? placeholders for all user-supplied values
    like_term = f"%{search_term}%"
    
    query = f"""
    SELECT 
      p.product_id,
      p.base_name,
      p.short_description,
      p.status,
      p.base_price,
      p.currency_code,
      p.modified_date
    FROM "{GLUE_DATABASE}".product p
    WHERE p.status = ?
      AND (
        LOWER(p.upc_ean) LIKE ?
        OR LOWER(p.base_name) LIKE ?
        OR LOWER(p.short_description) LIKE ?
      )
    ORDER BY p.modified_date DESC
    LIMIT ?
    """
    
    print(f"=== QUICK SEARCH: {search_term} ===")
    results = execute_athena_query(query, params=[status, like_term, like_term, like_term, str(limit)])
    
    return create_response(200, {'products': results, 'total': len(results)})


def handle_advanced_search(method: str, body: Dict) -> Dict[str, Any]:
    """Advanced search including attributes and subcategories"""
    
    if method != 'POST':
        return create_response(405, {'error': 'Method not allowed'})
    
    status = validate_enum(body.get('status', 'active'),
                          ['active', 'draft', 'inactive', 'deleted'], 'active')
    category_id = body.get('category_id', '') if body.get('category_id') else None
    brand = body.get('brand', '') if body.get('brand') else None
    search_term = body.get('search_term', '').lower().strip()
    attributes = body.get('attributes', {})
    limit = validate_int(body.get('limit', 50), default=50, min_val=1, max_val=100)
    
    # Build CTEs and collect params for ? placeholders
    ctes = []
    where_conditions = ["p.status = ?"]
    params = [status]
    
    # Category filter (includes subcategories)
    if category_id:
        ctes.append(f"""
        matching_by_category AS (
          SELECT DISTINCT pc.product_id
          FROM "{GLUE_DATABASE}".product_category pc
          JOIN "{GLUE_DATABASE}".category c ON pc.category_id = c.category_id
          WHERE c.path LIKE ?
             OR c.category_id = ?
        )
        """)
        params.extend([f"%/{category_id}/%", category_id])
        where_conditions.append("p.product_id IN (SELECT product_id FROM matching_by_category)")
    
    # Attribute filters
    for attr_code, attr_value in attributes.items():
        safe_code = sanitize_sql_identifier(attr_code)
        cte_name = f"matching_by_{safe_code}"
        ctes.append(f"""
        {cte_name} AS (
          SELECT DISTINCT pav.product_id
          FROM "{GLUE_DATABASE}".product_attribute_value pav
          JOIN "{GLUE_DATABASE}".attribute_definition ad ON pav.attribute_id = ad.attribute_id
          WHERE ad.code = ?
            AND LOWER(pav.value) LIKE ?
        )
        """)
        params.extend([safe_code, f"%{str(attr_value).lower()}%"])
        where_conditions.append(f"p.product_id IN (SELECT product_id FROM {cte_name})")
    
    # Base field search
    if search_term:
        where_conditions.append("LOWER(p.base_name) LIKE ?")
        params.append(f"%{search_term}%")
    
    # Brand filter
    if brand:
        where_conditions.append("p.brand = ?")
        params.append(brand)
    
    # Build final query
    with_clause = "WITH " + ",\n".join(ctes) if ctes else ""
    where_clause = " AND ".join(where_conditions)
    
    query = f"""
    {with_clause}
    SELECT 
      p.product_id,
      p.base_name,
      p.short_description,
      p.status,
      p.base_price,
      p.currency_code,
      p.modified_date
    FROM "{GLUE_DATABASE}".product p
    WHERE {where_clause}
    ORDER BY p.modified_date DESC
    LIMIT ?
    """
    params.append(str(limit))
    
    print(f"=== ADVANCED SEARCH ===")
    print(query)
    results = execute_athena_query(query, params=params)
    
    return create_response(200, {'products': results, 'total': len(results)})


def get_cache_version(cache_key: str = 'products_list') -> int:
    """Get current cache version from DynamoDB"""
    if not cache_table:
        return int(time.time() * 1000)
    
    try:
        response = cache_table.get_item(Key={'cache_key': cache_key})
        if 'Item' in response:
            return int(response['Item']['version'])
        return int(time.time() * 1000)
    except Exception as e:
        print(f"⚠️  Failed to get cache version: {e}")
        return int(time.time() * 1000)


def upload_sample_data() -> Dict[str, Any]:
    """Upload sample book products to S3 raw bucket"""
    
    sample_data = {
        "products": [
            {
                "upc_ean": "9780141182636",
                "brand": "penguin",
                "base_name": "The Great Gatsby",
                "short_description": "A classic American novel",
                "long_description": "The Great Gatsby is a 1925 novel by American writer F. Scott Fitzgerald. Set in the Jazz Age on Long Island, the novel depicts narrator Nick Carraway's interactions with mysterious millionaire Jay Gatsby and Gatsby's obsession to reunite with his former lover, Daisy Buchanan.",
                "base_price": 12.99,
                "currency_code": "USD",
                "stock_quantity": 50,
                "attributes": {
                    "author": "F. Scott Fitzgerald",
                    "publisher": "Penguin Classics",
                    "isbn13": "9780141182636",
                    "page_count": 180,
                    "publication_date": "1925-04-10",
                    "language": "English",
                    "binding": "Paperback"
                },
                "categories": ["cat_fiction"],
                "primary_category": "cat_fiction",
                "media_assets": []
            },
            {
                "upc_ean": "9780451524935",
                "brand": "penguin",
                "base_name": "1984",
                "short_description": "Dystopian social science fiction",
                "long_description": "1984 is a dystopian social science fiction novel and cautionary tale by English writer George Orwell. It was published on 8 June 1949 by Secker & Warburg as Orwell's ninth and final book completed in his lifetime.",
                "base_price": 14.99,
                "currency_code": "USD",
                "stock_quantity": 75,
                "attributes": {
                    "author": "George Orwell",
                    "publisher": "Penguin Books",
                    "isbn13": "9780451524935",
                    "page_count": 328,
                    "publication_date": "1949-06-08",
                    "language": "English",
                    "binding": "Paperback"
                },
                "categories": ["cat_fiction"],
                "primary_category": "cat_fiction",
                "media_assets": []
            },
            {
                "upc_ean": "9780061120084",
                "brand": "harpercollins",
                "base_name": "To Kill a Mockingbird",
                "short_description": "A gripping tale of racial injustice",
                "long_description": "To Kill a Mockingbird is a novel by the American author Harper Lee. It was published in 1960 and was instantly successful. In the United States, it is widely read in high schools and middle schools.",
                "base_price": 13.99,
                "currency_code": "USD",
                "stock_quantity": 5,
                "attributes": {
                    "author": "Harper Lee",
                    "publisher": "HarperCollins",
                    "isbn13": "9780061120084",
                    "page_count": 324,
                    "publication_date": "1960-07-11",
                    "language": "English",
                    "binding": "Paperback"
                },
                "categories": ["cat_fiction"],
                "primary_category": "cat_fiction",
                "media_assets": []
            }
        ]
    }
    
    return _upload_to_s3(sample_data)


def upload_custom_data(body_data: Dict) -> Dict[str, Any]:
    """Upload custom product data from user file to S3 raw bucket"""
    
    try:
        # Validate data structure
        if 'products' not in body_data:
            return create_response(400, {'error': 'Invalid data format. Must contain "products" array'})
        
        return _upload_to_s3(body_data)
        
    except Exception as e:
        print(f"❌ Error uploading custom data: {e}")
        return create_response(500, {'error': 'Failed to upload custom data'})


def _upload_to_s3(data: Dict) -> Dict[str, Any]:
    """Helper function to upload data to S3"""
    try:
        # Generate unique filename with timestamp
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f"products-{timestamp}.json"
        s3_key = f"raw/products/{filename}"
        
        # Upload to S3
        s3_client.put_object(
            Bucket=DATA_LAKE_BUCKET,
            Key=s3_key,
            Body=json.dumps(data, indent=2),
            ContentType='application/json'
        )
        
        print(f"✅ Uploaded data to s3://{DATA_LAKE_BUCKET}/{s3_key}")
        
        return create_response(200, {
            'message': 'Data uploaded successfully',
            'bucket': DATA_LAKE_BUCKET,
            'key': s3_key,
            'products_count': len(data.get('products', []))
        })
        
    except Exception as e:
        print(f"❌ Error uploading to S3: {e}")
        return create_response(500, {'error': 'Failed to upload to S3'})


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """Main Lambda handler for Products API"""
    
    try:
        # Parse the request
        http_method = event.get('httpMethod', '')
        path = event.get('path', '')
        path_parameters = event.get('pathParameters') or {}
        query_parameters = event.get('queryStringParameters') or {}
        body = event.get('body', '{}')
        
        # Parse JSON body if present
        if body and body != '{}':
            try:
                body_data = json.loads(body)
            except json.JSONDecodeError:
                return create_response(400, {'error': 'Invalid JSON in request body'})
        else:
            body_data = {}
        
        # --- P0-3: RBAC enforcement ---
        user_role = get_user_role(event)
        user_info = get_user_info(event)
        print(f"Processing {http_method} {path} | user={user_info['username']} role={user_role}")
        print(f"Path parameters: {path_parameters}")
        print(f"Query parameters: {query_parameters}")
        
        # Block write operations for read-only users
        if http_method in ('POST', 'PUT', 'DELETE') and user_role != 'write':
            return create_response(403, {
                'error': 'Insufficient permissions. Your role does not allow write operations.',
                'required_groups': list(WRITE_GROUPS)
            })
        
        # Route the request
        if path == '/api/v1/cache-version':
            # Cache version endpoint
            version = get_cache_version('products_list')
            return create_response(200, {'cache_version': version})
        elif path == '/api/v1/upload-sample-data':
            # Upload sample data to S3
            if http_method == 'POST':
                return upload_sample_data()
            else:
                return create_response(405, {'error': 'Method not allowed'})
        elif path == '/api/v1/upload-custom-data':
            # Upload custom data from user file to S3
            if http_method == 'POST':
                return upload_custom_data(body_data)
            else:
                return create_response(405, {'error': 'Method not allowed'})
        elif path == '/api/v1/stats' or path == '/api/v1/products/stats':
            # Lightweight stats endpoint for dashboard
            return get_product_stats()
        elif path.startswith('/api/v1/queues'):
            # Work queues for PIM workflows
            return handle_queues_api(http_method, path, query_parameters)
        elif path.startswith('/api/v1/products/search/advanced'):
            # Advanced search (must come before /api/v1/products)
            return handle_advanced_search(http_method, body_data)
        elif path.startswith('/api/v1/products/search'):
            # Quick search (must come before /api/v1/products)
            return handle_quick_search(http_method, query_parameters)
        elif path.startswith('/api/v1/products'):
            return handle_products_api(http_method, path, path_parameters, query_parameters, body_data)
        elif path.startswith('/api/v1/categories'):
            return handle_categories_api(http_method, path, path_parameters, query_parameters, body_data)
        elif path.startswith('/api/v1/search'):
            return handle_search_api(http_method, path, query_parameters)
        elif path.startswith('/api/v1/analytics'):
            return handle_analytics_api(http_method, path, query_parameters)
        elif path.startswith('/api/v1/data-quality'):
            return handle_data_quality_api(http_method, path, path_parameters, query_parameters, body_data)
        else:
            return create_response(404, {'error': 'API endpoint not found'})
            
    except Exception as e:
        print(f"Error processing request: {str(e)}")
        return create_response(500, {'error': 'Internal server error'})


def handle_products_api(method: str, path: str, path_params: Dict, query_params: Dict, body: Dict) -> Dict[str, Any]:
    """Handle products CRUD operations"""
    
    # Extract product_id from path or proxy parameter
    product_id = None
    if 'product_id' in path_params:
        product_id = path_params['product_id']
    elif 'proxy' in path_params:
        # Parse product_id from proxy path like "products/3966cb42-8f36-40ab-9eec-354aa49711bc"
        proxy_parts = path_params['proxy'].split('/')
        if len(proxy_parts) >= 2:
            product_id = proxy_parts[1]
    
    if method == 'GET':
        if product_id:
            # Get specific product
            return get_product(product_id)
        else:
            # List products with optional filtering
            return list_products(query_params)
    
    elif method == 'POST':
        # Create new product
        return create_product(body)
    
    elif method == 'PUT':
        if product_id:
            # Update existing product
            return update_product(product_id, body)
        else:
            return create_response(400, {'error': 'Product ID required for update'})
    
    elif method == 'DELETE':
        if product_id:
            # Delete product (soft delete)
            return delete_product(product_id)
        else:
            return create_response(400, {'error': 'Product ID required for delete'})
    
    else:
        return create_response(405, {'error': 'Method not allowed'})


def handle_search_api(method: str, path: str, query_params: Dict) -> Dict[str, Any]:
    """Handle search operations using Product object structure"""
    
    if method != 'GET':
        return create_response(405, {'error': 'Method not allowed'})
    
    # Use same search logic as list_products but with search-specific filters
    search_query = query_params.get('q', '')
    category = query_params.get('category', '')
    brand = query_params.get('brand', '')
    min_price = query_params.get('min_price', '')
    max_price = query_params.get('max_price', '')
    limit = int(query_params.get('limit', '20'))
    
    # Build search parameters for list_products
    search_params = {
        'limit': limit,
        'search': search_query
    }
    
    if brand:
        search_params['brand'] = brand
    
    # Use existing list_products with search parameters
    return list_products(search_params)


def handle_analytics_api(method: str, path: str, query_params: Dict) -> Dict[str, Any]:
    """Handle analytics queries using Product object structure"""
    
    if method != 'GET':
        return create_response(405, {'error': 'Method not allowed'})
    
    if 'products-by-category' in path:
        return get_products_by_category()
    elif 'inventory-report' in path:
        return get_inventory_report()
    elif 'price-analysis' in path:
        return get_price_analysis()
    else:
        return create_response(404, {'error': 'Analytics endpoint not found'})


def handle_data_quality_api(method: str, path: str, path_params: Dict, query_params: Dict, body: Dict) -> Dict[str, Any]:
    """Handle data quality operations"""
    
    if method == 'GET':
        if 'failed-records' in path:
            return get_failed_records(query_params)
        elif 'run-history' in path:
            return get_dq_run_history(query_params)
        elif 'dashboard' in path:
            return get_data_quality_dashboard()
        elif 'export-failed' in path:
            return export_failed_records(query_params)
    
    elif method == 'PUT' and 'correct-record' in path:
        if 'record_id' in path_params:
            return correct_single_record(path_params['record_id'], body)
    
    elif method == 'POST':
        if 'upload-corrections' in path:
            return upload_corrected_file(body)
        elif 'reprocess' in path:
            return trigger_reprocessing(body)
    
    return create_response(404, {'error': 'Data quality endpoint not found'})


def wait_for_query_completion(query_execution_id: str, max_wait: int = 30):
    """Wait for Athena query to complete"""
    import time
    for _ in range(max_wait):
        response = athena_client.get_query_execution(QueryExecutionId=query_execution_id)
        state = response['QueryExecution']['Status']['State']
        if state in ['SUCCEEDED', 'FAILED', 'CANCELLED']:
            return state
        time.sleep(1)
    return 'TIMEOUT'


class Product:
    """Product object model matching database schema"""
    
    def __init__(self, **kwargs):
        # Core product fields
        self.product_id = kwargs.get('product_id', '')
        self.upc_ean = kwargs.get('upc_ean', '')
        self.brand = kwargs.get('brand', '')
        self.base_name = kwargs.get('base_name', '')
        self.short_description = kwargs.get('short_description', '')
        self.base_price = str(round(float(kwargs.get('base_price', 0)), 2))
        self.currency_code = kwargs.get('currency_code', 'USD')
        self.stock_quantity = kwargs.get('stock_quantity', 0)
        self.status = kwargs.get('status', 'draft')
        self.dq_status = kwargs.get('dq_status', 'pending')
        self.created_date = kwargs.get('created_date', 'current_timestamp')
        self.modified_date = kwargs.get('modified_date', 'current_timestamp')
        
        # Attributes (author, publisher, etc.)
        self.attributes = kwargs.get('attributes', {})
        
        # Related data
        self.media_assets = kwargs.get('media_assets', [])
        self.categories = kwargs.get('categories', [])
    
    def update_from_frontend(self, frontend_data):
        """Update product object with frontend data"""
        
        # Map frontend fields to product fields
        field_mapping = {
            'title': 'base_name',
            'description': 'short_description',
            'price': 'base_price'
        }
        
        # Update core fields
        for frontend_field, product_field in field_mapping.items():
            if frontend_field in frontend_data:
                setattr(self, product_field, frontend_data[frontend_field])
        
        # Direct field mappings
        direct_fields = ['upc_ean', 'brand',
                        'currency_code', 'stock_quantity']
        for field in direct_fields:
            if field in frontend_data:
                setattr(self, field, frontend_data[field])
        
        # Handle ISBN mapping
        if 'isbn' in frontend_data:
            self.upc_ean = frontend_data['isbn']
        
        # Update attributes
        attribute_mapping = {
            'author': 'author',
            'publisher': 'publisher',
            'pages': 'page_count',  # Use page_count to match existing data
            'language': 'language',
            'publication_date': 'publication_date',
            'isbn': 'isbn13'
        }
        
        for frontend_field, attr_code in attribute_mapping.items():
            if frontend_field in frontend_data:
                self.attributes[attr_code] = frontend_data[frontend_field]
        
        # Always set to draft/pending when updated
        self.status = 'draft'
        self.dq_status = 'pending'
        self.modified_date = 'current_timestamp'
    
    def to_merge_query(self):
        """Generate MERGE query for product table"""
        
        return f"""
        MERGE INTO "{GLUE_DATABASE}".product AS target
        USING (
            SELECT 
                '{sanitize_sql_string(self.product_id)}' as product_id,
                '{sanitize_sql_string(self.upc_ean)}' as upc_ean,
                '{sanitize_sql_string(self.brand)}' as brand,
                '{sanitize_sql_string(self.base_name)}' as base_name,
                '{sanitize_sql_string(self.short_description)}' as short_description,
                CAST({validate_int(self.base_price, default=0, min_val=0, max_val=9999999)} AS decimal(10,2)) as base_price,
                '{sanitize_sql_string(self.currency_code)}' as currency_code,
                {validate_int(self.stock_quantity, default=0, min_val=0, max_val=9999999)} as stock_quantity,
                '{validate_enum(self.status, ["active","draft","inactive","deleted"], "draft")}' as status,
                '{validate_enum(self.dq_status, ["pending","passed","failed"], "pending")}' as dq_status,
                current_timestamp as modified_date
        ) AS source
        ON target.product_id = source.product_id
        WHEN MATCHED THEN 
            UPDATE SET 
                upc_ean = source.upc_ean,
                brand = source.brand,
                base_name = source.base_name,
                short_description = source.short_description,
                base_price = source.base_price,
                currency_code = source.currency_code,
                stock_quantity = source.stock_quantity,
                status = source.status,
                dq_status = source.dq_status,
                modified_date = source.modified_date
        """
    
    def to_update_query(self):
        """Generate simple UPDATE query for existing products - more reliable than MERGE"""
        
        return f"""
        UPDATE "{GLUE_DATABASE}".product
        SET 
            upc_ean = '{sanitize_sql_string(self.upc_ean)}',
            brand = '{sanitize_sql_string(self.brand)}',
            base_name = '{sanitize_sql_string(self.base_name)}',
            short_description = '{sanitize_sql_string(self.short_description)}',
            base_price = CAST({validate_int(self.base_price, default=0, min_val=0, max_val=9999999)} AS decimal(10,2)),
            currency_code = '{sanitize_sql_string(self.currency_code)}',
            stock_quantity = {validate_int(self.stock_quantity, default=0, min_val=0, max_val=9999999)},
            status = '{validate_enum(self.status, ["active","draft","inactive","deleted"], "draft")}',
            dq_status = '{validate_enum(self.dq_status, ["pending","passed","failed"], "pending")}',
            modified_date = current_timestamp
        WHERE product_id = '{sanitize_sql_string(self.product_id)}'
        """
    
    def get_attribute_batch_merge_query(self):
        """Generate single batched MERGE query for all attributes using numeric IDs"""
        if not self.attributes:
            return None
        
        attr_selects = []
        for attr_code, attr_value in self.attributes.items():
            if attr_value:
                escaped_value = sanitize_sql_string(str(attr_value))
                safe_code = sanitize_sql_identifier(attr_code)
                attr_selects.append(f"SELECT '{sanitize_sql_string(self.product_id)}' as product_id, '{safe_code}' as code, '{escaped_value}' as value")
        
        if not attr_selects:
            return None
        
        return f"""
        MERGE INTO "{GLUE_DATABASE}".product_attribute_value AS target
        USING (
            SELECT s.product_id, ad.attribute_id, s.value
            FROM ({' UNION ALL '.join(attr_selects)}) s
            JOIN (
                SELECT code, attribute_id, ROW_NUMBER() OVER (PARTITION BY code ORDER BY attribute_id) AS rn
                FROM "{GLUE_DATABASE}".attribute_definition
            ) ad ON s.code = ad.code AND ad.rn = 1
        ) AS source
        ON target.product_id = source.product_id AND target.attribute_id = source.attribute_id
        WHEN MATCHED AND (target.value IS NULL OR target.value <> source.value) THEN 
            UPDATE SET value = source.value
        WHEN NOT MATCHED THEN 
            INSERT (product_id, attribute_id, value) 
            VALUES (source.product_id, source.attribute_id, source.value)
        """

def get_product(product_id: str) -> Dict[str, Any]:
    """Get a specific product using Product object structure"""
    
    try:
        product_id = validate_uuid(product_id)
        
        # Use same CTE pattern as list_products for consistency
        query = f"""
        WITH product_data AS (
            SELECT 
                p.product_id,
                p.upc_ean,
                p.brand,
                p.base_name,
                p.short_description,
                p.base_price,
                p.currency_code,
                p.status,
                p.dq_status,
                p.stock_quantity,
                p.created_date,
                p.modified_date,
                
                map_agg(ad.code, pav.value) as attributes,
                
                array_agg(
                    CASE WHEN ma.asset_id IS NOT NULL THEN
                        map(
                            array['asset_id', 'file_name', 'url', 'type', 'usage_code', 'alt_text'],
                            array[ma.asset_id, ma.file_name, ma.url, ma.type, ma.usage_code, ma.alt_text]
                        )
                    END
                ) as media_assets
                
            FROM "{GLUE_DATABASE}".product p
            LEFT JOIN "{GLUE_DATABASE}".product_attribute_value pav ON p.product_id = pav.product_id
            LEFT JOIN "{GLUE_DATABASE}".attribute_definition ad ON pav.attribute_id = ad.attribute_id
            LEFT JOIN "{GLUE_DATABASE}".media_asset ma ON p.product_id = ma.product_id
            WHERE p.product_id = ? AND p.status != 'deleted'
            GROUP BY 
                p.product_id, p.upc_ean, p.brand, p.base_name, 
                p.short_description, p.base_price, p.currency_code,
                p.status, p.dq_status, p.stock_quantity, p.created_date, p.modified_date
        ),
        product_categories AS (
            SELECT 
                pc.product_id,
                array_agg(
                    map(
                        array['category_id', 'name', 'is_primary'],
                        array[pc.category_id, c.name, CAST(pc.is_primary AS VARCHAR)]
                    )
                ) as categories
            FROM "{GLUE_DATABASE}".product_category pc
            JOIN "{GLUE_DATABASE}".category c ON pc.category_id = c.category_id
            WHERE pc.product_id = ?
            GROUP BY pc.product_id
        )
        SELECT 
            pd.*,
            COALESCE(pcat.categories, ARRAY[]) as categories
        FROM product_data pd
        LEFT JOIN product_categories pcat ON pd.product_id = pcat.product_id
        """
        
        result = execute_athena_query(query, params=[product_id, product_id])
        
        if not result:
            return create_response(404, {'error': 'Product not found'})
        
        # Process result using same logic as list_products
        product_data = result[0]
        
        # Clean up attributes - parse Athena map format
        attributes = {}
        if product_data.get('attributes'):
            attr_str = product_data['attributes']
            if attr_str and attr_str != '{}':
                try:
                    # Remove outer braces
                    attr_str = attr_str.strip('{}')
                    if attr_str:
                        # Parse key=value pairs more carefully
                        # Athena map format: {key1=value1, key2=value2}
                        import re
                        # Match key=value where value can contain anything except comma followed by space and another key
                        pattern = r'(\w+)=([^,]+?)(?=,\s*\w+=|$)'
                        matches = re.findall(pattern, attr_str)
                        for key, value in matches:
                            attributes[key.strip()] = value.strip()
                except Exception as e:
                    print(f"Error parsing attributes: {e}, attr_str: {attr_str}")
                    pass
        
        # Clean up media assets and categories
        media_assets = []
        if product_data.get('media_assets'):
            media_list = product_data['media_assets']
            if isinstance(media_list, list):
                media_assets = [asset for asset in media_list if asset is not None]
        
        # Clean up categories - parse from Athena array format
        categories = []
        if product_data.get('categories'):
            cat_data = product_data['categories']
            if isinstance(cat_data, str) and cat_data.startswith('['):
                # Parse Athena array format: [{key1=val1, key2=val2}, ...]
                try:
                    import re
                    map_pattern = r'\{([^}]+)\}'
                    maps = re.findall(map_pattern, cat_data)
                    for map_str in maps:
                        cat_obj = {}
                        kv_pattern = r'(\w+)=([^,]+?)(?=,\s*\w+=|$)'
                        matches = re.findall(kv_pattern, map_str)
                        for key, value in matches:
                            cat_obj[key.strip()] = value.strip()
                        if cat_obj:
                            categories.append(cat_obj)
                except Exception as e:
                    print(f"Error parsing categories: {e}, cat_data: {cat_data}")
            elif isinstance(cat_data, list):
                categories = [cat for cat in cat_data if cat is not None]
        
        product = {
            'product_id': product_data.get('product_id'),
            'upc_ean': product_data.get('upc_ean'),
            'brand': product_data.get('brand'),
            'base_name': product_data.get('base_name'),
            'short_description': product_data.get('short_description'),
            'base_price': product_data.get('base_price'),
            'currency_code': product_data.get('currency_code'),
            'status': product_data.get('status'),
            'dq_status': product_data.get('dq_status'),
            'stock_quantity': product_data.get('stock_quantity'),
            'created_date': product_data.get('created_date'),
            'modified_date': product_data.get('modified_date'),
            'attributes': attributes,
            'media_assets': media_assets,
            'categories': categories
        }
        
        return create_response(200, {'product': product})
        
    except Exception as e:
        print(f"Error getting product: {str(e)}")
        return create_response(500, {'error': 'Failed to get product'})


def list_products(query_params: Dict) -> Dict[str, Any]:
    """List products with aggregated attributes and media as structured objects with efficient pagination"""
    
    # Build WHERE clause with ? placeholders and collect params
    where_conditions = ["p.status != 'deleted'"]
    category_join = ""
    params = []
    count_params = []
    
    # Status filter (default to active if not specified)
    if query_params.get('status'):
        safe_status = validate_enum(query_params['status'],
                                    ['active', 'draft', 'inactive', 'deleted'], 'active')
        where_conditions.append("p.status = ?")
        params.append(safe_status)
        count_params.append(safe_status)
    else:
        where_conditions.append("p.status = 'active'")
    
    # Brand filter
    if query_params.get('brand'):
        where_conditions.append("p.brand = ?")
        params.append(query_params['brand'])
        count_params.append(query_params['brand'])
    
    # Category filter (includes subcategories if include_subcategories=true)
    if query_params.get('category_id'):
        category_id = query_params['category_id']
        include_subcategories = query_params.get('include_subcategories', 'true').lower() == 'true'
        
        category_join = f"""
        JOIN "{GLUE_DATABASE}".product_category pc_filter ON p.product_id = pc_filter.product_id
        JOIN "{GLUE_DATABASE}".category c_filter ON pc_filter.category_id = c_filter.category_id
        """
        
        if include_subcategories:
            where_conditions.append("(c_filter.path LIKE ? OR c_filter.category_id = ?)")
            params.extend([f"%/{category_id}/%", category_id])
            count_params.extend([f"%/{category_id}/%", category_id])
        else:
            where_conditions.append("c_filter.category_id = ?")
            params.append(category_id)
            count_params.append(category_id)
    
    # DQ status filter
    if query_params.get('dq_status'):
        safe_dq = validate_enum(query_params['dq_status'],
                                ['pending', 'passed', 'failed'], '')
        if safe_dq:
            where_conditions.append("p.dq_status = ?")
            params.append(safe_dq)
            count_params.append(safe_dq)
    
    # Search filter (base fields only)
    if query_params.get('search'):
        search_term = query_params['search']
        where_conditions.append("(p.base_name LIKE ? OR p.short_description LIKE ?)")
        like_term = f"%{search_term}%"
        params.extend([like_term, like_term])
        count_params.extend([like_term, like_term])
    
    where_clause = " AND ".join(where_conditions)
    
    # Pagination parameters
    limit = validate_int(query_params.get('limit', '50'), default=50, min_val=1, max_val=100)
    offset = validate_int(query_params.get('offset', '0'), default=0, min_val=0, max_val=100000)
    
    # Sort parameters — allowlist only
    sort_field = query_params.get('sort', 'modified_date')
    sort_order = 'DESC' if query_params.get('order', 'desc').upper() == 'DESC' else 'ASC'
    
    sort_column_map = {
        'modified_date': 'p.modified_date',
        'created_date': 'p.created_date',
        'base_name': 'p.base_name',
        'completeness_score': 'p.completeness_score'
    }
    sort_column = sort_column_map.get(sort_field, 'p.modified_date')
    
    query = f"""
    WITH product_data AS (
        SELECT 
            p.product_id,
            p.upc_ean,
            p.brand,
            p.base_name,
            p.short_description,
            p.base_price,
            p.currency_code,
            p.status,
            p.dq_status,
            p.stock_quantity,
            p.completeness_score,
            p.low_stock,
            p.created_date,
            p.modified_date,
            
            map_agg(ad.code, pav.value) as attributes,
            
            array_agg(
                CASE WHEN ma.asset_id IS NOT NULL THEN
                    map(
                        array['asset_id', 'file_name', 'url', 'type', 'usage_code', 'alt_text'],
                        array[ma.asset_id, ma.file_name, ma.url, ma.type, ma.usage_code, ma.alt_text]
                    )
                END
            ) as media_assets,
            
            ROW_NUMBER() OVER (ORDER BY {sort_column} {sort_order}) as row_num
            
        FROM "{GLUE_DATABASE}".product p
        {category_join}
        LEFT JOIN "{GLUE_DATABASE}".product_attribute_value pav ON p.product_id = pav.product_id
        LEFT JOIN "{GLUE_DATABASE}".attribute_definition ad ON pav.attribute_id = ad.attribute_id
        LEFT JOIN "{GLUE_DATABASE}".media_asset ma ON p.product_id = ma.product_id
        WHERE {where_clause}
        GROUP BY 
            p.product_id, p.upc_ean, p.brand, p.base_name, 
            p.short_description, p.base_price, p.currency_code,
            p.status, p.dq_status, p.stock_quantity, p.completeness_score, p.low_stock,
            p.created_date, p.modified_date
    ),
    product_categories AS (
        SELECT 
            pc.product_id,
            array_agg(
                map(
                    array['category_id', 'name', 'is_primary'],
                    array[pc.category_id, c.name, CAST(pc.is_primary AS VARCHAR)]
                )
            ) as categories
        FROM "{GLUE_DATABASE}".product_category pc
        JOIN "{GLUE_DATABASE}".category c ON pc.category_id = c.category_id
        GROUP BY pc.product_id
    )
    SELECT 
        pd.*,
        COALESCE(pcat.categories, ARRAY[]) as categories
    FROM product_data pd
    LEFT JOIN product_categories pcat ON pd.product_id = pcat.product_id
    WHERE row_num > ? AND row_num <= ?
    ORDER BY row_num
    """
    params.extend([str(offset), str(offset + limit)])
    
    print(f"=== LIST PRODUCTS QUERY ===")
    print(query)
    
    products = execute_athena_query(query, params=params)
    
    print(f"DEBUG: Query returned {len(products) if products else 0} products")
    
    # Get total count
    count_query = f"""
    SELECT COUNT(DISTINCT p.product_id) as total
    FROM "{GLUE_DATABASE}".product p
    {category_join}
    WHERE {where_clause}
    """
    
    total_result = execute_athena_query(count_query, params=count_params)
    total = int(total_result[0]['total']) if total_result and total_result[0].get('total') else 0
    
    # Process the results to clean up the structure
    processed_products = []
    for product in products or []:
        # Clean up attributes - parse Athena map format
        attributes = {}
        attr_str = product.get('attributes')
        print(f"DEBUG: Raw attributes string for product: {attr_str}")
        if attr_str and attr_str != '{}':
            try:
                # Remove outer braces
                attr_str = attr_str.strip('{}')
                if attr_str:
                    # Parse key=value pairs more carefully
                    # Athena map format: {key1=value1, key2=value2}
                    import re
                    # Match key=value where value can contain anything except comma followed by space and another key
                    pattern = r'(\w+)=([^,]+?)(?=,\s*\w+=|$)'
                    matches = re.findall(pattern, attr_str)
                    for key, value in matches:
                        attributes[key.strip()] = value.strip()
                    print(f"DEBUG: Parsed attributes: {attributes}")
            except Exception as e:
                print(f"Error parsing attributes: {e}, attr_str: {attr_str}")
                pass
        
        # Clean up media assets - remove null entries
        media_assets = []
        if product.get('media_assets'):
            media_list = product['media_assets']
            if isinstance(media_list, list):
                media_assets = [asset for asset in media_list if asset is not None]
        
        # Clean up categories - parse from Athena array format
        categories = []
        if product.get('categories'):
            cat_data = product['categories']
            print(f"DEBUG: Categories raw data type: {type(cat_data)}, value: {cat_data}")
            if isinstance(cat_data, str) and cat_data.startswith('['):
                # Parse Athena array format: [{key1=val1, key2=val2}, ...]
                try:
                    import re
                    # Extract each map object
                    map_pattern = r'\{([^}]+)\}'
                    maps = re.findall(map_pattern, cat_data)
                    print(f"DEBUG: Found {len(maps)} category maps")
                    for map_str in maps:
                        cat_obj = {}
                        # Parse key=value pairs
                        kv_pattern = r'(\w+)=([^,]+?)(?=,\s*\w+=|$)'
                        matches = re.findall(kv_pattern, map_str)
                        for key, value in matches:
                            cat_obj[key.strip()] = value.strip()
                        if cat_obj:
                            categories.append(cat_obj)
                            print(f"DEBUG: Parsed category: {cat_obj}")
                except Exception as e:
                    print(f"Error parsing categories: {e}, cat_data: {cat_data}")
            elif isinstance(cat_data, list):
                categories = [cat for cat in cat_data if cat is not None]
                print(f"DEBUG: Categories is list with {len(categories)} items")
        
        processed_product = {
            # Core product fields
            'product_id': product.get('product_id'),
            'upc_ean': product.get('upc_ean'),
            'brand': product.get('brand'),
            'base_name': product.get('base_name'),
            'short_description': product.get('short_description'),
            # long_description removed - now in attributes as 'synopsis'
            'base_price': product.get('base_price'),
            'currency_code': product.get('currency_code'),
            'status': product.get('status'),
            'dq_status': product.get('dq_status'),
            'stock_quantity': product.get('stock_quantity'),
            'created_date': product.get('created_date'),
            'modified_date': product.get('modified_date'),
            
            # Aggregated related data
            'attributes': attributes,
            'media_assets': media_assets,
            'categories': categories
        }
        
        processed_products.append(processed_product)
    
    return create_response(200, {
        'products': processed_products,
        'total': total,
        'limit': limit,
        'offset': offset,
        'has_more': offset + limit < total
    })


def create_product(product_data: Dict) -> Dict[str, Any]:
    """Create a new product using Product object model"""
    
    try:
        # Validate required fields
        required_fields = ['base_name', 'brand', 'upc_ean']
        for field in required_fields:
            if field not in product_data or not product_data[field]:
                return create_response(400, {'error': f'Missing required field: {field}'})
        
        # Create Product object
        product = Product(**product_data)
        
        # Generate product ID if not provided
        if not product.product_id:
            product.product_id = f"prod_{uuid.uuid4().hex[:8]}"
        
        # Set creation timestamps and defaults
        product.created_date = 'current_timestamp'
        product.modified_date = 'current_timestamp'
        product.status = 'draft'  # New products start as draft
        product.dq_status = 'pending'  # Need DQ validation
        
        # Execute product creation with MERGE
        product_merge_query = product.to_merge_query().replace('WHEN MATCHED THEN', 'WHEN NOT MATCHED THEN INSERT')
        
        # Fix the MERGE for INSERT operation
        create_query = f"""
        INSERT INTO "{GLUE_DATABASE}".product (
            product_id, upc_ean, brand, base_name, short_description, 
            long_description, base_price, currency_code, stock_quantity, 
            status, dq_status, created_date, modified_date
        ) VALUES (
            '{sanitize_sql_string(product.product_id)}',
                        '{sanitize_sql_string(product.upc_ean)}',
            '{sanitize_sql_string(product.brand)}',
            '{sanitize_sql_string(product.base_name)}',
            '{sanitize_sql_string(product.short_description)}',
            '{sanitize_sql_string(product.long_description)}',
            '{sanitize_sql_string(product.base_price)}',
            '{sanitize_sql_string(product.currency_code)}',
            {validate_int(product.stock_quantity, default=0, min_val=0, max_val=9999999)},
            '{product.status}',
            '{product.dq_status}',
            current_timestamp,
            current_timestamp
        )
        """
        
        print(f"=== PRODUCT CREATE QUERY ===")
        print(create_query)
        
        response = athena_client.start_query_execution(
            QueryString=create_query,
            QueryExecutionContext={'Database': GLUE_DATABASE},
            ResultConfiguration={'OutputLocation': f's3://{ATHENA_RESULTS_BUCKET}/query-results/'},
            WorkGroup=ATHENA_WORKGROUP
        )
        
        # Create attributes if provided
        attr_query = product.get_attribute_batch_merge_query()
        if attr_query:
            try:
                athena_client.start_query_execution(
                    QueryString=attr_query,
                    QueryExecutionContext={'Database': GLUE_DATABASE},
                    ResultConfiguration={'OutputLocation': f's3://{ATHENA_RESULTS_BUCKET}/query-results/'},
                    WorkGroup=ATHENA_WORKGROUP
                )
            except Exception as e:
                print(f"Warning: Failed to create attributes: {e}")
        
        # Invalidate all query caches on write
        invalidate_query_cache('query_')
        update_cache_version('products_list')

        return create_response(201, {
            'message': 'Product created successfully',
            'product_id': product.product_id,
            'status': product.status,
            'dq_status': product.dq_status
        })
        
    except Exception as e:
        print(f"Error creating product: {str(e)}")
        return create_response(500, {'error': 'Failed to create product'})


def update_product(product_id: str, product_data: Dict) -> Dict[str, Any]:
    """Update product using Product object model"""
    
    try:
        print(f"Updating product {product_id} with data: {product_data}")
        
        # Create Product object from frontend data
        # Frontend should send current product data + changes
        product = Product(**product_data)
        product.product_id = product_id  # Ensure correct ID
        
        # Update with any changes from frontend
        product.update_from_frontend(product_data)
        
        # Execute product merge
        product_merge_query = product.to_update_query()  # Use UPDATE for existing products
        
        print(f"=== PRODUCT UPDATE QUERY ===")
        print(product_merge_query)
        
        response = athena_client.start_query_execution(
            QueryString=product_merge_query,
            QueryExecutionContext={'Database': GLUE_DATABASE},
            ResultConfiguration={'OutputLocation': f's3://{ATHENA_RESULTS_BUCKET}/query-results/'},
            WorkGroup=ATHENA_WORKGROUP
        )
        
        # Wait for merge to complete - Iceberg changes are immediately consistent
        query_execution_id = response['QueryExecutionId']
        status = wait_for_query_completion(query_execution_id)
        
        if status != 'SUCCEEDED':
            # Get error details
            query_result = athena_client.get_query_execution(QueryExecutionId=query_execution_id)
            error_msg = query_result['QueryExecution']['Status'].get('StateChangeReason', 'Unknown error')
            print(f"❌ Product MERGE failed: {error_msg}")
            raise Exception(f"Failed to update product: {error_msg}")
        
        print(f"✅ Product UPDATE succeeded")
        
        # Batch update all attributes in a single MERGE query
        if product.attributes:
            batch_attr_query = product.get_attribute_batch_merge_query()
            if batch_attr_query:
                try:
                    resp = athena_client.start_query_execution(
                        QueryString=batch_attr_query,
                        QueryExecutionContext={'Database': GLUE_DATABASE},
                        ResultConfiguration={'OutputLocation': f's3://{ATHENA_RESULTS_BUCKET}/query-results/'},
                        WorkGroup=ATHENA_WORKGROUP
                    )
                    attr_query_id = resp['QueryExecutionId']
                    attr_status = wait_for_query_completion(attr_query_id)
                    if attr_status == 'SUCCEEDED':
                        print(f"✅ Batch updated attributes for {product.product_id}")
                    else:
                        print(f"⚠️ Attribute batch update failed: {attr_status}")
                except Exception as e:
                    print(f"Warning: Failed to batch update attributes: {e}")
        
        # Update category if provided (only if changed)
        category_id = product_data.get('category_id')
        if category_id:
            # Validate inputs before interpolating into SQL
            safe_product_id = sanitize_sql_string(product_id)
            safe_category_id = sanitize_sql_string(category_id)
            # Use MERGE to update or insert category
            cat_query = f"""
            MERGE INTO "{GLUE_DATABASE}".product_category AS target
            USING (
                SELECT 
                    '{safe_product_id}' as product_id,
                    '{safe_category_id}' as category_id,
                    true as is_primary
            ) AS source
            ON target.product_id = source.product_id
            WHEN MATCHED AND target.category_id <> source.category_id THEN 
                UPDATE SET 
                    category_id = source.category_id,
                    is_primary = source.is_primary
            WHEN NOT MATCHED THEN 
                INSERT (product_id, category_id, is_primary)
                VALUES (source.product_id, source.category_id, source.is_primary)
            """
            try:
                resp = athena_client.start_query_execution(
                    QueryString=cat_query,
                    QueryExecutionContext={'Database': GLUE_DATABASE},
                    ResultConfiguration={'OutputLocation': f's3://{ATHENA_RESULTS_BUCKET}/query-results/'},
                    WorkGroup=ATHENA_WORKGROUP
                )
                cat_query_id = resp['QueryExecutionId']
                cat_status = wait_for_query_completion(cat_query_id)
                if cat_status == 'SUCCEEDED':
                    print(f"✅ Category updated successfully")
                else:
                    print(f"⚠️ Category update failed: {cat_status}")
            except Exception as e:
                print(f"Warning: Failed to update category: {e}")
        
        # Invalidate all query caches on write
        invalidate_query_cache('query_')
        update_cache_version('products_list')

        return create_response(200, {
            'message': 'Product updated successfully',
            'product_id': product_id,
            'status': product.status,
            'dq_status': product.dq_status
        })
        
    except Exception as e:
        print(f"Error updating product: {str(e)}")
        return create_response(500, {'error': f'Failed to update product: {str(e)}'})


def handle_categories_api(method: str, path: str, path_params: Dict, query_params: Dict, body: Dict) -> Dict[str, Any]:
    """Handle categories API operations"""
    
    if method == 'GET':
        level = query_params.get('level', '')  # Empty string = all categories
        parent_id = query_params.get('parent_id', '')
        return get_categories(level, parent_id)
    else:
        return create_response(405, {'error': 'Method not allowed'})


def get_categories(level: str = '', parent_id: str = '') -> Dict[str, Any]:
    """Get categories by level or parent ID, or all if no filter"""
    
    try:
        params = []
        if parent_id:
            query = f"""
            SELECT category_id, name, parent_category_id, level, display_order
            FROM "{GLUE_DATABASE}".category 
            WHERE parent_category_id = ?
            ORDER BY display_order, name
            """
            params = [parent_id]
        elif level:
            safe_level = validate_int(level, default=1, min_val=1, max_val=10)
            query = f"""
            SELECT category_id, name, parent_category_id, level, display_order
            FROM "{GLUE_DATABASE}".category 
            WHERE level = ?
            ORDER BY display_order, name
            """
            params = [str(safe_level)]
        else:
            query = f"""
            SELECT category_id, name, parent_category_id, level, display_order
            FROM "{GLUE_DATABASE}".category 
            ORDER BY level, display_order, name
            """
        
        print(f"Executing categories query")
        
        # Build execution kwargs
        exec_kwargs = {
            'QueryString': query,
            'QueryExecutionContext': {'Database': GLUE_DATABASE},
            'ResultConfiguration': {'OutputLocation': f's3://{ATHENA_RESULTS_BUCKET}/query-results/'},
            'WorkGroup': ATHENA_WORKGROUP
        }
        if params:
            exec_kwargs['ExecutionParameters'] = params
        
        response = athena_client.start_query_execution(**exec_kwargs)
        
        query_execution_id = response['QueryExecutionId']
        
        # Wait for query completion
        import time
        max_attempts = 30
        for attempt in range(max_attempts):
            query_status = athena_client.get_query_execution(QueryExecutionId=query_execution_id)
            status = query_status['QueryExecution']['Status']['State']
            
            if status == 'SUCCEEDED':
                break
            elif status in ['FAILED', 'CANCELLED']:
                error_reason = query_status['QueryExecution']['Status'].get('StateChangeReason', 'Unknown error')
                return create_response(500, {'error': f'Query failed: {error_reason}'})
            
            time.sleep(1)
        
        # Get results
        results = athena_client.get_query_results(QueryExecutionId=query_execution_id)
        
        categories = []
        for i, row in enumerate(results['ResultSet']['Rows']):
            if i == 0:  # Skip header row
                continue
                
            row_data = row['Data']
            category = {
                'category_id': row_data[0].get('VarCharValue', ''),
                'name': row_data[1].get('VarCharValue', ''),
                'parent_category_id': row_data[2].get('VarCharValue', ''),
                'level': int(row_data[3].get('VarCharValue', '0')),
                'sort_order': int(row_data[4].get('VarCharValue', '0'))
            }
            categories.append(category)

        return create_response(200, {
            'categories': categories,
            'total': len(categories)
        })
        
    except Exception as e:
        print(f"Error fetching categories: {str(e)}")
        return create_response(500, {'error': 'Failed to fetch categories'})


def delete_product(product_id: str) -> Dict[str, Any]:
    """Soft delete a product using Product object model"""
    
    try:
        product_id = validate_uuid(product_id)
        print(f"Soft deleting product {product_id}")
        
        # Use Product object for consistent soft delete
        # Just update status to 'deleted' instead of hard delete
        soft_delete_query = f"""
        UPDATE "{GLUE_DATABASE}".product 
        SET 
            status = 'deleted',
            modified_date = current_timestamp
        WHERE product_id = '{sanitize_sql_string(product_id)}'
        """
        
        print(f"=== SOFT DELETE QUERY ===")
        print(soft_delete_query)
        
        response = athena_client.start_query_execution(
            QueryString=soft_delete_query,
            QueryExecutionContext={'Database': GLUE_DATABASE},
            ResultConfiguration={'OutputLocation': f's3://{ATHENA_RESULTS_BUCKET}/query-results/'},
            WorkGroup=ATHENA_WORKGROUP
        )
        
        # Invalidate all query caches on write
        invalidate_query_cache('query_')
        update_cache_version('products_list')

        return create_response(200, {
            'message': 'Product deleted successfully',
            'product_id': product_id,
            'status': 'deleted'
        })
        
    except Exception as e:
        print(f"Error deleting product: {str(e)}")
        return create_response(500, {'error': f'Failed to delete product: {str(e)}'})


def get_products_by_category() -> Dict[str, Any]:
    """Get products count by category using Product object structure"""
    
    query = f"""
    SELECT 
        c.name as category_name,
        COUNT(*) as product_count,
        AVG(CAST(p.base_price AS DOUBLE)) as avg_price
    FROM "{GLUE_DATABASE}".product p 
    LEFT JOIN "{GLUE_DATABASE}".product_category pc ON p.product_id = pc.product_id
    LEFT JOIN "{GLUE_DATABASE}".category c ON pc.category_id = c.category_id
    WHERE p.status != 'deleted'
    GROUP BY c.name
    ORDER BY product_count DESC
    """
    
    result = execute_athena_query(query)
    return create_response(200, {'categories': result})


def get_inventory_report() -> Dict[str, Any]:
    """Get inventory report using Product object structure"""
    
    query = f"""
    SELECT 
        p.status,
        COUNT(*) as product_count,
        SUM(p.stock_quantity) as total_stock,
        AVG(CAST(p.base_price AS DOUBLE)) as avg_price
    FROM "{GLUE_DATABASE}".product p 
    WHERE p.status != 'deleted'
    GROUP BY p.status
    ORDER BY product_count DESC
    """
    
    result = execute_athena_query(query)
    return create_response(200, {'inventory': result})


def get_price_analysis() -> Dict[str, Any]:
    """Get price analysis using Product object structure"""
    
    query = f"""
    SELECT 
        p.brand as brand_name,
        COUNT(*) as product_count,
        MIN(CAST(p.base_price AS DOUBLE)) as min_price,
        MAX(CAST(p.base_price AS DOUBLE)) as max_price,
        AVG(CAST(p.base_price AS DOUBLE)) as avg_price
    FROM "{GLUE_DATABASE}".product p 
    WHERE p.status != 'deleted'
    GROUP BY p.brand
    ORDER BY avg_price DESC
    """
    
    result = execute_athena_query(query)
    return create_response(200, {'price_analysis': result})


def get_failed_records(query_params: Dict) -> Dict[str, Any]:
    """Get failed data quality records with pagination"""
    
    try:
        limit = validate_int(query_params.get('limit', 50), default=50, min_val=1, max_val=100)
        offset = validate_int(query_params.get('offset', 0), default=0, min_val=0, max_val=100000)
        
        failed_query = f"""
        SELECT 
            p.product_id,
            p.base_name as name,
            p.base_price as price,
            p.status,
            p.dq_status,
            p.modified_date as failed_at,
            COALESCE(dfr.validation_errors, ARRAY['Validation errors not available']) as validation_errors,
            COALESCE(dfr.job_run_id, 'N/A') as job_run_id
        FROM "{GLUE_DATABASE}".product p
        LEFT JOIN "{GLUE_DATABASE}".dq_failed_records dfr ON p.product_id = dfr.product_id
        WHERE p.dq_status in ('failed','pending')
        ORDER BY p.modified_date DESC
        LIMIT ?
        """
        
        result = execute_athena_query(failed_query, params=[str(limit)])
        
        # Get total count for pagination
        count_query = f"""
        SELECT COUNT(1) as total_count 
        FROM "{GLUE_DATABASE}".product
        WHERE dq_status = 'failed'
        """
        count_result = execute_athena_query(count_query)
        total_count = int(count_result[0].get('total_count', 0)) if count_result else 0

        return create_response(200, {
            'failed_records': result or [],
            'pagination': {
                'total': total_count,
                'limit': limit,
                'offset': offset,
                'has_more': (offset + limit) < total_count
            }
        })
        
    except Exception as e:
        print(f"Error getting failed records: {e}")
        return create_response(500, {'error': 'Failed to fetch failed records'})


def get_data_quality_dashboard() -> Dict[str, Any]:
    """Get data quality dashboard metrics from DQ summary table"""
    
    try:
        # Single query to get latest DQ run summary
        summary_query = f"""
        SELECT 
            total_records,
            failed_records,
            success_rate,
            timestamp
        FROM "{GLUE_DATABASE}".dq_run_summary 
        ORDER BY timestamp DESC 
        LIMIT 1
        """
        
        result = execute_athena_query(summary_query)
        
        if result and len(result) > 0:
            latest_run = result[0]
            return create_response(200, {
                'metrics': {
                    'total_records_processed': int(latest_run.get('total_records', 0)),
                    'failed_records': int(latest_run.get('failed_records', 0)),
                    'success_rate': round(float(latest_run.get('success_rate', 100.0)), 1)
                },
                'last_updated': latest_run.get('timestamp', 'Unknown')
            })
        else:
            # No DQ runs yet - return defaults
            return create_response(200, {
                'metrics': {
                    'total_records_processed': 0,
                    'failed_records': 0,
                    'success_rate': 100.0
                },
                'last_updated': 'No data quality runs yet'
            })
        
    except Exception as e:
        print(f"Error in get_data_quality_dashboard: {e}")
        return create_response(500, {'error': 'Failed to fetch data quality metrics'})


def get_dq_run_history(query_params: Dict) -> Dict[str, Any]:
    """Get DQ run summaries for a given period - on-demand only"""
    try:
        period = query_params.get('period', '7d')
        unit = period[-1]
        amount = int(period[:-1])
        days = {'d': 1, 'w': 7, 'm': 30}.get(unit, 1) * amount
        
        query = f"""
        SELECT job_run_id, timestamp, total_records, failed_records, 
               valid_records, success_rate
        FROM "{GLUE_DATABASE}".dq_run_summary 
        WHERE timestamp >= date_format(date_add('day', -?, current_timestamp), '%Y-%m-%dT%H:%i:%s')
        ORDER BY timestamp DESC
        """
        result = execute_athena_query(query, params=[str(days)])
        return create_response(200, {'runs': result, 'period': period})
    except Exception as e:
        print(f"Error fetching DQ run history: {e}")
        return create_response(500, {'error': 'Failed to fetch DQ run history'})


def export_failed_records(query_params: Dict) -> Dict[str, Any]:
    """Export failed records as CSV for bulk correction"""
    
    try:
        # Query failed records with attributes
        query = f"""
        SELECT 
            p.product_id,
            p.base_name,
            p.base_price,
            p.status,
            p.stock_quantity,
            p.brand,
            ARRAY_JOIN(ARRAY_AGG(DISTINCT dfr.failure_reason), '|') as validation_errors,
            ARRAY_JOIN(ARRAY_AGG(DISTINCT dfr.failed_field), '|') as failed_fields
        FROM "{GLUE_DATABASE}".product p
        LEFT JOIN "{GLUE_DATABASE}".dq_failed_records dfr ON p.product_id = dfr.product_id
        WHERE p.dq_status = 'failed'
        GROUP BY p.product_id, p.base_name, p.base_price, p.status, p.stock_quantity, p.brand, p.modified_date
        ORDER BY p.modified_date DESC
        """
        
        products = execute_athena_query(query)
        
        if not products:
            return create_response(404, {'error': 'No failed records found'})
        
        # Get attributes for each product using parameterized query
        product_ids = [p['product_id'] for p in products]
        placeholders = ', '.join(['?' for _ in product_ids])
        
        attr_query = f"""
        SELECT pav.product_id, ad.code, pav.value
        FROM "{GLUE_DATABASE}".product_attribute_value pav
        JOIN "{GLUE_DATABASE}".attribute_definition ad ON pav.attribute_id = ad.attribute_id
        WHERE pav.product_id IN ({placeholders})
        """
        
        attributes = execute_athena_query(attr_query, params=product_ids)
        
        # Group attributes by product_id
        attr_map = {}
        for attr in attributes:
            pid = attr['product_id']
            if pid not in attr_map:
                attr_map[pid] = {}
            attr_map[pid][attr['code']] = attr['value']
        
        # Get categories for each product
        cat_query = f"""
        SELECT pc.product_id, c.category_id
        FROM "{GLUE_DATABASE}".product_category pc
        JOIN "{GLUE_DATABASE}".category c ON pc.category_id = c.category_id
        WHERE pc.product_id IN ({placeholders})
        """
        
        categories = execute_athena_query(cat_query, params=product_ids)
        
        # Group categories by product_id
        cat_map = {}
        for cat in categories:
            pid = cat['product_id']
            if pid not in cat_map:
                cat_map[pid] = []
            cat_map[pid].append(cat['category_id'])
        
        # Build CSV content
        import csv
        import io
        
        output = io.StringIO()
        
        # CSV headers
        fieldnames = ['product_id', 'name', 'price', 'status', 'stock_quantity', 
                     'brand', 'attributes', 'categories', 'validation_errors']
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        
        # Write rows
        for product in products:
            pid = product['product_id']
            
            # Format attributes as key:value|key:value, with XXX placeholders for failed fields
            attrs = attr_map.get(pid, {})
            failed_fields = [f.strip() for f in (product.get('failed_fields', '') or '').split('|') if f.strip()]
            for ff in failed_fields:
                if ff not in attrs:
                    attrs[ff] = 'XXX'
            attr_str = '|'.join([f"{k}:{v}" for k, v in attrs.items()])
            
            # Format categories as cat1|cat2
            cats = cat_map.get(pid, [])
            cat_str = '|'.join(cats)
            
            # Format validation errors
            errors = product.get('validation_errors', '')
            if isinstance(errors, list):
                error_str = '|'.join(errors)
            else:
                error_str = errors if errors else ''
            
            writer.writerow({
                'product_id': pid,
                                'name': product.get('base_name', ''),
                'price': product.get('base_price', ''),
                'status': product.get('status', ''),
                'stock_quantity': product.get('stock_quantity', ''),
                'brand': product.get('brand', ''),
                'attributes': attr_str,
                'categories': cat_str,
                'validation_errors': error_str
            })
        
        csv_content = output.getvalue()
        
        # Upload to S3 for download
        import base64
        from datetime import datetime
        
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        s3_key = f"corrections/failed_products_{timestamp}.csv"
        
        s3_client.put_object(
            Bucket=ATHENA_RESULTS_BUCKET,
            Key=s3_key,
            Body=csv_content,
            ContentType='text/csv'
        )
        
        # Generate presigned URL
        download_url = s3_client.generate_presigned_url(
            'get_object',
            Params={'Bucket': ATHENA_RESULTS_BUCKET, 'Key': s3_key},
            ExpiresIn=3600  # 1 hour
        )

        return create_response(200, {
            'download_url': download_url,
            'filename': f'failed_products_{timestamp}.csv',
            'record_count': len(products),
            'expires_in': 3600
        })
        
    except Exception as e:
        print(f"Error exporting failed records: {e}")
        import traceback
        traceback.print_exc()
        return create_response(500, {'error': f'Failed to export records: {str(e)}'})


def correct_single_record(record_id: str, correction_data: Dict) -> Dict[str, Any]:
    """Correct a single failed record via UI - uses Product class for schema-safe queries"""
    
    try:
        record_id = validate_uuid(record_id)
        product_id = sanitize_sql_string(correction_data.get('product_id', record_id))
        
        # Build Product object from correction data (same pattern as bulk upload)
        product = Product(
            product_id=product_id,
                        base_name=correction_data.get('name', correction_data.get('base_name', '')),
            short_description=correction_data.get('short_description', ''),
            base_price=correction_data.get('price', correction_data.get('base_price', '0')),
            currency_code=correction_data.get('currency_code', 'USD'),
            stock_quantity=int(correction_data.get('stock_quantity', 0)),
            status=correction_data.get('status', 'active'),
            upc_ean=correction_data.get('upc_ean', correction_data.get('isbn', '')),
            brand=correction_data.get('brand', ''),
            attributes=correction_data.get('attributes', {})
        )
        product.dq_status = 'pending'
        
        # Update product table
        merge_query = product.to_merge_query()
        merge_response = athena_client.start_query_execution(
            QueryString=merge_query,
            QueryExecutionContext={'Database': GLUE_DATABASE},
            ResultConfiguration={'OutputLocation': f's3://{ATHENA_RESULTS_BUCKET}/query-results/'},
            WorkGroup=ATHENA_WORKGROUP
        )
        merge_state = wait_for_query_completion(merge_response['QueryExecutionId'])
        if merge_state != 'SUCCEEDED':
            return create_response(500, {'error': f'Product update failed: {merge_state}'})
        
        # Update attributes
        attr_query = product.get_attribute_batch_merge_query()
        if attr_query:
            attr_response = athena_client.start_query_execution(
                QueryString=attr_query,
                QueryExecutionContext={'Database': GLUE_DATABASE},
                ResultConfiguration={'OutputLocation': f's3://{ATHENA_RESULTS_BUCKET}/query-results/'},
                WorkGroup=ATHENA_WORKGROUP
            )
            wait_for_query_completion(attr_response['QueryExecutionId'])
        
        # Invalidate cache
        update_cache_version('products_list')

        return create_response(200, {
            'message': 'Record corrected successfully',
            'record_id': record_id
        })
        
    except Exception as e:
        print(f"Error correcting record: {e}")
        return create_response(500, {'error': 'Failed to correct record'})


def upload_corrected_file(body: Dict) -> Dict[str, Any]:
    """Handle bulk correction file upload - batched into single MERGE queries for cost efficiency"""
    
    try:
        csv_content = body.get('file_content')
        if not csv_content:
            return create_response(400, {'error': 'No file content provided'})
        
        import base64, csv, io
        
        decoded_content = base64.b64decode(csv_content).decode('utf-8')
        csv_reader = csv.DictReader(io.StringIO(decoded_content))
        
        # Parse all rows first
        products = []
        all_attributes = []  # (product_id, attr_code, value)
        all_categories = []  # (product_id, category_id, is_primary)
        errors = []
        product_ids = []
        
        for row_num, row in enumerate(csv_reader, start=2):
            try:
                product_id = validate_uuid(row['product_id'])
                product_ids.append(product_id)
                
                # Validate status against allowlist
                safe_status = validate_enum(
                    row['status'].strip(),
                    ['active', 'draft', 'inactive', 'deleted'],
                    'draft'
                )
                
                products.append({
                    'product_id': sanitize_sql_string(product_id),
                    'base_name': sanitize_sql_string(row['name'].strip()),
                    'base_price': sanitize_sql_string(row['price'].strip()),
                    'status': safe_status,
                    'stock_quantity': validate_int(row.get('stock_quantity', 0), default=0, min_val=0, max_val=9999999),
                    'brand': sanitize_sql_string(row.get('brand', '').strip()),
                })
                
                # Parse attributes
                if row.get('attributes'):
                    for pair in row['attributes'].split('|'):
                        if ':' in pair:
                            key, value = pair.split(':', 1)
                            safe_key = sanitize_sql_identifier(key.strip())
                            if safe_key:
                                all_attributes.append((sanitize_sql_string(product_id), safe_key, sanitize_sql_string(value.strip())))
                
                # Parse categories
                if row.get('categories'):
                    cats = [c.strip() for c in row['categories'].split('|') if c.strip()]
                    for idx, cat_id in enumerate(cats):
                        safe_cat_id = sanitize_sql_string(cat_id)
                        all_categories.append((sanitize_sql_string(product_id), safe_cat_id, 'true' if idx == 0 else 'false'))
                        
            except Exception as e:
                errors.append(f"Row {row_num}: {str(e)}")
        
        if not products:
            return create_response(400, {'error': 'No valid rows found', 'errors': errors})
        
        # --- 1. Single batched MERGE for all products ---
        product_selects = [
            f"SELECT '{p['product_id']}' as product_id, "
            f"'' as upc_ean, '{p['brand']}' as brand, '{p['base_name']}' as base_name, "
            f"'' as short_description, CAST({round(float(p['base_price']), 2)} AS decimal(10,2)) as base_price, "
            f"'USD' as currency_code, {p['stock_quantity']} as stock_quantity, "
            f"'{p['status']}' as status, 'pending' as dq_status, "
            f"current_timestamp as modified_date"
            for p in products
        ]
        
        product_merge = f"""
        MERGE INTO "{GLUE_DATABASE}".product AS target
        USING ({' UNION ALL '.join(product_selects)}) AS source
        ON target.product_id = source.product_id
        WHEN MATCHED THEN 
            UPDATE SET 
                base_name = source.base_name,
                short_description = source.short_description, brand = source.brand,
                base_price = source.base_price, currency_code = source.currency_code,
                stock_quantity = source.stock_quantity, status = source.status,
                dq_status = source.dq_status, modified_date = source.modified_date
        """
        
        print(f"Batched product MERGE for {len(products)} products")
        merge_response = athena_client.start_query_execution(
            QueryString=product_merge,
            QueryExecutionContext={'Database': GLUE_DATABASE},
            ResultConfiguration={'OutputLocation': f's3://{ATHENA_RESULTS_BUCKET}/query-results/'},
            WorkGroup=ATHENA_WORKGROUP
        )
        merge_state = wait_for_query_completion(merge_response['QueryExecutionId'])
        if merge_state != 'SUCCEEDED':
            return create_response(500, {'error': f'Batched product MERGE failed: {merge_state}'})
        
        # --- 2. Single batched MERGE for all attributes ---
        if all_attributes:
            attr_selects = [
                f"SELECT '{pid}' as product_id, '{code}' as code, '{val}' as value"
                for pid, code, val in all_attributes
            ]
            
            attr_merge = f"""
            MERGE INTO "{GLUE_DATABASE}".product_attribute_value AS target
            USING (
                SELECT s.product_id, ad.attribute_id, s.value
                FROM ({' UNION ALL '.join(attr_selects)}) s
                JOIN "{GLUE_DATABASE}".attribute_definition ad ON s.code = ad.code
            ) AS source
            ON target.product_id = source.product_id AND target.attribute_id = source.attribute_id
            WHEN MATCHED AND target.value <> source.value THEN 
                UPDATE SET value = source.value
            WHEN NOT MATCHED THEN 
                INSERT (product_id, attribute_id, value) 
                VALUES (source.product_id, source.attribute_id, source.value)
            """
            
            print(f"Batched attribute MERGE for {len(all_attributes)} attributes")
            attr_response = athena_client.start_query_execution(
                QueryString=attr_merge,
                QueryExecutionContext={'Database': GLUE_DATABASE},
                ResultConfiguration={'OutputLocation': f's3://{ATHENA_RESULTS_BUCKET}/query-results/'},
                WorkGroup=ATHENA_WORKGROUP
            )
            attr_state = wait_for_query_completion(attr_response['QueryExecutionId'])
            if attr_state != 'SUCCEEDED':
                errors.append(f"Attribute batch MERGE failed: {attr_state}")
        
        # --- 3. Batched category updates (delete old + insert new) ---
        if all_categories:
            cat_product_ids = list(set(pid for pid, _, _ in all_categories))
            # IDs are already sanitized during CSV parsing above
            id_list = ','.join(f"'{pid}'" for pid in cat_product_ids)
            
            delete_cat_query = f"""
            DELETE FROM "{GLUE_DATABASE}".product_category 
            WHERE product_id IN ({id_list})
            """
            del_response = athena_client.start_query_execution(
                QueryString=delete_cat_query,
                QueryExecutionContext={'Database': GLUE_DATABASE},
                ResultConfiguration={'OutputLocation': f's3://{ATHENA_RESULTS_BUCKET}/query-results/'},
                WorkGroup=ATHENA_WORKGROUP
            )
            wait_for_query_completion(del_response['QueryExecutionId'])
            
            cat_values = ','.join(
                f"('{pid}', '{cat_id}', {is_primary})"
                for pid, cat_id, is_primary in all_categories
            )
            insert_cat_query = f"""
            INSERT INTO "{GLUE_DATABASE}".product_category (product_id, category_id, is_primary)
            VALUES {cat_values}
            """
            athena_client.start_query_execution(
                QueryString=insert_cat_query,
                QueryExecutionContext={'Database': GLUE_DATABASE},
                ResultConfiguration={'OutputLocation': f's3://{ATHENA_RESULTS_BUCKET}/query-results/'},
                WorkGroup=ATHENA_WORKGROUP
            )
        
        update_cache_version('products_list')

        return create_response(200, {
            'message': f'Successfully processed {len(products)} corrections',
            'updated_count': len(products),
            'total_rows': len(products) + len(errors),
            'errors': errors if errors else None
        })
        
    except Exception as e:
        print(f"Error uploading corrections: {e}")
        import traceback
        traceback.print_exc()
        return create_response(500, {'error': f'Failed to upload corrections: {str(e)}'})


def trigger_reprocessing(body: Dict) -> Dict[str, Any]:
    """Trigger DQ revalidation via Step Functions (ETL → Tier 1 DQ → Tier 2 DQ)"""
    
    try:
        print(f"Triggering DQ revalidation via Step Functions...")
        
        import boto3
        sfn_client = boto3.client('stepfunctions')
        
        execution_name = f"manual-revalidation-{int(time.time())}"
        
        response = sfn_client.start_execution(
            stateMachineArn=ETL_WORKFLOW_ARN,
            name=execution_name,
            input='{"skip_etl": true}'
        )
        
        print(f"Step Functions execution started: {response['executionArn']}")
        
        region = os.environ.get('AWS_DEFAULT_REGION', os.environ.get('AWS_REGION', 'us-east-1'))
        console_url = f"https://{region}.console.aws.amazon.com/states/home?region={region}#/executions/details/{response['executionArn']}"
        
        update_cache_version('products_list')

        return create_response(200, {
            'message': 'DQ revalidation pipeline started (Managed DQ → Custom DQ)',
            'execution_arn': response['executionArn'],
            'console_url': console_url
        })
        
    except Exception as e:
        print(f"Error triggering DQ revalidation: {e}")
        import traceback
        traceback.print_exc()
        return create_response(500, {'error': f'Failed to trigger revalidation: {str(e)}'})


def execute_athena_query(query: str, params: list = None) -> list:
    """Execute Athena query with result caching and optional parameterised execution.
    
    Args:
        query: SQL query string. Use ? placeholders for parameterised values.
        params: Optional list of string parameter values matching ? placeholders.
    """
    
    try:
        # Generate query hash for caching (include params for uniqueness)
        cache_key_str = query + (json.dumps(params) if params else '')
        query_hash = generate_query_hash(cache_key_str)
        
        # Check if we have a cached execution ID
        cached_execution_id = get_cached_execution_id(query_hash)
        
        if cached_execution_id:
            # Reuse existing Athena execution results
            query_execution_id = cached_execution_id
            print(f"♻️  Reusing Athena execution: {query_execution_id}")
        else:
            # Build execution kwargs
            exec_kwargs = {
                'QueryString': query,
                'QueryExecutionContext': {'Database': GLUE_DATABASE},
                'ResultConfiguration': {'OutputLocation': f's3://{ATHENA_RESULTS_BUCKET}/query-results/'},
                'WorkGroup': ATHENA_WORKGROUP,
                'ResultReuseConfiguration': {
                    'ResultReuseByAgeConfiguration': {
                        'Enabled': True,
                        'MaxAgeInMinutes': 5
                    }
                }
            }
            if params:
                exec_kwargs['ExecutionParameters'] = [str(p) for p in params]
            
            response = athena_client.start_query_execution(**exec_kwargs)
            
            query_execution_id = response['QueryExecutionId']
            print(f"🚀 Started new Athena query: {query_execution_id}")
            
            # Cache the execution ID for future reuse
            cache_execution_id(query_hash, query_execution_id)
        
        # Wait for query to complete with proper polling
        max_attempts = 30  # 30 seconds max
        attempt = 0
        
        while attempt < max_attempts:
            result = athena_client.get_query_execution(QueryExecutionId=query_execution_id)
            status = result['QueryExecution']['Status']['State']
            
            if status == 'SUCCEEDED':
                break
            elif status in ['FAILED', 'CANCELLED']:
                error_reason = result['QueryExecution']['Status'].get('StateChangeReason', 'Unknown error')
                print(f"Query failed: {error_reason}")
                return []
            
            import time
            time.sleep(1)
            attempt += 1
        
        if attempt >= max_attempts:
            print("Query timed out")
            return []
        
        # Get query results
        result = athena_client.get_query_results(QueryExecutionId=query_execution_id)
        
        # Parse results
        if 'ResultSet' not in result or 'Rows' not in result['ResultSet']:
            print("No result set returned")
            return []
            
        columns = [col['Label'] for col in result['ResultSet']['ResultSetMetadata']['ColumnInfo']]
        rows = []
        
        # Skip header row (index 0)
        for row in result['ResultSet']['Rows'][1:]:
            row_data = {}
            for i, col_name in enumerate(columns):
                if i < len(row['Data']):
                    value = row['Data'][i].get('VarCharValue', '')
                    row_data[col_name] = value
                else:
                    row_data[col_name] = ''
            rows.append(row_data)
        
        print(f"Query returned {len(rows)} rows")
        return rows
        
    except Exception as e:
        print(f"Error executing Athena query: {str(e)}")
        print(f"Query was: {query}")
        return []


def create_response(status_code: int, body: Dict[str, Any], invalidate_cache: bool = False) -> Dict[str, Any]:
    """Create API Gateway response with optional cache invalidation"""
    
    headers = {
        'Content-Type': 'application/json',
        'Access-Control-Allow-Origin': '*',
        'Access-Control-Allow-Headers': 'Content-Type,X-Amz-Date,Authorization,X-Api-Key,X-Amz-Security-Token,Cache-Control',
        'Access-Control-Allow-Methods': 'GET,POST,PUT,DELETE,OPTIONS'
    }
    
    # Signal to client that cache should be invalidated
    # Client should add ?_t=timestamp to next GET request
    if invalidate_cache:
        headers['X-Cache-Invalidate'] = 'true'
        headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    
    return {
        'statusCode': status_code,
        'headers': headers,
        'body': json.dumps(body)
    }