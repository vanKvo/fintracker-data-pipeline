# Data Pipeline Service

## Summary
The asynchronous ingestion engine optimized for AWS Step Functions. It processes uploaded bank statements (PDFs) and bank-sync payloads, parses text utilizing AWS Textract, categorizes via `FinTracker_MerchantRegistry` and Comprehend, and pushes normalized rows into the Ledger.

## Local Setup
1. Uses Python 3.12+. Install `poetry`.
2. Run `poetry install`.
3. To test the pipeline logic, use `moto` decorators on pytest suites to emulate S3 and Step Functions locally. 

## Build, Test & Deploy
- **Build**: No heavy web framework. Uses native lambdas. `pip install -t package/` prior to Lambda zipping.
- **Test**: Run `poetry run pytest`.
- **Deploy**: AWS CDK defines both the Lambdas and the `AWS Step Functions State Machine` (ASL definition).

## Orchestration Flow
No direct REST endpoints expose the main engine. Entry is event-driven.

## Architecture

```mermaid
graph TD
    A[S3 Upload] -->|PutObject Event| B(Processor Lambda)
    B -->|StartExecution| C{AWS Step Functions}
    
    C --> D[Step 1: Vision Gatekeeper YOLO-nano]
    D --> E{Step 2: Table Detected?}
    E -->|No| F[Mark as Junk / EventBridge Alert]
    E -->|Yes| G[Step 3: AWS Textract Ingestion]
    
    G --> H[Step 4: Normalizer + Region Map]
    H -->|Fetch/Fallback| I[(MerchantRegistry DynamoDB)]
    
    H --> J[Step 5: Push to Ledger PostgreSQL]
    J --> K[Emit Completion Event]
```
