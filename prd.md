# Proof-Carrying Pipeline — PRD

Oct 22, 2026 · @Sou

## Overview

Proof-Carrying Pipeline is a hands-off delivery system where AI agents take a GitLab issue all the way to production, but no agent is ever trusted on its word. Every claim an agent makes at a stage gate ("tests pass", "vulnerability fixed", "deployed and healthy") must be backed by machine-checkable evidence before the pipeline promotes.

**The problem.** Hands-off agents are the goal of agentic DevSecOps, but teams won't give agents production access because agent summaries are unverifiable. An agent can write "all tests pass, security issue resolved" whether or not it's true, and humans rarely check.

**The pitch.** Autonomous delivery that can't lie: every promotion carries its own proof.

**What it does.**

1. A user opens an issue describing a change.
2. Builder agents plan, implement and open a merge request.
3. A Red Team agent attacks the staging deployment; a Blue Team agent patches what it finds; the attack is replayed to prove the fix.
4. At each gate, an independent Verifier checks every agent claim against real evidence from GitLab and the live system.
5. Unproven claims block promotion and open a follow-up issue.
6. On success, the change reaches production and a signed evidence pack is posted on the original issue.

## Goals, non-goals and success criteria

The product succeeds when one issue goes to production with zero human clicks, and a deliberately false agent claim is caught and blocked on camera.

**Goals**

- Fully hands-off flow from issue creation to production deploy.
- Every stage gate verified against evidence, never against agent text.
- A Red Team vs Blue Team security gate that proves fixes by replaying exploits.
- A signed, independently verifiable evidence pack posted on the issue.
- Coverage of all nine GitLab DevSecOps stages.

**Non-goals**

- A general-purpose security scanner or pentesting product.
- Supporting arbitrary languages or frameworks; one demo app stack is enough.
- Replacing GitLab's built-in scanners; the system consumes their output.
- Attacking anything other than the project's own staging environment.

**Success criteria**

| Criterion | Target |
| --- | --- |
| Issue-to-production, happy path | Completes with no human action |
| Seeded vulnerability | Found by Red Team, patched by Blue Team, replay fails after patch |
| False claim injection | Blocked at the gate, follow-up issue created |
| Evidence pack | Signed; signature verifies in a separate CI job |
| Stage coverage | All 9 stages touched by real automation |
| Pipeline history | 20+ visible runs, including at least 3 blocked promotions |

## Hackathon fit

