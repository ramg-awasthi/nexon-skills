from __future__ import annotations

import argparse
import importlib
from pathlib import Path

from .common import DEFAULT_CONFIG_PATH, ensure_provider, load_config, write_json


PARSER_MODULES = {
    "aapt": "provider_adapters.aapt.parser",
    "telstra": "provider_adapters.telstra.parser",
    "optus_pdf": "provider_adapters.optus.parser_pdf",
    "optus_excel_voice": "provider_adapters.optus.parser_excel_voice",
    "vocus": "provider_adapters.vocus.parser",
    "megaport": "provider_adapters.megaport.parser",
    "equinix": "provider_adapters.equinix.parser",
}

PROVIDER_PARSER_KEYS = {
    "AAPT": "aapt",
    "Telstra": "telstra",
    "Vocus": "vocus",
    "Megaport": "megaport",
    "Equinix": "equinix",
}


def select_parser(provider: str, source_files: list[Path]) -> str:
    if provider == "Optus":
        if not source_files:
            raise ValueError("Optus package has no source files.")
        has_pdf = any(path.suffix.lower() == ".pdf" for path in source_files)
        has_non_pdf = any(path.suffix.lower() != ".pdf" for path in source_files)
        if has_pdf and has_non_pdf:
            raise ValueError("Ambiguous Optus package: split PDF and Excel/voice files into separate runs.")
        return "optus_pdf" if has_pdf else "optus_excel_voice"
    if provider not in PROVIDER_PARSER_KEYS:
        raise ValueError(f"Provider is not supported: {provider}")
    return PROVIDER_PARSER_KEYS[provider]


def main() -> int:
    parser = argparse.ArgumentParser(description="Route provider invoice files to deterministic parser module.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--provider", required=True)
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--warnings", type=Path, required=True)
    parser.add_argument("--run-id")
    parser.add_argument("--manifest", type=Path)
    args = parser.parse_args()

    config = load_config(args.config)
    provider_config = ensure_provider(config, args.provider)
    source_files = [path for path in args.input_dir.rglob("*") if path.is_file()]
    try:
        parser_key = select_parser(args.provider, source_files)
        module = importlib.import_module(PARSER_MODULES[parser_key])
        context = {
            "provider": args.provider,
            "run_id": args.run_id,
            "input_dir": str(args.input_dir),
            "source_files": [str(path) for path in source_files],
            "provider_config": provider_config,
            "parser_key": parser_key,
        }
        result = module.parse(source_files, context)
        write_json(args.output, result)
        write_json(args.warnings, [])
        if args.manifest:
            write_json(
                args.manifest,
                {
                    "provider": args.provider,
                    "run_id": args.run_id,
                    "parser": parser_key,
                    "source_file_count": len(source_files),
                    "status": "parsed",
                },
            )
        return 0
    except (NotImplementedError, ValueError) as exc:
        write_json(args.output, {"headers": [], "lines": []})
        write_json(args.warnings, [{"severity": "error", "message": str(exc), "parser": locals().get("parser_key")}])
        if args.manifest:
            write_json(
                args.manifest,
                {
                    "provider": args.provider,
                    "run_id": args.run_id,
                    "parser": locals().get("parser_key"),
                    "source_file_count": len(source_files),
                    "status": "parser_warning",
                },
            )
        return 3
    except Exception as exc:
        write_json(args.output, {"headers": [], "lines": []})
        write_json(
            args.warnings,
            [
                {
                    "severity": "error",
                    "message": f"parser_failed: unexpected parser error ({type(exc).__name__})",
                    "parser": locals().get("parser_key"),
                }
            ],
        )
        if args.manifest:
            write_json(
                args.manifest,
                {
                    "provider": args.provider,
                    "run_id": args.run_id,
                    "parser": locals().get("parser_key"),
                    "source_file_count": len(source_files),
                    "status": "parser_warning",
                },
            )
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
