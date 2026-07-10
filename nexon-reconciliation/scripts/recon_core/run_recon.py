from __future__ import annotations

import argparse


def main() -> int:
    parser = argparse.ArgumentParser(description="Disabled full-run wrapper.")
    parser.add_argument("--provider", required=True)
    parser.add_argument("--source-file")
    parser.add_argument("--billing-period")
    parser.parse_args()
    raise NotImplementedError(
        "integration_unavailable: Full-run wrapper requires enabled provider parsers and read-only billing SQL flow. Use explicit step scripts under supervisor control."
    )


if __name__ == "__main__":
    raise SystemExit(main())
