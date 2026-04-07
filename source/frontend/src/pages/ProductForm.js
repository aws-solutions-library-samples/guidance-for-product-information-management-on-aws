import React, { useState, useEffect } from 'react';
import {
  Box,
  Typography,
  TextField,
  Button,
  Grid,
  Paper,
  MenuItem,
  Alert,
  CircularProgress,
} from '@mui/material';
import { useNavigate, useParams, useLocation } from 'react-router-dom';
import { apiService } from '../services/api';

const statuses = [
  { value: 'active', label: 'In Stock' },
  { value: 'outofstock', label: 'Out of Stock' },
  { value: 'discontinued', label: 'Discontinued' },
  { value: 'draft', label: 'Draft' },
];

function ProductForm() {
  const navigate = useNavigate();
  const { id } = useParams();
  const location = useLocation();
  const isEdit = Boolean(id);
  const productFromState = location.state?.product;

  const [categories, setCategories] = useState([]);
  const [selectedCategory, setSelectedCategory] = useState('');
  const [editingCategory, setEditingCategory] = useState(false);
  const [formData, setFormData] = useState({
    title: '',
    author: '',
    isbn: '',
    brand_id: '',
    publisher: '',
    publication_date: '',
    price: '',
    pages: '',
    language: 'English',
    binding: '',
    description: '',
    stock_quantity: '',
    status: 'active',
  });

  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [success, setSuccess] = useState(null);

  useEffect(() => {
    loadCategories();
    if (isEdit) {
      if (productFromState) {
        // Use product data from navigation state
        loadProductData(productFromState);
      } else {
        // Fallback to API call if no state (e.g., direct URL access)
        fetchProduct();
      }
    }
  }, [id, isEdit, productFromState]);

  const loadProductData = (product) => {
    const attributes = product.attributes || {};
    
    setFormData({
      title: product.base_name || '',
      author: product.attributes?.author || '',
      isbn: product.upc_ean || product.attributes?.isbn13 || '',
      brand_id: product.brand_id || '',
      publisher: product.brand_name || product.attributes?.publisher || '',
      publication_date: attributes.publication_date ? 
        attributes.publication_date.split('T')[0] : '',
      price: product.base_price || '',
      pages: attributes.page_count || attributes.pages || '',
      language: attributes.language || 'English',
      binding: attributes.binding || '',
      description: product.short_description || '',
      stock_quantity: product.stock_quantity || '',
      status: product.status?.toLowerCase() || 'active',
    });
    
    // Set primary category if exists
    if (product.categories && product.categories.length > 0) {
      console.log('Product categories:', product.categories);
      const primaryCat = product.categories.find(c => c.is_primary === 'true' || c.is_primary === true) 
        || product.categories[0];
      console.log('Selected category:', primaryCat);
      setSelectedCategory(primaryCat.category_id);
    } else {
      console.log('No categories found for product');
    }
  };

  const loadCategories = async () => {
    try {
      // Load all categories
      const response = await apiService.getCategories();
      console.log('Loaded categories:', response.categories);
      setCategories(response.categories || []);
    } catch (err) {
      console.error('Error loading categories:', err);
    }
  };

  const fetchProduct = async () => {
    try {
      setLoading(true);
      const data = await apiService.getProduct(id);
      console.log('API Response:', data); // Debug log
      
      // Handle both possible response structures
      const product = data.product || (data.products && data.products[0]) || data;
      
      if (product) {
        // Map API fields to form fields, including attributes
        const attributes = product.attributes || {};
        
        setFormData({
          title: product.base_name || '',
          author: product.attributes?.author || '',
          isbn: product.upc_ean || product.attributes?.isbn13 || '',
          brand_id: product.brand_id || '',
          publisher: product.brand_name || product.attributes?.publisher || '',
          publication_date: attributes.publication_date ? 
            attributes.publication_date.split('T')[0] : '',
          price: product.base_price || '',
          pages: attributes.page_count || attributes.pages || '',
          language: attributes.language || 'English',
          binding: attributes.binding || '',
          description: product.long_description || product.short_description || '',
          stock_quantity: product.stock_quantity || '',
          status: product.status?.toLowerCase() || 'active',
        });
        
        // Set primary category if exists
        if (product.categories && product.categories.length > 0) {
          console.log('Product categories:', product.categories);
          const primaryCat = product.categories.find(c => c.is_primary === 'true' || c.is_primary === true) 
            || product.categories[0];
          console.log('Selected category:', primaryCat);
          setSelectedCategory(primaryCat.category_id);
        } else {
          console.log('No categories found for product');
        }
      } else {
        setError('Product not found');
      }
    } catch (err) {
      console.error('Error fetching product:', err);
      setError('Failed to load product details');
    } finally {
      setLoading(false);
    }
  };

  const handleChange = (e) => {
    const { name, value } = e.target;
    setFormData(prev => ({
      ...prev,
      [name]: value,
    }));
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError(null);
    setSuccess(null);

    try {
      setLoading(true);

      const productData = {
        ...formData,
        category_id: selectedCategory,
        price: parseFloat(formData.price),
        pages: formData.pages ? parseInt(formData.pages) : undefined,
        stock_quantity: parseInt(formData.stock_quantity),
      };

      if (isEdit) {
        await apiService.updateProduct(id, productData);
        setSuccess('Product updated successfully! It will be validated in the next DQ run.');
      } else {
        await apiService.createProduct(productData);
        setSuccess('Product created successfully!');
      }

      setTimeout(() => {
        navigate('/products');
      }, 2000);
    } catch (err) {
      console.error('Error saving product:', err);
      // Check for 403 — RBAC denial from backend
      const status = err?.httpStatus || err?.response?.statusCode || err?.response?.status;
      if (status === 403) {
        setError('Access denied — your account does not have edit permissions. Contact an administrator to be added to the Editors group.');
      } else {
        setError(isEdit ? 'Failed to update product' : 'Failed to create product');
      }
    } finally {
      setLoading(false);
    }
  };



  if (loading && isEdit && !formData.title) {
    return (
      <Box display="flex" justifyContent="center" alignItems="center" minHeight="400px">
        <CircularProgress />
      </Box>
    );
  }

  return (
    <Box>
      <Typography variant="h4" gutterBottom>
        {isEdit ? 'Edit Product' : 'Add New Product'}
      </Typography>

      {error && (
        <Alert severity="error" sx={{ mb: 2 }}>
          {error}
        </Alert>
      )}

      {success && (
        <Alert severity="success" sx={{ mb: 2 }}>
          {success}
        </Alert>
      )}

      <Paper sx={{ p: 3 }}>
        <form onSubmit={handleSubmit}>
          <Grid container spacing={3}>
            <Grid item xs={12} md={6}>
              <TextField
                fullWidth
                label="Title"
                name="title"
                value={formData.title}
                onChange={handleChange}
                required
              />
            </Grid>
            <Grid item xs={12} md={6}>
              <TextField
                fullWidth
                label="Author"
                name="author"
                value={formData.author}
                onChange={handleChange}
                required
              />
            </Grid>
            <Grid item xs={12} md={6}>
              <TextField
                fullWidth
                label="ISBN"
                name="isbn"
                value={formData.isbn}
                onChange={handleChange}
                required
                helperText="ISBN-10 or ISBN-13 format"
              />
            </Grid>
            <Grid item xs={12} md={6}>
              <TextField
                fullWidth
                label="Publisher"
                name="publisher"
                value={formData.publisher}
                onChange={handleChange}
              />
            </Grid>
            <Grid item xs={12} md={6}>
              {!editingCategory ? (
                <Box display="flex" alignItems="center" gap={1}>
                  <TextField
                    fullWidth
                    label="Category"
                    value={categories.find(c => c.category_id === selectedCategory)?.name || 'Not set'}
                    disabled
                  />
                  <Button 
                    variant="outlined" 
                    onClick={() => setEditingCategory(true)}
                    sx={{ minWidth: '80px' }}
                  >
                    Edit
                  </Button>
                </Box>
              ) : (
                <Box display="flex" alignItems="center" gap={1}>
                  <TextField
                    fullWidth
                    select
                    label="Category"
                    name="category"
                    value={selectedCategory}
                    onChange={(e) => setSelectedCategory(e.target.value)}
                    required
                  >
                    {categories.length === 0 ? (
                      <MenuItem value="" disabled>Loading categories...</MenuItem>
                    ) : (
                      categories.map((category) => (
                        <MenuItem key={category.category_id} value={category.category_id}>
                          {category.name}
                        </MenuItem>
                      ))
                    )}
                  </TextField>
                  <Button 
                    variant="outlined" 
                    onClick={() => setEditingCategory(false)}
                    sx={{ minWidth: '80px' }}
                  >
                    Done
                  </Button>
                </Box>
              )}
            </Grid>
            <Grid item xs={12} md={6}>
              <TextField
                fullWidth
                label="Publication Date"
                name="publication_date"
                type="date"
                value={formData.publication_date}
                onChange={handleChange}
                InputLabelProps={{
                  shrink: true,
                }}
              />
            </Grid>
            <Grid item xs={12} md={4}>
              <TextField
                fullWidth
                label="Price"
                name="price"
                type="number"
                value={formData.price}
                onChange={handleChange}
                required
                inputProps={{ min: 0, step: 0.01 }}
                helperText="Price in AUD"
              />
            </Grid>
            <Grid item xs={12} md={4}>
              <TextField
                fullWidth
                label="Pages"
                name="pages"
                type="number"
                value={formData.pages}
                onChange={handleChange}
                inputProps={{ min: 1 }}
              />
            </Grid>
            <Grid item xs={12} md={4}>
              <TextField
                fullWidth
                label="Stock Quantity"
                name="stock_quantity"
                type="number"
                value={formData.stock_quantity}
                onChange={handleChange}
                required
                inputProps={{ min: 0 }}
              />
            </Grid>
            <Grid item xs={12} md={4}>
              <TextField
                fullWidth
                label="Language"
                name="language"
                value={formData.language}
                onChange={handleChange}
              />
            </Grid>
            <Grid item xs={12} md={4}>
              <TextField
                fullWidth
                select
                label="Binding"
                name="binding"
                value={formData.binding}
                onChange={handleChange}
              >
                <MenuItem value="">Not set</MenuItem>
                <MenuItem value="Hardcover">Hardcover</MenuItem>
                <MenuItem value="Paperback">Paperback</MenuItem>
                <MenuItem value="Ebook">Ebook</MenuItem>
                <MenuItem value="Audiobook">Audiobook</MenuItem>
              </TextField>
            </Grid>
            <Grid item xs={12} md={4}>
              <TextField
                fullWidth
                select
                label="Status"
                name="status"
                value={formData.status}
                onChange={handleChange}
                required
              >
                {statuses.map((status) => (
                  <MenuItem key={status.value} value={status.value}>
                    {status.label}
                  </MenuItem>
                ))}
              </TextField>
            </Grid>
            <Grid item xs={12}>
              <TextField
                fullWidth
                label="Description"
                name="description"
                value={formData.description}
                onChange={handleChange}
                multiline
                rows={4}
                helperText="Product summary or description"
              />
            </Grid>
          </Grid>

          <Box mt={3} display="flex" gap={2}>
            <Button
              type="submit"
              variant="contained"
              disabled={loading}
            >
              {loading ? <CircularProgress size={24} /> : (isEdit ? 'Update Product' : 'Create Product')}
            </Button>
            

            
            <Button
              variant="outlined"
              onClick={() => navigate('/products')}
            >
              Cancel
            </Button>
          </Box>
        </form>
      </Paper>
    </Box>
  );
}

export default ProductForm;