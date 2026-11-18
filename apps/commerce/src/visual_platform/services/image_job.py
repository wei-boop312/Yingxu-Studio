import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


async def transform(source, target, generation, *, verify=False):
    work = ROOT / "work" / "transform"
    work.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=work) as directory:
        folder = Path(directory)
        a = folder / "source.png"
        b = folder / "target.png"
        output = folder / "output.png"
        config = folder / "job.json"
        a.write_bytes(source)
        b.write_bytes(target)
        config.write_text(
            json.dumps(
                {
                    "source": str(a),
                    "target": str(b),
                    "output": str(output),
                    "mode": generation["protection_mode"],
                    "regions": generation["regions"],
                    "action": "verify" if verify else "protect",
                }
            ),
            encoding="utf-8",
        )
        env = {
            k: v
            for k, v in os.environ.items()
            if k.upper() in {"PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "TEMP", "TMP", "LANG"}
        }
        env["PYTHONPATH"] = str(ROOT / "src")
        child = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "visual_platform.imaging.transform",
            str(config),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env=env,
        )
        try:
            stdout, _ = await asyncio.wait_for(child.communicate(), timeout=12)
        except BaseException:
            if child.returncode is None:
                child.kill()
                await child.wait()
            raise
        if child.returncode == 2:
            raise ValueError("pixel_protection_failed")
        if child.returncode != 0:
            raise RuntimeError("image_process_failed")
        return (target if verify else output.read_bytes()), json.loads(stdout)
