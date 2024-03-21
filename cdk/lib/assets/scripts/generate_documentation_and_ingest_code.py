import json
import logging
import os
import uuid

import boto3
import github

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

s3_client = boto3.client('s3', endpoint_url='https://s3.amazonaws.com', use_ssl=True)
ssm = boto3.client('ssm')
secretsmanager = boto3.client('secretsmanager')
amazon_q = boto3.client('qbusiness')
amazon_q_app_id = os.environ['AMAZON_Q_APP_ID']
amazon_q_user_id = os.environ['AMAZON_Q_USER_ID']
s3_bucket = os.environ['S3_BUCKET']
index_id = os.environ['Q_APP_INDEX']
role_arn = os.environ['Q_APP_ROLE_ARN']
repo = os.environ['REPO_URL']
access_token_name = os.environ['ACCESS_TOKEN_NAME']
prompt_file_doc_config_param_name = os.environ['PROMPT_CONFIG_SSM_PARAM_NAME1']
prompt_file_diff_doc_config_param_name = os.environ['PROMPT_CONFIG_SSM_PARAM_NAME2']
# checkout specific ref and commit
ref = os.environ.get('REF')
commit = os.environ.get('COMMIT_SHA')
commit_user = os.environ.get('COMMIT_USER')
repo_owner, repo_name = repo.split('/')[-2:]
repo_name = repo_name.replace('.git', '')


def main():
    logger.info(f"Getting commit data... {commit}")
    repo_ref = retrieve_repo(access_token_name)
    files = get_commit_data(repo_ref, commit)
    process_commit_files(files, commit, repo_ref)
    logger.info(f"Finished processing commit {commit} files")


def ask_question_with_attachment(prompt, filename, file_str):
    answer = amazon_q.chat_sync(
        applicationId=amazon_q_app_id,
        userId=amazon_q_user_id,
        userMessage=prompt,
        attachments=[
            {
                'data': file_str,
                'name': filename
            },
        ],
    )
    return answer['systemMessage']


def upload_prompt_answer_and_file_name(filename, commit_sha, prompt, answer, repo_url, prompt_type):
    cleaned_file_name = os.path.join(repo_url[:-4], '/'.join(filename.split('/')[1:]))
    amazon_q.batch_put_document(
        applicationId=amazon_q_app_id,
        indexId=index_id,
        roleArn=role_arn,
        documents=[
            {
                'id': str(uuid.uuid5(uuid.NAMESPACE_URL, f"{cleaned_file_name}?prompt={prompt_type}")),
                'contentType': 'PLAIN_TEXT',
                'title': cleaned_file_name,
                'content': {
                    'blob': f"{cleaned_file_name} | {commit_sha} | {prompt} | {answer}".encode('utf-8')
                },
                'attributes': [
                    {
                        'name': 'url',
                        'value': {
                            'stringValue': cleaned_file_name
                        }
                    },
                    {
                        'name': 'commit_sha',
                        'value': {
                            'stringValue': commit_sha
                        }
                    }
                ]
            },
        ]
    )


def save_to_s3(bucket_name, filepath, folder, documentation):
    filepath = filepath + ".out"
    s3_client.put_object(Bucket=bucket_name, Key=f'{folder}/{repo_name}/{filepath}', Body=documentation.encode('utf-8'))
    logger.info(f"Saved {filepath} to S3")


def get_access_token(secret_name):
    response = secretsmanager.get_secret_value(SecretId=secret_name)
    return response['SecretString']


def get_questions_from_param_store(prompt_config_param_name):
    response = ssm.get_parameter(Name=prompt_config_param_name)['Parameter']['Value']
    return json.loads(response)


def include_file_type(filename):
    if not filename.endswith(('.png', '.jpg', '.jpeg', '.gif', '.zip')) and not filename.startswith('.'):
        return True
    else:
        return False


def check_if_missing_response(answer):
    if answer == "Sorry, I could not find relevant information to complete your request.":
        return True
    else:
        return False


def retrieve_repo(token_name):
    token = get_access_token(token_name)
    g = github.Github(token)
    return g.get_repo(f"{repo_owner}/{repo_name}")


def get_commit_data(repo_ref, commit_sha):
    commit_data = repo_ref.get_commit(commit_sha)
    return commit_data.files


def get_commit_file_data(repo_ref, filename, commit_sha):
    file_data = repo_ref.get_contents(filename, ref=commit_sha)
    return file_data.decoded_content.decode('utf-8')


def generate_file_doc(prompts, file_str, prefix_path, file_path, commit_sha):
    answers = ""
    for idx, prompt_data in enumerate(prompts):
        prompt, prompt_type = prompt_data
        answer = ask_question_with_attachment(prompt, file_path, file_str)
        if not check_if_missing_response(answer):
            upload_prompt_answer_and_file_name(file_path, commit_sha, prompt, answer,
                                               repo, prompt_type)
            answers = answers + f"{idx+1}. {prompt}:\n\n{answer}\n\n"
    save_to_s3(s3_bucket, file_path, f"documentation/{prefix_path}", answers)
    return None


def generate_file_diff_summary(prompts, file_str, prefix_path, file_path):
    answers = ""
    for idx, prompt in enumerate(prompts):
        answer = ask_question_with_attachment(prompt, file_path, file_str)
        if not check_if_missing_response(answer):
            answers = answers + f"{idx+1}. {prompt}:\n\n{answer}\n\n"
    save_to_s3(s3_bucket, file_path, f"documentation/{prefix_path}", answers)
    return answers


def create_github_pr(repo_ref, commit_sha, commit_content, branch="develop"):
    summary = f"PR request for {commit_sha}"
    source_branch = ref.replace("refs/heads/", "")
    pull = repo_ref.create_pull(
        title=summary,
        head=f"{commit_user}:{source_branch}@{{{commit_sha}}}",
        base=branch,
        body=commit_content
    )


def process_commit_files(files, commit_sha, repo_ref):
    prefix_commit = f"{repo_name}/commit/{commit_sha}/"
    prefix_whole_doc = f"{repo_name}/whole/"
    file_diff_summary_prompts = get_questions_from_param_store(prompt_file_diff_doc_config_param_name)
    file_doc_gen_prompts = get_questions_from_param_store(prompt_file_doc_config_param_name)
    pr_changes = "Description of changes:\n\n"
    for file_data in files:
        filename = file_data['filename']
        logger.info(f"Started processing data for {filename}")
        if include_file_type(filename):
            logger.info(f"Examining content of {filename}")
            file_content = get_commit_file_data(repo_ref, filename, commit_sha)
            generate_file_doc(file_doc_gen_prompts, file_content, prefix_whole_doc, filename, commit_sha)
            logger.info(f"Examining commit changes for {filename}")
            file_diff = file_data['patch']
            file_changes = generate_file_diff_summary(file_diff_summary_prompts, file_diff, prefix_commit, filename)
            pr_changes = pr_changes + f"## {filename}\n\n{file_changes}\n\n"
    logger.info(f"Creating PR for {commit_sha}")
    create_github_pr(repo_ref, commit_sha, pr_changes)


if __name__ == "__main__":
    main()
