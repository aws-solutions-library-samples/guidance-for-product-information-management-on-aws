import React, { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Grid,
  Card,
  CardContent,
  Typography,
  Box,
  CircularProgress,
  Alert,
  Button,
  Tooltip,
} from '@mui/material';
import {
  Inventory as ProductIcon,
  Analytics as AnalyticsIcon,
  HighQuality as QualityIcon,
  TrendingUp as TrendingUpIcon,
  Refresh as RefreshIcon,
  ErrorOutline as ErrorIcon,
  InfoOutlined as InfoIcon,
} from '@mui/icons-material';
import { apiService } from '../services/api';
import EtlTrigger from '../components/EtlTrigger';

function Dashboard() {
  const navigate = useNavigate();
  const [stats, setStats] = useState({
    totalProducts: 0,
    activeProducts: 0,
    outOfStock: 0,
    draftProducts: 0,
    dqFailedProducts: 0,
    discontinuedProducts: 0,
    queueDqFailed: 0,
    queueDrafts: 0,
    queueLowStock: 0,
  });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    let isMounted = true;
    
    const loadData = async () => {
      if (isMounted) {
        await fetchDashboardStats();
      }
    };
    
    loadData();
    
    return () => {
      isMounted = false; // Cleanup: prevent state updates if unmounted
    };
  }, []);

  const fetchDashboardStats = async () => {
    try {
      setLoading(true);
      
      // Use lightweight stats endpoint instead of fetching 100 products
      const statsResponse = await apiService.getProductStats();
      console.log('Stats response:', statsResponse);
      
      const totalProducts = statsResponse.total_products || 0;
      const activeProducts = statsResponse.active_products || 0;
      const draftProducts = statsResponse.draft_products || 0;
      const dqFailedProducts = statsResponse.dq_failed_products || 0;
      const inactiveProducts = statsResponse.inactive_products || 0;
      
      // Queue counts
      const queueDqFailed = statsResponse.queue_dq_failed || 0;
      const queueDrafts = statsResponse.queue_drafts || 0;
      const queueLowStock = statsResponse.queue_low_stock || 0;
      
      setStats({
        totalProducts,
        activeProducts,
        outOfStock: inactiveProducts,
        draftProducts,
        dqFailedProducts,
        discontinuedProducts: 0,
        queueDqFailed,
        queueDrafts,
        queueLowStock,
      });
    } catch (err) {
      console.error('Error fetching dashboard stats:', err);
      setError('Failed to load dashboard statistics');
    } finally {
      setLoading(false);
    }
  };

  const StatCard = ({ title, value, icon, color = 'primary', status, onClick, tooltip }) => (
    <Card 
      sx={{ 
        cursor: 'pointer',
        '&:hover': {
          boxShadow: 3,
          transform: 'translateY(-2px)',
          transition: 'all 0.2s ease-in-out'
        }
      }}
      onClick={onClick || (() => navigate(`/products?status=${status}`))}
    >
      <CardContent>
        <Box display="flex" alignItems="center" justifyContent="space-between">
          <Box>
            <Typography color="textSecondary" gutterBottom variant="body2">
              {title}
              {tooltip && (
                <Tooltip title={tooltip} arrow placement="top">
                  <InfoIcon sx={{ fontSize: 14, ml: 0.5, verticalAlign: 'middle', cursor: 'help', opacity: 0.6 }} />
                </Tooltip>
              )}
            </Typography>
            <Typography variant="h4" component="h2">
              {loading ? <CircularProgress size={24} /> : value}
            </Typography>
          </Box>
          <Box color={`${color}.main`} sx={{ color: `${color}.main` }}>
            {icon}
          </Box>
        </Box>
      </CardContent>
    </Card>
  );

  return (
    <Box>
      <Box display="flex" alignItems="center" gap={1} mb={2}>
        <Typography variant="h4">
          Dashboard
        </Typography>
        <Button
          size="small"
          onClick={fetchDashboardStats}
          disabled={loading}
          sx={{ minWidth: 'auto', p: 0.5 }}
        >
          {loading ? <CircularProgress size={20} /> : <RefreshIcon />}
        </Button>
      </Box>
      <Typography variant="body1" color="textSecondary" mb={2}>
        Welcome to your PIM System dashboard. Here's an overview of your product catalog.
      </Typography>

      {error && (
        <Alert severity="warning" sx={{ mb: 2 }}>
          {error} - Try triggering ETL to load data
        </Alert>
      )}

      <Grid container spacing={3}>
        <Grid item xs={12} sm={6} md={2.4}>
          <StatCard
            title="Total Products"
            value={stats.totalProducts}
            icon={<ProductIcon fontSize="large" />}
            color="primary"
            status="all"
            tooltip="All products in the catalog excluding deleted"
          />
        </Grid>
        <Grid item xs={12} sm={6} md={2.4}>
          <StatCard
            title="Active"
            value={stats.activeProducts}
            icon={<TrendingUpIcon fontSize="large" />}
            color="success"
            status="active"
            tooltip="Products live and available in the catalog"
          />
        </Grid>
        <Grid item xs={12} sm={6} md={2.4}>
          <StatCard
            title="Draft"
            value={stats.draftProducts - stats.dqFailedProducts}
            icon={<QualityIcon fontSize="large" />}
            color="warning"
            status="draft"
            tooltip="New products awaiting review before publishing"
          />
        </Grid>
        <Grid item xs={12} sm={6} md={2.4}>
          <StatCard
            title="DQ Failed"
            value={stats.dqFailedProducts}
            icon={<ErrorIcon fontSize="large" />}
            color="error"
            onClick={() => navigate('/queues/dq-failed')}
            tooltip="Products that failed data quality validation and need correction"
          />
        </Grid>
        <Grid item xs={12} sm={6} md={2.4}>
          <StatCard
            title="Inactive"
            value={stats.outOfStock}
            icon={<AnalyticsIcon fontSize="large" />}
            color="info"
            status="inactive"
            tooltip="Products deactivated from the catalog"
          />
        </Grid>
      </Grid>

      <Typography variant="h5" gutterBottom sx={{ mt: 4 }}>
        Work Queues
      </Typography>
      <Grid container spacing={3}>
        <Grid item xs={12} sm={6} md={3}>
          <StatCard
            title="DQ Failed"
            value={stats.queueDqFailed}
            icon={<QualityIcon fontSize="large" />}
            color="error"
            onClick={() => navigate('/queues/dq-failed')}
            tooltip="Fix validation errors: export CSV, correct, and revalidate"
          />
        </Grid>
        <Grid item xs={12} sm={6} md={3}>
          <StatCard
            title="Drafts"
            value={stats.queueDrafts}
            icon={<ProductIcon fontSize="large" />}
            color="warning"
            onClick={() => navigate('/queues/drafts')}
            tooltip="Review and publish draft products to make them active"
          />
        </Grid>
        <Grid item xs={12} sm={6} md={3}>
          <StatCard
            title="Low Stock"
            value={stats.queueLowStock}
            icon={<TrendingUpIcon fontSize="large" />}
            color="warning"
            onClick={() => navigate('/queues/low-stock')}
            tooltip="Active products with inventory below the minimum threshold"
          />
        </Grid>
      </Grid>

      {/* ETL Workflow Control */}
      <Box display="flex" justifyContent="center" mt={4}>
        <EtlTrigger />
      </Box>
    </Box>
  );
}

export default Dashboard;