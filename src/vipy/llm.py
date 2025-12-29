"""LLM integration for code generation using Ollama."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass


@dataclass
class LLMConfig:
    """Configuration for the LLM."""
    model: str = "qwen2.5-coder:14b"
    timeout: int = 120  # seconds


def generate_code(prompt: str, config: LLMConfig | None = None) -> str:
    """Generate Python code from a prompt using Ollama.

    Args:
        prompt: The prompt to send to the LLM
        config: Optional LLM configuration

    Returns:
        Generated Python code

    Raises:
        RuntimeError: If Ollama fails
    """
    if config is None:
        config = LLMConfig()

    try:
        result = subprocess.run(
            ["ollama", "run", config.model],
            input=prompt,
            capture_output=True,
            text=True,
            timeout=config.timeout,
        )

        if result.returncode != 0:
            raise RuntimeError(f"Ollama failed: {result.stderr}")

        return _extract_code(result.stdout)

    except subprocess.TimeoutExpired:
        raise RuntimeError(f"Ollama timed out after {config.timeout}s")
    except FileNotFoundError:
        raise RuntimeError("Ollama not found. Install from https://ollama.com")


def _extract_code(response: str) -> str:
    """Extract Python code from LLM response.

    Handles responses with or without markdown code blocks.
    """
    lines = response.strip().split('\n')

    # Check if response contains code blocks
    in_code_block = False
    code_lines = []

    for line in lines:
        if line.strip().startswith('```python'):
            in_code_block = True
            continue
        elif line.strip().startswith('```') and in_code_block:
            in_code_block = False
            continue
        elif in_code_block:
            code_lines.append(line)

    if code_lines:
        return '\n'.join(code_lines)

    # No code block found, return cleaned response
    # Filter out obvious non-code lines
    cleaned = []
    for line in lines:
        # Skip lines that look like markdown or explanatory text
        stripped = line.strip()
        if stripped.startswith('#') or stripped.startswith('def ') or \
           stripped.startswith('import ') or stripped.startswith('from ') or \
           stripped.startswith('class ') or stripped.startswith('return ') or \
           stripped == '' or line.startswith(' ') or line.startswith('\t'):
            cleaned.append(line)

    return '\n'.join(cleaned) if cleaned else response


def check_ollama_available() -> bool:
    """Check if Ollama is available and responding."""
    try:
        result = subprocess.run(
            ["ollama", "list"],
            capture_output=True,
            timeout=5,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def list_models() -> list[str]:
    """List available Ollama models."""
    try:
        result = subprocess.run(
            ["ollama", "list"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return []

        # Parse output: NAME  ID  SIZE  MODIFIED
        models = []
        for line in result.stdout.strip().split('\n')[1:]:  # Skip header
            if line.strip():
                name = line.split()[0]
                models.append(name)
        return models

    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []
