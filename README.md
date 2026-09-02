# everyfile-converter

회계 실무에서 오가는 파일 포맷 사이의 변환을 한 곳에서 처리하기 위한 엔진입니다.
지금은 **변환 코어**가 구현되어 있고, 웹 대시보드와 PDF 표 추출이 다음 단계입니다.

## 왜 이런 구조인가

### 1. N×M 이 아니라 중간표현(IR)

포맷이 5개면 조합은 20개가 되고, HWP 하나만 추가해도 30개가 됩니다. 모든 변환이
공통 IR 을 거치므로 **파서 N개 + 라이터 N개**로 끝납니다.

```
파일 ──readers──▶ Document/TableIR ──convert──▶ ConversionResult ──writers──▶ 파일
                                          └────preview────▶ 화면 페이로드
```

### 2. 모든 행이 원본 위치를 끝까지 들고 다닌다

`SourceRow.index` 는 원본 파일의 행 번호입니다. 소계 행이 제외되거나 배열이 평탄화되어
행 수가 달라져도 변환 결과가 원본 어느 줄에서 왔는지 잃지 않습니다. 미리보기 화면의
좌우 패널 정렬, 이슈 보고, 검수 추적이 전부 이 앵커 위에 서 있습니다. 나중에 붙이려면
IR 을 다시 설계해야 하므로 처음부터 넣었습니다.

### 3. 변환은 값이 아니라 "값 + 근거"를 돌려준다

셀 하나를 변환하면 결과값과 함께 **규칙 체인**과 **이슈**가 남습니다.

```
(240,000)  →  -240000    괄호→음수 › strip:thousands › cast:number
```

회계 자료는 숫자 하나가 틀리면 끝나기 때문에, "왜 이 값이 되었는가" 를 화면에서 셀 단위로
확인할 수 있어야 합니다.

### 4. 값을 조용히 잃지 않는다

- **계정코드의 선행 0**: `CODE` 타입은 문자열로 보존하며, 숫자로 캐스팅하면 경고를 남깁니다.
  엑셀 출력에서도 셀 서식이 아니라 셀 타입 자체를 텍스트로 씁니다 (서식만으로는 지켜지지 않음).
- **변환 실패**: 값을 버리고 `null` 로 만들지 않고 원문을 유지한 채 ERROR 로 보고합니다.
- **단위 표기**: `(단위: 천원)` 을 발견하면 알리기만 하고 자동으로 곱하지 않습니다.
  잘못 곱하면 조용히 1000배 틀린 값이 나갑니다.
- **`-` 표기**: 0이 아니라 '해당 없음'으로 읽습니다.

## 설치

```bash
uv venv && uv pip install -e ".[dev]"
```

## 사용법

### 파일 구조 확인

```bash
everyfile inspect 일반전표_2026Q1.xlsx
```

```
[일반전표]
  헤더 행 : 3
  컬럼    : 전표일자, 계정코드, 계정과목, 적요, 거래처, 차변, 대변
  데이터  : 11행 / 전체 15행
  추론 타입:
    전표일자           → entryDate        date
    계정코드           → accountCode      code
    차변             → debit            money
  · 2행에 단위 표기 '원' 이 있습니다 — 금액은 자동 환산하지 않습니다
  · 헤더를 3행에서 찾았습니다 (위 2행은 머리글)
  · 소계·합계로 보이는 1개 행을 데이터에서 제외했습니다
```

### 변환

```bash
everyfile convert 일반전표.xlsx out.json          # 프로파일 자동 추론
everyfile convert 일반전표.xlsx out.csv --encoding cp949
everyfile convert 일반전표.xlsx out.xlsx --profile 전표표준.json --fail-on-error
```

### 프로파일 (컬럼 매핑 + 타입)

스키마를 손으로 쓰게 하면 아무도 쓰지 않으므로, 추론된 초안을 저장해 고쳐 쓰는 방식입니다.

```bash
everyfile profile 일반전표.xlsx -o 전표표준.json
everyfile profile 일반전표.xlsx -o schema.json --json-schema   # 표준 JSON Schema
```

프로파일은 스키마 버전을 가지므로, 저장해 둔 프로파일이 엔진 업데이트로 조용히
깨지지 않습니다.

### 미리보기 페이로드

대시보드가 렌더링할 JSON 계약입니다. 좌우 패널이 `sourceRow` 로 짝지어져 있고,
출력에서 빠진 행도 `included: false` 로 자리를 지킵니다.

```bash
everyfile preview 일반전표.xlsx -o preview.json
```

샘플링은 *앞 20행 + 이슈 행 + 결정적 무작위 20행* 입니다. 전체를 매번 변환하면 타입을
바꿀 때마다 화면이 멈추고, 앞부분만 보면 3만 번째 행에서 깨지는 파일을 놓칩니다.
시드가 고정되어 있어 같은 파일의 미리보기는 항상 동일합니다.

### 라이브러리로

```python
from everyfile import load

job = load("일반전표.xlsx")
job.profile.field_by_key("accountCode").type      # FieldType.CODE
job.result.records                                 # [{...}, ...]
job.result.issues                                  # [(행번호, 필드, Issue), ...]
job.save("out.json")
job.preview()                                      # 화면 페이로드
```

## 현재 지원 범위

| | 읽기 | 쓰기 |
|---|---|---|
| xlsx / xlsm | ✓ | ✓ |
| csv / tsv | ✓ (UTF-8 / CP949 / EUC-KR 자동 감지) | ✓ |
| json | ✓ (중첩 평탄화) | ✓ |
| pdf | — | — |
| docx / md | — | — |

## 안전장치

- **CSV 수식 인젝션 방어**: `=`, `+`, `-`, `@` 로 시작하는 값을 이스케이프합니다.
  외부에서 받은 파일을 변환해 다시 배포하는 일이 잦은 환경에서 필수입니다.
  음수 금액(`-1240000`)은 통과시킵니다.
- **CSV 출력 기본 인코딩 `utf-8-sig`**: BOM 이 없으면 한국어 윈도우 엑셀이 UTF-8 CSV 를
  CP949 로 읽어 한글이 전부 깨집니다. 구형 시스템용으로는 `--encoding cp949`.
- **엑셀 출력의 `검수필요` 시트**: 변환 산출물만 받아본 사람도 무엇을 확인해야 하는지
  파일 안에서 알 수 있습니다.

## 개발

```bash
.venv/bin/python -m pytest -q          # 75 tests
.venv/bin/python -m ruff check src tests
```

## 다음 단계

1. FastAPI 로 미리보기 API 노출 → 대시보드 화면 연결
2. PDF 표 추출 (pdfplumber, MIT — PyMuPDF/iText 는 AGPL 이라 사내 배포에 부적합)
   `SourceRef` 에 `page`/`bbox`/`confidence` 자리를 이미 비워 두었습니다
3. Markdown ↔ Word (문서 IR 추가)
4. 배치 처리 — 다중 파일, ZIP, 병합/분할, 파일명 규칙
5. 감사 로그와 고객사별 데이터 격리
