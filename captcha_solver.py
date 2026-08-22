"""Helpers for solving Arkose FunCaptcha via official 2Captcha Python SDK."""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import re
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple
from urllib.parse import parse_qsl, unquote, urlparse

try:
    from twocaptcha import TwoCaptcha
except Exception:  # pragma: no cover - runtime dependency can be absent in tests
    TwoCaptcha = None  # type: ignore[assignment]


SITEKEY_FALLBACK_RE = re.compile(
    r"""(?:data-pkey|public_key|publicKey|sitekey)\s*[:=]\s*["']?([A-Za-z0-9_-]{8,})["']?""",
    flags=re.I,
)
SURL_FALLBACK_RE = re.compile(r"""https://[^\s"'<>]*arkoselabs[^\s"'<>]*""", flags=re.I)
BLOB_FALLBACK_RE = re.compile(r"""["']blob["']\s*:\s*["']([^"']+)["']""", flags=re.I)
UUID_SITEKEY_RE = re.compile(
    r"""\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b""",
    flags=re.I,
)
SITEKEY_URL_PATH_RE = re.compile(
    r"""(?:/v2/|/fc/|/enforcement\.|public_key=|pk=|render=)([0-9a-z_-]{8,}|[0-9a-f-]{36})""",
    flags=re.I,
)
COORDINATE_PAIR_RE = re.compile(r"""x\s*=\s*(-?\d+)\s*,\s*y\s*=\s*(-?\d+)""", flags=re.I)
COORDINATE_FALLBACK_RE = re.compile(r"""(-?\d+)\s*,\s*(-?\d+)""", flags=re.I)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "y", "on"}:
        return True
    if value in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _env_int(name: str, default: int, min_value: int = 0) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw.strip())
    except Exception:
        return default
    return max(min_value, value)


def _normalize_string(value: Any) -> str:
    return str(value or "").strip()


def _parse_fc_token_map(raw_token: str) -> Dict[str, str]:
    """
    Parse Arkose fc-token payload format:
    key=value|key2=value2|...
    """
    out: Dict[str, str] = {}
    raw = _normalize_string(raw_token)
    if not raw:
        return out

    for part in raw.split("|"):
        chunk = part.strip()
        if not chunk or "=" not in chunk:
            continue
        key, val = chunk.split("=", 1)
        key = key.strip().lower()
        val = unquote(val.strip())
        if key:
            out[key] = val
    return out


def _parse_query_values(raw_url: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    if not raw_url:
        return out
    try:
        parsed = urlparse(raw_url)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True):
            k = key.strip().lower()
            if k:
                out[k] = value.strip()
    except Exception:
        return out
    return out


def _extract_sitekey_from_text(raw_text: str) -> str:
    text = _normalize_string(raw_text)
    if not text:
        return ""

    match = SITEKEY_FALLBACK_RE.search(text)
    if match:
        return _normalize_string(match.group(1))

    match = UUID_SITEKEY_RE.search(text)
    if match:
        return _normalize_string(match.group(0))

    match = SITEKEY_URL_PATH_RE.search(text)
    if match:
        return _normalize_string(match.group(1))

    return ""


def _extract_surl_from_text(raw_text: str) -> str:
    text = _normalize_string(raw_text)
    if not text:
        return ""
    match = SURL_FALLBACK_RE.search(text)
    if not match:
        return ""
    url = _normalize_string(match.group(0))
    try:
        parsed = urlparse(url)
        if parsed.scheme and parsed.netloc:
            return f"{parsed.scheme}://{parsed.netloc}"
    except Exception:
        return url
    return url


def _parse_coordinates_from_code(raw_code: str) -> list[Tuple[int, int]]:
    code = _normalize_string(raw_code)
    if not code:
        return []

    points: list[Tuple[int, int]] = []
    for x_raw, y_raw in COORDINATE_PAIR_RE.findall(code):
        try:
            points.append((int(x_raw), int(y_raw)))
        except Exception:
            continue
    if points:
        return points

    # Fallback for variants like "39,59|252,72".
    for x_raw, y_raw in COORDINATE_FALLBACK_RE.findall(code):
        try:
            points.append((int(x_raw), int(y_raw)))
        except Exception:
            continue
    return points


