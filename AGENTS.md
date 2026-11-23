# MSM deployment agent instructions

This repository is the MSM operations console. The deployment Deep Agent in
`deploy_agent/` follows these rules:

1. Inspect before changing: `repo_status`, `list_files`, `read_file`.
2. Verify often: `run_tests`, `health_check` are safe and read-only.
3. Mutating tools require the operator to include the exact phrase `DEPLOY-MSM`.
4. Never push to a remote; this repo is local only.
5. Prefer the smallest change; report every change and verification result.
6. Deployment assets: `Dockerfile`, `docker-compose.yml`, `.dockerignore`.
