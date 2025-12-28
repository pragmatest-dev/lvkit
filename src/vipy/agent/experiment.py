"""Experiment runner for comparing conversion strategies.

Run multiple strategies on the same VI(s) and compare results.

Usage:
    vipy experiment path/to/file.vi --strategies baseline,two_phase
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..graph import GraphConfig, VIGraph
from ..llm import LLMConfig
from .loop import ConversionAgent, ConversionConfig
from .strategies import list_strategies


@dataclass
class StrategyResult:
    """Result from running a strategy."""

    strategy_name: str
    success: bool
    attempts: int
    time_seconds: float
    errors: list[str] = field(default_factory=list)


@dataclass
class VIResult:
    """Results for a single VI across strategies."""

    vi_name: str
    results: dict[str, StrategyResult]  # strategy -> result


@dataclass
class ExperimentResults:
    """Results from a full experiment."""

    vis: list[VIResult]
    total_time: float

    def print_report(self) -> None:
        """Print experiment results."""
        print("=" * 60)
        print("EXPERIMENT RESULTS")
        print("=" * 60)

        for vi in self.vis:
            print(f"\nVI: {vi.vi_name}")
            print("-" * 40)

            for name, result in vi.results.items():
                status = "✓" if result.success else "✗"
                print(f"  {name:20} {status} ({result.attempts} attempts, {result.time_seconds:.1f}s)")
                if result.errors:
                    print(f"    Error: {result.errors[0][:50]}...")

        # Summary
        print("\n" + "=" * 60)
        print("SUMMARY")
        print("=" * 60)

        stats: dict[str, dict] = {}
        for vi in self.vis:
            for name, result in vi.results.items():
                if name not in stats:
                    stats[name] = {"success": 0, "total": 0, "attempts": 0, "time": 0.0}
                stats[name]["total"] += 1
                if result.success:
                    stats[name]["success"] += 1
                stats[name]["attempts"] += result.attempts
                stats[name]["time"] += result.time_seconds

        print(f"\n{'Strategy':<20} {'Success':>10} {'Avg Att':>10} {'Avg Time':>10}")
        print("-" * 52)
        for name, s in stats.items():
            rate = s["success"] / s["total"] * 100
            avg_att = s["attempts"] / s["total"]
            avg_time = s["time"] / s["total"]
            print(f"{name:<20} {rate:>9.0f}% {avg_att:>10.1f} {avg_time:>9.1f}s")

        print(f"\nTotal time: {self.total_time:.1f}s")


def run_experiment(
    vi_path: Path | str,
    strategies: list[str] | None = None,
    output_dir: Path | str | None = None,
    llm_config: LLMConfig | None = None,
    max_attempts: int = 3,
    search_paths: list[Path] | None = None,
) -> ExperimentResults:
    """Run experiment comparing strategies on a VI.

    Uses the main ConversionAgent in dependency order - same as production.
    Each strategy is run fresh, converting all VIs in the graph.
    """
    vi_path = Path(vi_path)

    if strategies is None:
        strategies = list_strategies()

    if output_dir is None:
        output_dir = Path("/tmp/vipy-experiment")
    else:
        output_dir = Path(output_dir)

    if llm_config is None:
        llm_config = LLMConfig()

    graph_config = GraphConfig()
    start_time = time.time()
    vi_results: list[VIResult] = []

    # Run each strategy separately (clean slate each time)
    strategy_results: dict[str, StrategyResult] = {}

    for strategy_name in strategies:
        print(f"Running strategy: {strategy_name}")

        # Create fresh output dir for this strategy
        strat_output = output_dir / strategy_name
        strat_output.mkdir(parents=True, exist_ok=True)

        # Fresh graph load for each strategy
        strat_start = time.time()

        with VIGraph(graph_config) as graph:
            graph.clear()
            graph.load_vi(
                vi_path,
                expand_subvis=True,
                search_paths=search_paths,
            )

            # Configure agent with this strategy
            config = ConversionConfig(
                output_dir=strat_output,
                max_retries=max_attempts,
                llm_config=llm_config,
                strategy=strategy_name,
            )

            agent = ConversionAgent(graph, config)

            # Convert all VIs in dependency order
            results = agent.convert_all()

            # Find the main VI result
            main_vi_name = None
            for name in graph.get_conversion_order():
                if vi_path.stem in name:
                    main_vi_name = name
                    break
            if main_vi_name is None:
                main_vi_name = graph.get_conversion_order()[-1]

            # Get result for main VI
            main_result = None
            total_attempts = 0
            for r in results:
                total_attempts += r.attempts
                if r.vi_name == main_vi_name:
                    main_result = r

            strat_time = time.time() - strat_start

            if main_result:
                strategy_results[strategy_name] = StrategyResult(
                    strategy_name=strategy_name,
                    success=main_result.success,
                    attempts=main_result.attempts,
                    time_seconds=strat_time,
                    errors=main_result.errors,
                )
            else:
                strategy_results[strategy_name] = StrategyResult(
                    strategy_name=strategy_name,
                    success=False,
                    attempts=0,
                    time_seconds=strat_time,
                    errors=["Main VI not found in results"],
                )

    # Build VI result
    main_vi_name = vi_path.stem
    vi_results.append(VIResult(vi_name=main_vi_name, results=strategy_results))

    experiment = ExperimentResults(
        vis=vi_results,
        total_time=time.time() - start_time,
    )

    experiment.print_report()
    return experiment
