import os
import shutil
import zipfile
from pathlib import Path

def create_release_zip():
    root = Path(__file__).parent.resolve()
    if "brain" in str(root):
        root = Path(r"d:\etixSetupTest")
    
    release_dir = root / "release"
    release_dir.mkdir(parents=True, exist_ok=True)
    zip_output = release_dir / "Etix_Checker_2026.zip"

    include_files = [
        "setup_installer.bat",
        "run_gui.bat",
        "run_gui.vbs",
        "run.bat",
        "gui_app.py",
        "cli.py",
        "requirements.txt",
        "ИНСТРУКЦИЯ.md",
        "README.md",
        ".env.example",
    ]

    include_dirs = [
        "src",
        "icons",
    ]

    # Specific data files for clean template
    data_files = [
        "data/shows.csv",
        "data/good_proxies.txt",
        "data/bad_proxies.txt",
        "data/warmup_sites.txt",
        "data/human.yml",
    ]

    exclude_patterns = [
        "__pycache__",
        ".pyc",
        ".git",
        "venv",
        "ms-playwright",
        "runs",
        "logs",
        "screens",
        ".pytest_cache",
        "release",
    ]

    print(f"[*] Packaging Etix Checker 2026 into {zip_output.name}...")
    if zip_output.exists():
        zip_output.unlink()

    with zipfile.ZipFile(zip_output, "w", zipfile.ZIP_DEFLATED) as zipf:
        # 1. Root files
        for filename in include_files:
            file_path = root / filename
            if file_path.exists():
                arcname = f"Etix Checker 2026/{filename}"
                zipf.write(file_path, arcname)
                print(f"  + {filename}")

        # 2. Source & Icon directories
        for dirname in include_dirs:
            dir_path = root / dirname
            if not dir_path.exists():
                continue
            for cur_dir, subdirs, files in os.walk(dir_path):
                subdirs[:] = [d for d in subdirs if not any(ex in d for ex in exclude_patterns)]
                for file in files:
                    if any(ex in file for ex in exclude_patterns):
                        continue
                    full_file = Path(cur_dir) / file
                    rel_to_root = full_file.relative_to(root)
                    arcname = f"Etix Checker 2026/{rel_to_root.as_posix()}"
                    zipf.write(full_file, arcname)
                    print(f"  + {rel_to_root.as_posix()}")

        # 3. Data files
        for rel_file in data_files:
            full_file = root / rel_file
            if full_file.exists():
                arcname = f"Etix Checker 2026/{rel_file}"
                zipf.write(full_file, arcname)
                print(f"  + {rel_file}")

        # 4. Empty directory placeholders
        for empty_dir in ["logs", "screens", "runs", "data/adspower_backup"]:
            zipinfo = zipfile.ZipInfo(f"Etix Checker 2026/{empty_dir}/")
            zipf.writestr(zipinfo, "")

    size_kb = zip_output.stat().st_size / 1024
    print("")
    print(f"[OK] Release archive created successfully: {zip_output} ({size_kb:.1f} KB)")
    print("     Ready for instant client distribution!")

if __name__ == "__main__":
    create_release_zip()
