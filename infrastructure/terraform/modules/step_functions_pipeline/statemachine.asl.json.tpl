{
  "Comment": "FinTracker Statement Upload Pipeline: Gatekeeper -> Extractor -> Normalizer -> Ledger Push",
  "TimeoutSeconds": ${execution_timeout_seconds},
  "StartAt": "Gatekeeper",
  "States": {
    "Gatekeeper": {
      "Type": "Task",
      "Resource": "arn:aws:states:::lambda:invoke.waitForTaskToken",
      "Parameters": {
        "FunctionName": "${gatekeeper_lambda_arn}",
        "Payload": {
          "job_id.$": "$.job_id",
          "bucket.$": "$.bucket",
          "key.$": "$.key",
          "user_id.$": "$.user_id",
          "statement_id.$": "$.statement_id",
          "account_id.$": "$.account_id",
          "bank_id.$": "$.bank_id",
          "TaskToken.$": "$$.Task.Token"
        }
      },
      "ResultPath": "$.gatekeeperOutput",
      "Catch": [
        {
          "ErrorEquals": ["States.ALL"],
          "ResultPath": "$.error",
          "Next": "PipelineFailed"
        }
      ],
      "Next": "Extractor"
    },
    "Extractor": {
      "Type": "Task",
      "Resource": "arn:aws:states:::lambda:invoke",
      "Parameters": {
        "FunctionName": "${extractor_lambda_arn}",
        "Payload.$": "$"
      },
      "OutputPath": "$.Payload",
      "Catch": [
        {
          "ErrorEquals": ["NoValidTransactionsError"],
          "ResultPath": "$.error",
          "Next": "NoValidTransactions"
        },
        {
          "ErrorEquals": ["States.ALL"],
          "ResultPath": "$.error",
          "Next": "PipelineFailed"
        }
      ],
      "Next": "Normalizer"
    },
    "Normalizer": {
      "Type": "Task",
      "Resource": "arn:aws:states:::lambda:invoke",
      "Parameters": {
        "FunctionName": "${normalizer_lambda_arn}",
        "Payload.$": "$"
      },
      "OutputPath": "$.Payload",
      "Catch": [
        {
          "ErrorEquals": ["States.ALL"],
          "ResultPath": "$.error",
          "Next": "PipelineFailed"
        }
      ],
      "Next": "LedgerPush"
    },
    "LedgerPush": {
      "Type": "Task",
      "Resource": "arn:aws:states:::lambda:invoke",
      "Parameters": {
        "FunctionName": "${ledger_push_lambda_arn}",
        "Payload.$": "$"
      },
      "OutputPath": "$.Payload",
      "Catch": [
        {
          "ErrorEquals": ["States.ALL"],
          "ResultPath": "$.error",
          "Next": "PipelineFailed"
        }
      ],
      "End": true
    },
    "PipelineFailed": {
      "Type": "Fail",
      "Error": "PipelineTaskFailed",
      "Cause": "A pipeline stage failed after recording job status FAILED."
    },
    "NoValidTransactions": {
      "Type": "Fail",
      "Error": "NoValidTransactionsError",
      "Cause": "No transactions could be extracted from the uploaded statement."
    }
  }
}
