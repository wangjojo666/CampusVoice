"""Print the local CampusVoice toolchain status without changing the machine."""

from __future__ import annotations

import platform
import shutil
import subprocess
import sys


def version(command: list[str]) -> str:
    executable = shutil.which(command[0])
    if executable is None:
        return "missing"
    result = subprocess.run(  # noqa: S603
        [executable, *command[1:]],
        capture_output=True,
        check=False,
        text=True,
    )
    return (result.stdout or result.stderr).splitlines()[0].strip()


def main() -> None:
    print(f"platform={platform.platform()}")
    print(f"python={sys.version.split()[0]}")
    print(f"node={version(['node', '--version'])}")
    print(
        f"pnpm={version(['pnpm.cmd' if platform.system() == 'Windows' else 'pnpm', '--version'])}"
    )
    print(f"git={version(['git', '--version'])}")
    print(f"ffmpeg={version(['ffmpeg', '-version'])}")
    print(f"docker={version(['docker', '--version'])}")


if __name__ == "__main__":
    main()
