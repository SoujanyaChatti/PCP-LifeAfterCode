# Setup — what you run

Claude Code built the project; these are the account-dependent steps that need
your GitLab and Google Cloud access. Do them in order. Items marked **(M0)** are
the PRD's open questions — confirm them as you go.

## 0. Prerequisites
- A public **GitLab** project (the hackathon judges GitLab, not GitHub).
- A Google Cloud project with billing enabled.
- Local tools: `python3.12`, `terraform`, `gcloud`, `docker`, and (for real
  signatures) `cosign`.

## 1. Create the public GitLab project
1. Create a new **public** project, e.g. `pcp-lifeaftercode`.
2. Add this code as a remote and push:
   ```bash
   git remote add gitlab https://gitlab.com/<you>/pcp-lifeaftercode.git
   git push gitlab main
   ```
3. Settings → General → make sure the **MIT License shows in the About panel**
   (GitLab reads `LICENSE` automatically).
4. Settings → Repository → **Protect `main`**; Settings → CI/CD → **protect the
   `production` environment**.
5. Edit [`CODEOWNERS`](CODEOWNERS) and [`infra/main.tf`](infra/main.tf): replace
   `@owner` / `gitlab_project_path` with your real values.

## 2. Google Cloud infrastructure (one command)
```bash
cd infra
terraform init
terraform apply -var project_id=<GCP_PROJECT> -var gitlab_project_path=<you>/pcp-lifeaftercode
```
Outputs you'll need: `prod_url`, `registry`, `wif_provider`, `deployer_sa`.

## 3. GitLab CI/CD variables (Settings → CI/CD → Variables)
| Variable | Value | Notes |
| --- | --- | --- |
| `GCP_PROJECT` | your project id | |
| `GCP_REGION` | `us-central1` | or your region |
| `GCP_WIF_PROVIDER` | the `wif_provider` output | |
| `GCP_DEPLOY_SA` | the `deployer_sa` output | |
| `VERIFIER_READONLY_TOKEN` | a **read-only** project access token | masked; verifier jobs only |
| `PCP_AGENT_TOKEN` | a write token for agents | masked; cannot touch protected paths |
| `COSIGN_KEYLESS` | `1` | keyless OIDC signing **(M0: confirm it works from GitLab CI)** |

- Keep the agent token and the verifier token **separate** — that separation is
  the whole point.
- If keyless cosign isn't available, set `COSIGN_KEYLESS=0` and add
  `COSIGN_KEY` / `COSIGN_PUB` (generate with `cosign generate-key-pair`).

## 4. GitLab Duo Agent Platform **(M0)**
Confirm against the current Duo docs how to register the agents in
[`flows/agents.yaml`](flows/agents.yaml) and the flow in
[`flows/pipeline.flow.yaml`](flows/pipeline.flow.yaml), and whether a flow can
wait on pipeline/verifier job outcomes. **If any capability is missing, the
`.gitlab-ci.yml` already encodes the identical gated logic** — the trust model
does not depend on Duo. Register the two MCP clients (`mcp_tools/verifier_server.py`,
`mcp_tools/replay_server.py`) so agents call checks as tools.

## 5. Labels and the feature issue
- Create labels: `pcp::go`, `pcp::blocked`, `risk::low|medium|high`,
  `security::finding`, `incident`.
- Open the demo issue **"Add note tagging"** and label it `pcp::go` to start a
  hands-off run.

## 6. Seed pipeline history (for judging)
Run the pipeline several times to produce the visible history the judges look
for — successful, security-blocked, and lie-blocked runs. See
[`scripts/seed_history.md`](scripts/seed_history.md).

## 7. Verify locally any time (no cloud needed)
```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
python -m pytest
python -m scripts.rehearse --scenario all
```

## Open questions to close (M0)
- Keyless cosign from GitLab CI (else use a managed key).
- Duo flow/trigger/MCP capabilities (else CI-job fallback, already written).
- Per-MR Cloud Run teardown reliability (watch spend; `stop-staging` job + max-instances cap help).
- Final tolerance numbers in [`verifier/catalogue.yaml`](verifier/catalogue.yaml)
  (`coverage_floor`, `error_rate_max`).
- Whether one project may win multiple prize categories (Official Rules).
