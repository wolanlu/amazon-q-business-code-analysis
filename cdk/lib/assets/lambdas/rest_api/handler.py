from datetime import datetime
from typing import Annotated

import boto3
import os
from aws_lambda_powertools import Logger, Tracer
from aws_lambda_powertools.event_handler import APIGatewayRestResolver
from aws_lambda_powertools.event_handler.exceptions import BadRequestError
from aws_lambda_powertools.event_handler.openapi.params import Header
from aws_lambda_powertools.logging import correlation_paths
from aws_lambda_powertools.utilities.typing import LambdaContext

tracer = Tracer()
logger = Logger()
app = APIGatewayRestResolver(enable_validation=True)

aws_batch_client = boto3.client('batch')

batch_job_queue = os.environ.get("BATCH_JOB_QUEUE")
batch_job_definition = os.environ.get("BATCH_JOB_DEFINITION")
s3_bucket = os.environ.get("S3_BUCKET")
q_app_role_arn = os.environ.get("Q_APP_ROLE_ARN")
q_app_user_id = os.environ.get("Q_APP_USER_ID")
q_app_id = os.environ.get("Q_APP_ID")
q_app_index = os.environ.get("Q_APP_INDEX_ID")
prompt_config_param_name = os.environ.get("PROMPT_CONFIG_SSM_PARAM_NAME")

@app.post("/webhooks/push")
@tracer.capture_method
def push(event: Annotated[str, Header(alias="X-GitHub-Event")]):
    if event == 'ping':
        return "pong"
    elif event != "push":
        raise BadRequestError("Invalid event")

    push_event: dict = app.current_event.json_body  # deserialize json str to dict

    if 'repository' not in push_event or 'clone_url' not in push_event['repository']:
        raise BadRequestError("Missing repository")
    clone_url = push_event['repository']['clone_url']

    if 'after' not in push_event:
        raise BadRequestError("Missing after")
    commit_sha = push_event['after']

    if 'ref' not in push_event:
        raise BadRequestError("Missing ref")
    ref = push_event['ref']

    logger.info("Starting batch", extra={"clone_url": clone_url, "commit_sha": commit_sha, "ref": ref})
    submit_job(repo_url=clone_url, commit_sha=commit_sha, ref=ref)

    return {"clone_url": clone_url, "commit_sha": commit_sha, "ref": ref}


@logger.inject_lambda_context(correlation_id_path=correlation_paths.API_GATEWAY_REST)
@tracer.capture_lambda_handler
def lambda_handler(event: dict, context: LambdaContext) -> dict:
    return app.resolve(event, context)

@tracer.capture_method
def submit_job(repo_url, ssh_url="", ssh_key_name="", ref="", commit_sha=""):

    container_overrides = {
        "environment": [
            {
                "name": "REPO_URL",
                "value": repo_url
            },
            {
                "name": "REF",
                "value": ref
            },
            {
                "name": "COMMIT_SHA",
                "value": commit_sha
            },
            {
                "name": "SSH_URL",
                "value": ssh_url
            },
            {
                "name": "SSH_KEY_NAME",
                "value": ssh_key_name
            },
            {
                "name": "AMAZON_Q_APP_ID",
                "value": q_app_id
            },
            {
                "name": "AMAZON_Q_USER_ID",
                "value": q_app_user_id
            },
            {
                "name": "Q_APP_INDEX",
                "value": q_app_index
            },
            {
                "name": "Q_APP_ROLE_ARN",
                "value": q_app_role_arn
            },
            {
                "name": "S3_BUCKET",
                "value": s3_bucket
            },
            {
                "name": "PROMPT_CONFIG_SSM_PARAM_NAME",
                "value": prompt_config_param_name
            }],
        "command": [
            "sh","-c",f"yum -y install python-pip git && pip install boto3 awscli GitPython && aws s3 cp s3://{s3_bucket}/code-processing/generate_documentation_and_ingest_code.py . && python3 generate_documentation_and_ingest_code.py"
        ]
    }

    batch_job_name = f"aws-batch-job-code-analysis{datetime.now().strftime('%Y-%m-%d-%H-%M-%S')}"

    logger.info("Submitting job", extra={"batch_job_name": batch_job_name, "queue": batch_job_queue, "batch_job_definition": batch_job_definition, "container_overrides": container_overrides})
    response = aws_batch_client.submit_job(jobName=batch_job_name,
                                           jobQueue=batch_job_queue,
                                           jobDefinition=batch_job_definition,
                                           containerOverrides=container_overrides)
    logger.info("Job submitted", extra={"response": response})