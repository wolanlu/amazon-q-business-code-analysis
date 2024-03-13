from aws_lambda_powertools import Logger, Tracer
from aws_lambda_powertools.event_handler import APIGatewayRestResolver
from aws_lambda_powertools.event_handler.exceptions import BadRequestError
from aws_lambda_powertools.logging import correlation_paths
from aws_lambda_powertools.utilities.typing import LambdaContext

tracer = Tracer()
logger = Logger()
app = APIGatewayRestResolver()


@app.post("/webhooks/push")
@tracer.capture_method
def push():
    push_event: dict = app.current_event.json_body  # deserialize json str to dict
    if 'type' not in push_event or push_event['type'] != 'PushEvent':
        raise BadRequestError("Not a push event")

    if 'repository' not in push_event or 'url' not in push_event['repository']:
        raise BadRequestError("Missing repository")
    repo_url = push_event['repository']['url']

    if 'payload' not in push_event:
        raise BadRequestError("Missing payload")
    if 'head' not in push_event['payload']:
        raise BadRequestError("Missing head")
    head = push_event['payload']['head']

    if 'ref' not in push_event['payload']:
        raise BadRequestError("Missing ref")
    ref = push_event['payload']['ref']

    logger.info("Starting batch", extra={"repo_url": repo_url, "head": head, "ref": ref})

    return {"repo_url": repo_url, "head": head, "ref": ref}


@logger.inject_lambda_context(correlation_id_path=correlation_paths.API_GATEWAY_REST)
@tracer.capture_lambda_handler
def lambda_handler(event: dict, context: LambdaContext) -> dict:
    return app.resolve(event, context)