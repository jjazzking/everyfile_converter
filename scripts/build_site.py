"""정적 사이트를 조립한다 — GitHub Pages 에 올릴 형태.

브라우저에서 엔진을 돌리려면 순수 파이썬 휠들이 사이트에 함께 있어야 한다.
PyPI 를 실행 시점에 부르지 않는 이유: 사내망에서 외부 접속이 막혀 있을 수 있고,
받아 오는 버전이 조용히 바뀌면 어제 되던 변환이 오늘 달라진다.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
OUT = ROOT / "site"

# 워커의 WHEELS 목록과 파일명이 일치해야 한다.
PURE_DEPS = ["et_xmlfile", "openpyxl"]


def fetch_pure_wheel(package: str, into: Path) -> str:
    """PyPI 에서 순수 파이썬 휠을 받는다."""
    meta = json.load(urllib.request.urlopen(f"https://pypi.org/pypi/{package}/json"))
    for f in meta["urls"]:
        name = f["filename"]
        if name.endswith(("py3-none-any.whl", "py2.py3-none-any.whl")):
            urllib.request.urlretrieve(f["url"], into / name)
            return name
    raise SystemExit(f"{package}: 순수 파이썬 휠을 찾지 못했습니다")


def main() -> int:
    if OUT.exists():
        shutil.rmtree(OUT)
    shutil.copytree(WEB, OUT)

    wheels = OUT / "wheels"
    wheels.mkdir(exist_ok=True)

    subprocess.run(
        [sys.executable, "-m", "build", "--wheel", "--outdir", str(wheels)],
        cwd=ROOT,
        check=True,
    )
    built = [p.name for p in wheels.glob("everyfile_converter-*.whl")]
    if not built:
        raise SystemExit("everyfile 휠이 만들어지지 않았습니다")

    names = [fetch_pure_wheel(p, wheels) for p in PURE_DEPS] + built

    # 워커가 참조하는 파일명과 실제 파일이 어긋나면 배포 후에야 알게 된다.
    worker = (OUT / "worker.js").read_text(encoding="utf-8")
    missing = [n for n in names if n not in worker]
    if missing:
        raise SystemExit(
            "worker.js 의 WHEELS 목록과 맞지 않는 휠이 있습니다: "
            + ", ".join(missing)
            + "\n실제 파일: "
            + ", ".join(sorted(p.name for p in wheels.glob('*.whl')))
        )

    (OUT / ".nojekyll").touch()  # Pages 가 _ 로 시작하는 경로를 지우지 않도록

    files = [p for p in OUT.rglob("*") if p.is_file()]
    total = sum(p.stat().st_size for p in files)
    print(f"\nsite/ 조립 완료 — 파일 {len(files)}개, {total / 1e6:.2f}MB")
    for p in sorted(wheels.glob("*.whl")):
        print(f"  휠 {p.name}  ({p.stat().st_size/1024:.0f}KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
