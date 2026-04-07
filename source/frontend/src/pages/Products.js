import React, { useState, useEffect } from 'react';
import {
  Box,
  Typography,
  Button,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Paper,
  CircularProgress,
  Alert,
  TextField,
  InputAdornment,
  IconButton,
  Chip,
  MenuItem,
  Grid
} from '@mui/material';
import {
  Add as AddIcon,
  Search as SearchIcon,
  Edit as EditIcon,
  Delete as DeleteIcon
} from '@mui/icons-material';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { apiService } from '../services/api';

function Products() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const [products, setProducts] = useState([]);
  const [categories, setCategories] = useState([]);
  const [selectedCategory, setSelectedCategory] = useState('');
  const [statusFilter, setStatusFilter] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [searchTerm, setSearchTerm] = useState('');
  const [total, setTotal] = useState(0);

  useEffect(() => {
    // Get status filter from URL parameters
    const status = searchParams.get('status');
    if (status && status !== 'all') {
      setStatusFilter(status);
      loadProducts('', '', status);
    } else {
      loadProducts();
    }
    loadCategories();
  }, [searchParams]);

  const loadCategories = async () => {
    try {
      const response = await apiService.getCategories(1); // Level 1 categories
      setCategories(response.categories || []);
    } catch (err) {
      console.error('Error loading categories:', err);
    }
  };

  const loadProducts = async (search = '', category = '', status = '') => {
    try {
      setLoading(true);
      setError(null);
      
      const params = { limit: 50 };
      if (search) params.search = search;
      if (category) params.category = category;
      if (status) params.status = status;
      
      const response = await apiService.getProducts(params);
      
      setProducts(response.products || []);
      setTotal(response.total || 0);
    } catch (err) {
      console.error('Error loading products:', err);
      setError('Failed to load products. Please try again.');
    } finally {
      setLoading(false);
    }
  };

  const handleSearch = () => {
    loadProducts(searchTerm, selectedCategory, statusFilter);
  };

  const handleCategoryChange = (event) => {
    const category = event.target.value;
    setSelectedCategory(category);
    loadProducts(searchTerm, category);
  };

  const handleDelete = async (productId) => {
    if (window.confirm('Are you sure you want to delete this product?')) {
      try {
        await apiService.deleteProduct(productId);
        loadProducts(searchTerm); // Reload the list
      } catch (err) {
        console.error('Error deleting product:', err);
        const status = err?.httpStatus || err?.response?.statusCode || err?.response?.status;
        if (status === 403) {
          setError('Access denied — your account does not have delete permissions. Contact an administrator to be added to the Editors group.');
        } else {
          setError('Failed to delete product. Please try again.');
        }
      }
    }
  };

  const formatPrice = (price, currency = 'AUD') => {
    if (!price) return 'N/A';
    return new Intl.NumberFormat('en-AU', {
      style: 'currency',
      currency: currency
    }).format(parseFloat(price));
  };

  if (loading) {
    return (
      <Box display="flex" justifyContent="center" alignItems="center" minHeight="400px">
        <CircularProgress />
      </Box>
    );
  }

  return (
    <Box>
      <Box display="flex" justifyContent="space-between" alignItems="center" mb={3}>
        <Typography variant="h4">Products</Typography>
        <Button
          variant="contained"
          startIcon={<AddIcon />}
          onClick={() => navigate('/products/new')}
        >
          Add Product
        </Button>
      </Box>

      {error && (
        <Alert severity="error" sx={{ mb: 2 }}>
          {error}
        </Alert>
      )}

      {/* Search and Filter Controls */}
      <Grid container spacing={2} sx={{ mb: 3 }}>
        <Grid item xs={12} md={4}>
          <TextField
            fullWidth
            placeholder="Search products..."
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
            onKeyPress={(e) => e.key === 'Enter' && handleSearch()}
            InputProps={{
              endAdornment: (
                <InputAdornment position="end">
                  <IconButton onClick={handleSearch}>
                    <SearchIcon />
                  </IconButton>
                </InputAdornment>
              ),
            }}
          />
        </Grid>
        <Grid item xs={12} md={4}>
          <TextField
            fullWidth
            select
            label="Category"
            value={selectedCategory}
            onChange={handleCategoryChange}
          >
            <MenuItem value="">All Categories</MenuItem>
            {categories.map((category) => (
              <MenuItem key={category.category_id} value={category.name}>
                {category.name}
              </MenuItem>
            ))}
          </TextField>
        </Grid>
        <Grid item xs={12} md={4}>
          <TextField
            fullWidth
            select
            label="Status"
            value={statusFilter}
            onChange={(e) => {
              setStatusFilter(e.target.value);
              loadProducts(searchTerm, selectedCategory, e.target.value);
            }}
          >
            <MenuItem value="">All Status</MenuItem>
            <MenuItem value="active">In Stock</MenuItem>
            <MenuItem value="outofstock">Out of Stock</MenuItem>
            <MenuItem value="draft">Draft</MenuItem>
            <MenuItem value="discontinued">Discontinued</MenuItem>
          </TextField>
        </Grid>
      </Grid>

      <Typography variant="body2" color="textSecondary" gutterBottom>
        Showing {products.length} of {total} products
      </Typography>

      <TableContainer component={Paper}>
        <Table>
          <TableHead>
            <TableRow>
              <TableCell>Product Name</TableCell>
              <TableCell>Author</TableCell>
              <TableCell>ISBN/EAN</TableCell>
              <TableCell>Price</TableCell>
              <TableCell>Status</TableCell>
              <TableCell>DQ Status</TableCell>
              <TableCell>Actions</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {products.length === 0 ? (
              <TableRow>
                <TableCell colSpan={6} align="center">
                  <Typography variant="body2" color="textSecondary">
                    No products found. {searchTerm ? 'Try a different search term.' : 'Add your first product to get started.'}
                  </Typography>
                </TableCell>
              </TableRow>
            ) : (
              products.map((product, index) => (
                <TableRow key={`${product.product_id}-${index}`}>
                  <TableCell>
                    <Typography variant="body2" fontWeight="medium">
                      {product.base_name || 'Unnamed Product'}
                    </Typography>
                    {product.short_description && (
                      <Typography variant="caption" color="textSecondary">
                        {product.short_description.substring(0, 100)}...
                      </Typography>
                    )}
                  </TableCell>
                  <TableCell>{product.attributes?.author || 'N/A'}</TableCell>
                  <TableCell>{product.upc_ean || 'N/A'}</TableCell>
                  <TableCell>
                    {formatPrice(product.base_price, product.currency_code)}
                  </TableCell>
                  <TableCell>
                    <Chip
                      label={product.status || 'unknown'}
                      color={product.status === 'active' ? 'success' : 'default'}
                      size="small"
                    />
                  </TableCell>
                  <TableCell>
                    <Chip
                      label={product.dq_status || 'unknown'}
                      color={
                        product.dq_status === 'passed' ? 'success' :
                        product.dq_status === 'failed' ? 'error' :
                        product.dq_status === 'pending' ? 'warning' :
                        'default'
                      }
                      size="small"
                    />
                  </TableCell>
                  <TableCell>
                    <IconButton
                      size="small"
                      onClick={() => navigate(`/products/edit/${product.product_id}`, { state: { product } })}
                    >
                      <EditIcon />
                    </IconButton>
                    <IconButton
                      size="small"
                      onClick={() => handleDelete(product.product_id)}
                      color="error"
                    >
                      <DeleteIcon />
                    </IconButton>
                  </TableCell>
                </TableRow>
              ))
            )}
          </TableBody>
        </Table>
      </TableContainer>
    </Box>
  );
}

export default Products;
