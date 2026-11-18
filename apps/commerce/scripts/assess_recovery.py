import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from visual_platform.services.recovery_objectives import assess_recovery

p = argparse.ArgumentParser()
p.add_argument("--rto-seconds", type=float, required=True)
p.add_argument("--snapshot-age-seconds", type=float, required=True)
args = p.parse_args()
objectives = json.loads((ROOT / "ops/recovery-objectives.json").read_text(encoding="utf-8-sig"))
print(json.dumps(assess_recovery(args.rto_seconds, args.snapshot_age_seconds, objectives)))
