# Polygraph

**Autonomous delivery that can't lie: every promotion carries its own proof.**

AI agents take a GitLab issue all the way to production, but **no agent is ever
trusted on its word**. Every claim an agent makes ("tests pass", "vulnerability
fixed", "deployed and healthy") is checked by deterministic code against
evidence the agent cannot write to, before anything is promoted. A claim the
evidence contradicts is blocked and recorded as a **caught lie**, and the agent
that made it loses unattended access to production until it earns it back.

> Built for the [Life After Code](https://gitlab-transcend.devpost.com/) hackathon — Path A (Start Fresh), Hands-off autonomy.

---

## Why this exists

Hands-off agents are the goal of agentic DevSecOps, but teams won't give agents
production access because agent summaries are unverifiable. An agent can write
"all tests pass, security issue resolved" whether or not it's true, and humans
rarely check. This project removes that trust blocker: gates check evidence,
never agent text.

## What makes it different

| | |
| --- | --- |
| **Claims vs evidence** | Agents emit structured claims (MR notes). The verifier re-derives every number from the JUnit report, the container registry, the Cloud Run API, Cloud Monitoring and an exploit replay. A mismatch is a `claim_mismatch`: a caught lie. |
| **Fixes proven by replay** | The Red Team's exploits run against a *pre-patch twin* of the build; the identical exploits must then fail against the hardened build. "Fixed" is demonstrated, not asserted. |
| **Tests that must prove themselves** | Each regression test is run against the pre-patch twin, where it must **fail**, and against the patch, where it must **pass**. A test that passes on both (`assert True`) is rejected as a false claim. |
| **A verifier that tests itself** | Every pipeline plants known lies (one per claim type, plus one about *this run's* real report). If the verifier misses any, the run blocks: a gate that always says PASS can't hide. |
| **Who verified the verifier** | Every evidence pack carries a fingerprint of the exact verifier, playbook, gate wiring and agent prompts that judged it. MRs that touch that core are flagged and need CODEOWNER approval. |
| **Earned autonomy** | A hash-chained trust ledger scores every agent's claims across runs. Agents whose claims keep verifying deploy unattended; an agent caught lying goes on probation (a human must approve its next promotion) and is suspended after repeated lies. |
| **Signed, public record** | Each run's evidence pack is signed keyless with Sigstore cosign (pipeline OIDC identity, Rekor transparency log) and verified in a separate job pinned to this project's identity. A GitLab Pages dashboard shows the chain. |
| **Proofs expire** | A scheduled job re-verifies that production still serves the last proven digest and is healthy, and opens an incident on drift. |

## The trust model

1. **Claims are data, not truth.** Every agent ends its step by posting a
   structured JSON claim ([`schemas/claim.schema.json`](schemas/claim.schema.json)).
   Free-text summaries are never used for gating.
2. **Evidence comes from authoritative sources only**: the GitLab API, the
   container registry, the live service, the pinned exploit replay.
3. **Separation of duties.** Verifier jobs read GitLab with a read-only token
   and Google Cloud as a read-only service account; only deploy jobs hold the
   deployer identity. Work jobs never judge (the `test` job succeeds even on
   red tests; `verify:tests` is what fails).
4. **Fail closed.** Missing, stale or ambiguous evidence is not a pass. A gate
   that never ran did not pass. Empty SHAs match nothing.
5. **Evidence is bound to the artifact.** Checks compare commit SHAs and image
   digests; the promoted digest is the exact one the security gate replayed
   against, and behaviour is baked into the image, not set at deploy time.
6. **Reproducible.** Each check stores its query, a hash of the raw response,
   and the verdict.
7. **The LLM never decides a verdict.** Verdict logic is deterministic,
   versioned code under CODEOWNERS.

### The CODEOWNERS boundary

[`CODEOWNERS`](CODEOWNERS) assigns the trust-critical core to a human: the
verifier, exploit playbook, schemas, gate wiring (`.gitlab-ci.yml`, `ci/`),
the scripts that turn CI output into evidence, the test harness (`conftest.py`,
`pytest.ini`), and the agents' own prompts and flows. With a protected `main`
and code-owner approval, **no agent can alter its own grader or instructions.**
[`verifier/fingerprint.py`](verifier/fingerprint.py) hashes the same paths into
every evidence pack, and a test keeps the two lists identical.

### Threat model: what this does and doesn't stop

- **Stops:** an agent misreporting test results, coverage, or which commit was
  tested; claiming a vulnerability is fixed when the exploit still works;
  shipping a regression test that doesn't test anything; promoting the
  vulnerable twin or a different digest; adding `allow_failure` to the CI file;
  production silently drifting from the proven digest; a broken or
  rubber-stamping verifier; edited history in the trust ledger.
- **Relies on GitLab settings:** protected `main` + code-owner approval (so
  agents can't edit the core), and keeping the two tokens separate.
- **Doesn't stop:** a weakness the playbook has no probe for, or tests that
  are weak in ways other than "passes on the vulnerable build". The playbook
  covers the seven seeded weaknesses, by design; it's not a general scanner
  (GitLab SAST/dependency/secret scanning run alongside it).

## How a run works

```
Issue assigned to the PCP Deliver flow (GitLab Duo)
  Planner      acceptance criteria + risk label        claim plan.ready   ─▶ verify:plan     (live issue)
  Builder      code + tests, MR "Closes #n"            claim code.ready   ─▶ verify:create   (MR API)
                                                        claim tests.pass   ─▶ verify:tests    (JUnit + coverage)
  CI           planted lies                                               ─▶ verify:canary   (verifier alive?)
  CI           hardened image + pre-patch twin                            ─▶ verify:package  (registry labels)
  Red Team     7 probes vs the twin (own staging only)
  Blue Team    patches + regression tests (Duo flow on pipeline failure)
                                                        claim vuln.fixed   ─▶ blueteam-verify (replay + fail-before/pass-after)
  Trust        every agent in the run must be autonomous                  ─▶ policy:autonomy (ledger)
  Release      promote the verified digest to Cloud Run claim deploy.healthy ─▶ verify:deploy (Cloud Run API + Monitoring; rollback)
  Evidence     assemble + sign (cosign keyless)                           ─▶ verify-evidence (signature + decision)
  Report       comment on issue/MR, follow-up issue if blocked, ledger, dashboard
```

## See it in 30 seconds (offline)

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
python -m scripts.rehearse --scenario all --out rehearsal
```

The rehearsal runs the real pipeline steps in-process: an honest change is
**promoted**; a Blue Team patch that misses V1 is **blocked** (the same SQL
injection still dumps the user tokens, and the "V1 fixed" claim is a caught
lie), then the real patch is promoted; a Builder claiming "tests pass" over a
real failure is **caught**. The ledger ends with the Builder and Blue Team on
probation and opens a dashboard at `rehearsal/public/index.html`. (Offline,
only the image digest and the signature are stand-ins, and both say so.)

## The demo app and its seeded weaknesses

A small FastAPI "team notes" app ([`app/`](app/)) ships seven known weaknesses
so the security gate has concrete, synthetic targets. The pipeline builds two
images from every commit: the **hardened build** (what ships) and a
**pre-patch twin** (hardening compiled out, vulnerable dependency installed)
that only the Red Team ever sees. The exploit runner refuses any host but the
run's own staging, and data is synthetic.

| ID | Weakness | Fix |
| --- | --- | --- |
| V1 | SQL injection in note search | Parameterized query |
| V2 | Reflected XSS in search echo | Output escaping |
| V3 | IDOR on `GET /notes/{id}` | Ownership check |
| V4 | Admin route with no auth | Require admin role |
| V5 | Stack trace leaked on 500 | Generic error handler |
| V6 | Vulnerable dependency (python-multipart 0.0.6) | Patched version; `/healthz` reports the *installed* version |
| V7 | Prompt injection in `/summarize` | Input/output guardrails |

## GitLab Duo Agent Platform

- **Flows** ([`duo/flows/`](duo/flows/)), generated from the prompts in
  [`agents/`](agents/) and validated against GitLab's flow schema in CI:
  - **PCP Deliver**: Planner → Builder. Triggered by assigning an issue.
  - **PCP Blue Team**: Triage → Blue Team | Pipeline Fixer. Triggered by a
    failed pipeline; conditional routing; bounded to 3 rounds.
- **Agent**: **PCP Evidence Explainer** (Duo Chat) explains a verdict from the
  signed pack.
- **MCP**: the verifier and the exploit replay are MCP tools
  ([`mcp_tools/`](mcp_tools/), [`.gitlab/duo/mcp.json`](.gitlab/duo/mcp.json)),
  so an agent can *request* a check or a self-test but never compute the verdict.
- Published to the AI Catalog by GitLab's catalog-sync component on tag pipelines.

LLM agents do the creative work (plan, code, patch). Deterministic code does the
attacking, judging and releasing. Without Duo the pipeline still runs every
gate; claims are then synthesized by CI and marked `origin: ci-synthesized`.

## Google Cloud

Cloud Run for an ephemeral twin + hardened staging pair per MR and for
production; Artifact Registry (images copied from GitLab's registry with the
digest preserved); Cloud Monitoring for the error-rate evidence; Workload
Identity Federation restricted to this project's CI tokens, with three
identities: a production deployer only protected-branch jobs can obtain, a
staging deployer, and a read-only verifier. All in [`infra/`](infra/). See
[`SETUP.md`](SETUP.md).

## Repository layout

```
app/            FastAPI demo app (seeded weaknesses, V1..V7)
tests/          app, exploit self-tests, verifier, ledger, canary, sources, CI wiring
agents/         one prompt per agent (source of truth for the Duo flows)   (CODEOWNERS)
duo/            generated Duo flows + agent, vendored flow schema          (CODEOWNERS)
verifier/       checks, catalogue, CLI, canary, fingerprint, ledger, gcp   (CODEOWNERS)
redteam/        exploit playbook, replay gate, regression proof            (CODEOWNERS)
schemas/        claim + evidence JSON schemas                              (CODEOWNERS)
evidence_pack/  assemble + render the evidence pack                        (CODEOWNERS)
scripts/        claims, signing, reporting, seeding, rehearsal             (CODEOWNERS)
dashboard/      GitLab Pages dashboard from the ledger
mcp_tools/      MCP servers exposing the verifier + replay
infra/          Terraform + Cloud Run deploy scripts                       (CODEOWNERS)
.gitlab-ci.yml  the gated pipeline                                         (CODEOWNERS)
```

## Tests

```bash
python -m pytest        # 112 tests, ~92% coverage of app/verifier/redteam/evidence_pack
```

Highlights: every probe must succeed on the twin and fail on the patch; every
regression test must fail-before/pass-after; the canary must catch all planted
lies and must notice a sabotaged check; the ledger must detect edited or
deleted history; the CI graph must resolve; CODEOWNERS must match the
fingerprinted core; the Duo flows must validate against GitLab's schema.

## License

[MIT](LICENSE).
