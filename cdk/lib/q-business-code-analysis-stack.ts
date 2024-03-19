import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import { CustomQBusinessConstruct } from './constructs/custom-amazon-q-construct'
import { QIamRoleConstruct } from './constructs/q-iam-role-construct';
import { AwsBatchAnalysisConstruct } from './constructs/aws-batch-analysis-construct';
import {RestApiConstruct} from "./constructs/rest-api-construct";


export class QBusinessCodeAnalysisStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    // Cloudformation Description
    this.templateOptions.description = '(uksb-1tupboc55) - Amazon Q Business Code Analysis stack';

    const qAppRoleName = 'QBusiness-Application-Code-Analysis';

    // Input Project name that satisfies regular expression pattern: [a-zA-Z0-9][a-zA-Z0-9_-]*
    const projectNameParam = new cdk.CfnParameter(this, 'ProjectName', {
      type: 'String',
      description: 'The project name, for example langchain-agents.',
      allowedPattern: '^[a-zA-Z0-9][a-zA-Z0-9_-]*$'
    });

    const qAppUserIdParam = new cdk.CfnParameter(this, 'QAppUserId', {
      type: 'String',
      description: 'The user ID of the Amazon Q Business user. At the time of writing, any value will be accepted.',
    });

    const repositoryUrlParam = new cdk.CfnParameter(this, 'RepositoryUrl', {
      type: 'String',
      description: 'The Git URL of the repository to scan and ingest into Amazon Q Business. Note it should end with .git, i.e. https://github.com/aws-samples/langchain-agents.git',
      allowedPattern: '^https?://.+(\.git)$'
    });

    // Optional Access Token Param Name
    const accessTokenNameParam = new cdk.CfnParameter(this, 'AccessTokenSecretName', {
      type: 'String',
      description: 'Optional. The name of the access token to use to access the repository. It should be the name of the access token stored in the AWS Systems Manager Parameter Store.',
      default: 'None'
    });

    // Check if the repository url is provided
    const repositoryUrl = repositoryUrlParam.valueAsString;
    const qAppUserId = qAppUserIdParam.valueAsString;
    const projectName = projectNameParam.valueAsString;
    const accessTokenName = accessTokenNameParam.valueAsString;

    const qAppName = projectName;

    const qAppRole = new QIamRoleConstruct(this, `QIamConstruct`, {
      roleName: qAppRoleName
    });

    const layer = new cdk.aws_lambda.LayerVersion(this, 'layerWithQBusiness', {
      code: cdk.aws_lambda.Code.fromAsset('lib/assets/lambda-layer/boto3v1-34-40.zip'),
      compatibleRuntimes: [cdk.aws_lambda.Runtime.PYTHON_3_12],
      description: 'Boto3 v1.34.40',
    });

    const qBusinessConstruct = new CustomQBusinessConstruct(this, 'QBusinessAppConstruct', {
      amazon_q_app_name: qAppName,
      amazon_q_app_role_arn: qAppRole.role.roleArn,
      boto3Layer: layer
    });

    qBusinessConstruct.node.addDependency(layer);
    qBusinessConstruct.node.addDependency(qAppRole);

    new cdk.CfnOutput(this, 'QBusinessAppName', {
      value: qAppName,
      description: 'Amazon Q Business Application Name',
    });

    // AWS Batch to run the code analysis
    const awsBatchConstruct = new AwsBatchAnalysisConstruct(this, 'AwsBatchConstruct', {
      qAppRoleArn: qAppRole.role.roleArn,
      qAppName: qAppName,
      repository: repositoryUrl,
      boto3Layer: layer,
      qAppUserId: qAppUserId,
      accessTokenName: accessTokenName
    });

    const restApi = new RestApiConstruct(this, 'RestApiConstruct', {
      boto3Layer: layer,
      jobDefinition: awsBatchConstruct.jobDefinition,
      jobExecutionRole: awsBatchConstruct.jobExecutionRole,
      jobQueue: awsBatchConstruct.jobQueue,
      promptConfig1: awsBatchConstruct.paramStore1,
      promptConfig2: awsBatchConstruct.paramStore2,
      qAppId: qBusinessConstruct.appId,
      qAppIndexId: qBusinessConstruct.indexId,
      qAppRoleArn: qAppRole.role.roleArn,
      qAppUserId: qAppUserId,
      s3Bucket: awsBatchConstruct.s3Bucket

    })

    awsBatchConstruct.node.addDependency(qBusinessConstruct);

  }
}
