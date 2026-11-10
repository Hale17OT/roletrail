"""Full multi-source cycle; uses the same policy and configuration as the API/CLI."""
import json

from discovery.pipeline import run_discovery
from harvester import log_harvest


def run_cycle(sync=True):
    _, report = run_discovery(sync=sync)
    log_harvest(f"Cycle: discovered={report['discovered']}, verified={report['verified']}, added={report['added']}")
    return report


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    print(json.dumps(run_cycle(sync=not args.dry_run), indent=2, ensure_ascii=False))
