"""Check that every committed workflow still passes the real validator.

The only check in the repo that needs no running stack: it runs the same chain
`workflow-mcp` runs on a write, so a broken seed workflow fails before deploy.

    uv run --package nautionette-workflow-mcp python scripts/check_workflows.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "services" / "workflow-mcp"))

from app.validate import run_checks  # noqa: E402

WORKFLOWS = Path(__file__).resolve().parent.parent / "workflows"


def main() -> int:
    files = sorted(WORKFLOWS.glob("*.py"))
    if not files:
        print("no workflows to check")
        return 0

    failed = 0
    for path in files:
        result = run_checks(path.stem, path.read_text())
        if result["valid"]:
            print(f"PASS  {path.name}")
            continue
        failed += 1
        print(f"FAIL  {path.name}")
        for error in result["errors"]:
            print(f"      {error}")
        for step in result["steps"]:
            if not step["ok"]:
                print(f"      step {step['step']}: {step['detail']}")

    print(f"\n{len(files) - failed}/{len(files)} workflows valid")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
