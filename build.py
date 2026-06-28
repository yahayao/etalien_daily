"""PyInstaller 打包脚本。"""

import os
import subprocess
from pathlib import Path


def main():
    # 切换到脚本所在目录
    os.chdir(Path(__file__).parent)

    subprocess.run(
        [
            "uv", "run", "pyinstaller",
            "--clean", "--noconfirm",
            "etalien-daily.spec",
        ],
        check=True,
    )

    print("\nBuild complete: dist/etalien-daily.exe")


if __name__ == "__main__":
    main()
