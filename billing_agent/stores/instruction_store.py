"""
Instruction Store — formats project-level PL instructions for LLM context.

Wraps the existing ProjectInstruction list already loaded in IngestionResult
and renders it as a prompt-ready text block for the exception reasoning agent.
"""

from typing import List

from billing_agent.models.instruction import ProjectInstruction


def format_for_prompt(instructions: List[ProjectInstruction]) -> str:
    if not instructions:
        return "No project instructions on record."
    lines = []
    for inst in instructions:
        lines.append(
            f"- [{inst.instruction_id}] {inst.instruction_type} ({inst.instruction_date})"
            f" — {inst.scope}\n"
            f"  Body: {inst.body[:300]}"
        )
    return "\n".join(lines)
