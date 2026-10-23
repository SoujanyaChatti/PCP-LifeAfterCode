# Seeding pipeline history

Judges look for visible CI/CD history that tells the story: successful runs,
security-blocked runs, and lie-blocked runs (target: 20+ runs incl. 3+ blocked).
Here's how to produce each kind. Run these on branches/MRs so each triggers a
pipeline.

## Successful end-to-end runs (×N)
Open an MR with a small, clean change (e.g. a copy tweak or a new test) and let
the flow run to a proven prod deploy. Repeat a handful of times.

## Security-blocked, then fixed (the climax)
1. Open the **"Add note tagging"** MR against the vulnerable build (SECURE_MODE=0
   staging). The Red Team finds V1/V3 etc.; the `blueteam-verify` gate is red.
2. Push the Blue Team patch (flip to the patched behaviour + regression tests).
   The replay now fails for every exploit; the gate goes green and it promotes.

## Lie-blocked runs (×3+)
Inject a false claim so the verifier catches it:
```bash
# in a branch pipeline, replace the honest claim step with:
python scripts/inject_false_claim.py --out claims/tests.pass.json
# and make one test actually fail, so asserted (0 failures) != observed.
```
Or temporarily point the `make-claim:tests` job at `inject_false_claim.py`. The
`verify:tests` job exits non-zero, tags `claim_mismatch`, and blocks `package`.

## Quick local proof for the video (no cloud)
```bash
python -m scripts.rehearse --scenario lie       # the money shot
python -m scripts.rehearse --scenario security  # the exploit that stops working
python -m scripts.rehearse --scenario happy     # proven promotion
```
