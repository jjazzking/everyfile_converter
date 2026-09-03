"""명령줄 인터페이스.

화면이 붙기 전에 엔진을 실제 파일로 돌려볼 수 있어야 하고, 붙은 뒤에도 배치 처리와
자동화(감시 폴더, CI)의 진입점으로 남는다.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .fields import Severity
from .pipeline import load
from .profile import Profile, infer_profile
from .readers import SUPPORTED as READ_FORMATS
from .readers import read
from .writers import SUPPORTED as WRITE_FORMATS


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="everyfile",
        description="회계 실무용 파일 변환 엔진",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_inspect = sub.add_parser("inspect", help="파일 구조를 읽어 요약한다")
    p_inspect.add_argument("source")
    p_inspect.add_argument("--sheet")

    p_profile = sub.add_parser("profile", help="프로파일 초안을 추론해 저장한다")
    p_profile.add_argument("source")
    p_profile.add_argument("-o", "--out", required=True, help="저장할 프로파일 JSON 경로")
    p_profile.add_argument("--sheet")
    p_profile.add_argument("--name")
    p_profile.add_argument(
        "--json-schema", action="store_true", help="프로파일 대신 JSON Schema 를 저장한다"
    )

    p_convert = sub.add_parser("convert", help="변환해서 파일로 쓴다")
    p_convert.add_argument("source")
    p_convert.add_argument("target")
    p_convert.add_argument("--profile", help="프로파일 JSON 경로 (없으면 자동 추론)")
    p_convert.add_argument("--sheet")
    p_convert.add_argument(
        "--encoding", default="utf-8-sig", help="CSV 출력 인코딩 (기본 utf-8-sig)"
    )
    p_convert.add_argument(
        "--fail-on-error",
        action="store_true",
        help="ERROR 이슈가 하나라도 있으면 종료코드 2 로 끝낸다",
    )

    p_preview = sub.add_parser("preview", help="미리보기 페이로드를 JSON 으로 출력한다")
    p_preview.add_argument("source")
    p_preview.add_argument("--profile")
    p_preview.add_argument("--sheet")
    p_preview.add_argument("-o", "--out", help="파일로 저장 (기본: 표준출력)")

    p_serve = sub.add_parser("serve", help="미리보기 대시보드를 띄운다")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8000)
    p_serve.add_argument("--reload", action="store_true")

    args = parser.parse_args(argv)

    try:
        return _dispatch(args)
    except (ValueError, FileNotFoundError) as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 1


def _dispatch(args: argparse.Namespace) -> int:
    if args.command == "inspect":
        return _inspect(args)
    if args.command == "profile":
        return _profile(args)
    if args.command == "convert":
        return _convert(args)
    if args.command == "preview":
        return _preview(args)
    if args.command == "serve":
        return _serve(args)
    raise ValueError(f"알 수 없는 명령: {args.command}")


def _serve(args: argparse.Namespace) -> int:
    try:
        import uvicorn
    except ImportError:
        raise ValueError(
            "웹 의존성이 설치되어 있지 않습니다 — uv pip install -e '.[web]'"
        ) from None

    print(f"대시보드: http://{args.host}:{args.port}")
    uvicorn.run("everyfile.api:app", host=args.host, port=args.port, reload=args.reload)
    return 0


def _inspect(args: argparse.Namespace) -> int:
    document = read(args.source)
    print(f"{document.origin}  ({document.source_format}, 표 {len(document.tables)}개)")
    print(f"  읽기 지원: {', '.join(READ_FORMATS)}   쓰기 지원: {', '.join(WRITE_FORMATS)}")

    for table in document.tables:
        if args.sheet and table.name != args.sheet:
            continue
        print(f"\n[{table.name}]")
        print(f"  헤더 행 : {table.header_row or '(못 찾음)'}")
        print(f"  컬럼    : {', '.join(table.header) or '(없음)'}")
        print(f"  데이터  : {table.total_data_rows:,}행 / 전체 {len(table.rows):,}행")
        if table.encoding:
            print(f"  인코딩  : {table.encoding}")

        profile = infer_profile(table)
        print("  추론 타입:")
        for spec in profile.fields:
            print(f"    {spec.source:<14} → {spec.key:<16} {spec.type.value}")

        for note in table.notes:
            print(f"  · {note}")
    return 0


def _profile(args: argparse.Namespace) -> int:
    document = read(args.source)
    table = _pick(document, args.sheet)
    profile = infer_profile(table, name=args.name)

    out = Path(args.out)
    if args.json_schema:
        out.write_text(
            json.dumps(profile.to_json_schema(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"JSON Schema 저장: {out}  (필드 {len(profile.fields)}개)")
    else:
        profile.save(out)
        print(f"프로파일 저장: {out}  (필드 {len(profile.fields)}개)")
        print("타입이 잘못 추론된 필드는 이 파일에서 고친 뒤 convert 에 --profile 로 넘기세요.")
    return 0


def _convert(args: argparse.Namespace) -> int:
    profile = Profile.load(args.profile) if args.profile else None
    job = load(args.source, profile=profile, sheet=args.sheet)

    kwargs = {}
    if Path(args.target).suffix.lower() in (".csv", ".tsv"):
        kwargs["encoding"] = args.encoding

    job.save(args.target, **kwargs)

    counts = job.result.counts()
    written = len(job.result.records)
    dropped = sum(1 for r in job.result.rows if not r.included)

    print(f"{job.document.origin} → {args.target}")
    print(f"  기록 {written:,}행" + (f" · 제외 {dropped:,}행" if dropped else ""))
    print(
        "  셀: 변경 {changed:,} · 신규 {created:,} · 이슈 {issue:,} · 유지 {unchanged:,}".format(
            **counts
        )
    )

    for note in job.result.notes:
        print(f"  · {note}")

    _print_issues(job.result.issues)
    if args.fail_on_error and job.result.error_count:
        print(f"\nERROR 이슈 {job.result.error_count}건으로 실패 처리합니다.", file=sys.stderr)
        return 2
    return 0


def _preview(args: argparse.Namespace) -> int:
    profile = Profile.load(args.profile) if args.profile else None
    job = load(args.source, profile=profile, sheet=args.sheet)
    payload = json.dumps(job.preview(), ensure_ascii=False, indent=2)

    if args.out:
        Path(args.out).write_text(payload + "\n", encoding="utf-8")
        print(f"미리보기 저장: {args.out}")
    else:
        print(payload)
    return 0


def _pick(document, sheet: str | None):
    if sheet is None:
        return document.primary
    table = next((t for t in document.tables if t.name == sheet), None)
    if table is None:
        names = ", ".join(t.name for t in document.tables)
        raise ValueError(f"시트 {sheet!r} 를 찾을 수 없습니다 (있는 시트: {names})")
    return table


def _print_issues(issues, limit: int = 15) -> None:
    if not issues:
        return
    errors = sum(1 for _, _, i in issues if i.severity is Severity.ERROR)
    print(f"\n검수 필요 {len(issues)}건 (오류 {errors}건):")
    for source_row, key, issue in issues[:limit]:
        print(f"  [{issue.severity.value:<7}] {source_row}행 {key}: {issue.message}")
    if len(issues) > limit:
        print(f"  … 외 {len(issues) - limit}건")


if __name__ == "__main__":
    raise SystemExit(main())
