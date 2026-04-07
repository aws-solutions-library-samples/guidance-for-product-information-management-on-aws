import React, { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Box,
  Typography,
  Card,
  CardContent,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Paper,
  Chip,
  CircularProgress,
  Alert,
  Button,
  Snackbar
} from '@mui/material';
import {
  ArrowBack as ArrowBackIcon,
  Edit as EditIcon,
  Download as DownloadIcon,
  Upload as UploadIcon
} from '@mui/icons-material';
import { apiService } from '../services/api';

function FailedRecords() {
  const navigate = useNavigate();
  const [failedRecords, setFailedRecords] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [uploading, setUploading] = useState(false);
  const [snackbar, setSnackbar] = useState({ open: false, message: '', severity: 'info' });

  useEffect(() => {
    fetchFailedRecords();
  }, []);

  const fetchFailedRecords = async () => {
    try {
      setLoading(true);
      setError(null);
      
      console.log('Fetching failed records...');
      const response = await apiService.getFailedRecords();
      console.log('Failed records response:', response);
      
      setFailedRecords(response.failed_records || []);
      console.log('Failed records count:', response.failed_records?.length || 0);
    } catch (err) {
      console.error('Error fetching failed records:', err);
      setError('Failed to load failed records');
    } finally {
      setLoading(false);
    }
  };

  const handleEditRecord = (record) => {
    // Navigate to product edit page using product_id
    navigate(`/products/edit/${record.product_id}`);
  };

  const handleExport = async () => {
    try {
      setSnackbar({ open: true, message: 'Generating CSV export...', severity: 'info' });
      
      const response = await apiService.exportFailedRecords();
      
      if (response.download_url) {
        // Download the file
        window.open(response.download_url, '_blank');
        setSnackbar({ 
          open: true, 
          message: `Exported ${response.record_count} records successfully`, 
          severity: 'success' 
        });
      }
    } catch (err) {
      console.error('Error exporting records:', err);
      setSnackbar({ open: true, message: 'Failed to export records', severity: 'error' });
    }
  };

  const handleFileUpload = async (event) => {
    const file = event.target.files[0];
    if (!file) return;

    try {
      setUploading(true);
      setSnackbar({ open: true, message: 'Uploading corrections...', severity: 'info' });

      // Read file as base64
      const reader = new FileReader();
      reader.onload = async (e) => {
        const base64Content = e.target.result.split(',')[1]; // Remove data:text/csv;base64, prefix
        
        const response = await apiService.uploadCorrections(base64Content, file.name);
        
        setSnackbar({ 
          open: true, 
          message: `Successfully updated ${response.updated_count} products. Revalidation triggered.`, 
          severity: 'success' 
        });
        
        // Refresh the list after a delay
        setTimeout(fetchFailedRecords, 2000);
      };
      
      reader.readAsDataURL(file);
    } catch (err) {
      console.error('Error uploading file:', err);
      setSnackbar({ open: true, message: 'Failed to upload corrections', severity: 'error' });
    } finally {
      setUploading(false);
      event.target.value = ''; // Reset file input
    }
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
      <Box>
        <Alert severity="error" sx={{ mb: 2 }}>
          {error}
        </Alert>
        <Button
          variant="contained"
          startIcon={<ArrowBackIcon />}
          onClick={() => navigate('/data-quality')}
        >
          Back to Data Quality
        </Button>
      </Box>
    );
  }

  return (
    <Box>
      <Box display="flex" alignItems="center" justifyContent="space-between" mb={3}>
        <Box display="flex" alignItems="center">
          <Button
            startIcon={<ArrowBackIcon />}
            onClick={() => navigate('/data-quality')}
            sx={{ mr: 2 }}
          >
            Back
          </Button>
          <Typography variant="h4"> DQ Failed Records</Typography>
        </Box>
        
        <Box>
          <Button
            variant="outlined"
            startIcon={<DownloadIcon />}
            onClick={handleExport}
            disabled={loading || failedRecords.length === 0}
            sx={{ mr: 2 }}
          >
            Export CSV
          </Button>
          <Button
            variant="contained"
            component="label"
            startIcon={<UploadIcon />}
            disabled={uploading}
          >
            {uploading ? 'Uploading...' : 'Upload Corrections'}
            <input
              type="file"
              hidden
              accept=".csv"
              onChange={handleFileUpload}
            />
          </Button>
        </Box>
      </Box>

      <Typography variant="body1" color="textSecondary" paragraph>
        Records that failed data quality validation. Click "Edit" to correct individual records, 
        or use "Export CSV" to download all failed records for bulk correction.
      </Typography>

      <Snackbar
        open={snackbar.open}
        autoHideDuration={6000}
        onClose={() => setSnackbar({ ...snackbar, open: false })}
      >
        <Alert severity={snackbar.severity} onClose={() => setSnackbar({ ...snackbar, open: false })}>
          {snackbar.message}
        </Alert>
      </Snackbar>

      {failedRecords.length === 0 ? (
        <Card>
          <CardContent>
            <Typography variant="h6" color="success.main">
              No DQ failed records found!
            </Typography>
            <Typography variant="body2" color="textSecondary">
              All records have passed data quality validation.
            </Typography>
          </CardContent>
        </Card>
      ) : (
        <TableContainer component={Paper}>
          <Table>
            <TableHead>
              <TableRow>
                <TableCell>ISBN/EAN</TableCell>
                <TableCell>Product Name</TableCell>
                <TableCell>Validation Errors</TableCell>
                <TableCell>Failed At</TableCell>
                <TableCell>Actions</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {failedRecords.map((record, index) => (
                <TableRow key={record.product_id || index}>
                  <TableCell>
                    <Typography variant="body2" fontFamily="monospace">
                      {record.upc_ean}
                    </Typography>
                  </TableCell>
                  <TableCell>{record.name}</TableCell>
                  <TableCell>
                    <Box>
                      {Array.isArray(record.validation_errors) 
                        ? record.validation_errors.map((error, idx) => (
                            <Chip
                              key={idx}
                              label={error}
                              color="error"
                              size="small"
                              sx={{ mr: 0.5, mb: 0.5 }}
                            />
                          ))
                        : <Chip label={record.validation_errors} color="error" size="small" />
                      }
                    </Box>
                  </TableCell>
                  <TableCell>
                    <Typography variant="body2" color="textSecondary">
                      {new Date(record.failed_at).toLocaleString()}
                    </Typography>
                  </TableCell>
                  <TableCell>
                    <Button
                      variant="outlined"
                      size="small"
                      startIcon={<EditIcon />}
                      onClick={() => handleEditRecord(record)}
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
    </Box>
  );
}

export default FailedRecords;
