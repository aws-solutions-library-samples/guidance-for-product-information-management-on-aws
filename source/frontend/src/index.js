import React from 'react';
import ReactDOM from 'react-dom/client';
import './index.css';
import App from './App';
import { Amplify } from 'aws-amplify';

console.log('Starting PIM System...');

// Amplify v6 Configuration
Amplify.configure({
  Auth: {
    Cognito: {
      region: process.env.REACT_APP_REGION,
      userPoolId: process.env.REACT_APP_USER_POOL_ID,
      userPoolClientId: process.env.REACT_APP_USER_POOL_CLIENT_ID,
      identityPoolId: process.env.REACT_APP_IDENTITY_POOL_ID,
    }
  },
  API: {
    REST: {
      PimAPI: {
        endpoint: process.env.REACT_APP_API_URL,
        region: process.env.REACT_APP_REGION,
        service: 'execute-api',
        authMode: 'userPool'
      }
    }
  }
});

console.log('Amplify configured successfully');

const root = ReactDOM.createRoot(document.getElementById('root'));
root.render(<App />);

console.log('PIM System loaded');