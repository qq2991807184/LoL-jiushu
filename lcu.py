import sys
import re
import ssl
import json
import time
import threading
import subprocess
import ctypes
import base64
import queue as _queue
import logging
from ctypes import wintypes as wt
from typing import Optional, Callable, Any, List, Dict

if sys.platform != "win32":
    raise RuntimeError("Unsupported platform")

class LCUConfig:
    def __init__(
        self,
        poll_interval_disconnected: float = 2.0,
        poll_interval_connected: float = 60.0,
        heartbeat_interval: float = 15.0,
        http_timeout: float = 17.5,
        http_retries: int = 2,
        max_reconnect: int = 10,
        ws_backoff_base: float = 1.0,
        enable_cim_fallback: bool = False,
        cmd_read_fail_limit: int = 5,
        log_level: str = "INFO"
    ):
        self.poll_interval_disconnected = poll_interval_disconnected
        self.poll_interval_connected = poll_interval_connected
        self.heartbeat_interval = heartbeat_interval
        self.http_timeout = http_timeout
        self.http_retries = http_retries
        self.max_reconnect = max_reconnect
        self.ws_backoff_base = ws_backoff_base
        self.enable_cim_fallback = enable_cim_fallback
        self.cmd_read_fail_limit = cmd_read_fail_limit
        self.log_level = log_level

ERR_NATIVE_FAILED = "native_failed"
ERR_CIM_FAILED = "cim_failed"
ERR_VERIFY_FAILED = "verify_failed"
ERR_WS_ERROR = "ws_error"
ERR_RECONNECT_EXHAUSTED = "reconnect_exhausted"
ERR_NO_CMD_LINE = "has_client_no_cmdline"

ntdll = ctypes.WinDLL("ntdll")
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PROCESS_CMDLINE = 60

class UNICODE_STRING(ctypes.Structure):
    _fields_ = [("Length", wt.USHORT), ("MaximumLength", wt.USHORT), ("Buffer", ctypes.c_wchar_p)]

def _read_ux_native() -> List[Dict[str, Any]]:
    snap = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
    if snap == -1:
        raise OSError("snapshot")
    class PE32(ctypes.Structure):
        _fields_ = [("dwSize", wt.DWORD), ("cntUsage", wt.DWORD), ("th32ProcessID", wt.DWORD),
                    ("th32DefaultHeapID", ctypes.c_void_p), ("th32ModuleID", wt.DWORD),
                    ("cntThreads", wt.DWORD), ("th32ParentProcessID", wt.DWORD),
                    ("pcPriClassBase", wt.LONG), ("dwFlags", wt.DWORD),
                    ("szExeFile", ctypes.c_char * 260)]
    pe = PE32()
    pe.dwSize = ctypes.sizeof(PE32)
    if not kernel32.Process32First(snap, ctypes.byref(pe)):
        kernel32.CloseHandle(snap)
        raise OSError("process32first")
    results = []
    try:
        while True:
            if b"LeagueClientUx.exe" in pe.szExeFile:
                h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pe.th32ProcessID)
                if h:
                    try:
                        buf = ctypes.create_string_buffer(8192)
                        retlen = wt.ULONG()
                        if ntdll.NtQueryInformationProcess(h, PROCESS_CMDLINE, buf, 8192, ctypes.byref(retlen)) == 0:
                            us = UNICODE_STRING.from_buffer_copy(buf)
                            if us.Buffer and us.Length > 0:
                                cmd = ctypes.wstring_at(us.Buffer, us.Length // 2)
                                results.append({"pid": pe.th32ProcessID, "cmd": cmd})
                    finally:
                        kernel32.CloseHandle(h)
            if not kernel32.Process32Next(snap, ctypes.byref(pe)):
                break
    finally:
        kernel32.CloseHandle(snap)
    return results

def _read_ux_cim() -> List[Dict[str, Any]]:
    ps = [
        "Get-CimInstance Win32_Process -Filter \"Name='LeagueClientUx.exe'\"",
        "| Select-Object ProcessId,CommandLine | ConvertTo-Json"
    ]
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", " ".join(ps)],
            capture_output=True, text=True, timeout=5
        )
        if r.stdout:
            data = json.loads(r.stdout)
            if isinstance(data, dict):
                data = [data]
            return [{"pid": d["ProcessId"], "cmd": d["CommandLine"]} for d in data if d.get("CommandLine")]
    except Exception:
        pass
    return []

