#!/usr/bin/env bash
set -euo pipefail

# Usage: ./generate_docker_env_from_stack.sh STACK_NAME OUTPUT_FILE [FRONTEND_HOST]
# Example: ./generate_docker_env_from_stack.sh mediacms-dev .env.dev https://dev.example.com

if (($# < 2)); then
    printf 'Usage: %s STACK_NAME OUTPUT_FILE [FRONTEND_HOST]\n' "${0##*/}" >&2
    printf 'Example: %s mediacms-dev .env.dev https://dev.example.com\n' "${0##*/}" >&2
    exit 64
fi

stack_name=$1
output_file=$2
frontend_host=${3:-https://localhost}

if [[ -z "$stack_name" || -z "$output_file" ]]; then
    printf '%s\n' 'Stack name and output file are required' >&2
    exit 64
fi

# Detect environment from stack name
if [[ "$stack_name" == *-prod ]]; then
    environment="prod"
    project_name="mediacms-prod"
elif [[ "$stack_name" == *-dev ]]; then
    environment="dev"
    project_name="mediacms-dev"
else
    printf 'Warning: Cannot detect environment from stack name, defaulting to dev\n' >&2
    environment="dev"
    project_name="mediacms-dev"
fi

printf 'Fetching outputs from CloudFormation stack: %s\n' "$stack_name" >&2

# Fetch stack outputs
outputs=$(aws --profile default --region us-east-1 \
    cloudformation describe-stacks \
    --stack-name "$stack_name" \
    --query 'Stacks[0].Outputs[*].[OutputKey,OutputValue]' \
    --output text)

if [[ -z "$outputs" ]]; then
    printf 'Error: No outputs found for stack %s\n' "$stack_name" >&2
    exit 1
fi

# Parse outputs into associative array
declare -A stack_outputs
while IFS=$'\t' read -r key value; do
    stack_outputs["$key"]="$value"
done <<< "$outputs"

# Fetch runtime credentials from Secrets Manager
secret_arn="${stack_outputs[RuntimeCredentialsSecretArn]}"
if [[ -z "$secret_arn" ]]; then
    printf 'Error: RuntimeCredentialsSecretArn not found in stack outputs\n' >&2
    exit 1
fi

printf 'Fetching runtime credentials from Secrets Manager...\n' >&2
secret_json=$(aws --profile default --region us-east-1 \
    secretsmanager get-secret-value \
    --secret-id "$secret_arn" \
    --query 'SecretString' \
    --output text)

# Parse credentials
aws_access_key=$(echo "$secret_json" | python3 -c "import sys, json; print(json.load(sys.stdin)['AWS_ACCESS_KEY_ID'])")
aws_secret_key=$(echo "$secret_json" | python3 -c "import sys, json; print(json.load(sys.stdin)['AWS_SECRET_ACCESS_KEY'])")

# Generate .env file
cat > "$output_file" <<EOF
# MediaCMS Docker Environment - Generated from CloudFormation stack: $stack_name
# Generated at: $(date -u +"%Y-%m-%d %H:%M:%S UTC")
# DO NOT commit this file - it contains secrets

# Compose Configuration
COMPOSE_PROJECT_NAME=$project_name

# Image Configuration (UPDATE THIS with your release tag)
MEDIACMS_IMAGE=ghcr.io/caoyjie/mediacms:latest

# Web Service Port
MEDIACMS_WEB_PORT=8080

# Application Configuration
FRONTEND_HOST=$frontend_host
PORTAL_NAME=MediaCMS ${environment^}
SECRET_KEY=GENERATE_YOUR_OWN_SECRET_KEY_50_CHARS_MIN

# Admin User (change these!)
ADMIN_USER=admin
ADMIN_EMAIL=admin@example.com
ADMIN_PASSWORD=CHANGE_THIS_PASSWORD

# PostgreSQL Configuration
POSTGRES_HOST=db
POSTGRES_PORT=5432
POSTGRES_NAME=mediacms
POSTGRES_USER=mediacms
POSTGRES_PASSWORD=CHANGE_THIS_DB_PASSWORD

# Redis Configuration
REDIS_LOCATION=redis://redis:6379/1

# AWS Configuration
AWS_REGION=us-east-1
AWS_DEFAULT_REGION=us-east-1
AWS_ENVIRONMENT=$environment

# AWS Runtime Credentials (from stack)
AWS_ACCESS_KEY_ID=$aws_access_key
AWS_SECRET_ACCESS_KEY=$aws_secret_key

# AWS Resources (from stack outputs)
AWS_MEDIA_BUCKET=${stack_outputs[MediaBucketName]}
AWS_MEDIACONVERT_ROLE_ARN=${stack_outputs[MediaConvertServiceRoleArn]}

# MediaConvert Templates (from stack)
AWS_MEDIACONVERT_VIDEO_TEMPLATE=${stack_outputs[VideoHlsJobTemplateName]}
AWS_MEDIACONVERT_AUDIO_TEMPLATE=${stack_outputs[AudioHlsJobTemplateName]}

# CloudFront Configuration (from stack)
AWS_CLOUDFRONT_DISTRIBUTION_ID=${stack_outputs[MediaDistributionId]}
AWS_CLOUDFRONT_DOMAIN=${stack_outputs[MediaDistributionDomainName]}
AWS_CLOUDFRONT_KEY_GROUP_ID=${stack_outputs[MediaKeyGroupId]}
AWS_CLOUDFRONT_PUBLIC_KEY_ID=${stack_outputs[CloudFrontPublicKeyCurrentId]}

# CloudFront Private Key Path (you need to download this separately)
# AWS_CLOUDFRONT_PRIVATE_KEY_PATH=/etc/mediacms/secrets/cloudfront-private-key.pem

# Worker Configuration
CELERY_WORKER_CONCURRENCY=1

# Timezone
TZ=UTC

# Debug Mode (set to False for production)
DEBUG=$([[ "$environment" == "prod" ]] && echo "False" || echo "True")
EOF

chmod 600 "$output_file"

printf '\n✅ Environment file generated: %s\n' "$output_file" >&2
printf '\n⚠️  IMPORTANT: Update these values before using:\n' >&2
printf '   - MEDIACMS_IMAGE: Change from :latest to your release tag (e.g., :bed7a63)\n' >&2
printf '   - SECRET_KEY: Generate with: openssl rand -base64 48\n' >&2
printf '   - ADMIN_PASSWORD: Set a strong password\n' >&2
printf '   - POSTGRES_PASSWORD: Set a strong password\n' >&2
printf '   - FRONTEND_HOST: Set your actual domain\n' >&2
printf '   - AWS_CLOUDFRONT_PRIVATE_KEY_PATH: Download private key and set path\n' >&2
printf '\n📋 To download CloudFront private key, see: docs/PRODUCTION-DEPLOYMENT-GUIDE.md\n' >&2
