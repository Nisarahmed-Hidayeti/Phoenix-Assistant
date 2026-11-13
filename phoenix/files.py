"""Bounded operations within folders granted for the current app session."""
import hashlib
import os
import re
import shutil
import stat
import subprocess
import unicodedata
import uuid
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from tempfile import TemporaryFile


class TaskError(RuntimeError):
    pass


class Clarify(TaskError):
    pass


DIRECTORY = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


class Grant:
    """Pin the directory selected by the user for the lifetime of the grant."""

    def __init__(self, path):
        self.path = Path(path).resolve(strict=True)
        self.fd = os.open(self.path, DIRECTORY)

    def close(self):
        os.close(self.fd)

MAX_FILE = 32 * 1024 * 1024
MAX_TEXT = 64 * 1024


def safe_name(value):
    return not any(unicodedata.category(char) in {"Cc", "Cf", "Cs"} for char in value)


class Files:
    def __init__(self, grants):
        self.grants = {name: (path.path if isinstance(path, Grant) else Path(path).resolve(strict=True))
                       for name, path in grants.items()}
        self.roots = {}
        try:
            for name, path in grants.items():
                self.roots[name] = os.dup(path.fd) if isinstance(path, Grant) else os.open(self.grants[name], DIRECTORY)
        except OSError:
            self.close()
            raise

    def close(self):
        for fd in self.roots.values():
            os.close(fd)
        self.roots.clear()

    @contextmanager
    def parent(self, grant, path, make=False):
        if grant not in self.roots:
            raise TaskError("Grant access to the requested folder first.")
        parts = path.split("/")
        if not safe_name(path) or len(parts) > 16 or any(part in ("", ".", "..") for part in parts):
            raise TaskError("Use a relative path within the granted folder.")
        fd = os.dup(self.roots[grant])
        try:
            for index, part in enumerate(parts):
                actual = Path(os.readlink(f"/proc/self/fd/{fd}"))
                if actual != self.grants[grant].joinpath(*parts[:index]):
                    raise TaskError("The authorized directory moved. Grant its current location again.")
                if index == len(parts) - 1:
                    break
                if make:
                    try:
                        os.mkdir(part, dir_fd=fd)
                    except FileExistsError:
                        pass
                next_fd = os.open(part, DIRECTORY, dir_fd=fd)
                os.close(fd)
                fd = next_fd
            yield fd, parts[-1]
        finally:
            os.close(fd)

    def read(self, grant, path):
        with self.parent(grant, path) as (fd, name):
            flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
            with os.fdopen(os.open(name, flags, dir_fd=fd), "rb") as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > MAX_FILE:
                    raise TaskError("Only regular files with a single hard link and up to 32 MiB can be read.")
                data = stream.read(MAX_FILE + 1)
                if len(data) > MAX_FILE:
                    raise TaskError("File exceeds the size limit.")
                return data

    def write(self, grant, path, data):
        with self.parent(grant, path) as (fd, name):
            temp = ".phoenix-" + uuid.uuid4().hex
            output = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o600, dir_fd=fd)
            try:
                with os.fdopen(output, "wb") as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                try:
                    os.link(temp, name, src_dir_fd=fd, dst_dir_fd=fd, follow_symlinks=False)
                except FileExistsError as exc:
                    raise Clarify(f"{path} already exists. Choose a different destination name.") from exc
            finally:
                os.unlink(temp, dir_fd=fd)
            os.fsync(fd)
        if hashlib.sha256(self.read(grant, path)).digest() != hashlib.sha256(data).digest():
            raise TaskError(f"Could not verify the bytes written to {path}.")
        return path

    def mkdir(self, grant, path):
        with self.parent(grant, path, make=True) as (fd, name):
            try:
                os.mkdir(name, dir_fd=fd)
            except FileExistsError:
                pass
            check = os.open(name, DIRECTORY, dir_fd=fd)
            os.close(check)
        return path

    def search(self, grant, query, recursive=True, cancel=lambda: None):
        if grant not in self.roots:
            raise TaskError("Grant access to the requested folder first.")
        matches = []
        seen = 0
        with os.scandir(self.roots[grant]) as scan:
            # Walk directory descriptors so symlinks cannot redirect enumeration.
            def visit(entries, fd, prefix="", depth=0):
                nonlocal seen
                if Path(os.readlink(f"/proc/self/fd/{fd}")) != self.grants[grant] / prefix:
                    raise TaskError("The authorized search directory moved.")
                for entry in entries:
                    cancel()
                    seen += 1
                    if seen > 5000:
                        raise TaskError("Search exceeded 5,000 entries. Grant a narrower folder.")
                    if not safe_name(entry.name):
                        continue
                    path = prefix + entry.name
                    if entry.is_file(follow_symlinks=False) and query.casefold() in entry.name.casefold():
                        if entry.stat(follow_symlinks=False).st_nlink == 1:
                            matches.append(path)
                    elif recursive and depth < 15 and entry.is_dir(follow_symlinks=False):
                        child = os.open(entry.name, DIRECTORY, dir_fd=fd)
                        try:
                            with os.scandir(child) as children:
                                visit(children, child, path + "/", depth + 1)
                        finally:
                            os.close(child)
            visit(scan, self.roots[grant])
        return sorted(matches)

    def local_path(self, grant, path, directory=False):
        with self.parent(grant, path) as (fd, name):
            target = os.stat(name, dir_fd=fd, follow_symlinks=False)
            check_type = stat.S_ISDIR if directory else stat.S_ISREG
            actual = Path(os.readlink(f"/proc/self/fd/{fd}")) / name
            if (not check_type(target.st_mode) or (not directory and target.st_nlink != 1)
                    or not actual.is_relative_to(self.grants[grant])):
                raise TaskError("The file or folder no longer matches the authorized path.")
            return actual


