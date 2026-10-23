"""Static checks on the gate wiring: the CI graph resolves, the trust core in
CODEOWNERS matches the fingerprinted paths, and the Duo catalog items are
current and valid against GitLab's flow schema."""
import json
import os
import re

import yaml

from verifier.fingerprint import TRUST_CORE

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class _GitLabLoader(yaml.SafeLoader):
    pass


_GitLabLoader.add_constructor("!reference", lambda loader, node: loader.construct_sequence(node))


def _ci():
    with open(os.path.join(ROOT, ".gitlab-ci.yml")) as f:
        return yaml.load(f, Loader=_GitLabLoader)


RESERVED = {"stages", "variables", "workflow", "include", "default"}


def _jobs(ci):
    return {k: v for k, v in ci.items() if k not in RESERVED and isinstance(v, dict)}


def test_needs_and_extends_resolve():
    ci = _ci()
    jobs = _jobs(ci)
    stages = set(ci["stages"]) | {".pre", ".post"}
    for name, job in jobs.items():
        for parent in [job["extends"]] if isinstance(job.get("extends"), str) else job.get("extends", []):
            assert parent in jobs, f"{name} extends unknown {parent}"
        if name.startswith("."):
            continue
        assert job.get("stage", "test") in stages, f"{name}: unknown stage"
        for need in job.get("needs", []):
            target = need["job"] if isinstance(need, dict) else need
            assert target in jobs, f"{name} needs unknown job {target}"


def test_verifier_jobs_never_deploy_and_deploy_needs_every_gate():
    jobs = _jobs(_ci())
    deploy_needs = {n if isinstance(n, str) else n["job"] for n in jobs["deploy-prod"]["needs"]}
    assert {"blueteam-verify", "policy:autonomy"} <= deploy_needs
    for name, job in jobs.items():
        if name.startswith("verify"):
            body = json.dumps(job)
            for forbidden in ("gcloud run deploy", "deploy_prod.sh", "deploy_staging.sh",
                              "GCP_DEPLOY_SA", ".gcloud"):
                assert forbidden not in body, f"{name} uses {forbidden}"


def test_work_jobs_do_not_judge():
    """The test job must not fail on red tests; the verifier decides."""
    script = " ".join(_jobs(_ci())["test"]["script"])
    assert "|| echo" in script


def test_codeowners_matches_fingerprint():
    with open(os.path.join(ROOT, "CODEOWNERS")) as f:
        owned = {line.split()[0].strip("/") for line in f
                 if line.strip() and not line.startswith("#")}
    assert owned == set(TRUST_CORE)


def test_duo_catalog_is_current_and_schema_valid():
    import jsonschema

    from scripts.build_duo import main as build_duo

    assert build_duo(["--check"]) == 0, "run: python -m scripts.build_duo"
    with open(os.path.join(ROOT, "duo", "schema", "flow_v2.json")) as f:
        schema = json.load(f)
    # yaml_definition is filled in by GitLab when it stores the flow.
    schema["required"] = [r for r in schema["required"] if r != "yaml_definition"]
    for name in os.listdir(os.path.join(ROOT, "duo", "flows")):
        with open(os.path.join(ROOT, "duo", "flows", name)) as f:
            item = yaml.safe_load(f)
        assert 3 <= len(item["name"]) <= 255 and len(item["description"]) <= 1024
        jsonschema.validate(item["definition"], schema)
        # Jinja-sensitive sequences in system prompts would break templating.
        for prompt in item["definition"]["prompts"]:
            assert not re.search(r"\{\{|\{%|\{#", prompt["prompt_template"]["system"])
