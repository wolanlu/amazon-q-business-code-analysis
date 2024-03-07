import boto3
import datetime
import os
import git
import shutil
import tempfile
import uuid

amazon_q = boto3.client('qbusiness')
s3 = boto3.client('s3')
amazon_q_app_id = os.environ['AMAZON_Q_APP_ID']
amazon_q_user_id = os.environ['AMAZON_Q_USER_ID']
index_id = os.environ['Q_APP_INDEX']
role_arn = os.environ['Q_APP_ROLE_ARN']
repo_url = os.environ['REPO_URL']
s3_bucket = os.environ['S3_BUCKET']
# Optional retrieve the SSH URL and SSH_KEY_NAME for the repository
ssh_url = os.environ.get('SSH_URL')
ssh_key_name = os.environ.get('SSH_KEY_NAME')


def main():
    print(f"Processing repository... {repo_url}")
    # If ssh_url ends with .git then process it
    if ssh_url and ssh_url.endswith('.git'):
        process_repository(repo_url, ssh_url)
    else:
        process_repository(repo_url)
    print(f"Finished processing repository {repo_url}")


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
    # Replace all file endings with .out
    filepath = filepath + ".out"
    # Write answer to s3
    s3.put_object(Body=answer.encode('utf-8'), Bucket=s3_bucket, Key=f"{folder}/{filepath}")


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


def process_repository(repo_url, ssh_url=None):
    # Temporary clone location
    tmp_dir = f"/tmp/{datetime.datetime.now().strftime('%Y-%m-%d-%H-%M-%S')}"

    destination_folder = 'repositories/'

    if not os.path.exists(destination_folder):
        os.makedirs(destination_folder)

    # Clone the repository
    # If you authenticate with some other repo provider just change the line below
    print(f"Cloning repository... {repo_url}")
    if ssh_url:
        ssh_key = get_ssh_key(ssh_key_name)
        ssh_key_file = write_ssh_key_to_tempfile(ssh_key)
        ssh_command = f"ssh -i {ssh_key_file} -o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no"
        repo = git.Repo.clone_from(ssh_url, tmp_dir, env={"GIT_SSH_COMMAND": ssh_command})
    else:
        repo = git.Repo.clone_from(repo_url, tmp_dir)
    print(f"Finished cloning repository {repo_url}")
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

    import time

    processed_files = []
    failed_files = []
    print(f"Processing files in {destination_folder}")
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

            questions = [
                {
                    "prompt": "Come up with a list of questions and answers about the attached file. Keep answers dense with information. A good question for a database related file would be 'What is the database technology and architecture?' or for a file that executes SQL commands 'What are the SQL commands and what do they do?' or for a file that contains a list of API endpoints 'What are the API endpoints and what do they do?'",
                    "type": "questions"
                },
                {
                    "prompt": "Generate comprehensive documentation about the attached file. Make sure you include what dependencies and other files are being referenced as well as function names, class names, and what they do.",
                    "type": "documentation"
                },
                {
                    "prompt": "Identify anti-patterns in the attached file. Make sure to include examples of how to fix them. Try Q&A like 'What are some anti-patterns in the file?' or 'What could be causing high latency?'",
                    "type": "anti-patterns"
                },
                {
                    "prompt": "Suggest improvements to the attached file. Try Q&A like 'What are some ways to improve the file?' or 'Where can the file be optimized?'",
                    "type": "improvements"
                },
            ]

            for attempt in range(3):
                try:
                    print(f"\033[92mProcessing file: {file_path}\033[0m")
                    all_answers = ""
                    for question in questions:
                        answer = ask_question_with_attachment(question['prompt'], file_path)
                        upload_prompt_answer_and_file_name(file_path, question['prompt'], answer, repo_url, prompt_type=question['type'])
                        all_answers = all_answers + f"[{question['type']}]{question['prompt']}:\n{answer}\n"

                    # Upload the file itself to the index
                    code = open(file_path, 'r')
                    upload_prompt_answer_and_file_name(file_path, "", code.read(), repo_url, prompt_type="code")
                    save_answers(all_answers, file_path, "documentation")
                    processed_files.append(file)
                    break
                except Exception as e:
                    print(f"Error: {e}")
                    time.sleep(15)
            else:
                print(f"\033[93mSkipping file: {file_path}\033[0m")
                failed_files.append(file_path)

    print(f"Processed files: {processed_files}")
    print(f"Failed files: {failed_files}")


if __name__ == "__main__":
    main()
