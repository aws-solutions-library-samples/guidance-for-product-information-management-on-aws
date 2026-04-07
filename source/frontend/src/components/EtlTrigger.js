import React, { useState } from 'react';
import {
  Button,
  Card,
  CardContent,
  Typography,
  Alert,
  CircularProgress,
  Box,
  Link
} from '@mui/material';
import PlayArrowIcon from '@mui/icons-material/PlayArrow';
import { Upload as UploadIcon, CloudUpload as CloudUploadIcon } from '@mui/icons-material';
import { fetchAuthSession } from 'aws-amplify/auth';
import { apiService } from '../services/api';

const EtlTrigger = () => {
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const [sampleLoading, setSampleLoading] = useState(false);
  const [uploadMsg, setUploadMsg] = useState(null);

  const onLoadSample = async () => {
    setSampleLoading(true);
    setUploadMsg(null);
    try {
      const response = await apiService.uploadSampleData();
      setUploadMsg({ type: 'success', text: `Uploaded ${response.products_count} sample products` });
    } catch (err) {
      setUploadMsg({ type: 'error', text: `Failed: ${err.message}` });
    } finally {
      setSampleLoading(false);
    }
  };

  const onFileUpload = async (event) => {
    const file = event.target.files[0];
    if (!file) return;
    setSampleLoading(true);
    setUploadMsg(null);
    try {
      const jsonData = JSON.parse(await file.text());
      const response = await apiService.uploadCustomData(jsonData);
      setUploadMsg({ type: 'success', text: `Uploaded ${response.products_count} products from ${file.name}` });
    } catch (err) {
      setUploadMsg({ type: 'error', text: `Failed: ${err.message}` });
    } finally {
      setSampleLoading(false);
      event.target.value = null;
    }
  };

  const triggerEtl = async () => {
    setLoading(true);
    setError(null);
    setResult(null);

    try {
      // Get the current auth session
      const session = await fetchAuthSession();
      const token = session.tokens.idToken.toString();

      // Get API endpoint from environment - use existing REACT_APP_API_URL
      const apiEndpoint = process.env.REACT_APP_API_URL?.replace(/\/$/, '') || 
                         process.env.REACT_APP_API_ENDPOINT || 
                         'https://3volc9bvi4.execute-api.ap-southeast-2.amazonaws.com/development';

      const url = `${apiEndpoint}/api/v1/etl/trigger`;
      
      console.log('Triggering ETL at:', url);
      console.log('Token available:', !!token);

      const response = await fetch(url, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': token
        },
        body: JSON.stringify({
          description: 'Manual trigger from UI'
        })
      });

      console.log('Response status:', response.status);
      const data = await response.json();
      console.log('Response data:', data);

      if (response.ok) {
        setResult(data);
      } else {
        setError(data.error || data.message || 'Failed to trigger ETL workflow');
      }
    } catch (err) {
      console.error('ETL trigger error:', err);
      setError(err.message || 'Network error occurred. Check console for details.');
    } finally {
      setLoading(false);
    }
  };

  return (
    <Card>
      <CardContent>
        <Typography variant="h6" gutterBottom>
          ETL Workflow Control
        </Typography>
        
        <Typography variant="body2" color="textSecondary" paragraph>
          Manually trigger the ETL workflow to process new product data from the data lake.
        </Typography>

        <Box sx={{ mb: 2, display: 'flex', gap: 1 }}>
          <Button
            variant="outlined"
            color="primary"
            onClick={onLoadSample}
            disabled={sampleLoading}
            startIcon={sampleLoading ? <CircularProgress size={20} /> : <UploadIcon />}
          >
            Load Sample Data
          </Button>
          <Button
            variant="outlined"
            component="label"
            disabled={sampleLoading}
            startIcon={<CloudUploadIcon />}
          >
            Upload File
            <input type="file" accept=".json" hidden onChange={onFileUpload} />
          </Button>
        </Box>

        {uploadMsg && (
          <Alert severity={uploadMsg.type} sx={{ mb: 2 }}>{uploadMsg.text}</Alert>
        )}

        <Box sx={{ mb: 2 }}>
          <Button
            variant="contained"
            color="primary"
            startIcon={loading ? <CircularProgress size={20} /> : <PlayArrowIcon />}
            onClick={triggerEtl}
            disabled={loading}
            fullWidth
          >
            {loading ? 'Starting ETL Workflow...' : 'Trigger ETL Workflow'}
          </Button>
        </Box>

        {result && (
          <Alert severity="success" sx={{ mt: 2 }}>
            <Typography variant="subtitle2">
              ETL Workflow Started Successfully!
            </Typography>
            <Typography variant="body2" sx={{ mt: 1 }}>
              Execution: {result.execution_name}
            </Typography>
            <Typography variant="body2">
              Started at: {new Date(result.started_at).toLocaleString()}
            </Typography>
            {result.console_url && (
              <Link 
                href={result.console_url} 
                target="_blank" 
                rel="noopener noreferrer"
                sx={{ mt: 1, display: 'block' }}
              >
                View in AWS Console →
              </Link>
            )}
          </Alert>
        )}

        {error && (
          <Alert severity="error" sx={{ mt: 2 }}>
            <Typography variant="subtitle2">Error</Typography>
            <Typography variant="body2">{error}</Typography>
          </Alert>
        )}

        <Typography variant="caption" color="textSecondary" sx={{ mt: 2, display: 'block' }}>
          Note: This triggers the Step Functions workflow that runs Glue ETL and Data Quality jobs.
          The process typically takes 5-10 minutes to complete.
        </Typography>
      </CardContent>
    </Card>
  );
};

export default EtlTrigger;
