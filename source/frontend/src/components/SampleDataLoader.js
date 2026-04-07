import React, { useState } from 'react';
import { Button, CircularProgress, Alert, Box } from '@mui/material';
import { Upload as UploadIcon, CloudUpload as CloudUploadIcon } from '@mui/icons-material';
import { apiService } from '../services/api';

const SampleDataLoader = () => {
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState(null);

  const loadSampleData = async () => {
    setLoading(true);
    setMessage(null);

    try {
      const response = await apiService.uploadSampleData();
      
      setMessage({
        type: 'success',
        text: `✅ Uploaded ${response.products_count} sample products to ${response.key}`
      });
      
    } catch (error) {
      console.error('Error loading sample data:', error);
      setMessage({
        type: 'error',
        text: `Failed to upload sample data: ${error.message}`
      });
    } finally {
      setLoading(false);
    }
  };

  const handleFileUpload = async (event) => {
    const file = event.target.files[0];
    if (!file) return;

    setLoading(true);
    setMessage(null);

    try {
      const fileContent = await file.text();
      const jsonData = JSON.parse(fileContent);
      
      const response = await apiService.uploadCustomData(jsonData);
      
      setMessage({
        type: 'success',
        text: `✅ Uploaded ${response.products_count} products from ${file.name}`
      });
      
    } catch (error) {
      console.error('Error uploading file:', error);
      setMessage({
        type: 'error',
        text: `Failed to upload file: ${error.message}`
      });
    } finally {
      setLoading(false);
      event.target.value = null; // Reset file input
    }
  };

  return (
    <Box display="flex" gap={1} alignItems="center">
      <Button
        variant="outlined"
        color="primary"
        onClick={loadSampleData}
        disabled={loading}
        startIcon={loading ? <CircularProgress size={20} /> : <UploadIcon />}
      >
        Load Sample Data
      </Button>
      
      <Button
        variant="outlined"
        component="label"
        disabled={loading}
        startIcon={<CloudUploadIcon />}
      >
        Upload File
        <input
          type="file"
          accept=".json"
          hidden
          onChange={handleFileUpload}
        />
      </Button>

      {message && (
        <Alert severity={message.type} sx={{ position: 'absolute', top: 60, right: 16, zIndex: 1000 }}>
          {message.text}
        </Alert>
      )}
    </Box>
  );
};

export default SampleDataLoader;
