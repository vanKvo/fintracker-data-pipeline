"""AWS CDK Python stack for Data Pipeline Step Functions State Machine.

Defines the full Statement Upload pipeline as a CDK construct.
Step Function tasks are co-located with their Lambda implementations.

__author__ = "Van Vo"
"""

from __future__ import annotations

import aws_cdk as cdk
from aws_cdk import (
    Duration,
    Stack,
    aws_lambda as _lambda,
    aws_stepfunctions as sfn,
    aws_stepfunctions_tasks as tasks,
    aws_s3 as s3,
    aws_s3_notifications as s3n,
    aws_iam as iam,
    aws_logs as logs,
)
from constructs import Construct


class DataPipelineStack(Stack):
    """AWS CDK Stack for the FinTracker Data Pipeline Service.

    Provisions:
      - 5 Lambda Functions (one per Step Function task)
      - S3 Processor Lambda triggered by S3 PutObject events
      - Step Functions Express State Machine orchestrating the pipeline
      - IAM roles with least-privilege policies
    """

    def __init__(self, scope: Construct, construct_id: str, env_name: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # ─── Shared Lambda Layer (YOLO model weights) ────────────────────────
        yolo_layer = _lambda.LayerVersion(
            self, "YoloModelLayer",
            code=_lambda.Code.from_asset("layers/yolo"),
            compatible_runtimes=[_lambda.Runtime.PYTHON_3_12],
            description="YOLOv8-Nano model weights",
        )

        shared_env = {
            "APP_ENV": env_name,
            "PIPELINE_TABLE": f"FinTracker_DataPipeline",
        }

        # ─── Lambda: S3 Processor (Step Function trigger) ────────────────────
        s3_processor_fn = _lambda.Function(
            self, "S3ProcessorLambda",
            runtime=_lambda.Runtime.PYTHON_3_12,
            handler="src.api.handlers.s3_processor_handler",
            code=_lambda.Code.from_asset("."),
            memory_size=256,
            timeout=Duration.seconds(30),
            environment=shared_env,
        )

        # ─── Lambda: Vision Gatekeeper ────────────────────────────────────────
        gatekeeper_fn = _lambda.Function(
            self, "GatekeeperLambda",
            runtime=_lambda.Runtime.PYTHON_3_12,
            handler="src.api.handlers.gatekeeper_handler",
            code=_lambda.Code.from_asset("."),
            memory_size=1024,
            timeout=Duration.minutes(5),
            layers=[yolo_layer],
            environment={**shared_env, "YOLO_MODEL_PATH": "/opt/yolov8n.pt"},
        )

        # ─── Lambda: Textract Ingestion ───────────────────────────────────────
        ingestion_fn = _lambda.Function(
            self, "IngestionLambda",
            runtime=_lambda.Runtime.PYTHON_3_12,
            handler="src.api.handlers.ingestion_handler",
            code=_lambda.Code.from_asset("."),
            memory_size=512,
            timeout=Duration.minutes(5),
            environment=shared_env,
        )
        ingestion_fn.add_to_role_policy(
            iam.PolicyStatement(actions=["textract:AnalyzeDocument"], resources=["*"])
        )

        # ─── Lambda: Normalizer + Categorizer ────────────────────────────────
        normalizer_fn = _lambda.Function(
            self, "NormalizerLambda",
            runtime=_lambda.Runtime.PYTHON_3_12,
            handler="src.api.handlers.normalizer_handler",
            code=_lambda.Code.from_asset("."),
            memory_size=512,
            timeout=Duration.minutes(3),
            environment=shared_env,
        )
        normalizer_fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=["comprehend:ClassifyDocument"],
                resources=["*"],
            )
        )

        # ─── Lambda: Ledger Push ──────────────────────────────────────────────
        ledger_push_fn = _lambda.Function(
            self, "LedgerPushLambda",
            runtime=_lambda.Runtime.PYTHON_3_12,
            handler="src.api.handlers.ledger_push_handler",
            code=_lambda.Code.from_asset("."),
            memory_size=256,
            timeout=Duration.minutes(3),
            environment={
                **shared_env,
                "LEDGER_API_URL": f"https://api.fintracker.app/{env_name.lower()}",
            },
        )

        # ─── Step Function Tasks ──────────────────────────────────────────────
        gatekeeper_task = tasks.LambdaInvoke(
            self, "GatekeeperTask",
            lambda_function=gatekeeper_fn,
            output_path="$.Payload",
        )

        ingestion_task = tasks.LambdaInvoke(
            self, "IngestionTask",
            lambda_function=ingestion_fn,
            output_path="$.Payload",
        )

        normalizer_task = tasks.LambdaInvoke(
            self, "NormalizerTask",
            lambda_function=normalizer_fn,
            output_path="$.Payload",
        )

        ledger_push_task = tasks.LambdaInvoke(
            self, "LedgerPushTask",
            lambda_function=ledger_push_fn,
            output_path="$.Payload",
        )

        # ─── Step 2: Decision Gate ────────────────────────────────────────────
        decision_gate = sfn.Choice(self, "HasValidPages?")
        no_valid_pages = sfn.Fail(
            self, "NoValidPages",
            error="GatekeeperRejected",
            cause="Uploaded document contained no valid transaction tables.",
        )

        # ─── State Machine Definition ─────────────────────────────────────────
        pipeline_definition = (
            gatekeeper_task
            .next(
                decision_gate
                .when(
                    sfn.Condition.boolean_equals("$.has_valid_pages", False),
                    no_valid_pages,
                )
                .otherwise(
                    ingestion_task
                    .next(normalizer_task)
                    .next(ledger_push_task)
                )
            )
        )

        log_group = logs.LogGroup(self, "PipelineLogs", retention=logs.RetentionDays.ONE_WEEK)

        state_machine = sfn.StateMachine(
            self, "StatementUploadPipeline",
            state_machine_name=f"FinTracker-StatementUpload-{env_name}",
            definition_body=sfn.DefinitionBody.from_chainable(pipeline_definition),
            state_machine_type=sfn.StateMachineType.EXPRESS,
            timeout=Duration.minutes(15),
            logs=sfn.LogOptions(
                destination=log_group,
                level=sfn.LogLevel.ALL,
            ),
        )

        # ─── Grant S3 Processor permission to start the state machine ────────
        state_machine.grant_start_execution(s3_processor_fn)
        s3_processor_fn.add_environment("STATE_MACHINE_ARN", state_machine.state_machine_arn)

        cdk.CfnOutput(self, "StateMachineArn", value=state_machine.state_machine_arn)
