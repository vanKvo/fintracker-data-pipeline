# Data Pipeline Infrastructure (Terraform)

Replaces `infrastructure/pipeline_stack.py` (AWS CDK) as this service's IaC, and replaces the
LocalStack-based local setup — this deploys against **real AWS**, dev included. See
`docs/2026-09-16-processing-csv-bank-statement.md` for what the pipeline itself does; this only
covers how it's provisioned.

## Layout

```
terraform/
├── modules/                     # Reusable, environment-agnostic building blocks
│   ├── dynamodb_table/          # Generic PK/SK table, instantiated 2x (job tracker, bank mapping)
│   ├── lambda_function/         # Generic: IAM role + log group + function, parameterized
│   ├── s3_statement_bucket/     # Statement bucket + CORS + the S3 -> Lambda trigger
│   ├── step_functions_pipeline/ # The Gatekeeper -> Extractor -> Normalizer -> LedgerPush state machine
│   └── http_api/                # HTTP API v2 + Cognito JWT authorizer for the two user-facing routes
└── environments/
    └── dev/                     # Root module for dev: wires the modules together with dev's values
        ├── main.tf
        ├── variables.tf
        ├── outputs.tf
        ├── providers.tf
        └── terraform.tfvars.example
```

Adding a new environment (staging, prod) means adding `environments/<name>/` that composes the
same modules with different variable values — the modules themselves never change per
environment. Nothing here is dev-specific except what lives under `environments/dev/`.

## Dev environment: no SSM / Secrets Manager

Per this environment's scope, dev does **not** provision `aws_ssm_parameter` resources. Every
value the application needs (`LEDGER_API_URL`, resource limits, etc.) is
declared in `variables.tf` and passed straight through as plain Lambda environment variables from
`terraform.tfvars`. `core/config.py::get_param()` checks the environment first and only falls back
to SSM when a variable isn't set that way — see that file for the exact fallback.

**This is a deliberate dev-only shortcut, not a new standard.** CLAUDE.md's Python Standards
still says secrets go through SSM SecureString in every real environment; a `staging`/`prod`
`environments/` folder should provision `aws_ssm_parameter` (`SecureString`) resources and NOT
follow this pattern.

## One-time setup

1. **Build the Lambda deployment package** (all seven functions share one artifact, same as the
   old CDK stack's `Code.from_asset(".")`):
   ```bash
   cd services/fintracker-data-pipeline
   pip install -t package/ .
   cp -r src package/src
   ```
2. **Copy and fill in tfvars:**
   ```bash
   cd infrastructure/terraform/environments/dev
   cp terraform.tfvars.example terraform.tfvars
   # edit terraform.tfvars — see inline comments, especially ledger_api_url
   ```
3. **Init and apply:**
   ```bash
   terraform init
   terraform plan
   terraform apply
   ```

## Known dev-environment gap: reaching the Ledger

The Ledger normally runs locally (`mvn spring-boot:run` or its own `docker-compose.yml`, per the
root `CLAUDE.md`) — a Lambda running in real AWS cannot call `localhost`. `ledger_api_url` must
point at something actually reachable from AWS: a tunnel to your local Ledger (ngrok, Cloudflare
Tunnel) for quick iteration, or a real deployed dev Ledger for anything closer to end-to-end.
Everything up through the Normalizer (Gatekeeper, Extractor, Normalizer) works regardless, since
none of those stages call the Ledger — only the final `LedgerPush` step does.

## What's provisioned

- **S3 bucket** (`statements_bucket_name`) with versioning, default encryption, blocked public
  access, CORS (for the browser's direct PUT), and the `PutObject` -> S3 Processor Lambda
  notification. This notification did not actually exist in the old CDK stack or in LocalStack —
  closing that gap is part of this migration, not a behavior change from before.
- **Two DynamoDB tables**, one per repository module — `job_tracker_table_name`
  (`JOB_TRACKER_TABLE`, TTL enabled — `update_job_status` sets a 7-day expiry) and `bank_mapping_table_name`
  (`BANK_MAPPING_TABLE`). All on-demand billing (no per-table base cost — see the cost note
  below), each Lambda's IAM policy scoped to only the table(s) its own handler actually touches.
- **Seven Lambdas**, one IAM role each, least-privilege inline policies (S3, Textract,
  DynamoDB, Step Functions task-token resolution — scoped per function, matching what each
  handler's code actually touches).
- **Step Functions STANDARD state machine** — `Gatekeeper -> Extractor -> Normalizer -> LedgerPush`,
  with the csv-col-mapping-confirmation pause (`waitForTaskToken`) and per-task failure routing.
- **HTTP API (v2)** with a Cognito JWT authorizer, exposing `GET /jobs/{jobId}` and
  `POST /jobs/{jobId}/csv-col-mapping-confirmation`, both authenticated against the shared Cognito User
  Pool passed in via `cognito_user_pool_id`/`cognito_user_pool_client_id`.

## Cost note (dev)

Lambda, Step Functions (Standard, per-state-transition), S3, and DynamoDB (on-demand) all cost
fractions of a cent at dev/test volume — a handful of statement uploads a day is effectively free.
The one real per-use cost is **Textract** (billed per page analyzed), which applies to any PDF/
Image upload regardless of how this infra is provisioned. Keep test files small, and `terraform
destroy` this environment when it's not actively in use.