def extract(data, timeout):
    if not all(shutil.which(name) for name in ("pdftotext", "bwrap", "prlimit")):
        raise TaskError("Install Poppler, bubblewrap, and util-linux to extract PDFs in isolation.")
    # Give the parser the PDF via stdin. Only expose system runtime files.
    sandbox = [
        "prlimit", "--as=536870912", "--fsize=65536", "--core=0", "--", "bwrap",
        "--unshare-all", "--die-with-parent", "--new-session", "--ro-bind", "/usr", "/usr",
        "--symlink", "usr/lib", "/lib", "--symlink", "usr/lib64", "/lib64",
        "--tmpfs", "/tmp", "--chdir", "/tmp", "--clearenv", "--setenv", "LANG", "C.UTF-8",
        "--", "/usr/bin/pdftotext", "-layout", "-", "-",
    ]
    with TemporaryFile() as output:
        result = subprocess.run(sandbox, input=data, stdout=output,
                                stderr=subprocess.DEVNULL, timeout=timeout)
        output.seek(0)
        text = output.read(MAX_TEXT + 1).decode("utf-8", errors="replace")
    if result.returncode or not text.strip() or len(text) > MAX_TEXT:
        raise TaskError("PDF is unreadable, has no extractable text, exceeds the text limit, or the sandbox failed.")
    return text


def invoice_fields(text):
    match = re.search(r"(?im)^\s*(?:invoice date|date)\s*:\s*(\d{4}-\d{2}-\d{2})\b", text)
    try:
        invoice_date = date.fromisoformat(match[1] if match else "")
    except ValueError as exc:
        raise TaskError("Missing or invalid invoice date (expected YYYY-MM-DD).") from exc
    total = re.search(r"(?im)^\s*(?:invoice total|grand total|total due|total)\s*:\s*((?:[A-Z]{3}\s+|[$€£]\s*)?[\d,]+\.\d{2}(?:\s+[A-Z]{3})?)\s*$", text)
    if not total:
        raise TaskError("Missing invoice total.")
    return invoice_date, total[1]
