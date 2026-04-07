import { get, post, put, del } from 'aws-amplify/api';
import { fetchAuthSession } from 'aws-amplify/auth';

const API_NAME = 'PimAPI';

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
  // Cache buster timestamp - updated after mutations
  _cacheBuster: Date.now(),
  
  async getProducts(params = {}) {
    try {
      // Add cache buster to force fresh data after mutations
      const allParams = {
        ...params,
        _t: this._cacheBuster
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
      
      // Update cache buster to force fresh data on next GET
      this._cacheBuster = Date.now();
      console.log('🔄 Cache invalidated after create');
      
      return result;
    } catch (error) {
      console.error('Error creating product:', error);
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
      
      // Update cache buster to force fresh data on next GET
      this._cacheBuster = Date.now();
      console.log('🔄 Cache invalidated after update');
      
      return result;
    } catch (error) {
      console.error('Error updating product:', error);
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
      
      // Update cache buster to force fresh data on next GET
      this._cacheBuster = Date.now();
      console.log('🔄 Cache invalidated after delete');
      
      return result;
    } catch (error) {
      console.error('Error deleting product:', error);
      throw error;
    }
  },

  async getCategories(level = 1, parentId = '') {
    try {
      const params = new URLSearchParams();
      if (level) params.append('level', level.toString());
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
