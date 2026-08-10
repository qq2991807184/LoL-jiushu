@echo off
chcp 65001
echo ======================================
echo LCU国服接口扫描器打包程序
echo 创作者：救赎 QQ：2991807184
echo ======================================
echo 正在安装打包依赖...
pip install pyinstaller requests
echo.
echo 开始编译单文件EXE...
pyinstaller -F -c main.py -n "LCU国服接口扫描器_救赎版"
echo.
echo 打包完成！EXE文件输出至 dist 文件夹
echo 按任意键关闭窗口
pause