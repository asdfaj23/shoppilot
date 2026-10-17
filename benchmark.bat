@echo off
chcp 65001 >nul
cd /d %~dp0
echo ============================================
echo   ShopPilot - 25 题全量评测 + token 对照
echo   预计 20 分钟，零裁判 token
echo ============================================
D:\conda_envs\agent\python.exe -X utf8 benchmark.py --run --compare
echo.
echo 报告已生成: docs\benchmark_report.md
pause
