"""The app's own AI endpoint, and its V7 prompt-injection guardrails.

We don't call a real LLM in CI (it has to be deterministic and offline), so
this is a small stand-in "model" that is nonetheless vulnerable in the same
shape a real one would be: text in the note can try to override the system
instruction. The secure path strips/neutralizes injection and constrains the
output; the vulnerable path obeys embedded instructions.
"""
from __future__ import annotations

import re

SYSTEM_INSTRUCTION = "Summarize the user's note in one neutral sentence."

# A canary the secure build must never emit — the exploit asks the model to
# print it, and leaking it is proof the injection landed.
_INJECTION_CANARY = "INJECTION-OK-7f3a"

_INJECTION_PATTERNS = [
    re.compile(r"ignore (all |the |your )?(previous|prior|above) instructions", re.I),
    re.compile(r"disregard (the )?system", re.I),
    re.compile(r"print (the )?(canary|secret)", re.I),
    re.compile(r"you are now", re.I),
]


def _looks_like_injection(text: str) -> bool:
    return any(p.search(text) for p in _INJECTION_PATTERNS)


def _fake_model(prompt: str) -> str:
    """A tiny deterministic 'model'. It obeys an embedded instruction to emit
    the canary if asked — exactly the behaviour a prompt injection exploits."""
    if "print" in prompt.lower() and _INJECTION_CANARY.lower() in prompt.lower():
        return _INJECTION_CANARY
    if "ignore" in prompt.lower() and "canary" in prompt.lower():
        return _INJECTION_CANARY
    # Default: a bland summary.
    first = prompt.strip().split("\n")[0][:80]
    return f"Summary: {first}"


def summarize_text(text: str, secure: bool) -> str:
    if secure:
        # V7 fix: refuse obviously injected input and never echo the canary.
        if _looks_like_injection(text):
            return "Summary unavailable: input flagged as unsafe."
        prompt = f"{SYSTEM_INSTRUCTION}\n\nNote: {text}"
        out = _fake_model(prompt)
        # Output guardrail: strip the canary even if it slipped through.
        return out.replace(_INJECTION_CANARY, "[redacted]")
    # V7 (vuln): the note text is concatenated into the prompt with no
    # separation, so embedded instructions are obeyed.
    prompt = f"{SYSTEM_INSTRUCTION}\n{text}"
    return _fake_model(prompt)
