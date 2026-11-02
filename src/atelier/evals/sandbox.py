"""A throwaway Docker sandbox for running untrusted, model-written commands.

Everything the model writes is hostile until proven otherwise, so:

* **no host mounts of any kind.** Files go in and out as tar streams through `docker exec`. No
  host directory is ever visible to the container, and this works with remote daemons, rootless
  Docker and CI, where bind mounts do not. (Bind mounts also hung outright on Docker Desktop
  when file sharing failed, which is what motivated this design.)
* no network, read-only root filesystem, no capabilities, no privilege escalation, memory / CPU /
  process limits, and small tmpfs mounts as the only writable places (which also bounds disk use);
* the only thing taken back from the container is regular files from `out/`, extracted
  defensively: no links, devices or directories, names flattened to a bare file name, and caps on
  count and size;
* every Docker call has a timeout, so a sick daemon cannot hang the run. A command is wrapped in
  `timeout(1)` inside the container and passed as one argv element (never through a host shell).
"""

from __future__ import annotations

import contextlib
import io
import shutil
import subprocess
import tarfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Protocol

DEFAULT_IMAGE = "atelier-baseline"
MAX_OUTPUT_CHARS = 10_000
MAX_DELIVERABLES = 8
MAX_DELIVERABLE_BYTES = 64 << 20
MAX_TAR_BYTES = MAX_DELIVERABLES * MAX_DELIVERABLE_BYTES + (1 << 20)
DOCKER_CALL_TIMEOUT = 60  # seconds, for every docker CLI call that is not a model command
START_TIMEOUT = 120
UID = 10001  # the unprivileged user in docker/baseline.Dockerfile


class SandboxError(RuntimeError):
    """The sandbox itself failed: the environment is broken, not the model's answer."""


@dataclass(frozen=True)
class CommandResult:
    output: str
    exit_code: int
    timed_out: bool = False


class Sandbox(Protocol):
    def run(self, command: str) -> CommandResult: ...

    def upload(self, files: Mapping[str, Path]) -> None:
        """Put files in the sandbox at /work/in/<name>."""

    def download(self, dest: Path) -> list[Path]:
        """Copy the deliverables from /work/out into `dest`; return the copies."""

    def close(self) -> None: ...


def clip(text: str, limit: int = MAX_OUTPUT_CHARS) -> str:
    """Keep the start and end of long output, as real agents do, so one command cannot flood
    the context."""
    if len(text) <= limit:
        return text
    half = limit // 2
    return f"{text[:half]}\n[... {len(text) - limit} characters omitted ...]\n{text[-half:]}"


# -- tar in, tar out ---------------------------------------------------------------------------


