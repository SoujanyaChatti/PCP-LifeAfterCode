# Submission — Proof-Carrying Pipeline

For [Life After Code](https://gitlab-transcend.devpost.com/). Path A (Start
Fresh) · Hands-off autonomy · opt into **Most Creative**.
Deadline: **Oct 27, 2026, 1:00pm UTC.**

## One-liner
Autonomous delivery that can't lie: AI agents take a GitLab issue to production,
and every promotion carries its own machine-checkable proof.

## Text description (for the form)
Hands-off agents are the goal of agentic DevSecOps, but teams won't give agents
production access because agent summaries are unverifiable — an agent can write
"all tests pass, security issue resolved" whether or not it's true. Proof-Carrying
Pipeline fixes this: agents still do all the work (plan, build, attack, patch,
release), but at every GitLab DevSecOps stage an **independent, deterministic
verifier** checks the agent's structured claim against real evidence from the
GitLab API, the container registry, the live service, and a pinned exploit
replay — sources the agents cannot write to. A claim that disagrees with the
evidence is blocked and tagged as a caught lie; a security fix must be proven by
re-running the exploit, not merely asserted. The verifier, exploit playbook,
schemas and gate wiring are locked behind CODEOWNERS and a protected branch, so
no agent can weaken its own grader. The result is a fully hands-off flow from
issue to a signed, independently verifiable evidence pack on production.

## How GitLab automates the post-code lifecycle here
All nine DevSecOps stages are touched by real automation:

| Stage | Automation |
| --- | --- |
| Plan | Planner agent writes acceptance criteria + risk label |
| Create | Builder agent writes code/tests, opens the MR |
| Verify | pytest JUnit + coverage; `verify:tests` gate checks the real report |
| Package | Image built + pushed to the Container Registry, digest recorded |
| Secure | SAST/dependency/secret scanning + Red vs Blue replay gate |
| Release | Staged staging→prod promotion of the exact verified digest |
| Configure | Terraform IaC for Cloud Run + Workload Identity Federation |
| Monitor | 5/5 post-deploy health probes + auto-rollback |
| Govern | Signed evidence pack + CODEOWNERS policy + issue audit trail |

## Required links (fill in)
- Public GitLab project: `________`
- Live Google Cloud URL (prod Cloud Run): `________`
- 3-minute YouTube demo (public, no third-party music/marks): `________`

## Demo video script (~2:45)
1. **0:00–0:20** The problem: agents can claim anything; nobody checks. Say the
   one-liner.
2. **0:20–0:50** Open issue "Add note tagging", label `pcp::go`, walk away. The
   flow kicks off.
3. **0:50–1:30** Agents plan, build, open the MR; tests + scans run; each gate
   shows a green **verified** check (not just "the agent said so").
4. **1:30–2:10** Red Team dumps the user table via SQL injection; Blue Team
   patches; the **identical** exploit now returns a safe 400 — side by side.
   Gate goes green. (Use `python -m scripts.rehearse --scenario security` if you
   want a clean, fast capture.)
5. **2:10–2:30** Production deploy; the signed evidence pack is posted on the
   issue; `verify-evidence` shows the signature checks out (and fails on a
   tampered copy).
6. **2:30–2:45** The money shot: a run where an agent claims "tests pass" but a
   test actually failed. The gate blocks, tags the lie, opens a follow-up issue.
   (`python -m scripts.rehearse --scenario lie`.) Close on the line.

## Submission checklist
- [ ] Public GitLab project, MIT License shown in About.
- [ ] Visible CI/CD history: successful, security-blocked, and lie-blocked runs.
- [ ] 3-minute YouTube video, public, no third-party music/marks.
- [ ] Text description (above) in the form.
- [ ] Live Google Cloud URL + deployment code in repo (`infra/`).
- [ ] Opt into **Most Creative**.
- [ ] README explains the trust model + CODEOWNERS boundary. ✅
- [ ] Contributor onboarding completed and approved.
- [ ] Confirm whether one project may win multiple prizes (Official Rules).