Submit to [Life After Code](https://gitlab-transcend.devpost.com/) as Path A (Start Fresh), Hands-off autonomy, and opt into Most Creative. Most Stages Covered is judged per path automatically. Deploy on Google Cloud for the up-to-0.2 bonus. Deadline: Oct 27, 2026, 1:00pm UTC.

**Hard requirements from the hackathon page**

- Uses GitLab Duo Agent Platform features (agents, flows, or MCP clients).
- New work, created for the hackathon.
- Public GitLab project with an MIT License visible in the About section.
- Visible CI/CD pipeline history showing the automation running.
- Demo video under 3 minutes on YouTube, public, no third-party trademarks or copyrighted music.
- Google Cloud bonus: deployment code in the repo and a public live link.

**Judging criteria mapped to features**

| Criterion | What earns it here |
| --- | --- |
| Technological implementation | Real Duo flows, deterministic verifier, signed attestations, non-trivial CI |
| Design | One coherent loop: issue in, proven production deploy out |
| Potential impact | Removes the trust blocker to giving agents production access |
| Innovation | Claims-vs-evidence gates plus adversarial agents proving fixes |
| Presentation | Live run, a caught lie, an exploit that stops working |

**Nine DevSecOps stages covered**

| Stage | Automation |
| --- | --- |
| Plan | Planner agent triages the issue, writes acceptance criteria, labels it |
| Create | Builder agent writes code and tests, opens the MR |
| Verify | Unit and integration tests, coverage; verifier checks job results |
| Package | Container image built and pushed to GitLab Container Registry |
| Secure | SAST, dependency scanning, secret detection, Red vs Blue gate |
| Release | Staged promotion staging to production with evidence gates |
| Configure | Infrastructure as code for Cloud Run, environment config |
| Monitor | Post-deploy health checks, error-rate watch, auto-rollback |
| Govern | Signed evidence pack, policy rules, audit trail on the issue |

## Trust model and core principles

The verifier is deterministic code that reads evidence from sources agents cannot write to; an LLM only translates claims into checks and writes the human-readable report. This is what makes "can't lie" true, and the demo must say so.

1. **Claims are data, not truth.** Every agent ends its step by emitting a structured claim (JSON, schema below). Free-text summaries are never used for gating.
2. **Evidence comes from authoritative sources only.** GitLab API (job status, artifacts, scan reports, MR state), the container registry (image digest), and the live service (HTTP checks, metrics). Never from files an agent wrote in the same job.
3. **Separation of duties.** Agents that build cannot verify. The verifier runs in its own CI job with its own read-only token. Build agents get no permission to modify verifier code, gate rules or the signing key; a CODEOWNERS rule and protected branch enforce this.
4. **Fail closed.** Missing, stale or ambiguous evidence equals a failed check. Timeouts fail. Parse errors fail.
5. **Evidence is bound to the artifact.** Every check records the commit SHA and image digest it applies to. Evidence for a different SHA or digest is rejected.
6. **Everything is reproducible.** Each check stores the exact query, the raw response hash and the verdict, so anyone can re-run it.
7. **The LLM never decides a verdict.** It may propose which checks a claim needs, but the check catalogue and verdict logic are code, reviewed and versioned.

## End-to-end flow

&#91;embedded content: issue-to-production flow · gates block when evidence is missing\]

The happy path runs left to right and down; a gate that cannot verify its claim (red, dashed) blocks promotion and opens a follow-up issue instead of advancing.

## System architecture

&#91;embedded content: system architecture · agents, verifier, evidence sources\]

Agents produce claims; the verifier reads evidence from GitLab, the registry, the live service and the exploit replay, none of which agents can write to, and only a fully verified run reaches production.

## Agent specifications

Seven agents do the work and one deterministic verifier judges it; each agent has a narrow job, scoped permissions and a structured claim as its only output.

| Agent | Trigger | Does | Emits claim | Cannot |
| --- | --- | --- | --- | --- |
| Planner | Issue labeled `pcp::go` | Writes acceptance criteria, risk level, affected files; labels issue | `plan.ready` | Write code |
| Builder | Plan approved by policy | Creates branch, implements change and tests, opens MR | `code.ready`, `tests.pass` | Merge, deploy, edit gate rules |
| Pipeline Fixer | Failed MR pipeline | Reads job logs, pushes a fix commit (max 3 attempts) | `pipeline.green` | Skip or delete tests |
| Red Team | Staging deploy of MR | Runs exploit playbook against staging, files findings as vulnerability issues | `attack.complete` with findings | Touch anything but staging URL |
| Blue Team | Red Team findings | Patches code, adds a regression test per finding | `vuln.fixed` per finding | Disable scanners, edit exploit scripts |
| Release | All gates passed | Merges MR, promotes image digest to production | `deploy.healthy` | Promote an unverified digest |
| Reporter | Run complete or blocked | Writes the human summary and posts evidence pack link on the issue | none | Change verdicts |
| Verifier (code, not agent) | Every gate | Runs checks, issues verdicts, signs the evidence pack | n/a | Be edited by any agent |

**Agent output contract.** Every agent's last action writes `claims/<stage>.json` as a CI artifact and posts it as an MR note with a fixed marker (`<!-- pcp-claim -->`). Claims follow the schema in the next section.

**Prompts.** Each agent's system prompt lives in `agents/<name>/prompt.md`, is versioned, and states its role, allowed tools, the claim schema, and an explicit instruction that unverifiable claims will block the run.

**Bounded loops.** Pipeline Fixer and Blue Team each get at most 3 attempts per run. After that the gate fails and the Reporter opens a follow-up issue for a human.

## Claim and evidence model

Each claim type maps to a fixed list of checks in a versioned catalogue; a gate passes only if every required check returns PASS for the same commit SHA and image digest.

**Claim schema** (`schemas/claim.schema.json`)

```json
{
  "claim_id": "uuid",
  "type": "tests.pass",
  "agent": "builder",
  "stage": "verify",
  "subject": {"project_id": 123, "mr_iid": 45, "commit_sha": "abc123", "image_digest": "sha256:..."},
  "statement": "All 142 unit tests pass with 87% line coverage",
  "asserted": {"tests_passed": 142, "tests_failed": 0, "coverage_pct": 87.0},
  "created_at": "2026-10-22T10:00:00Z"
}
```

**Evidence record schema** (`schemas/evidence.schema.json`)

```json
{
  "check_id": "verify.junit_all_passed",
  "claim_id": "uuid",
  "source": "gitlab_api",
  "query": "GET /projects/123/pipelines/9/test_report",
  "response_sha256": "...",
  "observed": {"total": 142, "failed": 0},
  "subject_sha": "abc123",
  "verdict": "PASS",
  "reason": "0 failures in pipeline 9 for abc123",
  "checked_at": "2026-10-22T10:02:11Z"
}
```

**Check catalogue** (`verifier/catalogue.yaml`)

| Claim type | Required checks | Evidence source |
| --- | --- | --- |
| `plan.ready` | Issue has acceptance criteria section; risk label set | Issues API |
| `code.ready` | MR exists, targets main, head SHA matches claim | MR API |
| `tests.pass` | JUnit report has 0 failures; test count not lower than main; coverage within 1 point of asserted and not below threshold | Pipeline test report API, coverage artifact |
| `pipeline.green` | Latest pipeline for SHA succeeded; no jobs with `allow_failure` added in this MR | Pipelines API, MR diff of `.gitlab-ci.yml` |
| `vuln.fixed` | Finding absent in post-patch SAST/dependency report; exploit replay fails on patched staging; regression test exists and passes | Security report artifacts, replay job result |
| `image.built` | Digest exists in registry; built from claimed SHA (label check) | Container Registry API |
| `deploy.healthy` | Production serves claimed digest; `/healthz` 200 for 5 of 5 probes; error rate below threshold for 5 minutes | Cloud Run API, HTTP probes, Cloud Monitoring |

**Verdict rules**

- PASS: every required check passed for the claimed subject.
- FAIL: any check failed, or any asserted number disagrees with observed beyond tolerance (a lie).
- INCONCLUSIVE: evidence missing or stale; treated as FAIL for promotion, labeled separately in the report.
- A FAIL caused by asserted-vs-observed mismatch is tagged `claim_mismatch` and highlighted in the evidence pack as a caught false claim.

## Red Team vs Blue Team security gate

The security gate proves a fix instead of asserting it: the same exploit that succeeded before the patch must fail after it, and that before/after replay is the evidence.

**Scope and safety.** The Red Team only ever targets the project's own ephemeral staging URL for the current MR, read from a pipeline variable. The exploit runner refuses any host not matching that URL. All attacks run inside CI against a throwaway deployment with synthetic data; no production, no third-party systems.

**Exploit playbook.** A versioned set of checks in `redteam/playbook/`, each a small script returning a structured result (`exploit_id`, `succeeded`, `evidence`). Starter set aimed at the seeded app:

1. SQL injection on a search parameter.
2. Reflected XSS in an input echoed back.
3. IDOR: access another user's record by ID.
4. Missing authn on an admin route.
5. Secret or stack trace leaked in an error response.
6. Vulnerable dependency with a known CVE (confirmed reachable).
7. Prompt injection on the app's own AI endpoint, if present.

**Flow.**

1. MR deploys to staging.
2. Red Team runs the full playbook, records which exploits succeed, files one vulnerability issue per success with reproduction steps, emits `attack.complete`.
3. If no exploit succeeds, the gate passes immediately.
4. Blue Team takes each finding, writes a patch and a regression test, pushes a commit, emits `vuln.fixed` per finding.
5. Staging redeploys with the patch.
6. Verifier replays only the previously successful exploits. The gate passes only if every one now fails and every regression test passes.
7. Up to 3 Blue Team rounds; otherwise the gate fails and a follow-up issue is opened.

**Why it is trustworthy.** The replay is run by the verifier job, not the Blue Team agent, using the pinned exploit scripts the Blue Team cannot edit (enforced by CODEOWNERS on `redteam/`). A fix that only hides the finding from the scanner still fails the replay.

**Demo value.** This is the climax: show exploit 1 returning a dumped user table before the patch, then the identical exploit returning a safe 400 after it, side by side.

## Evidence pack and signing

The evidence pack is a single signed JSON document listing every claim, every check, every verdict and the artifact it applies to; a separate CI job verifies the signature, so "signed" is demonstrated, not asserted.

**Contents** (`evidence-pack.json`)

- Run metadata: issue IID, MR IID, commit SHA, image digest, pipeline IDs, timestamps.
- Every claim emitted, in order.
- Every evidence record, grouped by gate, with query, response hash and verdict.
- The Red vs Blue section: exploits attempted, which succeeded pre-patch, replay results post-patch.
- Final decision per gate and overall (PROMOTED or BLOCKED).
- Any caught false claims, tagged `claim_mismatch`.

**Signing.** A CI job signs the canonicalized pack with Sigstore cosign in keyless mode (OIDC from the CI job identity), producing a `.sig` and certificate. Falls back to cosign with a CI-managed key if keyless is unavailable. The signature and the pack are published as a release asset and attached to the issue.

**Verification.** A separate `verify-evidence` job re-canonicalizes the pack and runs `cosign verify-blob`. The demo runs this live to show the signature checks out, and shows it failing on a tampered copy.

**Human-readable view.** The Reporter renders the pack into a Markdown comment on the issue: a green or red line per gate, the caught-lie callout if any, and a link to the signed pack and its verification job. This comment is the artifact a judge reads first.

## Target demo application

A small Python FastAPI "team notes" app, deliberately seeded with vulnerabilities the Red Team can find and the Blue Team can fix, kept minimal so the whole loop runs in minutes.

**Stack.** Python 3.12, FastAPI, SQLite (or Postgres on Cloud SQL), pytest, a tiny HTML front end. One container image.

**Features.** Create and search notes, user accounts, an admin route, and one AI endpoint (`/summarize`) that summarizes a note via an LLM, so the prompt-injection exploit has a target.

**Seeded vulnerabilities** (each with a clean fix the Blue Team can reach):

| ID | Vulnerability | Fix |
| --- | --- | --- |
| V1 | SQL injection in note search (string-formatted query) | Parameterized query |
| V2 | Reflected XSS in search echo | Output escaping |
| V3 | IDOR on `GET /notes/{id}` (no owner check) | Ownership check |
| V4 | Admin route with no auth | Require admin role |
| V5 | Stack trace leaked on 500 | Generic error handler |
| V6 | Pinned dependency with a known CVE | Bump to patched version |
| V7 | Prompt injection in `/summarize` | Input/output guardrails |

**Why seeded.** For a reliable demo the vulnerabilities must be real but known, so the Red Team dependably finds them and the Blue Team dependably fixes them within the attempt budget. The feature issue in the demo ("add note tagging") also lands near V1 and V3, so a natural change exercises the security gate.

## CI/CD pipeline design

The pipeline is the spine: each stage runs its work, then a verifier job gates promotion on evidence, so a stage cannot advance on an agent's say-so.

**Stages** (`.gitlab-ci.yml`): `plan`, `build`, `test`, `scan`, `package`, `deploy-staging`, `redteam`, `blueteam-verify`, `deploy-prod`, `evidence`.

**Pattern per gated stage.** A work job writes a claim artifact; a `verify:<stage>` job consumes the claim plus authoritative evidence and exits non-zero on FAIL, which blocks the next stage. Verifier jobs use a read-only token distinct from the agent token.

**Key jobs**

| Job | Does | Gates on |
| --- | --- | --- |
| `test` | Runs pytest, emits JUnit + coverage | — |
| `verify:tests` | Checks JUnit 0 failures, coverage, test count vs main | Blocks `package` |
| `scan` | GitLab SAST, dependency scanning, secret detection | — |
| `package` | Builds image, pushes to registry, records digest | — |
| `deploy-staging` | Deploys digest to an ephemeral Cloud Run staging service | — |
| `redteam` | Runs exploit playbook against staging URL | — |
| `blueteam-verify` | After Blue Team patch, replays exploits, checks regressions | Blocks `deploy-prod` |
| `verify:deploy` | Confirms prod serves the digest, health probes pass | Marks run healthy |
| `evidence` | Assembles, signs, publishes pack; posts issue comment | — |
| `verify-evidence` | Independently verifies the signature | Final proof |

**Controls.** Protected `main`; production is a protected environment; the signing identity and prod deploy token are masked CI variables available only to their jobs. `deploy-prod` runs only on `main` after all gates. Concurrency is limited so one run's staging service is unique per MR.

**History for judging.** Seed the repo with many runs: successful end-to-end runs, runs blocked at the security gate, and runs blocked by an injected false claim, so the pipeline history visibly tells the story.

## GitLab Duo Agent Platform integration

Agents and flows are defined with Duo Agent Platform features and triggered by GitLab events, so the automation is native to GitLab rather than an external script poking the API. Confirm exact config formats against the current Duo docs during Milestone 0, since the platform is new and the schema may differ from this plan.

**Agents.** Each agent in the spec table is a Duo agent with its own prompt (`agents/<name>/prompt.md`), an allowed-tools list, and a scoped token. Keep tool access minimal per agent.

**Flows.** A Duo flow wires the hands-off sequence: issue label to plan to MR to the gated pipeline to Red/Blue to promotion to report. The flow calls agents and waits on pipeline/verifier job outcomes; a failed gate routes to the fixer or blue-team branch, then to a follow-up issue after the attempt budget.

**Triggers.** Issue label `pcp::go` starts a run; MR pipeline events drive the fixer; a staging-deploy event starts the Red Team; Red Team findings start the Blue Team; all-gates-green starts Release.

**MCP.** Expose the verifier and the exploit-replay as MCP tools so agents can request a check but never compute the verdict themselves. Optionally add an MCP client to a Cloud Monitoring server for health evidence. Using MCP clients satisfies one of the platform-feature requirements and keeps the trust boundary clean: agents call tools, code returns verdicts.

**Fallback.** If a specific Duo trigger or flow capability isn't available in time, implement that link as a CI job calling the same agent prompt via API, keeping the flow's logic identical. The verifier and gates do not depend on Duo and work either way.

## Google Cloud infrastructure and deployment

Deploy the app to Cloud Run and keep all infrastructure as code in the repo, which satisfies the Google Cloud bonus requirement (code in repo plus a public live link) and keeps staging ephemeral and cheap.

**Services.** Cloud Run for the app (one staging service per MR, one production service). Artifact Registry for images. Optional Cloud SQL (Postgres) for production; SQLite is fine for staging. Cloud Monitoring and Cloud Logging for health and error-rate evidence.

**Deploy.** `deploy-staging` deploys the built digest to a uniquely named Cloud Run service (`pcp-staging-mr-<iid>`), returns its URL as a pipeline variable for the Red Team, and is torn down at the end of the run. `deploy-prod` deploys the same digest to the production service after all gates pass.

**Identity.** Use Workload Identity Federation between GitLab CI and Google Cloud so no long-lived service-account key is stored; the deploy jobs authenticate via OIDC. This also strengthens the keyless-signing story.

**IaC.** Terraform (or gcloud scripts) in `infra/`, covering the services, registry, IAM and monitoring. Include a single command to stand the whole thing up, so judges can reproduce it.

**Public link.** The production Cloud Run URL is public and listed in the submission; keep it live until winners are announced around November 16.

## Repository structure

```text
.
├── app/                  # FastAPI demo app (seeded vulnerabilities)
│   ├── main.py
│   ├── db.py
│   └── templates/
├── tests/                # pytest unit + integration tests
├── agents/               # one folder per agent
│   ├── planner/prompt.md
│   ├── builder/prompt.md
│   ├── pipeline_fixer/prompt.md
│   ├── redteam/prompt.md
│   ├── blueteam/prompt.md
│   ├── release/prompt.md
│   └── reporter/prompt.md
├── verifier/             # deterministic checks (NOT agent-editable)
│   ├── catalogue.yaml
│   ├── checks/
│   └── run.py
├── redteam/              # exploit playbook (NOT agent-editable)
│   └── playbook/
├── schemas/              # claim + evidence JSON schemas
├── flows/                # Duo flow + agent definitions
├── infra/                # Terraform / gcloud IaC for Cloud Run etc.
├── .gitlab-ci.yml
├── CODEOWNERS            # protects verifier/, redteam/, schemas/, flows/
├── LICENSE               # MIT, referenced in About
└── README.md
```

**Ownership boundary.** `CODEOWNERS` assigns `verifier/`, `redteam/`, `schemas/` and `flows/` to the human owner, so no build agent can alter the checks, the exploits, the claim schema or the gate wiring. This is the technical backbone of the trust model and should be called out in the README.

## Failure modes, blocking behaviour and safety rails

The system is designed to fail closed and to make every block visible, because a caught failure is the product's whole value proposition.

| Situation | Behaviour |
| --- | --- |
| Agent asserts a number that evidence contradicts | Gate FAILs, tagged `claim_mismatch`, highlighted in pack, follow-up issue |
| Evidence missing or stale | INCONCLUSIVE, treated as FAIL for promotion |
| Blue Team can't fix within 3 rounds | Security gate fails, follow-up issue with findings, no prod deploy |
| Pipeline Fixer can't green within 3 tries | Verify gate fails, human follow-up issue |
| Red Team tries a non-staging host | Exploit runner refuses, run aborts with an error |
| Agent edits a protected path | Blocked by CODEOWNERS + protected branch; MR can't merge |
| Prod health checks fail post-deploy | Auto-rollback to previous digest, incident issue opened |
| Signing or signature verification fails | Evidence gate fails; run marked unverifiable |
| LLM/API outage mid-run | Flow pauses and retries with backoff; after limit, opens a follow-up issue |

**Safety rails.** Exploits run only against the ephemeral staging URL, only inside CI, only against synthetic data. The production deploy token and signing identity are scoped to their jobs. No agent has a token that can both build and verify. All destructive actions (merge, prod deploy) are gated on verified evidence and happen only on protected `main`.

## Testing strategy

The verifier and the gates are the trust-critical code, so they get the most testing; the demo app only needs enough tests to make the Verify gate meaningful.

- **Verifier unit tests.** For each check, feed recorded GitLab API responses (fixtures) representing PASS, FAIL and INCONCLUSIVE, and assert the verdict. Include a fixture where an agent's asserted number disagrees with the evidence, to prove the lie is caught.
- **Exploit self-tests.** Each exploit script runs against the known-vulnerable app and must succeed, and against the patched app and must fail. This keeps the playbook honest.
- **End-to-end rehearsal.** A scripted dry run on a fixed issue that exercises the full flow, used to populate pipeline history and to sanity-check before recording.
- **Negative-path test.** A run with a planted false claim artifact, asserting the gate blocks and the follow-up issue is created.
- **Tamper test.** Modify the signed evidence pack and assert `verify-evidence` fails.
- **App tests.** Enough pytest coverage that `verify:tests` has a real signal, including regression tests the Blue Team adds per finding.

## Demo script and submission checklist

The under-3-minute video should show three things running live: a hands-off success, a caught lie, and an exploit that stops working after a patch.

**Video beats (target \~2:45)**

1. 0:00–0:20 The problem: agents can claim anything; nobody checks. State the one-liner.
2. 0:20–0:50 Open an issue "add note tagging", label `pcp::go`, walk away. Show the flow kicking off.
3. 0:50–1:30 Agents plan, build, open the MR, tests and scans run, each gate shows a green verified check.
4. 1:30–2:10 Red Team dumps the user table via SQL injection; Blue Team patches; the same exploit now returns a safe 400, side by side. Gate goes green.
5. 2:10–2:30 Production deploy; the signed evidence pack is posted on the issue; `verify-evidence` shows the signature checks out.
6. 2:30–2:45 The money shot: a run where an agent claims "tests pass" but a test actually failed. The gate blocks, tags the lie, opens a follow-up issue. Close on the line.

**Submission checklist**

- [ ] Public GitLab project, MIT License shown in About.
- [ ] Visible CI/CD history with successful, security-blocked and lie-blocked runs.
- [ ] 3-minute YouTube video, public, no third-party music/marks.
- [ ] Text description: what it does, the problem, how GitLab automates the post-code lifecycle.
- [ ] Live Google Cloud URL, deployment code in repo.
- [ ] Opt into Most Creative on the form.
- [ ] README explains the trust model and the CODEOWNERS boundary.
- [ ] Contributor onboarding completed and approved.
- [ ] Confirm whether one project may win multiple prizes (rules).

## Assumptions to verify and open questions

Resolve these in Milestone 0, before building, since a few depend on the current GitLab Duo platform and the hackathon rules.

- **Duo capabilities.** Confirm the exact way to define agents, flows, triggers and MCP clients in the current Duo Agent Platform, and whether a flow can wait on pipeline/verifier job outcomes. Adjust the integration section to match; use the CI-job fallback where a capability is missing.
- **MCP exposure.** Confirm agents can call a custom MCP tool (the verifier/replay) and that this counts toward the required platform features.
- **Keyless signing in CI.** Confirm cosign keyless (OIDC) works from GitLab CI; otherwise use a CI-managed key.
- **Workload Identity Federation.** Confirm GitLab-to-Google Cloud OIDC federation is set up, to avoid storing a service-account key.
- **Multiple prizes.** Confirm in the Official Rules whether one project can win more than one category; this affects how hard to push Most Stages vs Most Creative vs Hands-off.
- **Ephemeral staging cost.** Confirm per-MR Cloud Run services tear down reliably to control spend.
- **Tolerances.** Decide coverage threshold and error-rate threshold numbers used by the verifier.

This PRD is complete enough to start building; these items refine it rather than block it.

## Build order for Claude Code

Build in vertical slices so there is always a working end-to-end path, adding one gate at a time. Each milestone ends in a committed, runnable state.

1. **M0 — Verify assumptions and scaffold.** Resolve the open questions. Create the GitLab project, MIT license, repo skeleton, CODEOWNERS, empty schemas. Confirm Duo, cosign and Google Cloud OIDC.
2. **M1 — App + pipeline + Cloud Run.** Build the FastAPI app with seeded vulnerabilities, pytest suite, Dockerfile, `.gitlab-ci.yml` with build/test/package, and deploy to Cloud Run staging and prod via IaC.
3. **M2 — Claims + verifier core.** Implement claim/evidence schemas, the verifier runner, and the `verify:tests`, `code.ready` and `image.built` checks with fixtures. Wire these as gates.
4. **M3 — Agents + hands-off flow.** Add Planner, Builder, Pipeline Fixer and Release as Duo agents, and the flow from `pcp::go` to a verified prod deploy. First full hands-off run.
5. **M4 — Red vs Blue gate.** Build the exploit playbook with self-tests, the Red Team and Blue Team agents, the replay-based `vuln.fixed` check, and wire the security gate. Prove an exploit fails after patch.
6. **M5 — Monitoring + rollback.** Add `deploy.healthy` checks, post-deploy health watch and auto-rollback with an incident issue.
7. **M6 — Evidence pack + signing.** Assemble the pack, sign with cosign, add `verify-evidence`, and the Reporter's issue comment.
8. **M7 — Harden + seed history + negative paths.** Add the false-claim and tamper tests, run the pipeline many times to build history (success, security-blocked, lie-blocked), polish the issue comment.
9. **M8 — Record + submit.** Record the video to the demo script, write the submission text, verify every checklist item, submit before Oct 27 1:00pm UTC.

**Guiding rule for the agent.** The verifier, exploit playbook, schemas and gate wiring are the trust-critical core: keep them deterministic, versioned and under CODEOWNERS. Agents may be imperfect; the gates must not be.
