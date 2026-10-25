@echo off
git check-ignore .env >nul || (echo .env is NOT ignored - stopping & exit /b 1)
git add .
git commit -m "traffic tracker"
git branch -M main
git remote get-url origin >nul 2>&1 || git remote add origin https://github.com/lalasonycierny/ba-livetraffic.git
git push -u origin main
pause
