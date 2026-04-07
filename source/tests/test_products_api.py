#!/usr/bin/env python3
"""
Comprehensive API Tests for PIM Products API
Tests CRUD operations with Cognito authentication
"""

import requests
import boto3
import json
import sys
import os
from typing import Dict, Optional

# Configuration
API_BASE_URL = os.environ.get("API_BASE_URL", "https://your-api-gateway-url.execute-api.ap-southeast-2.amazonaws.com/development")
USER_POOL_ID = os.environ.get("USER_POOL_ID", "ap-southeast-2_XXXXXXXXX")
CLIENT_ID = os.environ.get("CLIENT_ID", "REPLACE_ME")
USERNAME = os.environ.get("TEST_USERNAME", "testuser")
PASSWORD = os.environ.get("TEST_PASSWORD", "REPLACE_ME")  # pragma: allowlist secret
REGION = os.environ.get("AWS_REGION", "us-east-1")


class CognitoAuth:
    """Handle Cognito authentication"""
    
    def __init__(self):
        self.client = boto3.client('cognito-idp', region_name=REGION)
        self.token = None
    
    def authenticate(self) -> str:
        """Get JWT token from Cognito"""
        try:
            response = self.client.initiate_auth(
                ClientId=CLIENT_ID,
                AuthFlow='USER_PASSWORD_AUTH',
                AuthParameters={
                    'USERNAME': USERNAME,
                    'PASSWORD': PASSWORD
                }
            )
            self.token = response['AuthenticationResult']['IdToken']
            print("✅ Authentication successful")
            return self.token
        except Exception as e:
            print(f"❌ Authentication failed: {e}")
            sys.exit(1)
    
    def get_headers(self) -> Dict[str, str]:
        """Get headers with auth token"""
        if not self.token:
            self.authenticate()
        return {
            'Authorization': self.token,
            'Content-Type': 'application/json'
        }


class ProductAPITests:
    """Test suite for Products API"""
    
    def __init__(self):
        self.auth = CognitoAuth()
        self.base_url = API_BASE_URL
        self.test_product_id = None
        self.passed = 0
        self.failed = 0
    
    def test_list_products(self):
        """Test GET /api/v1/products - List all products"""
        print("\n📋 Test: List Products")
        
        url = f"{self.base_url}/api/v1/products"
        response = requests.get(url, headers=self.auth.get_headers(), timeout=30)
        
        if response.status_code == 200:
            data = response.json()
            print(f"✅ Status: {response.status_code}")
            print(f"   Total products: {data.get('total', 0)}")
            print(f"   Returned: {len(data.get('products', []))}")
            
            # Display first product if available
            if data.get('products'):
                product = data['products'][0]
                print(f"   Sample product: {product.get('upc_ean')} - {product.get('base_name')}")
                print(f"   Attributes: {list(product.get('attributes', {}).keys())}")
                self.test_product_id = product.get('product_id')
            
            self.passed += 1
            return True
        else:
            print(f"❌ Status: {response.status_code}")
            print(f"   Error: {response.text}")
            self.failed += 1
            return False
    
    def test_get_product_by_id(self):
        """Test GET /api/v1/products/{id} - Get specific product"""
        print("\n🔍 Test: Get Product by ID")
        
        if not self.test_product_id:
            print("⚠️  Skipped: No product ID available")
            return
        
        url = f"{self.base_url}/api/v1/products/{self.test_product_id}"
        response = requests.get(url, headers=self.auth.get_headers(), timeout=30)
        
        if response.status_code == 200:
            data = response.json()
            print(f"✅ Status: {response.status_code}")
            print(f"   Product ID: {data.get('product', {}).get('product_id')}")
            print(f"   UPC/EAN: {data.get('product', {}).get('upc_ean')}")
            print(f"   Name: {data.get('product', {}).get('base_name')}")
            print(f"   Status: {data.get('product', {}).get('status')}")
            print(f"   Attributes: {list(data.get('product', {}).get('attributes', {}).keys())}")
            print(f"   Categories: {len(data.get('product', {}).get('categories', []))}")
            print(f"   Media: {len(data.get('product', {}).get('media_assets', []))}")
            self.passed += 1
            return True
        else:
            print(f"❌ Status: {response.status_code}")
            print(f"   Error: {response.text}")
            self.failed += 1
            return False
        
    def test_update_product(self):
        """Test PUT /api/v1/products/{id} - Update product"""
        print("\n✏️  Test: Update Product")
        
        if not self.test_product_id:
            print("⚠️  Skipped: No product ID available")
            return
        
        update_data = {
            "base_name": "Updated Test Book",
            "base_price": "34.99",
            "stock_quantity": 150,
            "attributes": {
                "author": "Updated Author",
                "page_count": "350"
            }
        }
        
        url = f"{self.base_url}/api/v1/products/{self.test_product_id}"
        response = requests.put(
            url,
            headers=self.auth.get_headers(),
            json=update_data,
            timeout=30
        )
        
        if response.status_code == 200:
            data = response.json()
            print(f"✅ Status: {response.status_code}")
            print(f"   Updated Product ID: {data.get('product_id')}")
            print(f"   New Name: {data.get('base_name')}")
            print(f"   New Price: {data.get('base_price')}")
            self.passed += 1
            return True
        else:
            print(f"❌ Status: {response.status_code}")
            print(f"   Error: {response.text}")
            self.failed += 1
            return False
    
    def test_search_products(self):
        """Test GET /api/v1/products?search=term - Search products"""
        print("\n🔎 Test: Search Products")
        
        url = f"{self.base_url}/api/v1/products?search=book"
        response = requests.get(url, headers=self.auth.get_headers(), timeout=30)
        
        if response.status_code == 200:
            data = response.json()
            print(f"✅ Status: {response.status_code}")
            print(f"   Search results: {len(data.get('products', []))}")
            self.passed += 1
            return True
        else:
            print(f"❌ Status: {response.status_code}")
            print(f"   Error: {response.text}")
            self.failed += 1
            return False
    
    def test_filter_by_status(self):
        """Test GET /api/v1/products?status=active - Filter by status"""
        print("\n🎯 Test: Filter Products by Status")
        
        url = f"{self.base_url}/api/v1/products?status=active"
        response = requests.get(url, headers=self.auth.get_headers(), timeout=30)
        
        if response.status_code == 200:
            data = response.json()
            print(f"✅ Status: {response.status_code}")
            print(f"   Active products: {len(data.get('products', []))}")
            self.passed += 1
            return True
        else:
            print(f"❌ Status: {response.status_code}")
            print(f"   Error: {response.text}")
            self.failed += 1
            return False
        
    def run_all_tests(self):
        """Run all test cases"""
        print("=" * 60)
        print("🧪 PIM Products API Test Suite")
        print("=" * 60)
        
        # Authenticate first
        self.auth.authenticate()
        
        # Run tests in order
        self.test_list_products()
        self.test_get_product_by_id()
        self.test_search_products()
        self.test_filter_by_status()
        self.test_update_product()
        
        # Summary
        print("\n" + "=" * 60)
        print("📊 Test Summary")
        print("=" * 60)
        print(f"✅ Passed: {self.passed}")
        print(f"❌ Failed: {self.failed}")
        print(f"📈 Success Rate: {(self.passed / (self.passed + self.failed) * 100):.1f}%")
        print("=" * 60)
        
        return self.failed == 0


if __name__ == "__main__":
    tests = ProductAPITests()
    success = tests.run_all_tests()
    sys.exit(0 if success else 1)
