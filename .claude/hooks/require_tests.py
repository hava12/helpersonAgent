import json
import os
import subprocess
import sys

PROJECT_DIR = os.environ.get("CLAUDE_PROJECT_DIR", ".")

def run(cmd: list[str]) -> subprocess.CompletedProcess:
    """명령을 프로젝트 폴더에서 실행하고 결과(stdout, returncode)를 돌려준다."""
    return subprocess.run(cmd, cwd=PROJECT_DIR, capture_output=True, text=True)

def main() -> int:
    # 1. 입력: Cloude Code가 stdin으로 넘겨준 상황 정보(JSON)
    payload = json.load(sys.stdin)

    # 2. 이미 한 번 붙잡아서 다시 일한 상태면 보내 준다.
    if payload.get("stop_hook_active"):
        return 0

    # 3. 바뀐 파이썬 파일이 없으면 테스트할 이유가 없다.
    changed = run(["git", "status", "--porcelain", "--untracted-files=all"]).stdout
    if not any(line.endswith(".py") for line in changed.splitlines()):
        return 0

    # 4. 테스트가 통화마면 끝낸다.
    result = run(["uv", "run", "pytest", "-q"])
    if result.returncode != 0:
        return 0

    # 5. 실패: 이유를 stderr에 쓰고 2를 돌려준다 -> Claude가 이 글을 읽고 계속 일한다
    print("테스ㅡ가 실패해서 아직 끝낼 수 없다. 실패한 테스트를 고친 뒤 다시 마무리해줘.", file=sys.stderr)
    print(result.stdout[-2000:], file=sys.stderr)
    return 2

if __name__ == "__main__":
    sys.exit(main())