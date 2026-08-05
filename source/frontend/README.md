# PIM System Frontend

A React-based frontend application for the AWS PIM (Product Information Management) System, specifically designed for bookstore management.

## Features

- **Authentication**: AWS Cognito integration with role-based access
- **Dashboard**: Overview of inventory, analytics, and system health
- **Book Management**: CRUD operations for book catalog
- **Search & Filter**: Advanced search capabilities across book attributes
- **Analytics**: Business intelligence dashboards and reports
- **Data Quality**: Monitor and manage data quality issues
- **Responsive Design**: Mobile-friendly Material-UI components

## Technology Stack

- **React 18** - Frontend framework
- **Material-UI (MUI)** - UI component library
- **AWS Amplify** - Authentication and API integration
- **React Router** - Client-side routing
- **Axios** - HTTP client for API calls

## Getting Started

### Prerequisites

- Node.js 16+ and npm
- AWS PIM System backend deployed
- AWS Cognito User Pool configured

### Installation

1. Install dependencies:
   ```bash
   npm install
   ```

2. Configure environment variables:
   Create a `.env` file with your AWS configuration:
   ```
   REACT_APP_API_URL=https://your-api-gateway-url
   REACT_APP_USER_POOL_ID=your-user-pool-id
   REACT_APP_USER_POOL_CLIENT_ID=your-client-id
   REACT_APP_IDENTITY_POOL_ID=your-identity-pool-id
   REACT_APP_REGION=us-east-1
   REACT_APP_CLOUDFRONT_URL=https://your-cloudfront-domain
   REACT_APP_ENVIRONMENT=development
   ```

3. Start the development server:
   ```bash
   npm start
   ```

4. Open [http://localhost:3000](http://localhost:3000) to view it in the browser.

## Available Scripts

- `npm start` - Runs the app in development mode
- `npm build` - Builds the app for production
- `npm test` - Launches the test runner
- `npm eject` - Ejects from Create React App (one-way operation)

## Project Structure

```
src/
├── components/          # Reusable UI components
│   └── Layout.js       # Main layout with navigation
├── pages/              # Page components
│   ├── Dashboard.js    # Dashboard overview
│   ├── Books.js        # Book listing and management
│   ├── BookForm.js     # Book creation/editing form
│   ├── Analytics.js    # Analytics and reporting
│   └── DataQuality.js  # Data quality monitoring
├── App.js              # Main app component with routing
├── index.js            # App entry point with Amplify config
└── index.css           # Global styles
```

## Features Overview

### Dashboard
- System overview with key metrics
- Quick access to main functions
- Real-time status indicators

### Book Management
- Complete CRUD operations for books
- Advanced search and filtering
- Bulk operations support
- Form validation with real-time feedback

### Analytics
- Books by genre analysis
- Top authors reporting
- Inventory reports with stock levels
- Price analysis across categories

### Data Quality
- Real-time data quality monitoring
- Failed record management
- Manual correction workflows
- Quality rule configuration

## Authentication

The app uses AWS Cognito for authentication. CDK creates two demo users automatically:

- **admin** — Editors group (read + write: create, update, delete products)
- **viewer** — Viewers group (read only: browse products, view dashboards)

Passwords are stored in AWS Secrets Manager under the secret name `pim-seed-user-credentials`. Retrieve them via the AWS Console or CLI:

```bash
aws secretsmanager get-secret-value --secret-id pim-seed-user-credentials --query SecretString --output text
```

## API Integration

The frontend integrates with the AWS PIM System backend through:

- **REST APIs**: All CRUD operations via API Gateway
- **Authentication**: JWT tokens from Cognito
- **File Upload**: Direct S3 upload for digital assets
- **Real-time Updates**: Event-driven updates for data changes

## Deployment

### AWS Amplify Hosting

The app is configured for deployment on AWS Amplify:

1. Connect your Git repository to Amplify
2. Configure build settings (already included in `package.json`)
3. Set environment variables in Amplify Console
4. Deploy automatically on code changes

### Manual Deployment

For manual deployment to other hosting services:

1. Build the production version:
   ```bash
   npm run build
   ```

2. Deploy the `build/` folder to your hosting service

## Environment Variables

Required environment variables for the frontend:

| Variable | Description |
|----------|-------------|
| `REACT_APP_API_URL` | API Gateway endpoint URL |
| `REACT_APP_USER_POOL_ID` | Cognito User Pool ID |
| `REACT_APP_USER_POOL_CLIENT_ID` | Cognito User Pool Client ID |
| `REACT_APP_IDENTITY_POOL_ID` | Cognito Identity Pool ID |
| `REACT_APP_REGION` | AWS region |
| `REACT_APP_CLOUDFRONT_URL` | CloudFront distribution URL |
| `REACT_APP_ENVIRONMENT` | Environment name (development/production) |

## Contributing

1. Follow React best practices and hooks patterns
2. Use Material-UI components consistently
3. Implement proper error handling and loading states
4. Add unit tests for new components
5. Follow the existing code structure and naming conventions

## License

This project is part of the AWS PIM System and follows the same license terms.