def _parse_command_line(cmd: str) -> Dict[str, str]:
    m1 = re.search(r"--app-port=(\d+)", cmd)
    m2 = re.search(r"--remoting-auth-token=([^\s\"']+)", cmd)
    if not m1 or not m2:
        raise ValueError("parse")
    return {"port": m1.group(1), "token": m2.group(1)}

class HTTPAdapter:
    def __init__(self, base_url: str, headers: dict, config: LCUConfig):
        self._ctx = ssl._create_unverified_context()
        self._base = base_url
        self._headers = headers
        self._config = config

    def request(self, method: str, path: str, body: Any = None) -> Any:
        import urllib.request
        last_err = None
        for i in range(self._config.http_retries + 1):
            try:
                req = urllib.request.Request(self._base + path, headers=self._headers, method=method)
                if body is not None:
                    data = json.dumps(body).encode()
                    req.data = data
                    req.add_header("Content-Type", "application/json")
                with urllib.request.urlopen(req, context=self._ctx, timeout=self._config.http_timeout) as r:
                    return json.loads(r.read().decode())
            except Exception as e:
                last_err = e
                time.sleep(0.5 * (i + 1))
        raise last_err

class WSAdapter:
    def __init__(self, url: str, headers: list, on_message: Callable, on_error: Callable, config: LCUConfig):
        self._url = url
        self._headers = headers
        self._on_message = on_message
        self._on_error = on_error
        self._config = config
        self._ws = None
        self._running = False
        self._fail = 0

    def run_forever(self, running_flag: Callable[[], bool], on_close: Callable[[], None]):
        self._running = True
        try:
            import websocket
        except Exception as e:
            self._on_error(ERR_WS_ERROR, str(e))
            on_close()
            return
        while running_flag() and self._running:
            try:
                self._ws = websocket.WebSocketApp(
                    self._url,
                    header=self._headers,
                    on_open=lambda w: w.send(json.dumps([5, "OnJsonApiEvent"])),
                    on_message=lambda w, m: self._on_message(w, m),
                    on_error=lambda w, e: self._on_error(ERR_WS_ERROR, str(e)),
                    on_close=lambda w, c, m: on_close()
                )
                self._ws.run_forever(
                    sslopt={"cert_reqs": ssl.CERT_NONE},
                    ping_interval=20,
                    ping_timeout=10
                )
            except Exception as e:
                self._on_error(ERR_WS_ERROR, str(e))
            if not running_flag() or not self._running:
                break
            self._fail += 1
            backoff = min(self._config.ws_backoff_base * (2 ** self._fail), 30)
            for _ in range(int(backoff * 10)):
                if not running_flag() or not self._running:
                    return
                time.sleep(0.1)
        on_close()

    def close(self):
        self._running = False
        if self._ws:
            try:
                self._ws.close()
            except Exception:
                pass

class LCULogger:
    def __init__(self, name: str = "lcu", level: str = "INFO"):
        self._logger = logging.getLogger(name)
        if not self._logger.handlers:
            h = logging.StreamHandler()
            h.setFormatter(logging.Formatter("[%(asctime)s] [%(levelname)s] %(message)s"))
            self._logger.addHandler(h)
        self._logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    def debug(self, msg: str): self._logger.debug(msg)
    def info(self, msg: str): self._logger.info(msg)
    def warn(self, msg: str): self._logger.warning(msg)
    def error(self, msg: str): self._logger.error(msg)
    def fatal(self, msg: str): self._logger.critical(msg)