def build_tar(files: Mapping[str, Path], *, prefix: str) -> bytes:
    """A deterministic tar of regular files, owned by the sandbox user, under `prefix/`."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for name, path in sorted(files.items()):
            data = path.read_bytes()
            info = tarfile.TarInfo(f"{prefix}/{PurePosixPath(name).name}")
            info.size, info.mode, info.uid, info.gid, info.mtime = len(data), 0o644, UID, UID, 0
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def extract_deliverables(
    data: bytes,
    dest: Path,
    *,
    max_files: int = MAX_DELIVERABLES,
    max_bytes: int = MAX_DELIVERABLE_BYTES,
) -> list[Path]:
    """Take regular files out of an untrusted tar, and nothing else.

    Links, devices, FIFOs and directories are skipped. Each file is written to `dest` under its
    bare name, so no member name (`../x`, `/etc/x`, `a/b`) can place anything outside `dest`.
    """
    out: list[Path] = []
    seen: set[str] = set()
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as tar:
            for member in tar:
                name = PurePosixPath(member.name).name
                if (
                    not member.isreg()
                    or name in ("", ".", "..")
                    or "\x00" in name
                    or name in seen
                    or member.size > max_bytes
                ):
                    continue
                source = tar.extractfile(member)
                if source is None:
                    continue
                target = dest / name
                with target.open("wb") as f:
                    shutil.copyfileobj(source, f)
                seen.add(name)
                out.append(target)
                if len(out) >= max_files:
                    break
    except tarfile.TarError:
        pass  # not a tar, or a corrupt tail: keep whatever was read cleanly
    return out


# -- the container -----------------------------------------------------------------------------


class DockerSandbox:
    def __init__(
        self,
        *,
        image: str = DEFAULT_IMAGE,
        memory: str = "2g",
        cpus: str = "2",
        pids: int = 256,
        work_size: str = "512m",
        command_timeout: int = 120,
    ) -> None:
        self.command_timeout = command_timeout
        self.cid = ""
        argv = [
            "docker", "run", "-d", "--rm",
            "--network", "none",
            "--read-only",
            "--tmpfs", "/tmp:rw,size=256m,mode=1777",
            "--tmpfs", f"/work:rw,size={work_size},mode=0755,uid={UID},gid={UID}",
            "--memory", memory, "--memory-swap", memory, "--cpus", cpus,
            "--pids-limit", str(pids),
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--label", "atelier-eval-baseline",
            "-e", "HOME=/tmp", "-w", "/work",
            image, "sleep", "infinity",
        ]  # fmt: skip
        try:
            started = subprocess.run(argv, capture_output=True, text=True, timeout=START_TIMEOUT)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise SandboxError(f"could not start the sandbox container: {e}") from None
        if started.returncode != 0:
            raise SandboxError(started.stderr.strip() or "docker run failed")
        self.cid = started.stdout.strip()
        made = self._docker("exec", self.cid, "mkdir", "-p", "/work/in", "/work/out")
        if made.returncode != 0:
            self.close()
            raise SandboxError(made.stderr.decode(errors="replace").strip() or "mkdir failed")

    def _docker(
        self, *args: str, input: bytes | None = None, timeout: float = DOCKER_CALL_TIMEOUT
    ) -> subprocess.CompletedProcess[bytes]:
        try:
            return subprocess.run(
                ["docker", *args], input=input, capture_output=True, timeout=timeout, check=False
            )
        except (OSError, subprocess.TimeoutExpired) as e:
            raise SandboxError(f"docker {args[0]} failed: {e}") from None

    def run(self, command: str) -> CommandResult:
        argv = [
            "docker", "exec", "-w", "/work", self.cid,
            "timeout", "-k", "5", str(self.command_timeout), "bash", "-c", command,
        ]  # fmt: skip
        try:
            proc = subprocess.run(
                argv,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                errors="replace",
                timeout=self.command_timeout + 30,
            )
        except subprocess.TimeoutExpired:
            return CommandResult("[the command did not stop]", 124, timed_out=True)
        return CommandResult(clip(proc.stdout), proc.returncode, proc.returncode == 124)

    def upload(self, files: Mapping[str, Path]) -> None:
        sent = self._docker(
            "exec", "-i", self.cid, "tar", "xf", "-", "-C", "/work",
            input=build_tar(files, prefix="in"),
        )  # fmt: skip
        if sent.returncode != 0:
            raise SandboxError(f"could not copy inputs in: {sent.stderr.decode(errors='replace')}")

    def download(self, dest: Path) -> list[Path]:
        argv = ["docker", "exec", self.cid, "tar", "cf", "-", "-C", "/work", "out"]
        try:
            proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        except OSError as e:
            raise SandboxError(f"docker exec failed: {e}") from None
        assert proc.stdout is not None
        try:
            data = proc.stdout.read(MAX_TAR_BYTES + 1)
            if len(data) > MAX_TAR_BYTES:  # far more than the caps allow: not a deliverable
                proc.kill()
                return []
            proc.wait(timeout=DOCKER_CALL_TIMEOUT)
        except subprocess.TimeoutExpired:
            proc.kill()
            raise SandboxError("docker exec timed out reading the outputs") from None
        finally:
            proc.stdout.close()
        return extract_deliverables(data, dest)

    def close(self) -> None:
        if not self.cid:
            return
        with contextlib.suppress(SandboxError):
            self._docker("rm", "-f", self.cid)
        self.cid = ""


def image_available(image: str = DEFAULT_IMAGE) -> bool:
    """Is the docker CLI usable and the image present? Never hangs: a sick daemon means no.

    `image inspect` alone is not trusted: after a Docker Desktop restart it can report "no such
    image" for an image that `docker images` lists and `docker run` starts.
    """
    for argv in (["docker", "image", "inspect", image], ["docker", "images", "-q", image]):
        try:
            done = subprocess.run(argv, capture_output=True, timeout=15, check=False)
        except (OSError, subprocess.TimeoutExpired):
            return False
        if done.returncode == 0 and (argv[1] == "image" or done.stdout.strip()):
            return True
    return False


SandboxFactory = Callable[[], Sandbox]


def docker_factory(image: str = DEFAULT_IMAGE) -> SandboxFactory:
    return lambda: DockerSandbox(image=image)
