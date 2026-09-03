/**
 * 실행 백엔드 — 화면과 엔진 사이의 콘센트.
 *
 * 화면은 open / preview / convert / jsonSchema 네 가지만 부른다. 그것이 서버로
 * 가는지 브라우저 안에서 도는지는 화면이 알 필요가 없다.
 *
 * 이 층이 없으면 나중에 실행 방식을 바꿀 때 화면을 통째로 다시 손대야 한다.
 * 지금은 두 구현이 꽂혀 있다:
 *
 *   PyodideBackend — 브라우저 안(웹 워커)에서 같은 파이썬을 돌린다. 파일이 밖으로 안 나간다.
 *   HttpBackend    — 사내 서버의 FastAPI 를 부른다. 감사 로그와 프로파일 공유가 가능하다.
 */

/** 공통 오류 — 화면은 message 만 보면 된다. */
export class BackendError extends Error {
  constructor(message, code = "error") {
    super(message);
    this.code = code;
  }
}

/* -------------------------------------------------------------------------- */
/* 서버 실행                                                                   */
/* -------------------------------------------------------------------------- */

export class HttpBackend {
  constructor(base = "") {
    this.base = base;
    this.kind = "server";
    this.label = "사내 서버";
  }

  async init() {}

  async #post(path, body, asBlob = false) {
    const res = await fetch(this.base + path, {
      method: "POST",
      ...(body instanceof FormData
        ? { body }
        : { headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }),
    });
    if (!res.ok) {
      let detail = res.statusText;
      try {
        detail = (await res.json()).detail || detail;
      } catch {
        /* 본문이 JSON 이 아닌 경우 */
      }
      throw new BackendError(detail, String(res.status));
    }
    return asBlob ? res : res.json();
  }

  open(file) {
    const form = new FormData();
    form.append("file", file);
    return this.#post("/api/files", form);
  }

  sample() {
    return this.#post("/api/sample", undefined);
  }

  preview({ fileId, profile, sheet }) {
    return this.#post("/api/preview", { fileId, profile, sheet });
  }

  async convert({ fileId, profile, sheet, format }) {
    const res = await this.#post("/api/convert", { fileId, profile, sheet, format }, true);
    return {
      bytes: new Uint8Array(await res.arrayBuffer()),
      filename: filenameFrom(res.headers.get("content-disposition")),
      summary: {
        rows: Number(res.headers.get("X-Everyfile-Rows") || 0),
        issues: Number(res.headers.get("X-Everyfile-Issues") || 0),
        errors: Number(res.headers.get("X-Everyfile-Errors") || 0),
      },
    };
  }

  jsonSchema({ fileId, profile, sheet }) {
    return this.#post("/api/json-schema", { fileId, profile, sheet });
  }
}

/**
 * Content-Disposition 에서 파일명을 꺼낸다.
 * 한글 파일명은 RFC 5987(filename*=utf-8''...) 로 오므로 그쪽을 먼저 본다 —
 * `filename="..."` 만 보면 한글 이름이 전부 기본값으로 떨어진다.
 */
export function filenameFrom(header) {
  if (!header) return null;
  const ext = header.match(/filename\*\s*=\s*utf-8''([^;]+)/i);
  if (ext) {
    try {
      return decodeURIComponent(ext[1].trim());
    } catch {
      /* 잘못된 인코딩이면 아래 형식으로 넘어간다 */
    }
  }
  const plain = header.match(/filename\s*=\s*"?([^";]+)"?/i);
  return plain ? plain[1].trim() : null;
}

/* -------------------------------------------------------------------------- */
/* 브라우저 실행                                                               */
/* -------------------------------------------------------------------------- */

export class PyodideBackend {
  constructor({ onProgress } = {}) {
    this.kind = "browser";
    this.label = "브라우저";
    this.onProgress = onProgress || (() => {});
    this.worker = null;
    this.seq = 0;
    this.pending = new Map();
  }

  async init() {
    if (this.worker) return;
    // 워커에서 돌리는 이유: 3만 행 변환이 1초 넘게 걸리는데, 메인 스레드에서
    // 돌리면 그동안 화면이 통째로 얼어붙는다.
    this.worker = new Worker(new URL("./worker.js", import.meta.url), { type: "module" });
    this.worker.onmessage = (e) => this.#receive(e.data);
    this.worker.onerror = (e) => {
      const err = new BackendError(e.message || "워커를 시작하지 못했습니다", "worker");
      for (const { reject } of this.pending.values()) reject(err);
      this.pending.clear();
    };
    await this.#call("init", {});
  }

  #receive(msg) {
    if (msg.type === "progress") {
      this.onProgress(msg.text);
      return;
    }
    const entry = this.pending.get(msg.id);
    if (!entry) return;
    this.pending.delete(msg.id);
    if (msg.ok) entry.resolve(msg.result);
    else entry.reject(new BackendError(msg.error, msg.code || "error"));
  }

  #call(op, args, transfer = []) {
    const id = ++this.seq;
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      this.worker.postMessage({ id, op, args }, transfer);
    });
  }

  async open(file) {
    await this.init();
    const bytes = new Uint8Array(await file.arrayBuffer());
    return this.#call("open", { name: file.name, bytes }, [bytes.buffer]);
  }

  async sample() {
    await this.init();
    return this.#call("sample", {});
  }

  async preview(args) {
    await this.init();
    return this.#call("preview", args);
  }

  async convert(args) {
    await this.init();
    return this.#call("convert", args);
  }

  async jsonSchema(args) {
    await this.init();
    return this.#call("jsonSchema", args);
  }
}

/* -------------------------------------------------------------------------- */
/* 선택                                                                        */
/* -------------------------------------------------------------------------- */

/**
 * 어느 구현을 쓸지 고른다.
 *
 * 규칙은 하나뿐이다: **서버가 응답하면 서버, 아니면 브라우저.** 같은 빌드가
 * GitHub Pages 에서도 사내 서버에서도 그대로 돌아야 하므로, 빌드 시점이 아니라
 * 실행 시점에 판단한다. `?engine=browser` 로 강제할 수 있다.
 */
export async function pickBackend({ onProgress } = {}) {
  const forced = new URLSearchParams(location.search).get("engine");
  if (forced === "browser") return new PyodideBackend({ onProgress });
  if (forced === "server") return new HttpBackend();

  try {
    const res = await fetch("/api/health", { signal: AbortSignal.timeout(2500) });
    if (res.ok && (await res.json()).engine === "server") return new HttpBackend();
  } catch {
    /* 서버가 없다 — 정상적인 경우다 (정적 호스팅) */
  }
  return new PyodideBackend({ onProgress });
}
