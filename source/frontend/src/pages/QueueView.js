import React, { useState, useEffect, useRef } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import {
  Box,
  Typography,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Paper,
  Button,
  Chip,
  CircularProgress,
  Alert,
  Tooltip,
  Snackbar
} from '@mui/material';
import { Download as DownloadIcon, Upload as UploadIcon, CheckCircle as CheckIcon, ArrowUpward, ArrowDownward } from '@mui/icons-material';
import { apiService } from '../services/api';

const QueueView = () => {
  const { queueType } = useParams();
  const navigate = useNavigate();
  const [products, setProducts] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [snackbar, setSnackbar] = useState({ open: false, message: '', severity: 'info' });
  const [sortAsc, setSortAsc] = useState(false);
  const [exportDone, setExportDone] = useState(false);
  const [uploadDone, setUploadDone] = useState(false);
  const fileInputRef = useRef(null);
  
  useEffect(() => {
    loadQueue();
  }, [queueType]);
  
  const loadQueue = async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await apiService.getQueue(queueType, { limit: 50 });
      setProducts(data.products || []);
    } catch (err) {
      console.error('Error loading queue:', err);
      setError('Failed to load queue data');
    } finally {
      setLoading(false);
    }
  };
  
  const handleExport = async () => {
    try {
      setSnackbar({ open: true, message: 'Generating CSV...', severity: 'info' });
      const response = await apiService.exportFailedRecords();
      if (response.download_url) {
        window.open(response.download_url, '_blank');
        setExportDone(true);
        setSnackbar({ open: true, message: `Downloaded ${response.record_count} records`, severity: 'success' });
      }
    } catch (err) {
      setSnackbar({ open: true, message: 'Export failed: ' + err.message, severity: 'error' });
    }
  };

  const handleUpload = async (event) => {
    const file = event.target.files[0];
    if (!file) return;
    try {
      setSnackbar({ open: true, message: 'Uploading corrections...', severity: 'info' });
      const content = await file.text();
      const base64Content = btoa(unescape(encodeURIComponent(content)));
      await apiService.uploadCorrections(base64Content, file.name);
      setUploadDone(true);
      setSnackbar({ open: true, message: 'Corrections uploaded successfully', severity: 'success' });
      loadQueue();
    } catch (err) {
      setSnackbar({ open: true, message: 'Upload failed: ' + err.message, severity: 'error' });
    }
    event.target.value = '';
  };

  const getQueueTitle = () => {
    const titles = {
      'dq-failed': 'Data Quality Failed',
      'drafts': 'Draft Products',
      'low-stock': 'Low Stock',
      'recent': 'Recently Modified'
    };
    return titles[queueType] || 'Queue';
  };
  
  const getQueueDescription = () => {
    const descriptions = {
      'dq-failed': 'Products that failed data quality validation',
      'drafts': 'Products in draft status awaiting review',
      'low-stock': 'Products with low inventory levels',
      'recent': 'Products modified in the last 7 days'
    };
    return descriptions[queueType] || '';
  };
  
  const formatDate = (dateString) => {
    if (!dateString) return '-';
    return new Date(dateString).toLocaleString();
  };
  
  if (loading) {
    return (
      <Box display="flex" justifyContent="center" alignItems="center" minHeight="400px">
        <CircularProgress />
      </Box>
    );
  }
  
  if (error) {
    return (
      <Box p={3}>
        <Alert severity="error">{error}</Alert>
      </Box>
    );
  }
  
  return (
    <Box p={3}>
      <Box mb={3} display="flex" justifyContent="space-between" alignItems="flex-start">
        <Box>
          <Typography variant="h4" gutterBottom>
            {getQueueTitle()}
          </Typography>
          <Typography variant="body2" color="textSecondary">
            {getQueueDescription()}
          </Typography>
          <Typography variant="body2" color="textSecondary" mt={1}>
            Showing {products.length} products
          </Typography>
        </Box>
        {queueType === 'dq-failed' && products.length > 0 && (
          <Box display="flex" gap={1} alignItems="center">
            <Button variant="outlined" startIcon={exportDone ? <CheckIcon /> : <DownloadIcon />}
              onClick={handleExport} size="small" color={exportDone ? 'success' : 'primary'}>
              {exportDone ? 'Downloaded' : 'Export CSV'}
            </Button>
            <input type="file" accept=".csv" ref={fileInputRef} onChange={handleUpload} style={{ display: 'none' }} />
            <Button variant="outlined" startIcon={uploadDone ? <CheckIcon /> : <UploadIcon />}
              onClick={() => fileInputRef.current?.click()} size="small" color={uploadDone ? 'success' : 'primary'}>
              {uploadDone ? 'Uploaded' : 'Upload Corrections'}
            </Button>
            {uploadDone && (
              <Button variant="contained" size="small" onClick={() => navigate('/data-quality')}>
                Go to DQ Dashboard
              </Button>
            )}
          </Box>
        )}
      </Box>
      
      {products.length === 0 ? (
        <Alert severity="info">No products in this queue</Alert>
      ) : (
        <TableContainer component={Paper}>
          <Table>
            <TableHead>
              <TableRow>
                <TableCell>ISBN/EAN</TableCell>
                <TableCell>Product Name</TableCell>
                <TableCell>Status</TableCell>
                {queueType === 'dq-failed' && <TableCell>Failed Fields</TableCell>}
                {queueType === 'dq-failed' && <TableCell>Errors</TableCell>}
                {queueType === 'low-stock' && <TableCell>Stock</TableCell>}
                <TableCell sx={{ cursor: 'pointer', userSelect: 'none' }} onClick={() => setSortAsc(s => !s)}>
                  Modified {sortAsc ? <ArrowUpward sx={{ fontSize: 14, verticalAlign: 'middle' }} /> : <ArrowDownward sx={{ fontSize: 14, verticalAlign: 'middle' }} />}
                </TableCell>
                <TableCell>Actions</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {[...products].sort((a, b) => {
                const da = new Date(a.modified_date || 0), db = new Date(b.modified_date || 0);
                return sortAsc ? da - db : db - da;
              }).map((product) => (
                <TableRow key={product.product_id} hover>
                  <TableCell>{product.upc_ean}</TableCell>
                  <TableCell>{product.base_name}</TableCell>
                  <TableCell>
                    <Chip 
                      label={product.status} 
                      size="small"
                      color={product.status === 'active' ? 'success' : 'warning'}
                    />
                  </TableCell>
                  {queueType === 'dq-failed' && (
                    <TableCell>
                      {product.failed_field
                        ? product.failed_field.split(', ').map(f => (
                            <Chip key={f} label={f} size="small" color="error" variant="outlined" sx={{ mr: 0.5, mb: 0.5 }} />
                          ))
                        : '-'}
                    </TableCell>
                  )}
                  {queueType === 'dq-failed' && (
                    <TableCell>
                      <Tooltip title={product.failure_reason || ''} arrow>
                        <Typography variant="body2" noWrap sx={{ maxWidth: 250 }}>
                          {product.failure_reason || '-'}
                        </Typography>
                      </Tooltip>
                    </TableCell>
                  )}
                  {queueType === 'low-stock' && (
                    <TableCell>
                      <Chip 
                        label={product.stock_quantity} 
                        size="small"
                        color="error"
                      />
                    </TableCell>
                  )}
                  <TableCell>{formatDate(product.modified_date)}</TableCell>
                  <TableCell>
                    <Button 
                      size="small" 
                      variant="outlined"
                      onClick={() => navigate(`/products/${product.product_id}`)}
                    >
                      Edit
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>
      )}
      <Snackbar
        open={snackbar.open}
        autoHideDuration={4000}
        onClose={() => setSnackbar(s => ({ ...s, open: false }))}
        message={snackbar.message}
      />
    </Box>
  );
};

export default QueueView;