class LCUClient:
    def __init__(
        self,
        on_state: Callable[[str], None],
        on_error: Callable[[str, str], None],
        on_event: Callable[[str, Any], None],
        config: Optional[LCUConfig] = None
    ):
        if not callable(on_state): raise TypeError("on_state")
        if not callable(on_error): raise TypeError("on_error")
        if not callable(on_event): raise TypeError("on_event")

        self._config = config or LCUConfig()
        self._logger = LCULogger("lcu", self._config.log_level)
        self._on_state = on_state
        self._on_error = on_error
        self._on_event = on_event

        self._lock = threading.Lock()
        self._running = False
        self._connected = False
        self._status = "disconnected"

        self._port: Optional[str] = None
        self._token: Optional[str] = None
        self._headers: dict = {}
        self._cached: Optional[Dict] = None
        self._active_client: Optional[Dict] = None

        self._poll_timer: Optional[threading.Thread] = None
        self._heartbeat_thread: Optional[threading.Thread] = None
        self._ws_thread: Optional[threading.Thread] = None
        self._ws: Optional[WSAdapter] = None
        self._http: Optional[HTTPAdapter] = None

        self._reconnect_count = 0
        self._cmd_fail = 0
        self._pending_reconnect = False
        self._exhausted = False

        self._event_queue = _queue.Queue()
        self._launched_clients: List[Dict] = []
        self._has_client_but_no_command_line = False
        self._discovery_path = ""

    @property
    def status(self) -> str: return self._status
    @property
    def launched_clients(self) -> List[Dict]: return self._launched_clients
    @property
    def has_client_but_no_command_line(self) -> bool: return self._has_client_but_no_command_line
    @property
    def discovery_path(self) -> str: return self._discovery_path
    @property
    def config(self) -> LCUConfig: return self._config

    def _set_state(self, s: str):
        self._status = s
        self._logger.info(f"state -> {s}")
        try: self._on_state(s)
        except Exception: pass

    def _emit_error(self, code: str, msg: str):
        self._logger.error(f"{code}: {msg}")
        try: self._on_error(code, msg)
        except Exception: pass

    def _drain_events(self):
        while not self._event_queue.empty():
            try:
                uri, payload = self._event_queue.get_nowait()
                self._on_event(uri, payload)
            except Exception:
                pass

    def _dispatch(self, ws, msg):
        if not isinstance(msg, str) or not msg.strip():
            return
        if not (msg.startswith("{") or msg.startswith("[")):
            return
        try:
            data = json.loads(msg)
        except Exception:
            return
        if not (isinstance(data, list) and len(data) >= 3):
            return
        uri = data[1]
        payload = data[2]
        if uri == "/lol-matchmaking/v1/ready-check":
            if not isinstance(payload, dict) or payload.get("state") != "InProgress":
                return
        self._event_queue.put((uri, payload))

    def _select_valid_client(self, clients: List[Dict]) -> Optional[Dict]:
        for c in clients:
            try:
                info = _parse_command_line(c["cmd"])
                auth = "Basic " + base64.b64encode(f"riot:{info['token']}".encode()).decode()
                headers = {"Authorization": auth}
                http = HTTPAdapter(f"https://127.0.0.1:{info['port']}", headers, self._config)
                http.request("GET", "/lol-summoner/v1/current-summoner")
                info["cmd"] = c["cmd"]
                return info
            except Exception:
                continue
        return None

    def _update_launched_clients(self):
        try:
            clients = _read_ux_native()
            self._discovery_path = "native"
            self._cmd_fail = 0
            self._has_client_but_no_command_line = False
        except Exception:
            if self._config.enable_cim_fallback:
                clients = _read_ux_cim()
                self._discovery_path = "cim"
                if not clients:
                    raise RuntimeError(ERR_CIM_FAILED)
            else:
                raise RuntimeError(ERR_NATIVE_FAILED)

            self._cmd_fail += 1
            if self._cmd_fail >= self._config.cmd_read_fail_limit:
                self._has_client_but_no_command_line = True
                self._emit_error(ERR_NO_CMD_LINE, "continuous command line read failure")

        old_active = self._active_client
        self._launched_clients = clients
        valid = self._select_valid_client(clients)
        self._active_client = valid

        if old_active and not valid:
            self._logger.info("active client lost, schedule reconnect")
            self._schedule_reconnect()
        elif valid and (not old_active or old_active.get("port") != valid.get("port")):
            self._logger.info("new active client detected, schedule reconnect")
            self._schedule_reconnect()

    def _poll_loop(self):
        while self._running:
            try:
                self._update_launched_clients()
            except Exception as e:
                self._logger.debug(f"poll update failed: {e}")
            interval = self._config.poll_interval_connected if self._connected else self._config.poll_interval_disconnected
            for _ in range(int(interval * 10)):
                if not self._running:
                    return
                time.sleep(0.1)

    def _heartbeat_loop(self):
        while self._running and self._connected:
            for _ in range(int(self._config.heartbeat_interval * 10)):
                if not self._running:
                    return
                time.sleep(0.1)
            if not self._running:
                return
            try:
                self._http.request("GET", "/lol-summoner/v1/current-summoner")
            except Exception:
                self._logger.warn("heartbeat failed, schedule reconnect")
                self._schedule_reconnect()

    def _start_heartbeat(self):
        if self._heartbeat_thread and self._heartbeat_thread.is_alive():
            return
        self._heartbeat_thread = None
        self._heartbeat_thread = threading.Thread(target=self._heartbeat_loop, daemon=True)
        self._heartbeat_thread.start()

    def _schedule_reconnect(self):
        with self._lock:
            if self._pending_reconnect or self._exhausted:
                return
            self._pending_reconnect = True
        threading.Thread(target=self._reconnect_locked, daemon=True).start()

    def _on_ws_closed(self):
        self._logger.info("ws closed")
        if self._running:
            self._schedule_reconnect()

    def _reconnect_locked(self):
        with self._lock:
            self._pending_reconnect = False
            if not self._running:
                return
            if self._reconnect_count >= self._config.max_reconnect:
                self._logger.warn("reconnect exhausted, continue polling")
                self._exhausted = True
                self._connected = False
                self._set_state("disconnected")
                self._emit_error(ERR_RECONNECT_EXHAUSTED, "continue polling for LCU recovery")
                return
            self._reconnect_count += 1
            backoff = min(2 ** self._reconnect_count, 30)
            self._set_state("reconnecting")

        self._logger.info(f"reconnect attempt {self._reconnect_count}, backoff {backoff}s")
        time.sleep(backoff)

        with self._lock:
            if not self._running:
                return
            try:
                if self._cached:
                    try:
                        self._http.request("GET", "/lol-summoner/v1/current-summoner")
                        if not self._ws or not self._ws._running:
                            raise RuntimeError("ws_dead")
                        self._connected = True
                        self._reconnect_count = 0
                        self._exhausted = False
                        self._set_state("connected")
                        self._start_heartbeat()
                        return
                    except Exception:
                        self._cached = None

                if not self._active_client:
                    self._active_client = self._select_valid_client(self._launched_clients)
                if not self._active_client:
                    raise RuntimeError(ERR_VERIFY_FAILED)

                info = self._active_client
                auth = "Basic " + base64.b64encode(f"riot:{info['token']}".encode()).decode()
                headers = {"Authorization": auth}
                http = HTTPAdapter(f"https://127.0.0.1:{info['port']}", headers, self._config)
                http.request("GET", "/lol-summoner/v1/current-summoner")

                self._cached = info
                self._port = info["port"]
                self._token = info["token"]
                self._headers = headers
                self._http = http
                self._connected = True
                self._reconnect_count = 0
                self._exhausted = False
                self._discovery_path = "cached"

                if self._ws:
                    self._ws.close()
                self._ws = WSAdapter(
                    f"wss://127.0.0.1:{self._port}/",
                    [f"Authorization: {self._headers['Authorization']}"],
                    self._dispatch,
                    self._emit_error,
                    self._config
                )
                self._ws_thread = threading.Thread(
                    target=self._ws.run_forever,
                    args=(lambda: self._running, self._on_ws_closed),
                    daemon=True
                )
                self._ws_thread.start()

                self._set_state("connected")
                self._start_heartbeat()
            except Exception as e:
                self._emit_error(ERR_VERIFY_FAILED, str(e))
                self._connected = False
                if self._running and not self._exhausted:
                    self._schedule_reconnect()

    def connect(self):
        with self._lock:
            if self._running:
                return
            self._running = True
            self._connected = False
            self._reconnect_count = 0
            self._cmd_fail = 0
            self._pending_reconnect = False
            self._exhausted = False
            self._set_state("connecting")

            if self._poll_timer and self._poll_timer.is_alive():
                return
            self._poll_timer = threading.Thread(target=self._poll_loop, daemon=True)
            self._poll_timer.start()

        self._schedule_reconnect()

    def request(self, method: str, path: str, body: Any = None) -> Any:
        with self._lock:
            if not self._connected or not self._http:
                raise RuntimeError("not_connected")
        return self._http.request(method, path, body)

    def rebuild_wmi(self):
        self._cmd_fail = 0
        self._has_client_but_no_command_line = False
        self._update_launched_clients()

    def close(self):
        self._running = False
        self._connected = False
        self._pending_reconnect = False
        self._set_state("disconnected")

        if self._poll_timer:
            self._poll_timer.join(timeout=3)
            self._poll_timer = None
        if self._ws:
            self._ws.close()
            if self._ws_thread:
                self._ws_thread.join(timeout=3)
            self._ws = None
            self._ws_thread = None
        if self._heartbeat_thread:
            self._heartbeat_thread.join(timeout=3)
            self._heartbeat_thread = None

        self._http = None
        self._cached = None
        self._active_client = None
        self._launched_clients = []
        self._has_client_but_no_command_line = False

if __name__ == "__main__":
    def on_state(s): print("[state]", s)
    def on_error(c, m): print("[error]", c, m)
    def on_event(u, d): print("[event]", u)

    config = LCUConfig(enable_cim_fallback=False, log_level="INFO")
    c = LCUClient(on_state, on_error, on_event, config)
    try:
        c.connect()
        while True:
            c._drain_events()
            time.sleep(0.1)
    except KeyboardInterrupt:
        c.close()