def proxy_to_2captcha(playwright_proxy: Optional[Dict[str, Any]]) -> Optional[Dict[str, str]]:
    """
    Convert Playwright proxy dict to 2Captcha proxy format:
    {"type": "HTTP|HTTPS|SOCKS4|SOCKS5", "uri": "user:pass@host:port"}.
    """
    if not playwright_proxy:
        return None

    server_raw = _normalize_string(playwright_proxy.get("server"))
    if not server_raw:
        return None

    server = server_raw if "://" in server_raw else f"http://{server_raw}"
    parsed = urlparse(server)
    host = parsed.hostname
    port = parsed.port
    if not host or not port:
        return None

    proxy_type = (parsed.scheme or "http").upper()
    if proxy_type not in {"HTTP", "HTTPS", "SOCKS4", "SOCKS5"}:
        proxy_type = "HTTP"

    username = _normalize_string(playwright_proxy.get("username"))
    password = _normalize_string(playwright_proxy.get("password"))
    endpoint = f"{host}:{port}"
    if username:
        endpoint = f"{username}:{password}@{endpoint}"

    return {"type": proxy_type, "uri": endpoint}


@dataclass(frozen=True)
class CaptchaSolverConfig:
    api_key_env: str = "TWOCAPTCHA_API_KEY"
    enabled: bool = True
    max_attempts: int = 3
    timeout_seconds: int = 120
    polling_interval_seconds: int = 5
    server: str = "2captcha.com"
    mock_mode: bool = False
    mock_token: str = "MOCK_FUN_CAPTCHA_TOKEN"

    @classmethod
    def from_env(cls) -> "CaptchaSolverConfig":
        return cls(
            api_key_env=os.getenv("ETIX_2CAPTCHA_API_KEY_ENV", "TWOCAPTCHA_API_KEY").strip(),
            enabled=_env_bool("ETIX_2CAPTCHA_ENABLED", True),
            max_attempts=_env_int("ETIX_2CAPTCHA_MAX_ATTEMPTS", 3, min_value=1),
            timeout_seconds=_env_int("ETIX_2CAPTCHA_TIMEOUT_S", 120, min_value=15),
            polling_interval_seconds=_env_int("ETIX_2CAPTCHA_POLLING_S", 5, min_value=1),
            server=_normalize_string(os.getenv("ETIX_2CAPTCHA_SERVER", "2captcha.com")),
            mock_mode=_env_bool("ETIX_2CAPTCHA_MOCK_MODE", False),
            mock_token=_normalize_string(
                os.getenv("ETIX_2CAPTCHA_MOCK_TOKEN", "MOCK_FUN_CAPTCHA_TOKEN")
            ),
        )


@dataclass(frozen=True)
class FunCaptchaTask:
    sitekey: str
    page_url: str
    surl: Optional[str] = None
    blob: Optional[str] = None
    user_agent: Optional[str] = None
    proxy: Optional[Dict[str, str]] = None


@dataclass(frozen=True)
class SolveResult:
    ok: bool
    token: str = ""
    error: str = ""
    captcha_id: str = ""
    attempts_used: int = 0
    duration_seconds: float = 0.0


