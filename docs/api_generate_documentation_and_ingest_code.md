# Intent
The purpose is to generate documentation from code repo and compare it with human written documentation in documentation repo
# Resources
There is only one script file named generate_documentation_and_ingest_code.py
# Capabilities
There is only one capability which is to generate documentation from code repo, write it to Amazon Q for Business index,
and compare it with human generated documentation stored in documentation repo.
### Input:
1. code_repo: url of the source repo that stores the code
1. documentation_repo: url of the documentation repo that stores documentation for code repo
1. doc_repo_subdir: subdir within documentation repo in which documentation files are located
1. suffix: additional suffix of the documentation file
1. creds_name: name of the secret that stores credentials for both repos
