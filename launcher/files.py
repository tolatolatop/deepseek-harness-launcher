"""Directory-FD based file access: no symlinks or traversal, including rename races."""

import os
import secrets
import stat
from contextlib import contextmanager
from pathlib import PurePosixPath

from fastapi import HTTPException

from launcher.settings import settings


def components(path: str) -> list[str]:
    if "\x00" in path or "\\" in path or path.startswith("/"):
        raise HTTPException(400, "文件路径无效")
    parts = list(PurePosixPath(path).parts)
    if any(p in ("..", ".") or len(p) > 255 for p in parts) or len(parts) > 32:
        raise HTTPException(400, "文件路径无效")
    return parts


@contextmanager
def directory(workspace_id: str, path: str = ""):
    root = settings().data_root / "workspaces" / workspace_id / "project"
    fd = None
    try:
        fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        for part in components(path):
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        yield fd
    except (OSError, ValueError):
        raise HTTPException(404, "目录不存在或不可访问") from None
    finally:
        if fd is not None:
            os.close(fd)


def list_files(workspace_id: str, path: str):
    with directory(workspace_id, path) as fd:
        entries = []
        names = os.listdir(fd)
        for name in sorted(names)[:1000]:
            try:
                s = os.stat(name, dir_fd=fd, follow_symlinks=False)
                if stat.S_ISLNK(s.st_mode):
                    continue
                if not (stat.S_ISDIR(s.st_mode) or stat.S_ISREG(s.st_mode)):
                    continue
                entries.append(
                    {
                        "name": name,
                        "is_dir": stat.S_ISDIR(s.st_mode),
                        "size": s.st_size,
                        "modified_at": s.st_mtime,
                    }
                )
            except FileNotFoundError:
                continue
        return {"path": path, "entries": entries, "truncated": len(names) > 1000}


def open_download(workspace_id: str, path: str):
    parts = components(path)
    if not parts:
        raise HTTPException(400, "请选择文件")
    with directory(workspace_id, "/".join(parts[:-1])) as fd:
        try:
            file_fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
            if not stat.S_ISREG(os.fstat(file_fd).st_mode):
                os.close(file_fd)
                raise HTTPException(400, "只支持下载普通文件")
            return os.fdopen(file_fd, "rb"), parts[-1]
        except OSError:
            raise HTTPException(404, "文件不存在或不可访问") from None


async def save_upload(workspace_id: str, path: str, upload):
    name = upload.filename or ""
    parts = components(name)
    if len(parts) != 1 or name != parts[0]:
        raise HTTPException(400, "文件名无效")
    with directory(workspace_id, path) as fd:
        temporary = f".upload-{secrets.token_hex(12)}"
        total = 0
        try:
            out = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
            with os.fdopen(out, "wb") as target:
                while chunk := await upload.read(1024 * 1024):
                    total += len(chunk)
                    if total > settings().upload_limit_mb * 1024 * 1024:
                        raise HTTPException(413, "文件超过上传大小限制")
                    target.write(chunk)
            # link is an atomic no-overwrite publication. Symlinks are not followed.
            os.link(temporary, name, src_dir_fd=fd, dst_dir_fd=fd, follow_symlinks=False)
        except FileExistsError:
            raise HTTPException(409, "已存在同名文件，请重命名后上传") from None
        finally:
            try:
                os.unlink(temporary, dir_fd=fd)
            except FileNotFoundError:
                pass
        return {"name": name, "size": total}


def usage_bytes(workspace_id: str) -> int:
    root = settings().data_root / "workspaces" / workspace_id
    size = 0
    # fwalk never follows directory symlinks. Limit entries to bound request work.
    count = 0
    for _, _, files, fd in os.fwalk(root, follow_symlinks=False):
        for name in files:
            count += 1
            if count > 100000:
                return size
            try:
                s = os.stat(name, dir_fd=fd, follow_symlinks=False)
                if stat.S_ISREG(s.st_mode):
                    size += s.st_size
            except OSError:
                continue
    return size
