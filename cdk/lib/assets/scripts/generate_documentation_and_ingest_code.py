import datetime
import json
import logging
import os
import shutil
import tempfile
import time
import uuid

import boto3
import git

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

amazon_q = boto3.client('qbusiness')
s3_client = boto3.client('s3', endpoint_url='https://s3.amazonaws.com', use_ssl=True)
ssm = boto3.client('ssm')
amazon_q_app_id = os.environ['AMAZON_Q_APP_ID']
amazon_q_user_id = os.environ['AMAZON_Q_USER_ID']
s3_bucket = os.environ['S3_BUCKET']
index_id = os.environ['Q_APP_INDEX']
role_arn = os.environ['Q_APP_ROLE_ARN']
repo = os.environ['REPO_URL']
prompt_config_param_name = os.environ['PROMPT_CONFIG_SSM_PARAM_NAME']
# Optional retrieve the SSH URL and SSH_KEY_NAME for the repository
ssh = os.environ.get('SSH_URL')
ssh_key_name = os.environ.get('SSH_KEY_NAME')
# checkout specific ref and commit
ref = os.environ.get('REF', 'main')
commit = os.environ.get('COMMIT_SHA', 'HEAD')


def main():
    logger.info(f"Processing repository... {repo}")
    # If ssh_url ends with .git then process it
    if ssh and ssh.endswith('.git'):
        process_repository(repo, ssh)
    else:
        process_repository(repo)
    logger.info(f"Finished processing repository {repo}")


def ask_question_with_attachment(prompt, filename):
    data = open(filename, 'rb')
    answer = amazon_q.chat_sync(
        applicationId=amazon_q_app_id,
        userId=amazon_q_user_id,
        userMessage=prompt,
        attachments=[
            {
                'data': data.read(),
                'name': filename
            },
        ],
    )
    return answer['systemMessage']


def upload_prompt_answer_and_file_name(filename, prompt, answer, repo_url, prompt_type):
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
                    'blob': f"{cleaned_file_name} | {prompt} | {answer}".encode('utf-8')
                },
                'attributes': [
                    {
                        'name': 'url',
                        'value': {
                            'stringValue': cleaned_file_name
                        }
                    }
                ]
            },
        ]
    )


# Function to save generated answers to folder documentation/
def save_answers(answer, filepath, folder):
    # Only create directory until the last / of filepath
    sub_directory = f"{folder}{filepath[:filepath.rfind('/') + 1]}"
    if not os.path.exists(sub_directory):
        # Only create directory until the last /
        os.makedirs(sub_directory)
    # Replace all file endings with .txt
    filepath = filepath[:filepath.rfind('.')] + ".txt"
    with open(f"{folder}{filepath}", "w") as f:
        f.write(answer)


def save_to_s3(bucket_name, repo_name, filepath, folder, documentation):
    filepath = filepath + ".out"
    s3_client.put_object(Bucket=bucket_name, Key=f'{folder}/{repo_name}/{filepath}', Body=documentation.encode('utf-8'))
    logger.info(f"Saved {filepath} to S3")


def should_ignore_path(path):
    path_components = path.split(os.sep)
    for component in path_components:
        if component.startswith('.'):
            return True
        elif component == 'node_modules':
            return True
        elif component == '__pycache__':
            return True
    return False


def get_ssh_key(secret_name):
    client = boto3.client('secretsmanager')
    response = client.get_secret_value(SecretId=secret_name)
    return response['SecretString']


def write_ssh_key_to_tempfile(ssh_key):
    with tempfile.NamedTemporaryFile(delete=False) as f:
        os.chmod(f.name, 0o600)
        f.write(ssh_key.strip().encode() + b'\n')  # Add a newline character at the end
        return f.name


def get_questions_from_param_store(prompt_config_param_name):
    response = ssm.get_parameter(Name=prompt_config_param_name)['Parameter']['Value']
    return json.loads(response)


def process_repository(repo_url, ssh_url=None):
    # Temporary clone location
    tmp_dir = f"/tmp/{datetime.datetime.now().strftime('%Y-%m-%d-%H-%M-%S')}"
    repo_name, _ = "-".join(repo_url.split('/')[-2:]).split(".")
    destination_folder = 'repositories/'

    if not os.path.exists(destination_folder):
        os.makedirs(destination_folder)

    # Clone the repository
    # If you authenticate with some other repo provider just change the line below
    logger.info(f"Cloning repository... {repo_url}")
    if ssh_url:
        ssh_key = get_ssh_key(ssh_key_name)
        ssh_key_file = write_ssh_key_to_tempfile(ssh_key)
        ssh_command = f"ssh -i {ssh_key_file} -o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no"
        repo = git.Repo.clone_from(ssh_url, tmp_dir, env={"GIT_SSH_COMMAND": ssh_command})
    else:
        repo = git.Repo.clone_from(repo_url, tmp_dir)
    logger.info(f"Finished cloning repository {repo_url}")
    # switch to branch
    branch = repo.create_head(ref, commit=commit)
    branch.checkout()
    # Copy all files to destination folder
    for src_dir, dirs, files in os.walk(tmp_dir):
        dst_dir = src_dir.replace(tmp_dir, destination_folder)
        if not os.path.exists(dst_dir):
            os.mkdir(dst_dir)
        for file_ in files:
            src_file = os.path.join(src_dir, file_)
            dst_file = os.path.join(dst_dir, file_)
            if os.path.exists(dst_file):
                os.remove(dst_file)
            shutil.copy(src_file, dst_dir)

    # Delete temp clone
    shutil.rmtree(tmp_dir)

    processed_files = []
    failed_files = []
    logger.info(f"Processing files in {destination_folder}")
    questions = get_questions_from_param_store(prompt_config_param_name)
    for root, dirs, files in os.walk(destination_folder):
        if should_ignore_path(root):
            continue
        for file in files:
            if file.endswith(('.png', '.jpg', '.jpeg', '.gif', '.zip', '.pyc')):
                continue
            # Ignore files that start with a dot (.)
            if file.startswith('.'):
                continue

            file_path = os.path.join(root, file)

            for attempt in range(3):
                try:
                    logger.info(f"\033[92mProcessing file: {file_path}\033[0m")
                    all_answers = ""
                    for question in questions:
                        answer = ask_question_with_attachment(question['prompt'], file_path)
                        upload_prompt_answer_and_file_name(file_path, question['prompt'], answer, repo_url,
                                                           prompt_type=question['type'])
                        all_answers = all_answers + f"[{question['type']}]{question['prompt']}:\n{answer}\n"

                    # Upload the file itself to the index
                    code = open(file_path, 'r')
                    upload_prompt_answer_and_file_name(file_path, "", code.read(), repo_url, prompt_type="code")
                    save_to_s3(s3_bucket, repo_name, file_path, "documentation", all_answers)
                    processed_files.append(file)
                    break
                except Exception as e:
                    logger.error(f"Error: {e}")
                    time.sleep(15)
            else:
                logger.info(f"\033[93mSkipping file: {file_path}\033[0m")
                failed_files.append(file_path)

    logger.info(f"Processed files: {processed_files}")
    logger.info(f"Failed files: {failed_files}")


if __name__ == "__main__":
    main()
