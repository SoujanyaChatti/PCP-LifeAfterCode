# Setup

These steps need your GitLab and Google Cloud accounts. Everything else is in
the repo. Do them in order; each stage works on its own, so you can stop after
any step and still have a working, demonstrable pipeline.

| After step | What works |
| --- | --- |
| 0 | Everything offline: `pytest`, `python -m scripts.rehearse` |
| 2 | Full gated pipeline on GitLab: tests, canary, scans, images, Red vs Blue against CI service containers, signed evidence, ledger, dashboard |
| 4 | + Cloud Run staging per MR, production on `main`, live deploy verification, rollback, drift watch |
| 5 | + Duo flows: assign an issue and the agents plan, build, and patch |

## 0. Local check (no accounts)
```bash
python3.12 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
python -m pytest                                    # 112 tests
python -m scripts.rehearse --scenario all --out rehearsal
open rehearsal/public/index.html                    # the dashboard, from the local ledger
```

## 1. GitLab project
1. Create a **public** project (`polygraph`, at gitlab.com/SoujanyaChatti/polygraph), and push:
   ```bash
   git remote add gitlab https://gitlab.com/SoujanyaChatti/polygraph.git
   git push gitlab main
   ```
2. **Settings → General**: confirm the MIT license shows in the About panel.
3. [`CODEOWNERS`](CODEOWNERS) already names `@SoujanyaChatti`; change it if the owner differs.
4. **Settings → Repository → Protected branches**: protect `main`, *Allowed to
   push: No one*, and turn on **Code owner approval**. This is the trust
   boundary: no agent can change the verifier, playbook, gate wiring or its own
   prompts without you.
5. **Settings → Merge requests**: *Pipelines must succeed*.
6. **Settings → CI/CD → Environments**: protect `production`.
7. Create labels: `risk::low`, `risk::medium`, `risk::high`, `pcp::blocked`,
   `needs-human`, `incident`, `pcp::drift`.

## 2. Tokens (Settings → Access tokens → project access tokens)
Two separate identities: the verifier can read but never write, the reporter
can comment but never push code.

| CI/CD variable | Token | Role / scope | Used by |
| --- | --- | --- | --- |
| `VERIFIER_READONLY_TOKEN` | project access token | Reporter, `read_api` | `verify:*` jobs, reading agent claims |
| `PCP_REPORTER_TOKEN` | project access token | Reporter, `api` | `report`, `watch:prod` (comments, follow-up issues) |

Add both under **Settings → CI/CD → Variables**, *Masked* and *Protected* off
(MR pipelines need them). Push any commit: the pipeline runs every gate except
deploys. The first `main` runs are held by `policy:autonomy` until agents have
history; that's intended (see step 6).

## 3. Google Cloud (Terraform)
```bash
cd infra
terraform init
terraform apply -var project_id=<GCP_PROJECT_ID> -var gitlab_project_path=SoujanyaChatti/polygraph
terraform output gitlab_ci_variables
```
This creates Artifact Registry (`pcp`), the production Cloud Run service, three
service accounts and Workload Identity Federation restricted to your project's
CI tokens (no keys anywhere):

- `pcp-deployer`: production deploys and rollback. Only jobs on **protected**
  refs can obtain it, so an MR branch can't touch production even with an
  edited CI file.
- `pcp-staging`: per-MR staging services and image pushes.
- `pcp-verifier`: read-only (Cloud Run viewer, Monitoring viewer).

## 4. Connect GitLab to Google Cloud
Paste the `gitlab_ci_variables` output into **Settings → CI/CD → Variables**:
`GCP_PROJECT`, `GCP_REGION`, `GCP_WIF_PROVIDER`, `GCP_DEPLOY_SA`,
`GCP_STAGING_SA`, `GCP_VERIFIER_SA`, `PROD_URL`.

From now on, MR pipelines deploy two ephemeral Cloud Run services (the pre-patch
twin the Red Team attacks, and the hardened build the verifier replays against);
`main` promotes the verified digest to `pcp-prod` and `verify:deploy` checks it
against the Cloud Run API and Cloud Monitoring. Staging stops itself after 2 h.

**Drift watch:** CI/CD → Schedules → new schedule (e.g. hourly) on `main`. The
`watch:prod` job re-verifies that production still serves the last proven
digest and opens a `pcp::drift` incident if not.

## 5. GitLab Duo Agent Platform
The flows and agent live in [`duo/`](duo/), generated from
[`agents/*/prompt.md`](agents/) by `python -m scripts.build_duo`, and are
validated against GitLab's flow schema in the test suite.

Requires Duo Agent Platform on the group (Premium/Ultimate, or the hackathon's
provided group; triggers need it).

1. Create a project access token: Maintainer, `api`. Add it as CI/CD variable
   `CATALOG_SYNC_TOKEN`.
2. Tag a release to publish to the AI Catalog:
   `git tag v0.1.0 && git push gitlab v0.1.0`
   (the `ci/duo-catalog.yml` include runs GitLab's catalog-sync component).
3. In the project: **Automate → Flows** → enable **PCP Deliver** with trigger
   **Assign** (and **Mention**); enable **PCP Blue Team** with trigger
   **Pipeline → Failed**. Enable the **PCP Evidence Explainer** agent for Duo Chat.
4. Use it: open an issue (e.g. *Add note tagging*) and assign it to the
   `ai-pcp-deliver-<group>` service account. The Planner writes acceptance
   criteria + a risk label; the Builder opens an MR and posts its claims as MR
   notes; the pipeline judges them. If the security gate fails, the Blue Team
   flow patches and posts `vuln.fixed` claims, and the pipeline re-proves them.

**MCP (IDE / Agentic Chat):** [`.gitlab/duo/mcp.json`](.gitlab/duo/mcp.json)
registers the verifier and replay as local MCP tools (`pip install -r requirements-mcp.txt`).
A group Owner must allow external MCP tools (Settings → GitLab Duo).

## 6. Seed pipeline history
```bash
export GITLAB_TOKEN=glpat-...   # your PAT, api scope
python scripts/seed_pipelines.py --project SoujanyaChatti/polygraph --plan default --wait
```
20 runs on `main`: 2 human-approved bootstrap runs (new agents start on
probation), 3 caught lies, 3 incomplete patches (V1, V3, V7), then honest runs.
After a caught lie the agent is on probation, so the next few honest runs are
held at `policy:autonomy` until it re-earns trust — that's the feature, and the
dashboard (`https://soujanyachatti.gitlab.io/polygraph`) shows it.

## Demo knobs (CI/CD → Run pipeline → variables)
| Variable | Effect |
| --- | --- |
| `PCP_DEMO=lie` | Adds a failing test and has the Builder's claim deny it → caught lie |
| `PCP_UNPATCHED=V1` | Builds the hardened image with the V1 fix missing → replay catches it |
| `PCP_HUMAN_APPROVED=1` | A human approves promotion for agents on probation |
| `ISSUE_IID=<n>` | Ties a manual run to an issue |

## Troubleshooting
- **`verify:plan` fails on `main`**: the commit doesn't reference an issue.
  Merge commits from MRs with `Closes #n` work; for manual runs pass `ISSUE_IID`.
- **`policy:autonomy` holds**: read its log; an agent is on probation. Run with
  `PCP_HUMAN_APPROVED=1` if you've reviewed the change.
- **Duo flow didn't start**: check the flow's triggers and that the group has
  Duo Agent Platform enabled; the pipeline still works without it (claims are
  synthesized by CI and marked as such).
