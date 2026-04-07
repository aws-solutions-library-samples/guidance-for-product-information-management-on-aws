import React from 'react';
import { BrowserRouter as Router, Routes, Route } from 'react-router-dom';
import { ThemeProvider, createTheme } from '@mui/material/styles';
import CssBaseline from '@mui/material/CssBaseline';
import { Authenticator } from '@aws-amplify/ui-react';
import '@aws-amplify/ui-react/styles.css';

import Layout from './components/Layout';
import Dashboard from './pages/Dashboard';
import Products from './pages/Products';
import ProductForm from './pages/ProductForm';
import DataQuality from './pages/DataQuality';
import FailedRecords from './pages/FailedRecords';
import QueueView from './pages/QueueView';

const theme = createTheme({
  palette: {
    primary: {
      main: '#1976d2',
    },
    secondary: {
      main: '#dc004e',
    },
  },
});

function App() {
  return (
    <ThemeProvider theme={theme}>
      <CssBaseline />
      <Authenticator>
        {({ signOut, user }) => (
          <div className="app-container">
            <Router>
              <Layout user={user} signOut={signOut}>
                <Routes>
                  <Route path="/" element={<Dashboard />} />
                  <Route path="/products" element={<Products />} />
                  <Route path="/products/new" element={<ProductForm />} />
                  <Route path="/products/edit/:id" element={<ProductForm />} />
                  <Route path="/products/:id" element={<ProductForm />} />
                  <Route path="/data-quality" element={<DataQuality />} />
                  <Route path="/failed-records" element={<FailedRecords />} />
                  <Route path="/queues/:queueType" element={<QueueView />} />
                </Routes>
              </Layout>
            </Router>
          </div>
        )}
      </Authenticator>
    </ThemeProvider>
  );
}

export default App;