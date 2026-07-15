#!/usr/bin/env python3
"""Runtime verification: detect dead configurations in bootstrap.py.

A "dead config" is a configuration dictionary (e.g., AUTO_PAPER_SWING_RULES)
that is defined in bootstrap.py but never actually used by any bootstrap
function that gets called by bootstrap_local_paper_runtime(). This script
scans bootstrap.py to detect such cases and warns the operator.

This is part of the "delivery self-check" system to prevent future regressions
where config is written but never connected to the scheduler.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path


def extract_config_dicts(bootstrap_path: Path) -> set[str]:
    """Extract all top-level dict assignment names that look like config."""
    content = bootstrap_path.read_text(encoding="utf-8")
    config_names = set()
    for match in re.finditer(r"^([A-Z_]+_RULES|[A-Z_]+_KEY)\s*[=:]", content, re.MULTILINE):
        config_names.add(match.group(1))
    return config_names


def extract_bootstrap_functions(bootstrap_path: Path) -> set[str]:
    """Extract all function names that start with 'bootstrap_'."""
    content = bootstrap_path.read_text(encoding="utf-8")
    tree = ast.parse(content, filename=str(bootstrap_path))
    functions = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name.startswith("bootstrap_"):
            functions.add(node.name)
    return functions


def extract_all_function_calls(bootstrap_path: Path) -> dict[str, set[str]]:
    """Extract all function-to-function call relationships."""
    content = bootstrap_path.read_text(encoding="utf-8")
    tree = ast.parse(content, filename=str(bootstrap_path))

    call_graph: dict[str, set[str]] = {}

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            func_name = node.name
            call_graph[func_name] = set()
            for child in ast.walk(node):
                if isinstance(child, ast.Call) and isinstance(child.func, ast.Name):
                    call_graph[func_name].add(child.func.id)

    return call_graph


def extract_runtime_calls(bootstrap_path: Path) -> set[str]:
    """Extract all functions reachable from bootstrap_local_paper_runtime() (recursive)."""
    call_graph = extract_all_function_calls(bootstrap_path)

    if "bootstrap_local_paper_runtime" not in call_graph:
        return set()

    # BFS to find all reachable functions
    reachable = set()
    queue = list(call_graph["bootstrap_local_paper_runtime"])

    while queue:
        func = queue.pop(0)
        if func in reachable:
            continue
        reachable.add(func)
        if func in call_graph:
            for called in call_graph[func]:
                if called not in reachable:
                    queue.append(called)

    return reachable


def extract_config_references(bootstrap_path: Path) -> dict[str, set[str]]:
    """For each config dict, extract which functions reference it."""
    content = bootstrap_path.read_text(encoding="utf-8")
    tree = ast.parse(content, filename=str(bootstrap_path))

    config_names = extract_config_dicts(bootstrap_path)
    references: dict[str, set[str]] = {name: set() for name in config_names}

    current_function = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            current_function = node.name
        elif isinstance(node, ast.Name) and node.id in config_names and current_function:
            references[node.id].add(current_function)

    return references


def main() -> int:
    repo_root = Path(__file__).parent.parent
    bootstrap_path = repo_root / "services" / "execution" / "bootstrap.py"

    if not bootstrap_path.exists():
        print(f"ERROR: {bootstrap_path} not found", file=sys.stderr)
        return 1

    print("Scanning bootstrap.py for dead configurations...")
    print()

    config_dicts = extract_config_dicts(bootstrap_path)
    runtime_calls = extract_runtime_calls(bootstrap_path)
    config_refs = extract_config_references(bootstrap_path)

    print(f"Found {len(config_dicts)} configuration dicts")
    print(f"Found {len(runtime_calls)} functions called by bootstrap_local_paper_runtime()")
    print()

    dead_configs = []
    for config_name in sorted(config_dicts):
        referencing_funcs = config_refs[config_name]
        if not referencing_funcs:
            dead_configs.append((config_name, "not referenced by any function"))
            continue

        # Check if any referencing function is in the runtime call chain
        reachable = any(func in runtime_calls for func in referencing_funcs)
        if not reachable:
            dead_configs.append((config_name, f"only referenced by {referencing_funcs}, none in runtime chain"))

    if dead_configs:
        print("⚠️  DEAD CONFIGURATIONS DETECTED:")
        print()
        for config_name, reason in dead_configs:
            print(f"  • {config_name}")
            print(f"    Reason: {reason}")
            print()
        print("These configurations are defined but never used by bootstrap_local_paper_runtime().")
        print("Either:")
        print("  1. Create a bootstrap function that uses them")
        print("  2. Add that function to bootstrap_local_paper_runtime()")
        print("  3. Or remove the dead config if it's no longer needed")
        return 1

    print("✅ No dead configurations detected")
    print()
    print("All config dicts are referenced by at least one function in the runtime call chain.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
