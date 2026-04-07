import { get, post, put, del } from 'aws-amplify/api';
import { fetchAuthSession } from 'aws-amplify/auth';

const API_NAME = 'PimAPI';

// Helper to extract HTTP status from Amplify REST errors
const extractErrorStatus = (error) => {
  // Amplify v6 may put status in different places depending on error type
  return error?.response?.statusCode 
    || error?.response?.status 
    || error?.$metadata?.httpStatusCode
    || null;
};

const getAuthHeaders = async () => {
  try {
    const session = await fetchAuthSession();
    const token = session.tokens?.idToken?.toString();
    return token ? { Authorization: token } : {};
  } catch (error) {
    console.error('Error getting auth token:', error);
    return {};
  }
};

export const apiService = {
  _cacheBuster: null,
  _cachePromise: null,
  
  async getCacheVersion() {
    // Fetch cache version from DynamoDB via API
    if (!this._cachePromise) {
      this._cachePromise = this._fetchCacheVersion();
    }
    return this._cachePromise;
  },
  
  async _fetchCacheVersion() {
    try {
      const restOperation = get({
        apiName: API_NAME,
        path: '/api/v1/cache-version',
        options: {
          headers: await getAuthHeaders()
        }
      });
      
      const { body } = await restOperation.response;
      const data = await body.json();
      this._cacheBuster = data.cache_version;
      console.log('📌 Global cache version:', data.cache_version);
      return data.cache_version;
    } catch (error) {
      console.error('Error fetching cache version:', error);
      return Date.now();
    }
  },
  
  async getProducts(params = {}) {
    try {
      // Get global cache version from DynamoDB
      const cacheVersion = await this.getCacheVersion();
      
      const allParams = {
        ...params,
        _t: cacheVersion
      };
      
      const queryString = new URLSearchParams(allParams).toString();
      const url = queryString ? `/api/v1/products?${queryString}` : '/api/v1/products';
      
      const restOperation = get({
        apiName: API_NAME,
        path: url,
        options: {
          headers: await getAuthHeaders()
        }
      });
      
      const { body } = await restOperation.response;
      const data = await body.json();
      return data;
    } catch (error) {
      console.error('Error fetching products:', error);
      throw error;
    }
  },

  async getProductStats() {
    try {
      const restOperation = get({
        apiName: API_NAME,
        path: '/api/v1/stats',
        options: {
          headers: await getAuthHeaders()
        }
      });
      
      const { body } = await restOperation.response;
      const data = await body.json();
      return data;
    } catch (error) {
      console.error('Error fetching product stats:', error);
      throw error;
    }
  },

  async uploadSampleData() {
    try {
      const restOperation = post({
        apiName: API_NAME,
        path: '/api/v1/upload-sample-data',
        options: {
          headers: await getAuthHeaders()
        }
      });
      
      const { body } = await restOperation.response;
      const data = await body.json();
      return data;
    } catch (error) {
      console.error('Error uploading sample data:', error);
      throw error;
    }
  },

  async uploadCustomData(jsonData) {
    try {
      const restOperation = post({
        apiName: API_NAME,
        path: '/api/v1/upload-custom-data',
        options: {
          headers: await getAuthHeaders(),
          body: jsonData
        }
      });
      
      const { body } = await restOperation.response;
      const data = await body.json();
      return data;
    } catch (error) {
      console.error('Error uploading custom data:', error);
      throw error;
    }
  },

  async getQueue(queueType, params = {}) {
    try {
      const queryString = new URLSearchParams(params).toString();
      const url = `/api/v1/queues/${queueType}${queryString ? '?' + queryString : ''}`;
      
      const restOperation = get({
        apiName: API_NAME,
        path: url,
        options: {
          headers: await getAuthHeaders()
        }
      });
      
      const { body } = await restOperation.response;
      const data = await body.json();
      return data;
    } catch (error) {
      console.error("Error fetching queue:", queueType, error);
      throw error;
    }
  },

  async quickSearch(searchTerm, params = {}) {
    try {
      const allParams = { q: searchTerm, ...params };
      const queryString = new URLSearchParams(allParams).toString();
      const url = `/api/v1/products/search?${queryString}`;
      
      const restOperation = get({
        apiName: API_NAME,
        path: url,
        options: {
          headers: await getAuthHeaders()
        }
      });
      
      const { body } = await restOperation.response;
      const data = await body.json();
      return data;
    } catch (error) {
      console.error('Error in quick search:', error);
      throw error;
    }
  },

  async advancedSearch(searchParams) {
    try {
      const restOperation = post({
        apiName: API_NAME,
        path: '/api/v1/products/search/advanced',
        options: {
          headers: await getAuthHeaders(),
          body: searchParams
        }
      });
      
      const { body } = await restOperation.response;
      const data = await body.json();
      return data;
    } catch (error) {
      console.error('Error in advanced search:', error);
      throw error;
    }
  },

  async getAnalytics(endpoint) {
    try {
      const restOperation = get({
        apiName: API_NAME,
        path: `/api/v1/analytics/${endpoint}`,
        options: {
          headers: await getAuthHeaders()
        }
      });
      
      const { body } = await restOperation.response;
      return await body.json();
    } catch (error) {
      console.error('Error fetching analytics:', error);
      throw error;
    }
  },

  async getDataQualityDashboard() {
    try {
      const restOperation = get({
        apiName: API_NAME,
        path: '/api/v1/data-quality/dashboard',
        options: {
          headers: await getAuthHeaders()
        }
      });
      
      const { body } = await restOperation.response;
      return await body.json();
    } catch (error) {
      console.error('Error fetching data quality dashboard:', error);
      throw error;
    }
  },

  async getDqRunHistory(period = '7d') {
    try {
      const restOperation = get({
        apiName: API_NAME,
        path: `/api/v1/data-quality/run-history?period=${period}`,
        options: {
          headers: await getAuthHeaders()
        }
      });
      const { body } = await restOperation.response;
      return await body.json();
    } catch (error) {
      console.error('Error fetching DQ run history:', error);
      throw error;
    }
  },

  async getFailedRecords(params = {}) {
    try {
      const queryString = new URLSearchParams(params).toString();
      const url = queryString ? `/api/v1/data-quality/failed-records?${queryString}` : '/api/v1/data-quality/failed-records';
      
      const restOperation = get({
        apiName: API_NAME,
        path: url,
        options: {
          headers: await getAuthHeaders()
        }
      });
      
      const { body } = await restOperation.response;
      return await body.json();
    } catch (error) {
      console.error('Error fetching failed records:', error);
      throw error;
    }
  },

  async exportFailedRecords() {
    try {
      const restOperation = get({
        apiName: API_NAME,
        path: '/api/v1/data-quality/export-failed',
        options: {
          headers: await getAuthHeaders()
        }
      });
      
      const { body } = await restOperation.response;
      return await body.json();
    } catch (error) {
      console.error('Error exporting failed records:', error);
      throw error;
    }
  },

  async uploadCorrections(fileContent, filename) {
    try {
      const restOperation = post({
        apiName: API_NAME,
        path: '/api/v1/data-quality/upload-corrections',
        options: {
          body: {
            file_content: fileContent,
            filename: filename
          },
          headers: {
            ...await getAuthHeaders(),
            'Content-Type': 'application/json'
          }
        }
      });
      
      const { body } = await restOperation.response;
      return await body.json();
    } catch (error) {
      console.error('Error uploading corrections:', error);
      throw error;
    }
  },

  async triggerRevalidation() {
    try {
      const restOperation = post({
        apiName: API_NAME,
        path: '/api/v1/data-quality/reprocess',
        options: {
          body: {},
          headers: {
            ...await getAuthHeaders(),
            'Content-Type': 'application/json'
          }
        }
      });
      
      const { body } = await restOperation.response;
      return await body.json();
    } catch (error) {
      console.error('Error triggering revalidation:', error);
      throw error;
    }
  },

  async getProduct(id) {
    try {
      const restOperation = get({
        apiName: API_NAME,
        path: `/api/v1/products/${id}`,
        options: {
          headers: await getAuthHeaders()
        }
      });
      
      const { body } = await restOperation.response;
      return await body.json();
    } catch (error) {
      console.error('Error fetching product:', error);
      throw error;
    }
  },

  async createProduct(productData) {
    try {
      const restOperation = post({
        apiName: API_NAME,
        path: '/api/v1/products',
        options: {
          body: productData,
          headers: {
            ...await getAuthHeaders(),
            'Content-Type': 'application/json'
          }
        }
      });
      
      const { body } = await restOperation.response;
      const result = await body.json();
      
      // Clear cached version so next request fetches new one
      this._cachePromise = null;
      console.log('🔄 Cache version invalidated globally');
      
      return result;
    } catch (error) {
      console.error('Error creating product:', error);
      error.httpStatus = extractErrorStatus(error);
      throw error;
    }
  },

  async updateProduct(id, productData) {
    try {
      const restOperation = put({
        apiName: API_NAME,
        path: `/api/v1/products/${id}`,
        options: {
          body: productData,
          headers: {
            ...await getAuthHeaders(),
            'Content-Type': 'application/json'
          }
        }
      });
      
      const { body } = await restOperation.response;
      const result = await body.json();
      
      // Clear cached version so next request fetches new one
      this._cachePromise = null;
      console.log('🔄 Cache version invalidated globally');
      
      return result;
    } catch (error) {
      console.error('Error updating product:', error);
      error.httpStatus = extractErrorStatus(error);
      throw error;
    }
  },

  async deleteProduct(id) {
    try {
      const restOperation = del({
        apiName: API_NAME,
        path: `/api/v1/products/${id}`,
        options: {
          headers: await getAuthHeaders()
        }
      });
      
      const { body } = await restOperation.response;
      const result = await body.json();
      
      // Clear cached version so next request fetches new one
      this._cachePromise = null;
      console.log('🔄 Cache version invalidated globally');
      
      return result;
    } catch (error) {
      console.error('Error deleting product:', error);
      error.httpStatus = extractErrorStatus(error);
      throw error;
    }
  },

  async getCategories(level = null, parentId = '') {
    try {
      const params = new URLSearchParams();
      if (level !== null) params.append('level', level.toString());
      if (parentId) params.append('parent_id', parentId);
      
      const queryString = params.toString();
      const url = queryString ? `/api/v1/categories?${queryString}` : '/api/v1/categories';
      
      const restOperation = get({
        apiName: API_NAME,
        path: url,
        options: {
          headers: await getAuthHeaders()
        }
      });
      
      const { body } = await restOperation.response;
      return await body.json();
    } catch (error) {
      console.error('Error fetching categories:', error);
      throw error;
    }
  }
};
