import requests
import json
import warnings
import subprocess
import re
import time
from requests.auth import HTTPBasicAuth
from urllib3.exceptions import InsecureRequestWarning

# ====================== 创作者信息 ======================
AUTHOR_INFO = {
    "name": "救赎",
    "contact_qq": "2991807184",
    "project": "LCU-API-Scanner 国服LOL接口扫描工具",
    "copyright": "Copyright (c) 2026 救赎 All Rights Reserved",
    "license": "MIT License"
}
# ======================================================

warnings.filterwarnings("ignore", category=InsecureRequestWarning)

BASE_SAVE_PATH = r"C:\Users\Administrator\Desktop"
MAX_RETRY = 3
SLEEP_DELAY = 0.6

class LcuScanner:
    def __init__(self):
        self.port = None
        self.token = None
        self.base_url = None
        self.auth = None

    def print_copyright(self):
        print("=" * 65)
        print(f"工具名称：{AUTHOR_INFO['project']}")
        print(f"创作者：{AUTHOR_INFO['name']} | 联系QQ：{AUTHOR_INFO['contact_qq']}")
        print(f"版权声明：{AUTHOR_INFO['copyright']}")
        print(f"开源协议：{AUTHOR_INFO['license']}")
        print("=" * 65 + "\n")

    def get_lcu_param_from_process(self):
        print("【1/3】正在读取英雄联盟客户端进程...")
        cmd = 'wmic process where name="LeagueClientUx.exe" get commandline /value'
        result = subprocess.run(cmd, capture_output=True, text=True, shell=True)
        output = result.stdout

        port_match = re.search(r'--app-port=(\d+)', output)
        token_match = re.search(r'--remoting-auth-token=([\w-]+)', output)

        if not port_match or not token_match:
            print("❌ 未检测到英雄联盟客户端，请打开LOL大厅后重新运行！")
            return False

        self.port = port_match.group(1)
        self.token = token_match.group(1)
        self.base_url = f"https://127.0.0.1:{self.port}"
        self.auth = HTTPBasicAuth("riot", self.token)
        print(f"✅ 读取成功 | 端口：{self.port} | Token：{self.token[:10]}******")
        return True

    def fetch_all_api(self):
        print("\n【2/3】正在扫描全部LCU接口，请稍等...")
        for attempt in range(MAX_RETRY):
            try:
                resp = requests.get(
                    f"{self.base_url}/help",
                    auth=self.auth,
                    verify=False,
                    timeout=12
                )
                resp.raise_for_status()
                return resp.json()
            except Exception as e:
                if attempt != MAX_RETRY - 1:
                    print(f"第{attempt+1}次扫描失败，等待{SLEEP_DELAY}s重试...")
                    time.sleep(SLEEP_DELAY)
                else:
                    print(f"❌ 扫描接口失败：{str(e)}")
                    return None

    def export_file(self, raw_api_data):
        all_api_full = []
        http_api_only = []

        for method, paths in raw_api_data.items():
            for path in paths:
                item = {
                    "method": method,
                    "path": path,
                    "full_url": f"{self.base_url}{path}"
                }
                all_api_full.append(item)
                if not path.startswith("events/"):
                    http_api_only.append(item)

        export_header = {
            "tool_info": AUTHOR_INFO,
            "note": "本文件由LCU-API-Scanner自动生成，适配英雄联盟WeGame国服",
            "generate_time": time.strftime("%Y-%m-%d %H:%M:%S")
        }

        full_export_data = {
            "header": export_header,
            "api_list": all_api_full
        }
        full_file = f"{BASE_SAVE_PATH}\\LCU_全量接口_含WebSocket事件.json"
        with open(full_file, "w", encoding="utf-8") as f:
            json.dump(full_export_data, f, ensure_ascii=False, indent=2)

        http_export_data = {
            "header": export_header,
            "api_list": http_api_only
        }
        http_file = f"{BASE_SAVE_PATH}\\LCU_精简HTTP接口_可直接调用.json"
        with open(http_file, "w", encoding="utf-8") as f:
            json.dump(http_export_data, f, ensure_ascii=False, indent=2)

        print(f"\n【3/3】导出完成！")
        print(f"总接口/事件数量：{len(all_api_full)} 条")
        print(f"可调用HTTP接口数量：{len(http_api_only)} 条")
        print(f"文件保存位置：桌面")
        print(f"1. 全量清单：LCU_全量接口_含WebSocket事件.json")
        print(f"2. 开发精简清单：LCU_精简HTTP接口_可直接调用.json")

        print("\n===== 前10条HTTP接口预览 =====")
        for item in http_api_only[:10]:
            print(f"{item['method']:8} | {item['path']}")

    def run(self):
        self.print_copyright()
        if not self.get_lcu_param_from_process():
            input("\n按回车退出程序...")
            return
        api_raw = self.fetch_all_api()
        if api_raw is None:
            input("\n按回车退出程序...")
            return
        self.export_file(api_raw)
        input("\n扫描结束，按回车关闭窗口...")

if __name__ == "__main__":
    scanner = LcuScanner()
    scanner.run()