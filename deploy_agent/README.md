# Deployment Deep Agent

A LangChain **Deep Agents** harness that automates local deployment of the MSM
console. It uses the OpenAI-compatible local llama-server (GGUF) and a small set
of scoped tools.

## Launch

```bash
cd msm
# API key comes from ECOMOPS_DEEPAGENTS_API_KEY, ECOMOPS_DEEPAGENTS_KEY_FILE,
# or ~/.unsloth/studio/auth/llama_api_key_*
export ECOMOPS_DEEPAGENTS_BASE_URL=http://127.0.0.1:38285/v1
export ECOMOPS_DEEPAGENTS_MODEL=MiMo-V2.6-Distill-Qwen-9B-Q8_0

.venv/bin/python -m deploy_agent.cli "check the repo and run the tests"
.venv/bin/python -m deploy_agent.cli "build the Docker image"   # needs confirmation
```

## Safety model

| Tool | Requires `DEPLOY-MSM` | Effect |
|---|---|---|
| `repo_status`, `list_files`, `read_file` | no | read-only inspection |
| `run_tests`, `health_check`, `show_config` | no | safe verification |
| `write_deployment_file` | yes | writes only allowlisted deployment files |
| `git_commit` | yes | stages all changes and commits locally |
| `docker_build`, `docker_up`, `docker_down` | yes | local Docker lifecycle |

Mutating tools return an error unless the operator's request contains the exact
phrase `DEPLOY-MSM`. No tool pushes to a git remote.

## Docker target

`Dockerfile` builds a Python 3.14 image with poppler for document intake, and
`docker-compose.yml` serves the console on port 8501 with a healthcheck.

```bash
docker compose up -d --build
curl http://localhost:8501/_stcore/health
```
