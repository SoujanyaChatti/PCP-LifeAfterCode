"""Generate the GitLab Duo Agent Platform catalog items in duo/ from the agent
prompts in agents/<name>/prompt.md, so each prompt has one source of truth.

    python -m scripts.build_duo            # write duo/flows/*.yml, duo/agents/*.yml
    python -m scripts.build_duo --check    # exit 1 if duo/ is stale (CI / tests)

Flows (published with the ai-catalog catalog-sync component, ci/duo-catalog.yml):
  PCP Deliver    Planner -> Builder. Trigger: assign or mention the flow's
                 service account on an issue.
  PCP Blue Team  Triage -> Blue Team | Pipeline Fixer | end. Trigger: pipeline
                 failed.

Agent:
  PCP Evidence Explainer  Duo Chat agent that explains, from the signed
                 evidence pack, why a run was promoted or blocked.

LLM agents only ever *claim* (MR/issue notes with the pcp-claim marker); the
deterministic verifier in CI judges every claim against evidence.
"""
from __future__ import annotations

import argparse
import os
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONTEXT = [{"from": "context:goal", "as": "goal"},
           {"from": "context:project_id", "as": "project_id"}]
USER = "{{goal}}\n\nProject ID: {{project_id}}"
LOG = ["on_agent_final_answer", "on_tool_execution_success", "on_tool_execution_failed"]


def _prompt(name: str) -> str:
    with open(os.path.join(ROOT, "agents", name, "prompt.md")) as f:
        return f.read()


def _component(name: str, tools: list[str], inputs: list | None = None) -> dict:
    return {"name": name, "type": "AgentComponent", "prompt_id": f"pcp_{name}",
            "inputs": inputs or CONTEXT, "toolset": tools, "ui_log_events": LOG}


def _local_prompt(name: str, title: str, user: str = USER, timeout: int = 600) -> dict:
    return {"prompt_id": f"pcp_{name}", "name": title, "unit_primitives": [],
            "prompt_template": {"system": _prompt(name), "user": user, "placeholder": "history"},
            "params": {"timeout": timeout}}


READ_CODE = ["read_file", "read_files", "list_dir", "find_files", "grep"]
WRITE_CODE = ["edit_file", "create_file_with_contents", "mkdir"]


def deliver_flow() -> dict:
    builder_inputs = CONTEXT + [{"from": "context:planner.final_answer", "as": "plan"}]
    return {
        "name": "PCP Deliver",
        "description": ("Polygraph: plans an issue and implements it as a merge "
                        "request. Every step ends in a structured claim that the pipeline's "
                        "deterministic verifier checks against evidence before anything ships."),
        "public": True,
        "definition": {
            "version": "v1",
            "environment": "ambient",
            "components": [
                _component("planner", ["get_issue", "update_issue", "create_issue_note",
                                       "list_issue_notes"]),
                _component("builder", READ_CODE + WRITE_CODE + [
                    "run_tests", "run_command", "get_issue", "create_branch", "create_commit",
                    "create_merge_request", "create_merge_request_note"], builder_inputs),
            ],
            "prompts": [
                _local_prompt("planner", "PCP Planner", timeout=300),
                _local_prompt("builder", "PCP Builder",
                              user=("Plan from the Planner:\n{{plan}}\n\nOriginal request:\n"
                                    "{{goal}}\n\nProject ID: {{project_id}}"), timeout=1200),
            ],
            "routers": [{"from": "planner", "to": "builder"}, {"from": "builder", "to": "end"}],
            "flow": {"entry_point": "planner"},
        },
    }


def blue_team_flow() -> dict:
    fixer_tools = READ_CODE + WRITE_CODE + [
        "run_tests", "run_command", "get_pipeline_failing_jobs", "get_job_logs",
        "get_pipeline_errors", "get_merge_request", "list_all_merge_request_notes",
        "create_commit", "create_merge_request_note"]
    return {
        "name": "PCP Blue Team",
        "description": ("Polygraph: when an MR pipeline fails, triages it and either "
                        "patches the weaknesses the Red Team proved (with regression tests) or "
                        "fixes the build honestly. Bounded to 3 rounds; every fix is re-proven "
                        "by the pipeline's exploit replay, never taken on the agent's word."),
        "public": True,
        "definition": {
            "version": "v1",
            "environment": "ambient",
            "components": [
                _component("triage", ["get_pipeline_failing_jobs", "get_job_logs",
                                      "get_merge_request"]),
                _component("blueteam", fixer_tools),
                _component("pipeline_fixer", fixer_tools),
            ],
            "prompts": [
                _local_prompt("triage", "PCP Failure Triage", timeout=180),
                _local_prompt("blueteam", "PCP Blue Team", timeout=1200),
                _local_prompt("pipeline_fixer", "PCP Pipeline Fixer", timeout=1200),
            ],
            "routers": [
                {"from": "triage", "condition": {
                    "input": "context:triage.final_answer",
                    "routes": {"security": "blueteam", "pipeline": "pipeline_fixer",
                               "stop": "end", "default_route": "end"}}},
                {"from": "blueteam", "to": "end"},
                {"from": "pipeline_fixer", "to": "end"},
            ],
            "flow": {"entry_point": "triage"},
        },
    }


EXPLAINER = """You explain Polygraph verdicts to developers.

Each pipeline ends with a signed evidence pack (job `evidence`, artifact
`evidence-pack.json`) and a Reporter comment on the issue and MR. When asked
why a run was promoted or blocked:

1. Find the pipeline (from the MR, or the URL you are given) and read the
   Reporter comment or the `evidence`/`verify-evidence` job logs.
2. Explain each failed gate in plain words, quoting the check id and its
   reason. Call out any `claim_mismatch` (a caught false claim): which agent
   asserted what, and what the evidence showed.
3. Say what would make the gate pass, without suggesting ways to weaken it.

You never decide a verdict and never claim a gate passed unless the pack says
so. The verifier is deterministic code; you only explain its records."""


def explainer_agent() -> dict:
    return {"name": "PCP Evidence Explainer",
            "description": ("Explains, from the signed evidence pack, why a Proof-Carrying "
                            "Pipeline run was promoted or blocked, including caught false claims."),
            "public": True,
            "system_prompt": EXPLAINER,
            "tools": ["get_merge_request", "list_all_merge_request_notes", "get_issue",
                      "list_issue_notes", "get_pipeline_failing_jobs", "get_job_logs",
                      "gitlab_api_get"]}


OUTPUTS = {
    "duo/flows/pcp-deliver.yml": deliver_flow,
    "duo/flows/pcp-blue-team.yml": blue_team_flow,
    "duo/agents/pcp-evidence-explainer.yml": explainer_agent,
}
HEADER = "# GENERATED by scripts/build_duo.py from agents/*/prompt.md. Do not edit.\n"


class _NoAliases(yaml.SafeDumper):
    def ignore_aliases(self, data):
        return True


def render(fn) -> str:
    return HEADER + yaml.dump(fn(), Dumper=_NoAliases, sort_keys=False, width=100,
                              allow_unicode=True)


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--check", action="store_true")
    args = p.parse_args(argv)
    stale = []
    for rel, fn in OUTPUTS.items():
        path = os.path.join(ROOT, rel)
        text = render(fn)
        current = open(path).read() if os.path.exists(path) else None
        if args.check:
            if current != text:
                stale.append(rel)
            continue
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(text)
        print(f"wrote {rel}")
    if stale:
        print("stale (run python -m scripts.build_duo):", ", ".join(stale))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
