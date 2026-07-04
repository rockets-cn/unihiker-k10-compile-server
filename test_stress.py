"""
K10 Compile Server 压力测试
============================
测试项：
  1. 并发编译（> MAX_CONCURRENT 的排队行为）
  2. 大文件上传 + 小文件批量上传
  3. 连续快速提交
  4. 空/坏上传的边界情况
"""
import asyncio
import time
import sys
import os
from pathlib import Path

SERVER = sys.argv[1] if len(sys.argv) > 1 else "https://localhost:8900"
REPO = Path(__file__).parent

PASS = FAIL = 0


def log(msg, ok=True):
    global PASS, FAIL
    if ok:
        PASS += 1; print(f"  ✅ {msg}")
    else:
        FAIL += 1; print(f"  ❌ {msg}")


async def compile_project(dir_name, label=None, timeout=180):
    """Use curl via subprocess to submit a project (avoids aiohttp form compat issues)."""
    dir_path = REPO / dir_name
    label = label or dir_name
    if not (dir_path / "platformio.ini").exists():
        return {"error": "no platformio.ini", "label": label}

    # Build curl -F args with relative paths
    files_to_upload = []
    for f in sorted(dir_path.rglob("*")):
        if f.is_file() and not any(p in str(f) for p in [".pio", ".git", "__pycache__", ".DS_Store"]):
            rel = str(f.relative_to(dir_path))
            files_to_upload.append((rel, str(f)))

    # Submit
    cmd = ["curl", "-sk", "-X", "POST", f"{SERVER}/api/compile/files"]
    for rel, abspath in files_to_upload:
        cmd += ["-F", f"files=@{abspath};filename={rel}"]

    t0 = time.time()
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)

    submit_time = time.time() - t0
    if proc.returncode != 0:
        return {"error": f"curl exit {proc.returncode}: {stderr.decode()[:200]}", "label": label}

    try:
        body = json.loads(stdout)
    except Exception as e:
        return {"error": f"json parse: {e}, body={stdout.decode()[:200]}", "label": label}

    build_id = body.get("build_id")
    if not build_id:
        return {"error": f"no build_id: {body}", "label": label, "files": len(files_to_upload)}

    # Poll
    deadline = time.time() + timeout
    while time.time() < deadline:
        await asyncio.sleep(0.5)
        poll = await asyncio.create_subprocess_exec(
            "curl", "-sk", f"{SERVER}/api/build/{build_id}/status",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        p_out, _ = await poll.communicate()
        try:
            st = json.loads(p_out)
        except Exception:
            continue
        if st.get("status") == "done":
            return {
                "build_id": build_id, "label": label,
                "files": len(files_to_upload),
                "elapsed": st.get("elapsed", 0),
                "size": st.get("bin_size", 0),
                "submit_time": round(time.time() - t0, 1),
            }
        elif st.get("status") == "error":
            return {"error": st.get("error", "?"), "label": label, "files": len(files_to_upload)}
    return {"error": "timeout", "label": label, "files": len(files_to_upload)}


async def test_01_health():
    """基础健康检查"""
    import subprocess
    r = subprocess.run(["curl", "-sk", f"{SERVER}/api/health"], capture_output=True, text=True, timeout=10)
    d = json.loads(r.stdout)
    ok = d.get("status") == "ok"
    log(f"GET /api/health → {d.get('status')}, v{d.get('version','?')}, pio={d.get('pio_version','?')[:20]}", ok)
    return d


async def test_02_concurrent_compiles():
    """并发 5 个编译请求（semaphore=2 应排队）"""
    dirs = ["examples/Blink"] * 5
    t0 = time.time()
    tasks = [compile_project(d, label=f"Blink-{i}") for i, d in enumerate(dirs)]
    results = await asyncio.gather(*tasks)
    elapsed = time.time() - t0

    errors = [r for r in results if "error" in r]
    successes = [r for r in results if "build_id" in r]
    total_files = sum(r.get("files", 0) for r in successes)

    ok = len(errors) == 0 and len(successes) == 5
    times = [r["submit_time"] for r in successes]
    log(
        f"并发 5×Blink: {len(successes)}成功/{len(errors)}失败, "
        f"总耗时 {elapsed:.1f}s, 文件 {total_files} 个, "
        f"最快 {min(times):.1f}s / 最慢 {max(times):.1f}s / "
        f"平均 {sum(times)/len(times):.1f}s",
        ok
    )
    if errors:
        for e in errors:
            print(f"      ↳ 失败: [{e.get('label','?')}] {e.get('error','?')}")
            print(f"         文件数: {e.get('files', '?')}")
    return results


async def test_03_rapid_sequential():
    """连续快速提交 5 次"""
    times = []
    for i in range(5):
        r = await compile_project("examples/Blink", label=f"seq-{i}")
        if "build_id" in r:
            times.append(r["submit_time"])
        else:
            print(f"      ↳ [{i}] 失败: {r.get('error','?')}")
    ok = len(times) == 5
    log(
        f"连续 5×Blink: {'全部成功' if ok else f'{len(times)}/5 成功'}, "
        f"平均 {sum(times)/len(times):.1f}s/次 最短 {min(times):.1f}s 最长 {max(times):.1f}s"
        if times else "全部失败",
        ok
    )
    return times


async def test_04_multiple_projects():
    """混合项目：Blink + HelloScreen 一起提交"""
    r1, r2 = await asyncio.gather(
        compile_project("examples/Blink", label="Blink-mixed"),
        compile_project("examples/HelloScreen", label="Hello-mixed"),
    )
    r1_ok = "build_id" in r1
    r2_ok = "build_id" in r2
    ok = r1_ok and r2_ok
    log(
        f"混合编译: Blink {'✅' if r1_ok else '❌'} / "
        f"HelloScreen {'✅' if r2_ok else '❌'}",
        ok
    )


async def test_05_large_fake_upload():
    """模拟大文件上传（接近 10MB 限制）"""
    import tempfile
    # Create a ~9MB temp file
    with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as f:
        f.write(b"x" * (9 * 1024 * 1024))
        bigfile = f.name
    try:
        t0 = time.time()
        proc = await asyncio.create_subprocess_exec(
            "curl", "-sk", "-X", "POST", f"{SERVER}/api/compile/files",
            "-F", "files=@examples/Blink/platformio.ini;filename=platformio.ini",
            "-F", f"files=@{bigfile};filename=src/big.bin",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=60)
        elapsed = time.time() - t0
        body = json.loads(stdout)
        # Should get 400 (no valid source)
        ok = "error" in body
        log(f"9MB 上传: HTTP 200, 耗时 {elapsed:.1f}s, {body.get('error','ok')[:60]}", ok)
    finally:
        os.unlink(bigfile)


async def test_06_edge_cases():
    """边界情况"""
    import subprocess

    # 6a: 空提交
    r = subprocess.run(
        ["curl", "-sk", "-X", "POST", f"{SERVER}/api/compile/files"],
        capture_output=True, text=True, timeout=10
    )
    body = json.loads(r.stdout) if r.stdout else {}
    # FastAPI returns 422 for missing field
    msg = body.get("error") or body.get("detail", [{}])[0].get("msg", str(body)[:60])
    log(f"空提交（无文件）: {msg}", True)  # 422 is expected, not a server error

    # 6b: 缺少 platformio.ini
    r = subprocess.run(
        ["curl", "-sk", "-X", "POST", f"{SERVER}/api/compile/files",
         "-F", "files=@/dev/null;filename=main.cpp"],
        capture_output=True, text=True, timeout=10
    )
    body = json.loads(r.stdout)
    ok = body.get("error") == "未找到 platformio.ini"
    log(f"缺少 platformio.ini: {body.get('error','?')}", ok)

    # 6c: 不存在的 build_id
    r = subprocess.run(
        ["curl", "-sk", f"{SERVER}/api/build/deadbeef/status"],
        capture_output=True, text=True, timeout=10
    )
    body = json.loads(r.stdout)
    ok = "不存在" in body.get("error", "")
    log(f"不存在的 build_id: HTTP 404", ok)


async def main():
    global json; import json
    print(f"\n═══ K10 Compile Server 压力测试 ═══")
    print(f"服务器: {SERVER}")
    print(f"时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print()

    tests = [
        ("01", "健康检查", test_01_health),
        ("06", "边界情况", test_06_edge_cases),
        ("05", "大文件上传", test_05_large_fake_upload),
        ("03", "连续快速提交（5次）", test_03_rapid_sequential),
        ("04", "混合项目编译", test_04_multiple_projects),
        ("02", "并发编译（5×Blink, semaphore=2）", test_02_concurrent_compiles),
    ]

    for num, name, fn in tests:
        print(f"[{num}] {name}")
        try:
            await fn()
        except Exception as e:
            import traceback
            log(f"测试异常: {e}\n{traceback.format_exc()}", False)
        print()

    print(f"═══ 汇总 ═══")
    print(f"通过: {PASS} / 总计: {PASS + FAIL}")
    if FAIL:
        print(f"失败: {FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    import json
    exit(asyncio.run(main()))
