# LCU-API-Scanner 国服LOL LCU接口扫描工具
**创作者：救赎 | 联系QQ：2991807184**
开源协议：MIT License

适配WeGame英雄联盟国服，自动扫描本地客户端全部LCU接口，一键导出JSON接口文档，解决国服Swagger阉割、老旧LCU Explorer工具失效、线上接口文档过期等痛点。

## 功能亮点
1. 自动读取游戏进程，无需手动填写端口、Token，原生兼容WeGame国服
2. 自动区分HTTP调用接口 / WebSocket实时推送事件
3. 自动输出两份接口清单：全量完整版、开发精简调用版
4. 内置3次重试机制，完美兼容LCU客户端启动延迟404问题
5. 完整收录国服独有 `/tencent` `/riotclient` 腾讯专属接口
6. 导出的JSON文件头部嵌入创作者、联系方式、生成时间信息
7. 纯Python轻量化脚本，仅依赖requests，无重型运行库

## 运行环境
Windows 10 / Windows 11，Python 3.8 及以上版本

## 安装依赖
打开终端执行：
```bash
pip install -r requirements.txt