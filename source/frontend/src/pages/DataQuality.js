import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Box,
  Typography,
  Card,
  CardContent,
  Button,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Snackbar,
  Alert,
  CircularProgress,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Paper,
  Chip,
  ToggleButton,
  ToggleButtonGroup,
} from '@mui/material';
import {
  Build as BuildIcon,
  History as HistoryIcon,
  Error as ErrorIcon,
} from '@mui/icons-material';
import { apiService } from '../services/api';

function DataQuality() {
  const navigate = useNavigate();
  const [revalidationDialog, setRevalidationDialog] = useState(false);
  const [revalidating, setRevalidating] = useState(false);
  const [runHistory, setRunHistory] = useState(null);
  const [loadingHistory, setLoadingHistory] = useState(false);
  const [period, setPeriod] = useState('7d');
  const [executionUrl, setExecutionUrl] = useState(null);
  const [snackbar, setSnackbar] = useState({ open: false, message: '', severity: 'info' });

  const handleRevalidation = async () => {
    try {
      setRevalidating(true);
      const response = await apiService.triggerRevalidation();
      setExecutionUrl(response.console_url || null);
      setSnackbar({ open: true, message: 'DQ revalidation started successfully', severity: 'success' });
      setRevalidationDialog(false);
      setRunHistory(null);
    } catch (err) {
      console.error('Error triggering revalidation:', err);
      setSnackbar({ open: true, message: 'Failed to start revalidation', severity: 'error' });
    } finally {
      setRevalidating(false);
    }
  };

  const loadRunHistory = async (p = period) => {
    try {
      setLoadingHistory(true);
      const response = await apiService.getDqRunHistory(p);
      setRunHistory(response.runs || []);
    } catch (err) {
      console.error('Error loading DQ run history:', err);
      setSnackbar({ open: true, message: 'Failed to load run history', severity: 'error' });
    } finally {
      setLoadingHistory(false);
    }
  };

  const handlePeriodChange = (e, newPeriod) => {
    if (newPeriod) {
      setPeriod(newPeriod);
      loadRunHistory(newPeriod);
    }
  };

  return (
    <Box>
      <Typography variant="h4" gutterBottom>Data Quality Operations</Typography>
      <Typography variant="body1" color="textSecondary" paragraph>
        Manage data quality workflows. View failed records in the DQ Failed queue, or trigger revalidation after corrections.
      </Typography>

      {/* Action Cards */}
      <Box display="flex" gap={2} flexWrap="wrap" mb={4}>
        <Card sx={{ flex: '1 1 250px', maxWidth: 350 }}>
          <CardContent>
            <Box display="flex" alignItems="center" gap={1} mb={1}>
              <ErrorIcon color="error" />
              <Typography variant="h6">Failed Records</Typography>
            </Box>
            <Typography variant="body2" color="textSecondary" paragraph>
              View, edit, export, and bulk-upload corrections for records that failed validation.
            </Typography>
            <Button variant="contained" onClick={() => navigate('/queues/dq-failed')}>
              View Failed Records
            </Button>
          </CardContent>
        </Card>

        <Card sx={{ flex: '1 1 250px', maxWidth: 350 }}>
          <CardContent>
            <Box display="flex" alignItems="center" gap={1} mb={1}>
              <BuildIcon color="primary" />
              <Typography variant="h6">Revalidate</Typography>
            </Box>
            <Typography variant="body2" color="textSecondary" paragraph>
              Trigger DQ checks on all corrected draft products. Passing records are reactivated.
            </Typography>
            <Button variant="contained" onClick={() => setRevalidationDialog(true)}>
              Revalidate All
            </Button>
          </CardContent>
        </Card>
      </Box>

      {/* Execution link after revalidation */}
      {executionUrl && (
        <Alert severity="success" sx={{ mb: 4 }}>
          <Typography variant="subtitle2">Revalidation pipeline started!</Typography>
          <a href={executionUrl} target="_blank" rel="noopener noreferrer">
            View in AWS Console →
          </a>
        </Alert>
      )}

      {/* DQ Run History - on demand */}
      <Card>
        <CardContent>
          <Box display="flex" justifyContent="space-between" alignItems="center" mb={2}>
            <Box display="flex" alignItems="center" gap={1}>
              <HistoryIcon />
              <Typography variant="h6">DQ Run History</Typography>
            </Box>
            <Box display="flex" alignItems="center" gap={2}>
              {runHistory !== null && (
                <ToggleButtonGroup
                  value={period}
                  exclusive
                  onChange={handlePeriodChange}
                  size="small"
                >
                  <ToggleButton value="7d">7 Days</ToggleButton>
                  <ToggleButton value="4w">4 Weeks</ToggleButton>
                  <ToggleButton value="3m">3 Months</ToggleButton>
                  <ToggleButton value="6m">6 Months</ToggleButton>
                </ToggleButtonGroup>
              )}
              <Button
                variant="outlined"
                startIcon={loadingHistory ? <CircularProgress size={16} /> : <HistoryIcon />}
                onClick={() => loadRunHistory()}
                disabled={loadingHistory}
              >
                {runHistory === null ? 'Load History' : 'Refresh'}
              </Button>
            </Box>
          </Box>

          {runHistory === null && (
            <Typography variant="body2" color="textSecondary">
              Click "Load History" to view past DQ run results.
            </Typography>
          )}

          {runHistory !== null && runHistory.length === 0 && (
            <Typography variant="body2" color="textSecondary">No DQ runs found.</Typography>
          )}

          {runHistory !== null && runHistory.length > 0 && (
            <TableContainer component={Paper} variant="outlined">
              <Table size="small">
                <TableHead>
                  <TableRow>
                    <TableCell>Run Time</TableCell>
                    <TableCell align="right">Total</TableCell>
                    <TableCell align="right">Passed</TableCell>
                    <TableCell align="right">Failed</TableCell>
                    <TableCell align="right">Success Rate</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {runHistory.map((run, idx) => (
                    <TableRow key={idx}>
                      <TableCell>{new Date(run.timestamp).toLocaleString()}</TableCell>
                      <TableCell align="right">{run.total_records}</TableCell>
                      <TableCell align="right">{run.valid_records}</TableCell>
                      <TableCell align="right">{run.failed_records}</TableCell>
                      <TableCell align="right">
                        <Chip
                          label={`${run.success_rate}%`}
                          size="small"
                          color={parseFloat(run.success_rate) >= 90 ? 'success' : parseFloat(run.success_rate) >= 70 ? 'warning' : 'error'}
                        />
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </TableContainer>
          )}
        </CardContent>
      </Card>

      {/* Revalidation Dialog */}
      <Dialog open={revalidationDialog} onClose={() => setRevalidationDialog(false)}>
        <DialogTitle>Revalidate Data Quality</DialogTitle>
        <DialogContent>
          <Typography>
            This will trigger data quality validation for all draft products.
            Corrected products will be reactivated if they pass validation.
          </Typography>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setRevalidationDialog(false)}>Cancel</Button>
          <Button onClick={handleRevalidation} variant="contained" disabled={revalidating}>
            {revalidating ? <CircularProgress size={20} /> : 'Start Revalidation'}
          </Button>
        </DialogActions>
      </Dialog>

      <Snackbar
        open={snackbar.open}
        autoHideDuration={6000}
        onClose={() => setSnackbar({ ...snackbar, open: false })}
      >
        <Alert severity={snackbar.severity} onClose={() => setSnackbar({ ...snackbar, open: false })}>
          {snackbar.message}
        </Alert>
      </Snackbar>
    </Box>
  );
}

export default DataQuality;
