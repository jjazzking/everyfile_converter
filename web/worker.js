/**
 * 브라우저 안에서 변환 엔진을 돌리는 웹 워커.
 *
 * 여기서 도는 파이썬은 서버가 쓰는 것과 **같은 코드**다 (`everyfile.session.Session`).
 * 두 실행 환경이 다른 코드를 타면 "브라우저에선 되는데 서버에선 값이 다르다" 가 생긴다.
 *
 * 워커로 분리한 이유: 3만 행 변환이 1초 넘게 걸린다. 메인 스레드에서 돌리면
 * 그동안 화면이 통째로 얼어붙는다.
 */

const PYODIDE_VERSION = "314.0.6";

/**
 * 배포판(`/pyodide/vX/full/`) 이 아니라 npm 배포본을 쓴다.
 *
 * 우리는 Pyodide 가 함께 배포하는 패키지를 하나도 쓰지 않는다 — openpyxl 을 포함해
 * 필요한 것은 전부 아래 WHEELS 로 직접 싣기 때문이다. 그래서 코어만 있는 npm 배포본으로
 * 충분하고, 받는 양도 적다.
 *
 * 2단계에서 PDF 를 붙일 때는 Pillow·cryptography 같은 네이티브 패키지가 필요해지므로
 * `https://cdn.jsdelivr.net/pyodide/v${PYODIDE_VERSION}/full/` 로 바꿔야 한다.
 */
const PYODIDE_URL = `https://cdn.jsdelivr.net/npm/pyodide@${PYODIDE_VERSION}/`;

/** 사이트에 함께 배포되는 순수 파이썬 휠들. 설치 순서가 의존성 순서다. */
const WHEELS = [
  "et_xmlfile-2.0.0-py3-none-any.whl",
  "openpyxl-3.1.5-py2.py3-none-any.whl",
  "everyfile_converter-0.1.0-py3-none-any.whl",
];

let pyodide = null;
let ready = null;

function progress(text) {
  self.postMessage({ type: "progress", text });
}

async function boot() {
  progress("변환 엔진을 준비하는 중… (처음 한 번만)");

  let loadPyodide;
  try {
    ({ loadPyodide } = await import(PYODIDE_URL + "pyodide.mjs"));
  } catch {
    // 사내망이 외부 CDN 을 막는 경우가 흔하다. 원문 오류를 그대로 보여주면
    // 무엇을 해야 하는지 알 수 없다.
    throw new Error(
      "변환 엔진을 내려받지 못했습니다 — cdn.jsdelivr.net 접근이 막혀 있는지 확인해 주세요." +
        " 사내망이라면 관리자에게 이 주소의 허용을 요청하거나, 사내 서버 설치를 검토하세요.",
    );
  }
  pyodide = await loadPyodide({ indexURL: PYODIDE_URL });

  progress("엔진 구성 요소를 설치하는 중…");
  const site = pyodide.runPython("import site; site.getsitepackages()[0]");

  for (const wheel of WHEELS) {
    const res = await fetch(new URL("./wheels/" + wheel, self.location.href));
    if (!res.ok) throw new Error(`구성 요소를 받지 못했습니다: ${wheel} (${res.status})`);
    pyodide.unpackArchive(new Uint8Array(await res.arrayBuffer()), "zip", {
      extractDir: site,
    });
  }

  // 서버와 같은 Session 을 세운다. 파일은 브라우저 안의 가상 디스크에만 쓰인다.
  pyodide.runPython(`
import base64, json
from everyfile import samples
from everyfile.session import Session, SessionError

_session = Session(storage="/session")

def _open(name, b64):
    return json.dumps(_session.open(name, base64.b64decode(b64)), ensure_ascii=False)

def _sample():
    return json.dumps(_session.open(samples.SAMPLE_NAME, samples.sample_bytes()),
                      ensure_ascii=False)

def _preview(file_id, profile_json, sheet):
    profile = json.loads(profile_json) if profile_json else None
    return json.dumps(_session.preview(file_id, profile, sheet), ensure_ascii=False)

def _json_schema(file_id, profile_json, sheet):
    profile = json.loads(profile_json) if profile_json else None
    return json.dumps(_session.json_schema(file_id, profile, sheet), ensure_ascii=False)

def _convert(file_id, profile_json, sheet, fmt):
    profile = json.loads(profile_json) if profile_json else None
    kwargs = {"encoding": "utf-8-sig"} if fmt.lower() in ("csv", "tsv") else {}
    data, filename, summary = _session.convert(file_id, profile, sheet, fmt, **kwargs)
    return json.dumps({
        "b64": base64.b64encode(data).decode(),
        "filename": filename,
        "summary": summary,
    }, ensure_ascii=False)
`);

  progress("");
  return pyodide;
}

