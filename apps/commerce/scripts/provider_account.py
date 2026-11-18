"""Explicit read-only BFL account check; save balance evidence only in a new private directory."""

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from visual_platform.core.credentials import private_credential
from visual_platform.providers.bfl_account import probe_bfl_credits
from visual_platform.services.quality_export import private_destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bfl-credit-balance", action="store_true", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        output = args.output_dir.absolute()
        root = next((p for p in (ROOT / ".local", ROOT / "work") if output.is_relative_to(p)), None)
        if root is None:
            raise ValueError("private_account_output_required")
        target = private_destination(output, root)
        key = private_credential("BFL_API_KEY")
        if not key:
            print(
                json.dumps(
                    {
                        "result": "WAITING_PRIVATE_CREDENTIALS",
                        "variable": "BFL_API_KEY",
                        "paid_model_calls": 0,
                    }
                )
            )
            return 3
        receipt, raw = probe_bfl_credits(key)
        target.mkdir(mode=0o700)
        for name, data in (
            ("response.json", raw),
            ("receipt.json", (json.dumps(receipt, indent=2) + "\n").encode()),
        ):
            fd = os.open(target / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
        # Exact balance and key fingerprint remain in the private receipt, never stdout.
        print(
            json.dumps(
                {
                    "result": "BFL_ACCOUNT_READ_OBSERVED",
                    "positive_balance": receipt["positive_balance"],
                    "models_verified": False,
                    "spending_authorized": False,
                    "paid_model_calls": 0,
                }
            )
        )
        return 0
    except Exception as error:
        print(
            json.dumps(
                {
                    "result": "ACCOUNT_PROBE_REJECTED",
                    "error_type": type(error).__name__,
                    "paid_model_calls": 0,
                }
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
