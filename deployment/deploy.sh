#!/bin/bash
set -e

# ============================================
# CONFIGURATION
# ============================================
STACK_NAME="pim-on-aws"
AWS_REGION="${AWS_REGION:-us-east-1}"
REQUIRED_PYTHON_MAJOR=3
REQUIRED_PYTHON_MINOR=9
REQUIRED_NODE_MAJOR=16
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
VENV_DIR="$PROJECT_DIR/.venv"

# Ensure we run from the project root regardless of where the script is invoked
cd "$PROJECT_DIR"

# ============================================
# PARSE ARGUMENTS
# ============================================
FRONTEND_ONLY=false
for arg in "$@"; do
    case "$arg" in
        --frontend-only) FRONTEND_ONLY=true ;;
        --help|-h)
            echo "Usage: ./deploy.sh [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --frontend-only   Skip backend steps, deploy only the Amplify frontend"
            echo "  --help, -h        Show this help message"
            exit 0
            ;;
    esac
done

# ============================================
# PREREQUISITE CHECKS & INSTALLATION
# ============================================
echo "============================================"
echo "PIM on AWS - One-Click Deploy Script"
echo "============================================"
echo ""
echo "Step 0: Checking and installing prerequisites..."
echo "--------------------------------------------"

# --- Detect OS ---
OS="$(uname -s)"
install_pkg() {
    local pkg="$1"
    case "$OS" in
        Darwin)
            if command -v brew &>/dev/null; then
                echo "Installing $pkg via Homebrew..."
                brew install "$pkg"
            else
                echo "ERROR: Homebrew not found. Install Homebrew first: https://brew.sh"
                echo "  Then re-run this script."
                exit 1
            fi
            ;;
        Linux)
            if command -v yum &>/dev/null; then
                echo "Installing $pkg via yum..."
                sudo yum install -y "$pkg"
            elif command -v apt-get &>/dev/null; then
                echo "Installing $pkg via apt-get..."
                sudo apt-get update -qq && sudo apt-get install -y "$pkg"
            else
                echo "ERROR: No supported package manager found (yum/apt-get). Install $pkg manually."
                exit 1
            fi
            ;;
        *)
            echo "ERROR: Unsupported OS ($OS). Install $pkg manually."
            exit 1
            ;;
    esac
}

# --- Python 3.9+ ---
check_python() {
    local cmd="$1"
    if command -v "$cmd" &>/dev/null; then
        local ver
        ver=$("$cmd" --version 2>&1 | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1)
        local major minor
        major=$(echo "$ver" | cut -d. -f1)
        minor=$(echo "$ver" | cut -d. -f2)
        if [ "$major" -gt "$REQUIRED_PYTHON_MAJOR" ] || { [ "$major" -eq "$REQUIRED_PYTHON_MAJOR" ] && [ "$minor" -ge "$REQUIRED_PYTHON_MINOR" ]; }; then
            echo "$cmd"
            return 0
        fi
    fi
    return 1
}

PYTHON_CMD=""
for candidate in python3 python; do
    if PYTHON_CMD=$(check_python "$candidate"); then
        break
    fi
    PYTHON_CMD=""
done

if [ -z "$PYTHON_CMD" ]; then
    echo "Python ${REQUIRED_PYTHON_MAJOR}.${REQUIRED_PYTHON_MINOR}+ not found. Installing..."
    install_pkg python3
    PYTHON_CMD=$(check_python python3) || { echo "ERROR: Python installation failed or version too old."; exit 1; }
fi
echo "  Python: $($PYTHON_CMD --version)"

# --- Node.js 16+ ---
if command -v node &>/dev/null; then
    NODE_VER=$(node --version | grep -oE '[0-9]+' | head -1)
    if [ "$NODE_VER" -lt "$REQUIRED_NODE_MAJOR" ] 2>/dev/null; then
        echo "Node.js version too old ($(node --version)). Need v${REQUIRED_NODE_MAJOR}+. Installing..."
        install_pkg node
    fi
