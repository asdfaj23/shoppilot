@echo off
chcp 65001 >nul
cd /d %~dp0
echo ============================================
echo   ShopPilot - 电商导购智能体
echo   服务地址: http://localhost:8601
echo   关闭此窗口即停止服务
echo ============================================
start "" /b cmd /c "timeout /t 4 /nobreak >nul & start "" http://localhost:8601"
D:\conda_envs\agent\python.exe -m streamlit run app.py --server.port 8601 --browser.gatherUsageStats false
pause
