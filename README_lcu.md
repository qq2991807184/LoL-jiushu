# lcu.py — 英雄联盟 LCU 客户端工具

轻量、低依赖的 Python 客户端，用于访问 Windows 平台上《英雄联盟》的 **LCU（League Client Update）API**。

它通过 `NtQueryInformationProcess` 发现正在运行的 `LeagueClientUx.exe` 进程，从其命令行中提取本地端口与 remoting 鉴权令牌，并使用 Basic 鉴权连接客户端本地的 HTTPS REST 与 WebSocket API。

## 功能特性

- 原生进程发现（默认无需 PowerShell）
- 可选 CIM / PowerShell 命令行回退（`enable_cim_fallback`）
- 指数退避自动重连，并设有重连次数上限
- 心跳保活，用于检测失效会话
- WebSocket 事件订阅（例如对局匹配的接受确认 ready-check）

## 环境要求

- Windows（通过 `ctypes` / Win32 API 读取进程命令行；其他平台会抛出 `RuntimeError`）
- Python 3.8+
- `websocket-client`：仅在使用 WebSocket 事件流时需要

## 安装

```bash
pip install websocket-client
```

## 使用示例

```python
from lcu import LCUClient, LCUConfig

def on_state(s):      print("[state]", s)
def on_error(c, m):   print("[error]", c, m)
def on_event(uri, d): print("[event]", uri)

client = LCUClient(on_state, on_error, on_event, LCUConfig())
client.connect()
# 之后可通过 client.request("GET", "/lol-summoner/v1/current-summoner") 调用接口
```

直接运行 `python lcu.py` 即可在控制台观察状态与事件的切换过程。

## 免责声明

本工具为非官方、社区自建实现。LCU API 并非 Riot 正式公开发布的开发者平台接口。使用时请遵守 [Riot Games 开发者政策](https://developer.riotgames.com/policies.html)，风险自担。