else
    echo "Node.js not found. Installing..."
    install_pkg node
fi
echo "  Node.js: $(node --version)"

# --- npm (comes with Node, but verify) ---
if ! command -v npm &>/dev/null; then
    echo "npm not found. Installing..."
    install_pkg npm
fi
echo "  npm: $(npm --version)"

# --- AWS CLI 2.x ---
if command -v aws &>/dev/null; then
    AWS_CLI_VER=$(aws --version 2>&1 | grep -oE 'aws-cli/[0-9]+' | grep -oE '[0-9]+')
    if [ "$AWS_CLI_VER" -lt 2 ]; then
        echo "WARNING: AWS CLI v1 detected. Version 2.x is recommended."
        echo "  Install guide: https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html"
    fi
    echo "  AWS CLI: $(aws --version 2>&1)"
else
    echo "ERROR: AWS CLI not found. Install it first:"
    echo "  https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html"
    exit 1
fi

echo ""
echo "All prerequisites satisfied."

# ============================================
# ENVIRONMENT SETUP
# ============================================
echo ""
echo "Setting up environment..."
echo "--------------------------------------------"
export AWS_REGION="${AWS_REGION:-us-east-1}"
export AWS_DEFAULT_REGION="$AWS_REGION"

# Verify AWS credentials are configured
if ! aws sts get-caller-identity &>/dev/null; then
    echo "ERROR: AWS credentials not configured. Please run 'aws configure' or set"
    echo "  AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY environment variables."
    echo "  For SSO: 'aws sso login --profile your-profile'"
    exit 1
fi
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
echo "Account ID: $ACCOUNT_ID"
echo "Region: $AWS_REGION"

# ============================================
# PYTHON VIRTUAL ENVIRONMENT
# ============================================
echo ""
echo "Step 1: Setting up Python virtual environment and dependencies..."
echo "--------------------------------------------"

if [ ! -d "$VENV_DIR" ]; then
    echo "Creating virtual environment at $VENV_DIR..."
    $PYTHON_CMD -m venv "$VENV_DIR"
fi

echo "Activating virtual environment..."
source "$VENV_DIR/bin/activate"

echo "Upgrading pip..."
python3 -m pip install --upgrade pip --quiet

echo "Installing Python dependencies..."
pip install -r requirements.txt --quiet

# --- AWS CDK CLI ---
echo "Installing AWS CDK CLI..."
npm install -g aws-cdk

# ============================================
# CDK BOOTSTRAP CHECK
# ============================================
if [ "$FRONTEND_ONLY" = false ]; then

echo ""
echo "Step 2: Checking CDK bootstrap..."
echo "--------------------------------------------"
REQUIRED_BOOTSTRAP_VERSION=30
CDK_BOOTSTRAP_STACK=$(aws cloudformation describe-stacks --region $AWS_REGION --query "Stacks[?StackName=='CDKToolkit'].StackName" --output text 2>/dev/null || echo "")
CURRENT_BOOTSTRAP_VERSION=$(aws cloudformation describe-stacks --stack-name CDKToolkit --region $AWS_REGION --query "Stacks[0].Outputs[?OutputKey=='BootstrapVersion'].OutputValue" --output text 2>/dev/null || echo "0")

NEEDS_BOOTSTRAP=false
if [ -z "$CDK_BOOTSTRAP_STACK" ] || [ "$CDK_BOOTSTRAP_STACK" == "None" ]; then
    echo "CDK bootstrap not found."
    NEEDS_BOOTSTRAP=true
elif [ "$CURRENT_BOOTSTRAP_VERSION" -lt "$REQUIRED_BOOTSTRAP_VERSION" ] 2>/dev/null; then
    echo "CDK bootstrap version $CURRENT_BOOTSTRAP_VERSION is outdated (need >= $REQUIRED_BOOTSTRAP_VERSION)."
    NEEDS_BOOTSTRAP=true
else
    echo "CDK is already bootstrapped in $AWS_REGION (version $CURRENT_BOOTSTRAP_VERSION)."
