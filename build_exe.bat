@echo off
REM =====================================================================
REM  불량 이미지 자동 판정 프로그램 - EXE 빌드 스크립트
REM  * 반드시 Windows PC에서, 아래 순서대로 실행하세요.
REM  * Python 3.10~3.12 (64bit) 설치 후 사용하세요.
REM =====================================================================

echo [1/3] 필요한 라이브러리 설치 중...
pip install -r requirements.txt
if errorlevel 1 (
    echo 라이브러리 설치에 실패했습니다. 인터넷 연결 및 pip 상태를 확인하세요.
    pause
    exit /b 1
)

echo [2/3] 기존 빌드 결과물 정리 중...
rmdir /s /q build 2>nul
rmdir /s /q dist 2>nul
del /q DefectInspector.spec 2>nul

echo [3/3] EXE 빌드 중 (몇 분 정도 소요될 수 있습니다)...
pyinstaller --noconfirm --onefile --windowed ^
    --name "DefectInspector" ^
    --collect-all sklearn ^
    --collect-all skimage ^
    --collect-all cv2 ^
    --hidden-import "PIL._tkinter_finder" ^
    app.py

if errorlevel 1 (
    echo 빌드에 실패했습니다. 위 오류 메시지를 확인하세요.
    pause
    exit /b 1
)

echo.
echo =====================================================================
echo  빌드 완료! dist\DefectInspector.exe 파일을 확인하세요.
echo  * exe 파일은 실행 시 옆에 data\, model\ 폴더를 자동 생성합니다.
echo  * exe 파일과 함께 폴더 전체를 이동/배포하면 됩니다.
echo =====================================================================
pause
