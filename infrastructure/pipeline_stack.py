"""AWS CDK Python stack for Data Pipeline Step Functions State Machine.

SUPERSEDED — kept for reference only. This service's IaC moved to Terraform
(infrastructure/terraform/, see that directory's README); do not deploy from or extend this
file. It predates the S3 bucket/notification and DynamoDB table now provisioned in Terraform,
so it was never a complete definition of this service's infrastructure on its own.

Defines the full Statement Upload pipeline as a CDK construct.
Step Function tasks are co-located with their Lambda implementations.

__author__ = "Van Vo"
"""

from __future__ import annotations

import aws_cdk as cdk
from aws_cdk import (
    Duration,
    Stack,
    aws_apigatewayv2 as apigwv2,
    aws_apigatewayv2_authorizers as apigwv2_authorizers,
    aws_apigatewayv2_integrations as apigwv2_integrations,
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
      - Lambda Functions (one per Step Function task, plus the standalone
        mapping-confirmation callback endpoint)
      - S3 Processor Lambda triggered by S3 PutObject events
      - Step Functions STANDARD State Machine orchestrating the pipeline
      - IAM roles with least-privilege policies

    STANDARD, not EXPRESS: the Gatekeeper task uses the waitForTaskToken
    callback pattern (REQ-DP-01 "User Mapping Confirmation Gate") and can
    pause for as long as a user takes to respond to the mapping dialog.
    EXPRESS workflows cap total execution at 5 minutes, which is incompatible
    with pausing on human input; STANDARD supports up to a year and bills
    per state transition rather than per invocation, which is the right
    trade for a workflow that spends most of its wall-clock time idle.
    """

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        env_name: str,
        cognito_user_pool_id: str,
        cognito_user_pool_client_id: str,
        **kwargs,
    ) -> None:
        """
        Args:
            cognito_user_pool_id, cognito_user_pool_client_id: The shared
                Cognito User Pool this stack's API Gateway JWT authorizer
                validates against — the same pool every other service's API
                Gateway trusts, per CLAUDE.md's "API Gateway validates
                Cognito JWTs" request flow. Passed in rather than created
                here, since the pool is shared across services and owned
                by whichever stack provisions identity infrastructure.
        """
        super().__init__(scope, construct_id, **kwargs)

        shared_env = {
            "APP_ENV": env_name,
            "PIPELINE_TABLE": "FinTracker_DataPipeline",
        }

        # ─── Lambda: S3 Processor (Step Function trigger) ────────────────────
        s3_processor_fn = _lambda.Function(
            self, "S3ProcessorLambda",
            runtime=_lambda.Runtime.PYTHON_3_12,
            handler="src.statement_ingestion.orchestrator.s3_processor_handler",
            code=_lambda.Code.from_asset("."),
            memory_size=256,
            timeout=Duration.seconds(30),
            environment=shared_env,
        )

        # ─── Lambda: Gatekeeper ────────────────────────────────────────────────
        gatekeeper_fn = _lambda.Function(
            self, "GatekeeperLambda",
            runtime=_lambda.Runtime.PYTHON_3_12,
            handler="src.gatekeeper.handler.gatekeeper_handler",
            code=_lambda.Code.from_asset("."),
            memory_size=512,
            timeout=Duration.minutes(2),
            environment={
                **shared_env,
                "MAX_STATEMENT_FILE_SIZE_BYTES": str(25 * 1024 * 1024),
                "MAX_STATEMENT_PDF_PAGES": "50",
            },
        )
        # Resolving a waitForTaskToken task requires SendTaskSuccess/Failure.
        gatekeeper_fn.add_to_role_policy(
            iam.PolicyStatement(actions=["states:SendTaskSuccess", "states:SendTaskFailure"], resources=["*"])
        )

        # ─── Lambda: Mapping Confirmation (API Gateway-invoked, out of band) ──
        mapping_confirmation_fn = _lambda.Function(
            self, "MappingConfirmationLambda",
            runtime=_lambda.Runtime.PYTHON_3_12,
            handler="src.gatekeeper.mapping_confirmation_handler.mapping_confirmation_handler",
            code=_lambda.Code.from_asset("."),
            memory_size=256,
            timeout=Duration.seconds(30),
            environment=shared_env,
        )
        mapping_confirmation_fn.add_to_role_policy(
            iam.PolicyStatement(actions=["states:SendTaskSuccess"], resources=["*"])
        )
        # Routed via the HTTP API + Cognito authorizer defined below, not a
        # Function URL — it must sit behind the same JWT/X-Internal-User-Id
        # trust boundary as every other user-facing route (REQ-DP-05).

        # ─── Lambda: Extractor ─────────────────────────────────────────────────
        extractor_fn = _lambda.Function(
            self, "ExtractorLambda",
            runtime=_lambda.Runtime.PYTHON_3_12,
            handler="src.extractor.handler.ingestion_handler",
            code=_lambda.Code.from_asset("."),
            memory_size=1024,
            timeout=Duration.minutes(5),
            environment=shared_env,
        )
        # Textract's AnalyzeDocument does not support resource-level ARN
        # scoping (documented exception, REQ-DP-07 B. Constraints) — this
        # wildcard is the ceiling of what IAM allows for this action, not
        # an oversight.
        extractor_fn.add_to_role_policy(
            iam.PolicyStatement(actions=["textract:AnalyzeDocument"], resources=["*"])
        )

        # ─── Lambda: Normalizer + Categorizer ────────────────────────────────
        normalizer_fn = _lambda.Function(
            self, "NormalizerLambda",
            runtime=_lambda.Runtime.PYTHON_3_12,
            handler="src.normalizer.handler.normalizer_handler",
            code=_lambda.Code.from_asset("."),
            memory_size=512,
            timeout=Duration.minutes(3),
            environment=shared_env,
        )
        # Comprehend custom classifiers DO support resource-level ARN
        # scoping — REQ-DP-07 fix: scope to this account/region's specific
        # endpoint instead of a blanket wildcard.
        normalizer_fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=["comprehend:ClassifyDocument"],
                resources=[
                    f"arn:aws:comprehend:{self.region}:{self.account}:document-classifier-endpoint/fintracker-merchant-classifier"
                ],
            )
        )

        # ─── Lambda: Ledger Push ──────────────────────────────────────────────
        ledger_push_fn = _lambda.Function(
            self, "LedgerPushLambda",
            runtime=_lambda.Runtime.PYTHON_3_12,
            handler="src.data_dispatcher.handler.ledger_push_handler",
            code=_lambda.Code.from_asset("."),
            memory_size=256,
            timeout=Duration.minutes(5),
            environment={
                **shared_env,
                "LEDGER_API_URL": f"https://api.fintracker.app/{env_name.lower()}",
                "MAX_CONCURRENT_LEDGER_PUSHES": "10",
            },
        )
        # INTERNAL_API_KEY is read from SSM SecureString (core/config.py),
        # not an environment variable — this is the read permission for
        # that specific parameter path, not a blanket ssm:GetParameter.
        ledger_push_fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=["ssm:GetParametersByPath"],
                resources=[f"arn:aws:ssm:{self.region}:{self.account}:parameter/FinTracker/{env_name}/DataPipeline/*"],
            )
        )

        # ─── Step Function Tasks ──────────────────────────────────────────────
        gatekeeper_task = tasks.LambdaInvoke(
            self, "GatekeeperTask",
            lambda_function=gatekeeper_fn,
            integration_pattern=sfn.IntegrationPattern.WAIT_FOR_TASK_TOKEN,
            payload=sfn.TaskInput.from_object({
                "job_id.$": "$.job_id",
                "bucket.$": "$.bucket",
                "key.$": "$.key",
                "user_id.$": "$.user_id",
                "statement_id.$": "$.statement_id",
                "account_id.$": "$.account_id",
                "bank_id.$": "$.bank_id",
                "TaskToken": sfn.JsonPath.task_token,
            }),
            # result_path (not the default full-state replacement) so the
            # original input — bucket/key/user_id/statement_id/account_id —
            # survives into the Extractor task even for a job that paused
            # and resumed later via confirm_column_mapping's send_task_success,
            # whose output only carries {job_id, confirmed_mapping}.
            result_path="$.gatekeeperOutput",
            # No fixed heartbeat: a user reviewing the mapping dialog may
            # legitimately take minutes. REQ-DP-01 F. Error Handling —
            # MAPPING_CONFIRMATION_TIMEOUT is enforced by the overall
            # execution timeout below, not a per-task heartbeat.
        )

        extractor_task = tasks.LambdaInvoke(
            self, "ExtractorTask",
            lambda_function=extractor_fn,
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

        # ─── REQ-DP-03: Failure Handling ───────────────────────────────────────
        # Every task's own handler already records job status FAILED before
        # re-raising (see each handler's try/except) — these Catch blocks
        # exist so the *execution* also surfaces the failure to Step
        # Functions/CloudWatch, rather than only to the Job Tracker.
        pipeline_failed = sfn.Fail(
            self, "PipelineFailed",
            error="PipelineTaskFailed",
            cause="A pipeline stage failed after recording job status FAILED.",
        )
        for task in (gatekeeper_task, extractor_task, normalizer_task, ledger_push_task):
            task.add_catch(pipeline_failed, errors=["States.ALL"], result_path="$.error")

        no_valid_transactions = sfn.Fail(
            self, "NoValidTransactions",
            error="NoValidTransactionsError",
            cause="No transactions could be extracted from the uploaded statement.",
        )
        extractor_task.add_catch(
            no_valid_transactions,
            errors=["NoValidTransactionsError"],
            result_path="$.error",
        )

        # ─── State Machine Definition ─────────────────────────────────────────
        pipeline_definition = gatekeeper_task.next(extractor_task).next(normalizer_task).next(ledger_push_task)

        log_group = logs.LogGroup(self, "PipelineLogs", retention=logs.RetentionDays.ONE_WEEK)

        state_machine = sfn.StateMachine(
            self, "StatementUploadPipeline",
            state_machine_name=f"FinTracker-StatementUpload-{env_name}",
            definition_body=sfn.DefinitionBody.from_chainable(pipeline_definition),
            state_machine_type=sfn.StateMachineType.STANDARD,
            timeout=Duration.hours(24),  # REQ-DP-01 F. Error Handling — MAPPING_CONFIRMATION_TIMEOUT
            logs=sfn.LogOptions(
                destination=log_group,
                level=sfn.LogLevel.ALL,
            ),
        )

        # ─── Lambda: Job Status (API Gateway-invoked, polling) ────────────────
        status_fn = _lambda.Function(
            self, "JobStatusLambda",
            runtime=_lambda.Runtime.PYTHON_3_12,
            handler="src.statement_ingestion.status_handler.job_status_handler",
            code=_lambda.Code.from_asset("."),
            memory_size=128,
            timeout=Duration.seconds(10),
            environment=shared_env,
        )

        # ─── HTTP API: job status polling + mapping confirmation ─────────────
        # REQ-DP-01/REQ-DP-02's UI-facing surface. A Cognito JWT authorizer
        # (HTTP API v2, not REST API v1 — cheaper and sufficient for two
        # simple routes) validates the token; a parameter mapping copies the
        # JWT's `sub` claim into X-Internal-User-Id on the way to each Lambda,
        # mirroring how API Gateway is documented to inject that header for
        # every other service (CLAUDE.md's request-flow section) — so these
        # handlers use the exact same trust boundary as the Ledger's own API,
        # never a client-supplied identity (REQ-DP-05).
        authorizer = apigwv2_authorizers.HttpJwtAuthorizer(
            "CognitoAuthorizer",
            jwt_issuer=f"https://cognito-idp.{self.region}.amazonaws.com/{cognito_user_pool_id}",
            jwt_audience=[cognito_user_pool_client_id],
        )

        http_api = apigwv2.HttpApi(
            self, "DataPipelineHttpApi",
            api_name=f"FinTracker-DataPipeline-{env_name}",
            default_authorizer=authorizer,
        )

        user_id_header_mapping = apigwv2.ParameterMapping().append_header(
            "X-Internal-User-Id", apigwv2.MappingValue("$context.authorizer.jwt.claims.sub")
        )

        http_api.add_routes(
            path="/jobs/{jobId}",
            methods=[apigwv2.HttpMethod.GET],
            integration=apigwv2_integrations.HttpLambdaIntegration(
                "JobStatusIntegration", status_fn, parameter_mapping=user_id_header_mapping
            ),
        )
        http_api.add_routes(
            path="/jobs/{jobId}/mapping-confirmation",
            methods=[apigwv2.HttpMethod.POST],
            integration=apigwv2_integrations.HttpLambdaIntegration(
                "MappingConfirmationIntegration", mapping_confirmation_fn, parameter_mapping=user_id_header_mapping
            ),
        )

        cdk.CfnOutput(self, "DataPipelineApiUrl", value=http_api.api_endpoint)

        # ─── Grant S3 Processor permission to start the state machine ────────
        state_machine.grant_start_execution(s3_processor_fn)
        s3_processor_fn.add_environment("STATE_MACHINE_ARN", state_machine.state_machine_arn)

        cdk.CfnOutput(self, "StateMachineArn", value=state_machine.state_machine_arn)
