import os
import sys
import shutil
import subprocess
import platform

REQUIRED_MODULES = [
    "pandas",
    "numpy",
    "pdfplumber",
    "openpyxl",
    "pytesseract",
    "PIL",
    "networkx",
    "sklearn",
    "xgboost",
    "fastapi",
    "sqlalchemy",
    "reportlab",
    "faker",
]

def run(cmd):
    print(f"\n>>> {' '.join(cmd)}")
    subprocess.check_call(cmd)

def install_requirements():
    if not os.path.exists("requirements.txt"):
        print("ERROR: requirements.txt not found")
        sys.exit(1)

    run([sys.executable, "-m", "pip", "install", "--upgrade", "pip"])
    run([sys.executable, "-m", "pip", "install", "-r", "requirements.txt"])

def verify_imports():
    print("\n========== VERIFYING IMPORTS ==========")

    failed = []

    for module in REQUIRED_MODULES:
        try:
            __import__(module)
            print(f"[OK] {module}")
        except Exception as e:
            print(f"[FAIL] {module}: {e}")
            failed.append(module)

    return failed

def verify_tesseract():
    print("\n========== VERIFYING TESSERACT ==========")

    tesseract_path = shutil.which("tesseract")

    if tesseract_path:
        print(f"[OK] Tesseract found: {tesseract_path}")
        return True

    print("[FAIL] Tesseract not found")

    system = platform.system()

    if system == "Windows":
        print("\nInstall Tesseract:")
        print("https://github.com/UB-Mannheim/tesseract/wiki")
        print("Default path:")
        print(r"C:\Program Files\Tesseract-OCR\tesseract.exe")

    elif system == "Linux":
        print("\nRun:")
        print("sudo apt update")
        print("sudo apt install -y tesseract-ocr")

    return False

def main():
    print("=" * 60)
    print("CIDECODE AML PIPELINE INSTALLER")
    print("=" * 60)

    try:
        install_requirements()
    except Exception as e:
        print(f"\nInstallation failed: {e}")
        sys.exit(1)

    failed = verify_imports()

    verify_tesseract()

    print("\n========== SUMMARY ==========")

    if not failed:
        print("All Python dependencies installed successfully.")
    else:
        print("Missing modules:")
        for item in failed:
            print(f" - {item}")

    print("\nDone.")

if __name__ == "__main__":
    main()