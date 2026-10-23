# Submission — Proof-Carrying Pipeline

For [Life After Code](https://gitlab-transcend.devpost.com/). Path A (Start
Fresh) · Hands-off autonomy · opt into **Most Creative**.
Deadline: **Oct 27, 2026, 1:00pm UTC.**

## One-liner
Autonomous delivery that can't lie: AI agents take a GitLab issue to production,
and every promotion carries its own machine-checkable proof.

## Text description (for the form)
Hands-off agents are the goal of agentic DevSecOps, but teams won't give agents
production access because agent summaries are unverifiable: an agent can write
"all tests pass, security issue resolved" whether or not it's true.

Proof-Carrying Pipeline lets GitLab Duo agents do the work (plan, build, patch)
while a deterministic verifier checks every claim they make against evidence
they cannot write to: the JUnit report, the container registry, the Cloud Run
API, Cloud Monitoring, and a pinned exploit replay. A claim the evidence
contradicts is blocked and recorded as a caught lie.

Security fixes are proven, not asserted: the Red Team's exploits hit a
pre-patch twin of the build, and the identical exploits must fail against the
hardened build that ships. Even the Blue Team's regression tests must prove
themselves (fail before the patch, pass after), so a test that tests nothing is
rejected. The verifier tests itself on every run by planting known lies, and
every signed evidence pack records a fingerprint of the exact grader that
judged it.

Autonomy is earned: a hash-chained trust ledger scores each agent across runs;
an agent caught lying goes on probation and needs a human to approve its next
production deploy until its claims verify again. Evidence packs are signed
keyless with Sigstore, verified in a separate job, posted on the issue, and
published on a GitLab Pages dashboard; a scheduled job re-proves production
and opens an incident on drift. The verifier, playbook, gate wiring and the
agents' own prompts sit behind CODEOWNERS, so no agent can weaken its grader.

## How GitLab automates the post-code lifecycle here
| Stage | Automation |
| --- | --- |
| Plan | Duo Planner writes acceptance criteria + risk label; `verify:plan` checks the live issue |
| Create | Duo Builder opens the MR and posts claims; `verify:create` checks the MR |
| Verify | pytest JUnit + coverage; `verify:tests` re-derives every claimed number; `verify:canary` proves the verifier still catches lies |
| Package | Hardened image + pre-patch twin pushed to the Container Registry; `verify:package` reads labels from the registry |
| Secure | SAST/dependency/secret scanning + Red vs Blue: replay and fail-before/pass-after proofs; Duo Blue Team flow on failure |
| Release | `policy:autonomy` (earned trust) then promotion of the exact verified digest |
| Configure | Terraform: Cloud Run, Artifact Registry, WIF with separate deployer/verifier identities |
| Monitor | `verify:deploy` (Cloud Run API + Cloud Monitoring), auto-rollback, scheduled drift watch |
| Govern | Keyless-signed evidence pack, CODEOWNERS + fingerprint, hash-chained trust ledger, Pages dashboard |

## Required links (fill in)
- Public GitLab project: `________`
- Live Google Cloud URL (prod Cloud Run): `________`
- Evidence dashboard (GitLab Pages): `________`
- 3-minute YouTube demo (public, no third-party music/marks): `________`

## Demo video script (~2:50)
1. **0:00–0:15** The problem: agents can claim anything; nobody checks. One-liner.
2. **0:15–0:45** Assign the "Add note tagging" issue to the PCP Deliver flow.
   The Planner adds acceptance criteria; the Builder opens the MR and posts its
   `tests.pass` claim as an MR note.
3. **0:45–1:15** The pipeline graph: each `verify:*` job turns green against
   evidence. Open `verify:canary`: "planted 9 known lies, caught 9".
4. **1:15–1:55** Red vs Blue: the twin dumps user tokens via SQL injection; a
   run with `PCP_UNPATCHED=V1` is blocked because the same exploit still works
   and the "V1 fixed" claim is tagged as a lie. Then the real patch: the
   identical exploit returns nothing, and the regression test is shown failing
   on the twin and passing on the patch.
5. **1:55–2:20** The money shot: `PCP_DEMO=lie`. The Builder claims "tests
   pass" over a real failure; `verify:tests` blocks it as a caught lie; the
   Reporter opens a follow-up issue; the dashboard shows the Builder dropping
   to **probation**, and the next run is held at `policy:autonomy` until a human
   approves.
6. **2:20–2:50** Production: the verified digest on Cloud Run, `verify-evidence`
   checking the cosign signature against this project's identity (and failing
   on a tampered copy), the evidence comment on the issue, the dashboard's hash
   chain. Close on the line.

Offline capture of any beat: `python -m scripts.rehearse --scenario security|lie|happy`.

## Submission checklist
- [ ] Public GitLab project, MIT License shown in About.
- [ ] Protected `main` + code-owner approval; `@owner` replaced in CODEOWNERS.
- [ ] Visible CI/CD history (`scripts/seed_pipelines.py --plan default`): promoted, lie-blocked, security-blocked, probation-held.
- [ ] Duo flows published (tag) and enabled with triggers; one hands-off run from an assigned issue.
- [ ] Live Google Cloud URL + deployment code in repo (`infra/`).
- [ ] GitLab Pages dashboard live.
- [ ] 3-minute YouTube video, public, no third-party music/marks.
- [ ] Text description (above) in the form; opt into **Most Creative**.
- [ ] Contributor onboarding completed and approved.
- [ ] Confirm whether one project may win multiple prizes (Official Rules).
