import React from 'react';

function App() {
  return (
    <div style={{ padding: '20px' }}>
      <h1>PIM System Test</h1>
      <p>If you can see this, React is working!</p>
      <p>Environment variables:</p>
      <ul>
        <li>API URL: {process.env.REACT_APP_API_URL || 'Not set'}</li>
        <li>User Pool ID: {process.env.REACT_APP_USER_POOL_ID || 'Not set'}</li>
        <li>Region: {process.env.REACT_APP_REGION || 'Not set'}</li>
      </ul>
    </div>
  );
}

export default App;