class TwoCaptchaFunCaptchaSolver:
    """
    Production wrapper for 2Captcha FunCaptcha with retries and robust logging.
    """

    def __init__(
        self,
        config: Optional[CaptchaSolverConfig] = None,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self.config = config or CaptchaSolverConfig.from_env()
        self.logger = logger or logging.getLogger("etix_checker.captcha")
        self.api_key = _normalize_string(os.getenv(self.config.api_key_env))

    @property
    def is_active(self) -> bool:
        if not self.config.enabled:
            return False
        if self.config.mock_mode:
            return True
        return bool(self.api_key) and TwoCaptcha is not None

    async def solve_funcaptcha(self, task: FunCaptchaTask) -> SolveResult:
        started = time.monotonic()

        if not self.config.enabled:
            return SolveResult(ok=False, error="solver disabled by ETIX_2CAPTCHA_ENABLED")
        if self.config.mock_mode:
            token = f"{self.config.mock_token}|pk={task.sitekey}|ts={int(time.time())}"
            return SolveResult(
                ok=True,
                token=token,
                captcha_id="mock",
                attempts_used=1,
                duration_seconds=time.monotonic() - started,
            )
        if TwoCaptcha is None:
            return SolveResult(
                ok=False,
                error="twocaptcha package is not installed (pip install 2captcha-python)",
                duration_seconds=time.monotonic() - started,
            )
        if not self.api_key:
            return SolveResult(
                ok=False,
                error=f"missing API key in env {self.config.api_key_env}",
                duration_seconds=time.monotonic() - started,
            )

        last_error = "unknown error"
        for attempt in range(1, self.config.max_attempts + 1):
            try:
                payload = await asyncio.to_thread(self._solve_once_sync, task)
                token = _normalize_string(payload.get("code"))
                captcha_id = _normalize_string(payload.get("captchaId"))
                if token:
                    return SolveResult(
                        ok=True,
                        token=token,
                        captcha_id=captcha_id,
                        attempts_used=attempt,
                        duration_seconds=time.monotonic() - started,
                    )
                last_error = "empty token in 2Captcha response"
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                retryable = self._is_retryable_error(last_error)
                self.logger.warning(
                    "2Captcha funcaptcha failed: attempt=%s/%s retryable=%s error=%s",
                    attempt,
                    self.config.max_attempts,
                    retryable,
                    last_error,
                )
                if (not retryable) or attempt >= self.config.max_attempts:
                    break
                await asyncio.sleep(min(4, attempt))

        return SolveResult(
            ok=False,
            error=last_error,
            attempts_used=self.config.max_attempts,
            duration_seconds=time.monotonic() - started,
        )

    async def solve_coordinates(
        self,
        image_base64: str,
        *,
        hint_text: Optional[str] = None,
        min_clicks: Optional[int] = None,
        max_clicks: Optional[int] = None,
    ) -> SolveResult:
        """
        Solve click/coordinates captcha from base64 image.
        Returns SolveResult.token with raw coordinates answer.
        """
        started = time.monotonic()

        if not self.config.enabled:
            return SolveResult(ok=False, error="solver disabled by ETIX_2CAPTCHA_ENABLED")
        if self.config.mock_mode:
            return SolveResult(
                ok=True,
                token="coordinate:x=60,y=60;x=140,y=140",
                captcha_id="mock",
                attempts_used=1,
                duration_seconds=time.monotonic() - started,
            )
        if TwoCaptcha is None:
            return SolveResult(
                ok=False,
                error="twocaptcha package is not installed (pip install 2captcha-python)",
                duration_seconds=time.monotonic() - started,
            )
        if not self.api_key:
            return SolveResult(
                ok=False,
                error=f"missing API key in env {self.config.api_key_env}",
                duration_seconds=time.monotonic() - started,
            )

        body = _normalize_string(image_base64)
        if not body:
            return SolveResult(
                ok=False,
                error="empty captcha image body",
                duration_seconds=time.monotonic() - started,
            )

        last_error = "unknown error"
        for attempt in range(1, self.config.max_attempts + 1):
            try:
                payload = await asyncio.to_thread(
                    self._solve_coordinates_once_sync,
                    body,
                    hint_text,
                    min_clicks,
                    max_clicks,
                )
                code = _normalize_string(payload.get("code"))
                captcha_id = _normalize_string(payload.get("captchaId"))
                if code:
                    return SolveResult(
                        ok=True,
                        token=code,
                        captcha_id=captcha_id,
                        attempts_used=attempt,
                        duration_seconds=time.monotonic() - started,
                    )
                last_error = "empty coordinates code in 2Captcha response"
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                retryable = self._is_retryable_error(last_error)
                self.logger.warning(
                    "2Captcha coordinates failed: attempt=%s/%s retryable=%s error=%s",
                    attempt,
                    self.config.max_attempts,
                    retryable,
                    last_error,
                )
                if (not retryable) or attempt >= self.config.max_attempts:
                    break
                await asyncio.sleep(min(4, attempt))

        return SolveResult(
            ok=False,
            error=last_error,
            attempts_used=self.config.max_attempts,
            duration_seconds=time.monotonic() - started,
        )

    def _solve_once_sync(self, task: FunCaptchaTask) -> Dict[str, Any]:
        assert TwoCaptcha is not None  # guarded by caller

        solver = TwoCaptcha(
            self.api_key,
            defaultTimeout=self.config.timeout_seconds,
            recaptchaTimeout=self.config.timeout_seconds,
            pollingInterval=self.config.polling_interval_seconds,
            server=self.config.server,
        )
        kwargs: Dict[str, Any] = {}
        if task.surl:
            kwargs["surl"] = task.surl
        if task.user_agent:
            kwargs["userAgent"] = task.user_agent
        if task.blob:
            kwargs["data"] = {"blob": task.blob}
        if task.proxy:
            kwargs["proxy"] = task.proxy

        try:
            result = solver.funcaptcha(sitekey=task.sitekey, url=task.page_url, **kwargs)
        except Exception as exc:
            # Некоторые Arkose-конфигурации ожидают именно data[blob] вместо data={blob:...}.
            if task.blob and "ERROR_BAD_PARAMETERS" in str(exc).upper():
                kwargs_alt = dict(kwargs)
                kwargs_alt.pop("data", None)
                kwargs_alt["data[blob]"] = task.blob
                result = solver.funcaptcha(sitekey=task.sitekey, url=task.page_url, **kwargs_alt)
            else:
                raise
        if isinstance(result, dict):
            return result
        return {"code": str(result or "")}

    def _solve_coordinates_once_sync(
        self,
        image_base64: str,
        hint_text: Optional[str],
        min_clicks: Optional[int],
        max_clicks: Optional[int],
    ) -> Dict[str, Any]:
        assert TwoCaptcha is not None  # guarded by caller

        solver = TwoCaptcha(
            self.api_key,
            defaultTimeout=self.config.timeout_seconds,
            recaptchaTimeout=self.config.timeout_seconds,
            pollingInterval=self.config.polling_interval_seconds,
            server=self.config.server,
        )
        kwargs: Dict[str, Any] = {"lang": "en"}
        hint = _normalize_string(hint_text)
        if hint:
            kwargs["hintText"] = hint[:140]
        if min_clicks is not None:
            kwargs["min_clicks"] = int(min_clicks)
        if max_clicks is not None:
            kwargs["max_clicks"] = int(max_clicks)

        result = solver.coordinates(image_base64, **kwargs)
        if isinstance(result, dict):
            return result
        return {"code": str(result or "")}

    def _is_retryable_error(self, error_text: str) -> bool:
        text = error_text.upper()
        non_retryable = (
            "ERROR_ZERO_BALANCE",
            "ERROR_WRONG_USER_KEY",
            "ERROR_KEY_DOES_NOT_EXIST",
            "ERROR_IP_NOT_ALLOWED",
            "ERROR_PAGEURL",
            "ERROR_BAD_PARAMETERS",
            "ERROR_WRONG_CAPTCHA_ID",
            "MISSING API KEY",
        )
        if any(mark in text for mark in non_retryable):
            return False
        return True


async def extract_funcaptcha_task(
    page: Any,
    playwright_proxy: Optional[Dict[str, Any]] = None,
) -> Optional[FunCaptchaTask]:
    """
    Extract required Arkose parameters from page DOM.
    Returns None when sitekey cannot be found yet.
    """
    try:
        snapshot = await page.evaluate(
            """
            () => {
                const out = {
                    page_url: location.href || "",
                    user_agent: navigator.userAgent || "",
                    fc_token: "",
                    sitekey: "",
                    surl: "",
                    blob: "",
                    iframe_src: "",
                    script_srcs: [],
                    inline_scripts: [],
                };

                const first = (selectors) => {
                    for (const sel of selectors) {
                        const el = document.querySelector(sel);
                        if (el) return el;
                    }
                    return null;
                };
                const getAttr = (el, attr) => {
                    if (!el) return "";
                    const value = el.getAttribute(attr);
                    return (value || "").trim();
                };
                const collectDeep = (selector) => {
                    const outEls = [];
                    const visited = new Set();
                    const queue = [document];
                    while (queue.length) {
                        const root = queue.shift();
                        if (!root || visited.has(root)) continue;
                        visited.add(root);
                        try {
                            root.querySelectorAll(selector).forEach((el) => outEls.push(el));
                        } catch (e) {
                            // ignore selector errors
                        }
                        let all = [];
                        try {
                            all = root.querySelectorAll('*');
                        } catch (e) {
                            all = [];
                        }
                        for (const node of all) {
                            if (node && node.shadowRoot) {
                                queue.push(node.shadowRoot);
                            }
                        }
                    }
                    return outEls;
                };

                const tokenEl = first([
                    "input[name='fc-token']",
                    "input[id='fc-token']",
                    "input[name*='fc-token']",
                    "input[id*='fc-token']"
                ]);
                if (tokenEl) {
                    out.fc_token = (tokenEl.value || tokenEl.getAttribute("value") || "").trim();
                }

                const pkeyEl = first(["[data-pkey]", "[data-public-key]", "[data-sitekey]"]);
                if (pkeyEl) {
                    out.sitekey =
                        getAttr(pkeyEl, "data-pkey")
                        || getAttr(pkeyEl, "data-public-key")
                        || getAttr(pkeyEl, "data-sitekey");
                    out.surl = getAttr(pkeyEl, "data-surl");
                }

                const blobEl = first(["input[name='blob']", "input[name*='blob']", "[data-blob]"]);
                if (blobEl) {
                    out.blob = (blobEl.value || getAttr(blobEl, "data-blob") || "").trim();
                }

                try {
                    const w = window;
                    const keyCandidates = [
                        w.publicKey,
                        w.public_key,
                        w.arkosePublicKey,
                        w.ARKOSE_PUBLIC_KEY,
                        w?.arkoseEnforcement?.publicKey,
                        w?.arkoseEnforcement?.public_key,
                        w?.arkoseEnforcement?.config?.publicKey,
                        w?.arkoseEnforcement?.config?.public_key,
                        w?.ArkoseEnforcement?.publicKey,
                        w?.ArkoseEnforcement?.config?.publicKey
                    ];
                    for (const item of keyCandidates) {
                        if (typeof item === "string" && item.trim()) {
                            out.sitekey = out.sitekey || item.trim();
                            break;
                        }
                    }
                    const surlCandidates = [
                        w?.arkoseEnforcement?.surl,
                        w?.arkoseEnforcement?.config?.surl,
                        w?.ArkoseEnforcement?.surl,
                        w?.ArkoseEnforcement?.config?.surl
                    ];
                    for (const item of surlCandidates) {
                        if (typeof item === "string" && item.trim()) {
                            out.surl = out.surl || item.trim();
                            break;
                        }
                    }
                } catch (e) {
                    // ignore
                }

                const iframe = first([
                    "iframe[src*='arkoselabs']",
                    "iframe[src*='funcaptcha']",
                    "iframe[src*='arkose']",
                ]);
                if (iframe) {
                    out.iframe_src = getAttr(iframe, "src");
                }

                try {
                    const deepPkeyEls = collectDeep("[data-pkey], [data-public-key], [data-sitekey]");
                    for (const el of deepPkeyEls) {
                        const key =
                            getAttr(el, "data-pkey")
                            || getAttr(el, "data-public-key")
                            || getAttr(el, "data-sitekey");
                        if (key) {
                            out.sitekey = out.sitekey || key;
                            out.surl = out.surl || getAttr(el, "data-surl");
                            break;
                        }
                    }
                } catch (e) {
                    // ignore
                }

                try {
                    const scripts = Array.from(document.scripts || []);
                    out.script_srcs = scripts
                        .map((s) => (s && s.src) ? String(s.src) : "")
                        .filter(Boolean)
                        .slice(-200);
                    out.inline_scripts = scripts
                        .map((s) => (s && !s.src) ? String(s.textContent || "") : "")
                        .filter(Boolean)
                        .slice(-30);
                } catch (e) {
                    // ignore
                }

                return out;
            }
            """
        )
    except Exception:
        snapshot = {}

    page_url = _normalize_string(snapshot.get("page_url")) or _normalize_string(getattr(page, "url", ""))
    user_agent = _normalize_string(snapshot.get("user_agent"))
    fc_token = _normalize_string(snapshot.get("fc_token"))
    sitekey = _normalize_string(snapshot.get("sitekey"))
    surl = _normalize_string(snapshot.get("surl"))
    blob = _normalize_string(snapshot.get("blob"))
    iframe_src = _normalize_string(snapshot.get("iframe_src"))
    script_srcs = snapshot.get("script_srcs")
    inline_scripts = snapshot.get("inline_scripts")

    token_map = _parse_fc_token_map(fc_token)
    if not sitekey:
        sitekey = (
            token_map.get("pk")
            or token_map.get("public_key")
            or token_map.get("publickey")
            or token_map.get("sitekey")
            or ""
        )
    if not surl:
        surl = token_map.get("surl", "")
    if not blob:
        blob = token_map.get("blob", "")
    if not sitekey:
        sitekey = _extract_sitekey_from_text(page_url) or _extract_sitekey_from_text(iframe_src)
    if not surl:
        surl = _extract_surl_from_text(page_url) or _extract_surl_from_text(iframe_src)

    def _hydrate_from_query_map(query_map: Dict[str, str]) -> None:
        nonlocal sitekey, surl, blob
        if not query_map:
            return
        if not sitekey:
            sitekey = (
                _normalize_string(query_map.get("pk"))
                or _normalize_string(query_map.get("pkey"))
                or _normalize_string(query_map.get("public_key"))
                or _normalize_string(query_map.get("publickey"))
                or _normalize_string(query_map.get("sitekey"))
                or _normalize_string(query_map.get("render"))
            )
        if not surl:
            surl = _normalize_string(query_map.get("surl"))
        if not blob:
            blob = _normalize_string(query_map.get("blob"))

        token_raw = _normalize_string(query_map.get("token"))
        if token_raw:
            token_data = _parse_fc_token_map(token_raw)
            if not sitekey:
                sitekey = (
                    _normalize_string(token_data.get("pk"))
                    or _normalize_string(token_data.get("public_key"))
                    or _normalize_string(token_data.get("publickey"))
                    or _normalize_string(token_data.get("sitekey"))
                )
            if not surl:
                surl = _normalize_string(token_data.get("surl"))
            if not blob:
                blob = _normalize_string(token_data.get("blob"))

    # For full-page Arkose flows, params are often in the current URL query.
    page_params = _parse_query_values(page_url)
    _hydrate_from_query_map(page_params)

    iframe_params = _parse_query_values(iframe_src)
    _hydrate_from_query_map(iframe_params)

    # Cross-origin Arkose frames usually expose pk/surl in frame URL.
    candidate_urls: list[str] = []
    seen_urls: set[str] = set()

    def _push_url(raw: str) -> None:
        value = _normalize_string(raw)
        if not value or value in seen_urls:
            return
        seen_urls.add(value)
        candidate_urls.append(value)

    _push_url(page_url)
    _push_url(iframe_src)
    if isinstance(script_srcs, list):
        for raw_src in script_srcs:
            _push_url(str(raw_src))

    try:
        for frame in getattr(page, "frames", []):
            _push_url(_normalize_string(getattr(frame, "url", "")))
    except Exception:
        pass

    try:
        perf_urls = await page.evaluate(
            """
            () => {
                try {
                    return performance
                        .getEntriesByType('resource')
                        .map((e) => (e && e.name) ? String(e.name) : '')
                        .filter(Boolean)
                        .slice(-500);
                } catch (e) {
                    return [];
                }
            }
            """
        )
    except Exception:
        perf_urls = []

    if isinstance(perf_urls, list):
        for item in perf_urls:
            _push_url(str(item))

    for raw_url in candidate_urls:
        low = raw_url.lower()
        looks_related = (
            "arkoselabs" in low
            or "funcaptcha" in low
            or "fc-token" in low
            or "public_key=" in low
            or "pk=" in low
            or "sitekey=" in low
            or "surl=" in low
            or "blob=" in low
            or "token=" in low
        )
        if not looks_related:
            continue
        query_map = _parse_query_values(raw_url)
        _hydrate_from_query_map(query_map)
        if not sitekey:
            sitekey = _extract_sitekey_from_text(raw_url)
        if not surl:
            surl = _extract_surl_from_text(raw_url)
        if not surl and "arkoselabs" in low:
            try:
                parsed = urlparse(raw_url)
                if parsed.scheme and parsed.netloc:
                    surl = f"{parsed.scheme}://{parsed.netloc}"
            except Exception:
                pass
        if sitekey and (surl or "arkoselabs" not in low):
            break

    # Фолбэк по HTML нужен для страниц, где параметры кладутся в inline-скрипты.
    if not sitekey or not surl or not blob:
        try:
            html = await page.content()
        except Exception:
            html = ""
        if html:
            if not sitekey:
                match = SITEKEY_FALLBACK_RE.search(html)
                if match:
                    sitekey = _normalize_string(match.group(1))
            if not surl:
                match = SURL_FALLBACK_RE.search(html)
                if match:
                    surl = _normalize_string(match.group(0))
            if not blob:
                match = BLOB_FALLBACK_RE.search(html)
                if match:
                    blob = _normalize_string(match.group(1))

    # Last-resort scan in inline scripts collected from snapshot.
    if (not sitekey or not surl or not blob) and isinstance(inline_scripts, list):
        inline_blob = "\n".join(str(s) for s in inline_scripts if s)
        if inline_blob:
            if not sitekey:
                sitekey = _extract_sitekey_from_text(inline_blob)
            if not surl:
                surl = _extract_surl_from_text(inline_blob)
            if not blob:
                match = BLOB_FALLBACK_RE.search(inline_blob)
                if match:
                    blob = _normalize_string(match.group(1))

    if not sitekey:
        return None

    task = FunCaptchaTask(
        sitekey=sitekey,
        page_url=page_url,
        surl=(surl or None),
        blob=(blob or None),
        user_agent=(user_agent or None),
        proxy=proxy_to_2captcha(playwright_proxy),
    )
    return task


async def apply_funcaptcha_token(page: Any, token: str) -> bool:
    """
    Write solved token into fc-token field and dispatch change/input events.
    """
    normalized = _normalize_string(token)
    if not normalized:
        return False

    try:
        updated_count = await page.evaluate(
            """
            (token) => {
                const selectors = [
                    "input[name='fc-token']",
                    "input[id='fc-token']",
                    "input[name*='fc-token']",
                    "input[id*='fc-token']",
                ];
                const items = [];
                for (const sel of selectors) {
                    document.querySelectorAll(sel).forEach((el) => items.push(el));
                }
                if (items.length === 0) {
                    const host = document.querySelector("form") || document.body;
                    if (host) {
                        const hidden = document.createElement("input");
                        hidden.type = "hidden";
                        hidden.name = "fc-token";
                        host.appendChild(hidden);
                        items.push(hidden);
                    }
                }
                for (const el of items) {
                    el.value = token;
                    el.setAttribute("value", token);
                    el.dispatchEvent(new Event("input", { bubbles: true }));
                    el.dispatchEvent(new Event("change", { bubbles: true }));
                }
                window.dispatchEvent(new CustomEvent("fc-token-updated", {
                    detail: { token: token }
                }));
                return items.length;
            }
            """,
            normalized,
        )
    except Exception:
        return False

    return bool(updated_count and int(updated_count) > 0)


async def try_reload_recaptcha(page: Any) -> bool:
    """
    Best-effort click on reCAPTCHA reload button.
    Useful when a transient image challenge appears after Arkose.
    """
    selectors = (
        "#recaptcha-reload-button",
        "button[title*='new challenge' i]",
        "button[aria-label*='new challenge' i]",
        "button[aria-label*='reload' i]",
    )
    for frame in page.frames:
        for sel in selectors:
            try:
                btn = frame.locator(sel).first
                if await btn.count() > 0 and await btn.is_visible():
                    await btn.click(timeout=3000)
                    return True
            except Exception:
                continue
    return False


async def solve_visual_funcaptcha_via_coordinates(
    page: Any,
    solver: TwoCaptchaFunCaptchaSolver,
    logger: Optional[logging.Logger] = None,
) -> Tuple[bool, str]:
    """
    Fallback for visual challenge pages when Arkose token params are inaccessible.
    Sends screenshot to 2Captcha coordinates method, clicks returned points,
    then presses Confirm/Verify.
    """
    log = logger or getattr(solver, "logger", logging.getLogger("etix_checker.captcha"))

    try:
        if page.is_closed():
            return False, "page closed"
    except Exception:
        pass

    try:
        meta = await page.evaluate(
            """
            () => {
                const txt = (el) => (el && (el.innerText || el.textContent) || '').trim();
                const pickInstruction = () => {
                    const nodes = Array.from(document.querySelectorAll('*'));
                    for (const el of nodes) {
                        const t = txt(el);
                        if (!t || t.length > 180) continue;
                        if (/^(choose|select|click)\\b/i.test(t) && /\\b(all|image|images|squares|tiles|the)\\b/i.test(t)) {
                            return t.replace(/\\s+/g, ' ');
                        }
                    }
                    return '';
                };
                const hasConfirmLikeControl = () => {
                    const controls = Array.from(
                        document.querySelectorAll("button, input[type='submit'], input[type='button']")
                    );
                    for (const el of controls) {
                        const t = txt(el).toLowerCase();
                        const v = String(el.getAttribute('value') || '').toLowerCase();
                        if (t.includes('confirm') || t.includes('verify') || v.includes('confirm') || v.includes('verify')) {
                            return true;
                        }
                    }
                    return false;
                };

                const heading = Array.from(document.querySelectorAll('h1,h2,h3,div,span,p'))
                    .map((el) => txt(el))
                    .find((t) => /confirm you are human/i.test(t)) || '';

                const imageCandidates = document.querySelectorAll(
                    "img, canvas, [style*='background-image'], [class*='image'], [class*='tile']"
                ).length;

                let region = null;
                const btn = document.querySelector("button, input[type='submit'], input[type='button']");
                if (btn) {
                    let cur = btn;
                    for (let i = 0; i < 6 && cur; i += 1) {
                        const rect = cur.getBoundingClientRect();
                        const imgs = cur.querySelectorAll("img,canvas,[style*='background-image']").length;
                        if (rect.width > 180 && rect.height > 180 && imgs >= 6) {
                            region = { x: rect.x, y: rect.y, width: rect.width, height: rect.height };
                            break;
                        }
                        cur = cur.parentElement;
                    }
                }

                return {
                    heading,
                    instruction: pickInstruction(),
                    confirmPresent: hasConfirmLikeControl(),
                    imageCandidates,
                    innerWidth: window.innerWidth || 0,
                    innerHeight: window.innerHeight || 0,
                    dpr: window.devicePixelRatio || 1,
                    region
                };
            }
            """
        )
    except Exception as exc:
        return False, f"visual inspect failed: {type(exc).__name__}: {exc}"

    heading = _normalize_string((meta or {}).get("heading"))
    instruction = _normalize_string((meta or {}).get("instruction"))
    confirm_present = bool((meta or {}).get("confirmPresent"))
    image_candidates = int((meta or {}).get("imageCandidates") or 0)
    inner_w = float((meta or {}).get("innerWidth") or 0)
    inner_h = float((meta or {}).get("innerHeight") or 0)
    dpr = float((meta or {}).get("dpr") or 1.0)
    region = (meta or {}).get("region")

    text_hint = f"{heading} {instruction}".strip().lower()
    looks_visual = (
        image_candidates >= 6
        and (
            "confirm you are human" in text_hint
            or "choose all" in text_hint
            or "select all" in text_hint
            or confirm_present
        )
    )
    if not looks_visual:
        return False, "visual challenge not detected"

    clip = None
    offset_x = 0.0
    offset_y = 0.0
    if isinstance(region, dict):
        try:
            x = float(region.get("x") or 0.0)
            y = float(region.get("y") or 0.0)
            w = float(region.get("width") or 0.0)
            h = float(region.get("height") or 0.0)
            if w >= 220 and h >= 220 and inner_w > 0 and inner_h > 0:
                x = max(0.0, min(x, inner_w - 1))
                y = max(0.0, min(y, inner_h - 1))
                w = max(1.0, min(w, inner_w - x))
                h = max(1.0, min(h, inner_h - y))
                clip = {"x": x, "y": y, "width": w, "height": h}
                offset_x = x
                offset_y = y
        except Exception:
            clip = None
            offset_x = 0.0
            offset_y = 0.0

    coord_scale = 1.0
    try:
        ss_kwargs: Dict[str, Any] = {"type": "png"}
        if clip:
            ss_kwargs["clip"] = clip
        ss_kwargs["scale"] = "css"
        image_bytes = await page.screenshot(**ss_kwargs)
    except Exception:
        try:
            ss_kwargs = {"type": "png"}
            if clip:
                ss_kwargs["clip"] = clip
            image_bytes = await page.screenshot(**ss_kwargs)
            if dpr > 0:
                coord_scale = 1.0 / dpr
        except Exception as exc:
            return False, f"screenshot failed: {type(exc).__name__}: {exc}"

    image_b64 = base64.b64encode(image_bytes).decode("ascii")
    solve = await solver.solve_coordinates(
        image_b64,
        hint_text=instruction or None,
        min_clicks=1,
        max_clicks=9,
    )
    if not solve.ok or not solve.token:
        return False, f"visual solve failed: {solve.error or 'empty response'}"

    points = _parse_coordinates_from_code(solve.token)
    if not points:
        return False, "visual solve returned no coordinates"

    clicked = 0
    for x_raw, y_raw in points:
        x = offset_x + (x_raw * coord_scale)
        y = offset_y + (y_raw * coord_scale)
        try:
            await page.mouse.click(x, y, delay=70)
            clicked += 1
            await page.wait_for_timeout(120)
        except Exception:
            continue

    if clicked == 0:
        return False, "no clicks applied"

    clicked_confirm = False
    for sel in (
        "button:has-text('Confirm')",
        "button:has-text('VERIFY')",
        "button:has-text('Verify')",
        "input[type='submit'][value*='Confirm']",
        "input[type='submit'][value*='VERIFY']",
    ):
        if not _normalize_string(sel):
            continue
        try:
            btn = page.locator(sel).first
            if await btn.count() > 0 and await btn.is_visible():
                await btn.click(timeout=5000)
                clicked_confirm = True
                break
        except Exception:
            continue

    await page.wait_for_timeout(900)
    log.info(
        "captcha.visual solved clicks=%s confirm=%s instruction=%s id=%s",
        clicked,
        clicked_confirm,
        (instruction[:80] if instruction else ""),
        solve.captcha_id or "n/a",
    )
    return True, (
        f"visual coordinates applied (clicks={clicked}, confirm={clicked_confirm}, "
        f"id={solve.captcha_id or 'n/a'})"
    )


EXAMPLE_USAGE = """
# pip install 2captcha-python
# Пример: извлечь параметры Arkose -> решить -> вставить токен.
#
# solver = TwoCaptchaFunCaptchaSolver(CaptchaSolverConfig.from_env(), logger)
# task = await extract_funcaptcha_task(page, playwright_proxy=proxy_for_tab)
# if task:
#     result = await solver.solve_funcaptcha(task)
#     if result.ok:
#         await apply_funcaptcha_token(page, result.token)
#         # Дальше продолжаем обычный сценарий страницы.
"""
