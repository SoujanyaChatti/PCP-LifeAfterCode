# Proof-Carrying Pipeline

**Autonomous delivery that can't lie: every promotion carries its own proof.**

AI agents take a GitLab issue all the way to production — but **no agent is ever
trusted on its word**. Every claim an agent makes at a stage gate ("tests pass",
"vulnerability fixed", "deployed and healthy") must be backed by
machine-checkable evidence from a source the agent cannot write to, before the
pipeline promotes. Unproven claims block promotion and open a follow-up issue.

> Built for the [Life After Code](https://gitlab-transcend.devpost.com/) hackathon — Path A (Start Fresh), Hands-off autonomy.

---

## Why this exists

Hands-off agents are the goal of agentic DevSecOps, but teams won't give agents
production access because agent summaries are unverifiable. An agent can write
"all tests pass, security issue resolved" whether or not it's true, and humans
rarely check. This project removes that trust blocker: the gates check evidence,
never agent text.

## The trust model (read this first)

1. **Claims are data, not truth.** Every agent ends its step by emitting a
   structured JSON claim ([`schemas/claim.schema.json`](schemas/claim.schema.json)). Free-text summaries are never used for gating.
2. **Evidence comes from authoritative sources only** — the GitLab API, the
   container registry, the live service, and the pinned exploit replay. Never
   from a file an agent wrote in the same job.
3. **Separation of duties.** Agents that build cannot verify. The verifier runs
   in its own CI job with its own read-only token. No token can both build and
   verify.
4. **Fail closed.** Missing, stale or ambiguous evidence = FAIL. Timeouts fail.
   Parse errors fail.
5. **Evidence is bound to the artifact.** Every check records the commit SHA and
   image digest it applies to; evidence for a different one is rejected.
6. **Everything is reproducible.** Each check stores its query, the raw response
   hash, and the verdict.
7. **The LLM never decides a verdict.** It may propose which checks a claim
   needs; the check catalogue and verdict logic are deterministic code, reviewed
   and versioned.

### The CODEOWNERS boundary — the technical backbone

The trust-critical core — [`verifier/`](verifier/), [`redteam/`](redteam/),
[`schemas/`](schemas/), [`flows/`](flows/), and [`.gitlab-ci.yml`](.gitlab-ci.yml) —
is assigned to the human owner in [`CODEOWNERS`](CODEOWNERS). Combined with a
protected `main` branch, **no build or patch agent can alter the checks, the
exploits, the claim schema, or the gate wiring.** Agents may be imperfect; the
gates cannot be edited by them. This is what makes "can't lie" true.

## How a run works

```
issue (labeled pcp::go)
  → Planner   writes acceptance criteria + risk label         → plan.ready   ─┐
  → Builder   implements change + tests, opens MR              → code.ready    │ each gate:
  → CI        pytest (JUnit + coverage)                        → tests.pass    │ verify:<x>
  → Verifier  checks the REAL report vs the claim  ◀───────────── blocks on ───┤ reads evidence,
  → CI        build + push image (digest, SHA label)           → image.built   │ exits non-zero
  → CI        deploy ephemeral staging (Cloud Run)                             │ on FAIL
  → Red Team  runs the white-hat playbook on OUR staging       → attack.complete
  → Blue Team patches each finding + regression test           → vuln.fixed    │
  → Verifier  REPLAYS the exploits on the patched staging  ◀──── must now fail ─┤
  → Release   merges, promotes the exact verified digest       → deploy.healthy │
  → Verifier  confirms prod serves the digest, health probes                   ─┘
  → Evidence  assemble + sign pack (cosign), post issue comment
  → verify-evidence   independently checks the signature
```

A gate that cannot verify its claim blocks promotion and opens a follow-up issue
instead of advancing.

## The three things this proves on camera

| Scenario | What you see |
| --- | --- |
| **Happy path** | One issue goes to production with zero human clicks; every gate green. |
| **Security gate** | The same SQL-injection that dumped the user table before the patch returns a safe 400 after it — proven by replay, not asserted. |
| **Caught lie** | An agent claims "tests pass" but a test actually failed. The gate blocks, tags it `claim_mismatch`, and opens a follow-up issue. |

Run all three offline, no cloud needed:

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
python -m scripts.rehearse --scenario all
```

## The demo app and its (synthetic, seeded) weaknesses

A small FastAPI "team notes" app ([`app/`](app/)) deliberately ships seven known
weaknesses so the security gate has concrete, synthetic targets to find and fix.
They toggle on a single `SECURE_MODE` flag, which makes the before/after replay
crisp. These are standard pre-release security-test fixtures on the team's own
code — the exploit runner refuses any host but the project's own ephemeral
staging URL, and runs only in CI against synthetic data.

| ID | Weakness | Fix |
| --- | --- | --- |
| V1 | SQL injection in note search | Parameterized query |
| V2 | Reflected XSS in search echo | Output escaping |
| V3 | IDOR on `GET /notes/{id}` | Ownership check |
| V4 | Admin route with no auth | Require admin role |
| V5 | Stack trace leaked on 500 | Generic error handler |
| V6 | Vulnerable dependency pin | Bump to patched version |
| V7 | Prompt injection in `/summarize` | Input/output guardrails |

## GitLab Duo Agent Platform

Seven agents ([`agents/`](agents/)) each have a versioned prompt, a minimal
allowed-tools list, and a scoped token. A Duo flow ([`flows/pipeline.flow.yaml`](flows/pipeline.flow.yaml))
wires the hands-off sequence. The verifier and the exploit replay are exposed as
**MCP tools** ([`mcp_tools/`](mcp_tools/)) so agents can *request* a check but
never compute the verdict. The `.gitlab-ci.yml` encodes the identical gated
logic as the guaranteed fallback, so the gates work with or without Duo.

## Google Cloud

The app runs on Cloud Run (one ephemeral staging service per MR, one production
service). Everything is infrastructure-as-code in [`infra/`](infra/) (Terraform),
including GitLab→GCP **Workload Identity Federation**, so no long-lived key is
stored. See [`SETUP.md`](SETUP.md) to stand it up.

## Repository layout

```
app/        FastAPI demo app (seeded weaknesses)
tests/      pytest unit/integration + exploit self-tests + verifier tests
agents/     one prompt per agent (+ claim_lib)
verifier/   deterministic checks + catalogue + runner   (CODEOWNERS-protected)
redteam/    white-hat exploit playbook + replay gate     (CODEOWNERS-protected)
schemas/    claim + evidence JSON schemas                (CODEOWNERS-protected)
flows/      Duo flow + agent definitions                 (CODEOWNERS-protected)
mcp_tools/  MCP servers exposing verifier + replay as tools
evidence_pack/  assemble, sign, and report the signed evidence pack
infra/      Terraform + gcloud deploy scripts (Cloud Run, WIF)
scripts/    CI helpers + the offline end-to-end rehearsal
.gitlab-ci.yml   the gated pipeline spine                (CODEOWNERS-protected)
```

## Tests

```bash
python -m pytest        # 56 tests: app, exploit self-tests, verifier, evidence pack
```

The exploit self-tests prove every probe succeeds on the vulnerable build and
fails on the patched build — so the playbook can't drift. The verifier tests
include a fixture where an agent's asserted number disagrees with the evidence,
proving the lie is caught.

## License

[MIT](LICENSE).
