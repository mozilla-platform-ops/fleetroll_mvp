#!/usr/bin/env python3
"""Safe import verification tool for Claude Code bulk-allow.

This script ONLY imports specified modules/objects and reports success/failure.
It does NOT execute arbitrary Python code, making it safe to bulk-allow.

Usage:
    uv run verify-imports fleetroll.commands.monitor
    uv run verify-imports fleetroll.commands.monitor.MonitorDisplay
    uv run verify-imports module1.Class1 module2.function1
"""

import importlib
import sys


def verify_import(import_spec: str) -> bool:
    """Try to import a module or object and return True if successful.

    Supports:
    - Module imports: "fleetroll.commands.monitor"
    - Object imports: "fleetroll.commands.monitor.MonitorDisplay"
    """
    try:
        # Try to import as a module first
        try:
            importlib.import_module(import_spec)
            return True
        except ImportError:
            # If that fails, try importing as an object from a module
            # Split on the last dot to get module and object name
            parts = import_spec.rsplit(".", 1)
            if len(parts) == 2:
                module_name, object_name = parts
                module = importlib.import_module(module_name)
                if hasattr(module, object_name):
                    # Successfully imported the object
                    return True
            # Re-raise the original ImportError
            raise
    except ImportError as e:
        print(f"Failed to import {import_spec}: {e}", file=sys.stderr)
        return False
    except Exception as e:
        print(f"Unexpected error importing {import_spec}: {e}", file=sys.stderr)
        return False


def main() -> int:
    """Verify all module imports from command line arguments."""
    if len(sys.argv) < 2:
        print("Usage: verify_imports.py module1 [module2 ...]", file=sys.stderr)
        return 1

    module_names = sys.argv[1:]
    all_success = True

    for module_name in module_names:
        # Basic validation: module names should only contain letters, numbers, dots, underscores
        if not all(c.isalnum() or c in "._" for c in module_name):
            print(f"Invalid module name: {module_name}", file=sys.stderr)
            all_success = False
            continue

        if verify_import(module_name):
            print(f"✓ {module_name}")
        else:
            all_success = False

    return 0 if all_success else 1


if __name__ == "__main__":
    sys.exit(main())
