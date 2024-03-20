import {Construct} from "constructs";
import * as lambda from "aws-cdk-lib/aws-lambda";
import {EcsJobDefinition, IJobQueue, } from "aws-cdk-lib/aws-batch";
import {Role} from "aws-cdk-lib/aws-iam";
import {IBucket} from "aws-cdk-lib/aws-s3";
import {IParameter} from "aws-cdk-lib/aws-ssm";
import {aws_apigateway, aws_iam, Duration, Stack, StackProps} from "aws-cdk-lib";

export interface RestApiProps extends StackProps {
    boto3Layer: lambda.LayerVersion;
    promptConfig1: IParameter;
    promptConfig2: IParameter;
    s3Bucket: IBucket;
    qAppUserId: string;
    qAppRoleArn: string;
    qAppId: string;
    qAppIndexId: string
    jobExecutionRole: Role;
    jobDefinition: EcsJobDefinition;
    jobQueue: IJobQueue;

}

const defaultProps: Partial<RestApiProps> = {};
export class RestApiConstruct extends Construct {
    constructor(scope: Construct, name: string, props: RestApiProps) {
        super(scope, name);

        props = {...defaultProps, ...props};

        const awsAccountId = Stack.of(this).account;
        const region = Stack.of(this).region;

        // Role to submit job
        const submitJobRole = new aws_iam.Role(this, 'QBusinessSubmitJobRole', {
            assumedBy: new aws_iam.ServicePrincipal('lambda.amazonaws.com'),
        });

        submitJobRole.addToPolicy(new aws_iam.PolicyStatement({
            actions: [
                "qbusiness:ListApplications",
                "qbusiness:ListIndices",
            ],
            resources: [
                `*`,
            ],
        }));

        submitJobRole.addToPolicy(new aws_iam.PolicyStatement({
            actions: [
                "iam:PassRole",
            ],
            resources: [props.jobExecutionRole.roleArn],
        }));

        // Submit Job Role CloudWatch Logs
        submitJobRole.addToPolicy(new aws_iam.PolicyStatement({
            actions: [
                "logs:CreateLogGroup",
                "logs:CreateLogStream",
                "logs:PutLogEvents",
            ],
            resources: [
                `arn:aws:logs:${region}:${awsAccountId}:log-group:/aws/lambda/*`,
            ],
        }));

        props.jobDefinition.grantSubmitJob(submitJobRole, props.jobQueue);

        const powertools_layer = lambda.LayerVersion.fromLayerVersionArn(
            this,
            "lambda-powertools",
            `arn:aws:lambda:${region}:017000801446:layer:AWSLambdaPowertoolsPythonV2:67`
    )

        const backend = new lambda.Function(this, 'RestAPILambda', {
            code: lambda.Code.fromAsset('lib/assets/lambdas/rest_api'),
            handler: 'handler.lambda_handler',
            runtime: lambda.Runtime.PYTHON_3_12,
            environment: {
                BATCH_JOB_DEFINITION: props.jobDefinition.jobDefinitionArn,
                BATCH_JOB_QUEUE: props.jobQueue.jobQueueArn,
                Q_APP_ID: props.qAppId,
                Q_APP_INDEX_ID: props.qAppIndexId,
                Q_APP_ROLE_ARN: props.qAppRoleArn,
                S3_BUCKET: props.s3Bucket.bucketName,
                Q_APP_USER_ID: props.qAppUserId,
                PROMPT_CONFIG_SSM_PARAM_NAME1: props.promptConfig1.parameterName,
                PROMPT_CONFIG_SSM_PARAM_NAME2: props.promptConfig2.parameterName,
                POWERTOOLS_SERVICE_NAME: "RestAPI"
            },
            layers: [props.boto3Layer,powertools_layer],
            role: submitJobRole,
            timeout: Duration.seconds(30),
            memorySize: 2048,
        });

        const api = new aws_apigateway.LambdaRestApi(
            this,
            "CodeAnalysisApi",
            {
                handler: backend,
                defaultMethodOptions: {
                    // webhooks must be available without authorization
                    authorizationType: aws_apigateway.AuthorizationType.NONE
                },
            }
        )
    }
}