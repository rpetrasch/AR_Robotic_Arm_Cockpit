"""System prompts, the user turn with context and sources, and the proposed steps
(docs/specs/spec_cockpit.md §14.1)."""

from __future__ import annotations

import json
import re
from functools import cache

from config.envfiles import REPO

BASE = """You are the assistant of the AR3 Cockpit, a web app that installs, runs, monitors and \
tests an AR3 robot arm with ROS 2 Jazzy, MoveIt 2, Gazebo Harmonic, ros2_control, Lichtblick, \
noVNC, Zenoh and Docker. You answer questions about these tools, the AR3 hardware (Teensy 4.1 \
retrofit, wiring, e-stop, gripper) and this repository.

How to answer:
- The user turn holds a context pack (JSON: the page, host, stack, failing checks, alerts, the \
job) and numbered sources from the repository. Base your answer on them and cite sources inline \
as [1], [2]; cite only sources that support the sentence.
- If the sources do not cover the question, say so in one sentence, then answer from general \
knowledge and mark that part with "(general knowledge)".
- Hardware facts (pin map, steps per degree, link lengths, serial protocol) only from the \
sources. If they are not there, say they are not documented yet.
- You cannot run anything. To propose an action, write a whitelisted step as [[step:<id>]] or a \
Make target as [[make:<target>]], using only the ids listed below; the user runs it with a \
button after confirming. Show any other command as a code block for the user to copy.
- Never ask for or repeat secrets (passwords, tokens, API keys); «redacted» marks removed ones.
- Be concise and concrete. Use Markdown: short paragraphs, lists, code blocks."""

TASK = {
    "chat": "",
    "error_explain": """Task: explain the warnings and errors of the job in the context pack \
(job.distinct_lines, each with its count, plus job.matches from the error patterns).
Sort every distinct line into exactly one class and answer with these three headings, in \
this order:
### Harmless
### Worth fixing
### Needs action
"Harmless": expected for this step (e.g. shutdown noise of a test, a deprecation warning of a \
dependency). "Worth fixing": a defect in this repository or its configuration that does not \
block the user now. "Needs action": it blocks the user or makes the result wrong.
Under each heading one list item per line: the message shortened in backticks, the count if \
more than 1, why it belongs there, and the sources that support it. Write "- none" under an \
empty heading. The job's exit code decides whether it succeeded; say so in one first sentence. \
End with the fix step, if any, as [[step:<id>]].""",
    "scenario_help": """Task: help write the scenario in the context pack (scenario.yaml) for \
config/scenarios/. Keep the schema of the sources and the site values (named poses, bins, \
joint limits) of the context. Answer with a short explanation, then the complete scenario \
(not a fragment) in one ```yaml code block. It is validated and shown as a diff; the user \
decides whether to take it.""",
}


@cache
def make_targets() -> dict[str, str]:
    """The documented Make targets: {target: description} from the ``## `` comments."""
    out = {}
    try:
        text = (REPO / "Makefile").read_text()
    except OSError:
        return out
    for m in re.finditer(r"(?m)^([a-zA-Z0-9_-]+):[^\n#]*##\s*(.+)$", text):
        out.setdefault(m.group(1), m.group(2).strip())
    return out


def _steps() -> list:
    from setup import steps

    return [s for s in steps.STEPS if not s.needs_root]


def system_prompt(task: str) -> str:
    steps = "\n".join(f"- {s.id}: {s.title}" for s in _steps())
    targets = "\n".join(f"- {t}: {d}" for t, d in sorted(make_targets().items()))
    parts = [
        BASE,
        TASK.get(task, ""),
        f"Steps ([[step:<id>]]):\n{steps}",
        f"Make targets ([[make:<target>]]):\n{targets}",
    ]
    return "\n\n".join(p for p in parts if p)


def user_turn(question: str, pack: dict) -> str:
    """The question with its context pack and the numbered sources."""
    ctx = {k: v for k, v in pack.items() if k != "sources"}
    sources = "\n\n".join(
        f"[{s['n']}] {s['source']} — {s['heading']}\n{s['text']}" for s in pack.get("sources", [])
    )
    return (
        f"Context pack:\n```json\n{json.dumps(ctx, ensure_ascii=False, indent=1)}\n```\n\n"
        f"Sources:\n{sources or '(none found)'}\n\nQuestion: {question}\n\n"
        "(Answer from the context pack and the sources; cite every source you use as [n]. "
        "Propose steps only as [[step:<id>]] from the list.)"
    )


MARKER = re.compile(r"\[\[(step|make):([a-zA-Z0-9_.:-]+)\]\]")


def proposals(text: str) -> list[dict]:
    """The steps and Make targets an answer proposes, validated against the whitelist. A Make
    target becomes a button only when a step runs exactly that target; otherwise it is text."""
    from setup import steps

    by_id = {s.id: s for s in _steps()}
    targets = make_targets()
    out, seen = [], set()
    for kind, name in MARKER.findall(text):
        if (kind, name) in seen:
            continue
        seen.add((kind, name))
        if kind == "step":
            step = by_id.get(name)
            if step is None:
                continue
            out.append(
                {
                    "kind": "step",
                    "id": name,
                    "title": step.title,
                    "step_id": name,
                    "command": step.command(),
                    "params": bool(step.params),
                }
            )
        elif name in targets:
            step = next(
                (s for s in steps.STEPS if s.argv == ("make", name) and not s.needs_root), None
            )
            out.append(
                {
                    "kind": "make",
                    "id": name,
                    "title": targets[name],
                    "step_id": step.id if step else None,
                    "command": f"make {name}",
                    "params": bool(step and step.params),
                }
            )
    return out


def yaml_block(text: str) -> str | None:
    """The last ```yaml block of an answer."""
    blocks = re.findall(r"```ya?ml[ \t]*\n(.*?)```", text, re.S)
    return blocks[-1].rstrip() + "\n" if blocks else None