fi

if [ "$NEEDS_BOOTSTRAP" = true ]; then
    echo "Running cdk bootstrap..."
    cdk bootstrap aws://$ACCOUNT_ID/$AWS_REGION
    if [ $? -ne 0 ]; then
        echo "CDK bootstrap failed."
        exit 1
    fi
    echo "CDK bootstrap completed."
fi

# ============================================
# CDK DEPLOY
# ============================================
echo ""
echo "Step 3: Deploying CDK stack..."
echo "--------------------------------------------"
cdk deploy $STACK_NAME --require-approval never --context environment=development

echo "CDK deployment complete."

fi # end FRONTEND_ONLY skip

# ============================================
# RETRIEVE STACK OUTPUTS (always needed)
# ============================================
echo ""
echo "Step 4: Retrieving stack outputs..."
echo "--------------------------------------------"

get_output() {
    aws cloudformation describe-stacks \
        --stack-name "$STACK_NAME" --region "$AWS_REGION" \
        --query "Stacks[0].Outputs[?OutputKey==\`$1\`].OutputValue" \
        --output text 2>/dev/null
}

BUCKET=$(get_output DataLakeBucketName)
ATHENA_BUCKET=$(get_output AthenaResultsBucketName)
API_URL=$(get_output ApiGatewayUrl)
USER_POOL_ID=$(get_output UserPoolId)
CLIENT_ID=$(get_output UserPoolClientId)
ETL_WORKFLOW_ARN=$(get_output EtlWorkflowArn)
GLUE_DATABASE=$(get_output GlueCatalogName)
SECRET_NAME="pim-seed-user-credentials"

echo "  Data Lake Bucket:  $BUCKET"
echo "  Athena Bucket:     $ATHENA_BUCKET"
echo "  API URL:           $API_URL"
echo "  User Pool:         $USER_POOL_ID"
echo "  Client ID:         $CLIENT_ID"
echo "  Glue Database:     $GLUE_DATABASE"
echo "  ETL Workflow:      $ETL_WORKFLOW_ARN"

if [ "$FRONTEND_ONLY" = false ]; then

# ============================================
# RETRIEVE SEED USER CREDENTIALS
# ============================================
echo ""
echo "Step 5: Retrieving seed user credentials..."
echo "--------------------------------------------"
sleep 5
CREDS_JSON=$(aws secretsmanager get-secret-value \
    --secret-id "$SECRET_NAME" --region "$AWS_REGION" \
    --query SecretString --output text 2>/dev/null || echo "")

if [ -z "$CREDS_JSON" ] || [ "$CREDS_JSON" = "None" ]; then
    echo "WARNING: Could not retrieve seed credentials yet."
    echo "  Retrieve later: aws secretsmanager get-secret-value --secret-id $SECRET_NAME --query SecretString --output text"
    TEST_USERNAME="admin"
    TEST_PASSWORD=""
else
    TEST_USERNAME="admin"
    TEST_PASSWORD=$(echo "$CREDS_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin)['admin']['password'])")
    echo "Credentials retrieved (admin user)."
fi

# ============================================
# CREATE ICEBERG TABLES
# ============================================
echo ""
echo "Step 6: Creating Iceberg tables..."
echo "--------------------------------------------"
python3 source/scripts/manage_iceberg_tables.py \
    --action recreate-all \
    --bucket "$BUCKET" \
    --database "$GLUE_DATABASE" \
    --region "$AWS_REGION"
echo "Iceberg tables created."

# ============================================
# POPULATE REFERENCE DATA
# ============================================
echo ""
echo "Step 7: Populating reference data..."
echo "--------------------------------------------"
python3 source/scripts/populate_base_tables.py \
    --config source/config/bookstore-production-config.yaml \
    --stack-name "$STACK_NAME" \
    --region "$AWS_REGION"
echo "Reference data populated."

