"""LLM provider abstraction for code generation.

Supports Anthropic API and Ollama (local) backends.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass


@dataclass
class LLMConfig:
    """Configuration for LLM code generation."""

    provider: str = "anthropic"  # "anthropic" or "ollama"
    model: str = "claude-sonnet-4-20250514"
    timeout: int = 120
    max_tokens: int = 8192
    temperature: float = 0.0


@dataclass
class LLMResponse:
    """Response from an LLM generation call."""

    text: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    stop_reason: str = ""


def generate(
    prompt: str,
    system: str = "",
    config: LLMConfig | None = None,
) -> LLMResponse:
    """Generate text from an LLM.

    Args:
        prompt: User message
        system: System prompt
        config: Provider configuration

    Returns:
        LLMResponse with generated text
    """
    if config is None:
        config = LLMConfig()

    if config.provider == "anthropic":
        return _generate_anthropic(prompt, system, config)
    elif config.provider == "ollama":
        return _generate_ollama(prompt, system, config)
    else:
        raise ValueError(f"Unknown provider: {config.provider}")


def _generate_anthropic(
    prompt: str,
    system: str,
    config: LLMConfig,
) -> LLMResponse:
    """Generate using Anthropic API."""
    import anthropic  # type: ignore[import-untyped]

    client = anthropic.Anthropic()

    create_kwargs: dict[str, object] = {
        "model": config.model,
        "max_tokens": config.max_tokens,
        "temperature": config.temperature,
        "messages": [{"role": "user", "content": prompt}],
    }
    if system:
        create_kwargs["system"] = system
    message = client.messages.create(**create_kwargs)  # type: ignore[arg-type]

    text = ""
    for block in message.content:
        if block.type == "text":
            text += block.text

    return LLMResponse(
        text=text,
        model=config.model,
        input_tokens=message.usage.input_tokens,
        output_tokens=message.usage.output_tokens,
        stop_reason=message.stop_reason or "",
    )


def _generate_ollama(
    prompt: str,
    system: str,
    config: LLMConfig,
) -> LLMResponse:
    """Generate using local Ollama."""
    full_prompt = f"{system}\n\n{prompt}" if system else prompt

    result = subprocess.run(
        ["ollama", "run", config.model, full_prompt],
        capture_output=True,
        text=True,
        timeout=config.timeout,
    )

    if result.returncode != 0:
        raise RuntimeError(f"Ollama failed: {result.stderr}")

    return LLMResponse(
        text=result.stdout.strip(),
        model=config.model,
    )