/** 파이썬 쪽 SessionError 를 화면이 읽을 수 있는 오류로 옮긴다. */
function toError(err) {
  const text = String(err && err.message ? err.message : err);
  // Pyodide 는 파이썬 트레이스백을 통째로 담아 온다. 마지막 줄만 사람이 읽을 내용이다.
  const lines = text.trim().split("\n").filter(Boolean);
  const last = lines[lines.length - 1] || text;
  const message = last.replace(/^[A-Za-z_.]*(Error|Exception):\s*/, "");
  const code = /NotFound/.test(text)
    ? "not_found"
    : /UnsupportedFormat/.test(text)
      ? "unsupported_format"
      : /TooLarge/.test(text)
        ? "too_large"
        : /Unreadable/.test(text)
          ? "unreadable"
          : /BadProfile/.test(text)
            ? "bad_profile"
            : "error";
  return { message, code };
}

function b64(bytes) {
  let s = "";
  const chunk = 0x8000; // 한 번에 넘기면 큰 파일에서 스택이 넘친다
  for (let i = 0; i < bytes.length; i += chunk) {
    s += String.fromCharCode.apply(null, bytes.subarray(i, i + chunk));
  }
  return btoa(s);
}

function fromB64(text) {
  const bin = atob(text);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

const OPS = {
  init: async () => ({ engine: "browser", pyodide: PYODIDE_VERSION }),

  open: async ({ name, bytes }) =>
    JSON.parse(pyodide.globals.get("_open")(name, b64(bytes))),

  sample: async () => JSON.parse(pyodide.globals.get("_sample")()),

  preview: async ({ fileId, profile, sheet }) =>
    JSON.parse(
      pyodide.globals.get("_preview")(
        fileId,
        profile ? JSON.stringify(profile) : null,
        sheet ?? null,
      ),
    ),

  jsonSchema: async ({ fileId, profile, sheet }) =>
    JSON.parse(
      pyodide.globals.get("_json_schema")(
        fileId,
        profile ? JSON.stringify(profile) : null,
        sheet ?? null,
      ),
    ),

  convert: async ({ fileId, profile, sheet, format }) => {
    const out = JSON.parse(
      pyodide.globals.get("_convert")(
        fileId,
        profile ? JSON.stringify(profile) : null,
        sheet ?? null,
        format || "json",
      ),
    );
    return { bytes: fromB64(out.b64), filename: out.filename, summary: out.summary };
  },
};

self.onmessage = async ({ data: { id, op, args } }) => {
  try {
    ready = ready || boot();
    await ready;

    const handler = OPS[op];
    if (!handler) throw new Error(`알 수 없는 작업: ${op}`);

    const result = await handler(args || {});
    const transfer = result && result.bytes ? [result.bytes.buffer] : [];
    self.postMessage({ id, ok: true, result }, transfer);
  } catch (err) {
    if (op === "init") ready = null; // 기동 실패는 다시 시도할 수 있어야 한다
    const { message, code } = toError(err);
    self.postMessage({ id, ok: false, error: message, code });
  }
};