# ============================================
# UPLOAD MOCK DATA
# ============================================
echo ""
echo "Step 8: Uploading mock data to S3..."
echo "--------------------------------------------"
for f in source/mock-data/*.json; do
    aws s3 cp "$f" "s3://$BUCKET/raw/products/" --region "$AWS_REGION"
    echo "  Uploaded $(basename $f)"
done
echo "Mock data uploaded."

# ============================================
# TRIGGER ETL PIPELINE
# ============================================
echo ""
echo "Step 9: Triggering ETL + Data Quality pipeline..."
echo "--------------------------------------------"
EXEC_ARN=$(aws stepfunctions start-execution \
    --state-machine-arn "$ETL_WORKFLOW_ARN" \
    --region "$AWS_REGION" \
    --query 'executionArn' --output text)
echo "Execution ARN: $EXEC_ARN"
echo "Waiting for pipeline to complete (3-5 minutes)..."

while true; do
    STATUS=$(aws stepfunctions describe-execution \
        --execution-arn "$EXEC_ARN" --region "$AWS_REGION" \
        --query 'status' --output text)
    case "$STATUS" in
        SUCCEEDED) echo "ETL + DQ pipeline completed successfully."; break ;;
        FAILED|TIMED_OUT|ABORTED) echo "Pipeline failed with status: $STATUS"; exit 1 ;;
        *) printf "."; sleep 10 ;;
    esac
done

else
    echo ""
    echo "Skipping Steps 2-3, 5-9 (--frontend-only mode)"

    # Still retrieve credentials for validation
    echo ""
    echo "Retrieving seed user credentials..."
    CREDS_JSON=$(aws secretsmanager get-secret-value \
        --secret-id "$SECRET_NAME" --region "$AWS_REGION" \
        --query SecretString --output text 2>/dev/null || echo "")

    if [ -z "$CREDS_JSON" ] || [ "$CREDS_JSON" = "None" ]; then
        TEST_USERNAME="admin"
        TEST_PASSWORD=""
    else
        TEST_USERNAME="admin"
        TEST_PASSWORD=$(echo "$CREDS_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin)['admin']['password'])")
        echo "Credentials retrieved (admin user)."
    fi
fi

# ============================================
# DEPLOY FRONTEND TO AMPLIFY
# ============================================
echo ""
echo "Step 10: Deploying frontend to Amplify..."
echo "--------------------------------------------"

AMPLIFY_APP_ID=$(get_output AmplifyAppId)
IDENTITY_POOL_ID=$(get_output IdentityPoolId)
ASSETS_BUCKET=$(get_output AssetsBucketName)

if [ -z "$AMPLIFY_APP_ID" ]; then
    echo "WARNING: Amplify App ID not found. Skipping frontend deployment."
else
    # Wrap frontend deployment so a build failure doesn't abort the entire script
    set +e
    FRONTEND_FAILED=0

    cat > source/frontend/.env <<EOF
REACT_APP_API_URL=$API_URL
REACT_APP_USER_POOL_ID=$USER_POOL_ID
REACT_APP_USER_POOL_CLIENT_ID=$CLIENT_ID
REACT_APP_IDENTITY_POOL_ID=$IDENTITY_POOL_ID
REACT_APP_REGION=$AWS_REGION
REACT_APP_ENVIRONMENT=development
REACT_APP_ASSETS_BUCKET=$ASSETS_BUCKET
EOF
    echo "  .env generated"

    if [ -f source/frontend/package-lock.json ]; then
        (cd source/frontend && npm ci --silent && npm run build)
    else
        (cd source/frontend && npm install --silent && npm run build)
    fi
    if [ $? -ne 0 ]; then
        echo "  ERROR: Frontend build failed. Skipping Amplify deployment."
        echo ""
        echo "  To deploy the frontend manually:"
        echo "    1. cd source/frontend && npm ci && npm run build"
        echo "    2. cd build && zip -r /tmp/pim-frontend.zip ."
        echo "    3. Deploy via Amplify console: https://${AWS_REGION}.console.aws.amazon.com/amplify/apps/${AMPLIFY_APP_ID}"
        echo "       Or via CLI:"
        echo "         DEPLOY=\$(aws amplify create-deployment --app-id $AMPLIFY_APP_ID --branch-name main --region $AWS_REGION --output json)"
        echo "         JOB_ID=\$(echo \$DEPLOY | python3 -c \"import sys,json; print(json.load(sys.stdin)['jobId'])\")"
        echo "         URL=\$(echo \$DEPLOY | python3 -c \"import sys,json; print(json.load(sys.stdin)['zipUploadUrl'])\")"
        echo "         curl -T /tmp/pim-frontend.zip \"\$URL\" --header \"Content-Type: application/zip\""
        echo "         aws amplify start-deployment --app-id $AMPLIFY_APP_ID --branch-name main --job-id \$JOB_ID --region $AWS_REGION"
        FRONTEND_FAILED=1
    fi

    if [ "$FRONTEND_FAILED" -eq 0 ]; then
        echo "  React build complete"

        (cd source/frontend/build && zip -r -q /tmp/pim-frontend.zip .)

        DEPLOY_RESULT=$(aws amplify create-deployment \
            --app-id "$AMPLIFY_APP_ID" \
            --branch-name main \
            --region "$AWS_REGION" \
            --output json)

        JOB_ID=$(echo "$DEPLOY_RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin)['jobId'])")
        UPLOAD_URL=$(echo "$DEPLOY_RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin)['zipUploadUrl'])")

        curl -s -T /tmp/pim-frontend.zip "$UPLOAD_URL" --header "Content-Type: application/zip" > /dev/null

        aws amplify start-deployment \
            --app-id "$AMPLIFY_APP_ID" \
            --branch-name main \
            --job-id "$JOB_ID" \
            --region "$AWS_REGION" > /dev/null

        echo "  Waiting for Amplify deployment..."
        while true; do
            JOB_STATUS=$(aws amplify get-job \
                --app-id "$AMPLIFY_APP_ID" \
                --branch-name main \
                --job-id "$JOB_ID" \
                --region "$AWS_REGION" \
                --query 'job.summary.status' --output text 2>/dev/null)
            case "$JOB_STATUS" in
                SUCCEED) echo "  Frontend deployed successfully."; break ;;
                FAILED|CANCELLED) echo "  Frontend deployment failed: $JOB_STATUS"; break ;;
                *) printf "."; sleep 5 ;;
            esac
        done
        rm -f /tmp/pim-frontend.zip
    fi

    set -e
fi

# ============================================
# VALIDATION
# ============================================
echo ""
echo "Step 11: Validating deployment..."
echo "--------------------------------------------"

STACK_STATUS=$(aws cloudformation describe-stacks \
    --stack-name $STACK_NAME \
    --query 'Stacks[0].StackStatus' \
    --output text \
    --region $AWS_REGION)

if [ "$STACK_STATUS" == "CREATE_COMPLETE" ] || [ "$STACK_STATUS" == "UPDATE_COMPLETE" ]; then
    echo "Stack status: $STACK_STATUS - Deployment validated."
else
    echo "Stack status: $STACK_STATUS - Deployment may have issues."
    exit 1
fi

# Run API tests if credentials available
if [ -n "$TEST_PASSWORD" ]; then
    echo "Running API validation tests..."
    export API_BASE_URL="$API_URL"
    export USER_POOL_ID CLIENT_ID TEST_USERNAME TEST_PASSWORD
    python3 source/tests/test_products_api.py
    echo "API tests passed."
else
    echo "Skipping API tests (no credentials available)."
fi

# ============================================
# DONE
# ============================================
echo ""
echo "============================================"
echo "Deployment Complete!"
echo "============================================"
echo ""
echo "API URL:      $API_URL"
if [ -n "$AMPLIFY_APP_ID" ]; then
echo "Frontend URL: https://main.${AMPLIFY_APP_ID}.amplifyapp.com"
fi
echo "Credentials:  aws secretsmanager get-secret-value --secret-id $SECRET_NAME --query SecretString --output text --region $AWS_REGION"
echo ""
