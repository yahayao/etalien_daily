"""PyInstaller 打包脚本。"""

import subprocess
import sys
from pathlib import Path


def main():
    root = Path(__file__).parent

    # 安装 PyInstaller（如果未安装）
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "pyinstaller"],
        check=True,
    )

    # 打包
    subprocess.run(
        [
            sys.executable, "-m", "PyInstaller",
            "--clean", "--noconfirm",
            str(root / "etalien-daily.spec"),
        ],
        check=True,
    )

    print("\n打包完成: dist/etalien-daily/")


if __name__ == "__main__":
    main()
