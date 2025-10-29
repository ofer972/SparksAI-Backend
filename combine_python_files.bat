@echo off
REM Batch script to combine all Python files into combined_backend.txt
REM Generic script that works with any Python files in the directory

echo Combining all Python files into combined_backend.txt...

REM Delete the output file if it exists
if exist combined_backend.txt del combined_backend.txt

REM Loop through all .py files and append them to combined_backend.txt
for %%f in (*.py) do (
    echo. >> combined_backend.txt
    echo ========================================== >> combined_backend.txt
    echo File: %%f >> combined_backend.txt
    echo ========================================== >> combined_backend.txt
    echo. >> combined_backend.txt
    type "%%f" >> combined_backend.txt
    echo. >> combined_backend.txt
    echo. >> combined_backend.txt
)

echo Done! All Python files have been combined into combined_backend.txt
pause


