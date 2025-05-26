"""The implementation that talks to a real line PC.

Everything that is genuinely Windows lives here and nowhere else: ``winreg``,
``regsvr32``, ``pnputil``, the side by side store layout and the shortcut
directory. The module imports on any platform so the rest of the toolkit can be
developed and tested off a bench, and the calls that cannot work elsewhere fail
with a clear message rather than an ImportError halfway through an apply.

The registry access uses an explicit view flag. Without one, a 32 bit Python
reading ``Software\\Matricode\\V4T`` on a 64 bit terminal is silently redirected
into ``Software\\Wow6432Node\\Matricode\\V4T`` and reconcile reports a
commissioned machine as untouched. The toolkit always reads the 64 bit view
unless a component says it is 32 bit.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path, PureWindowsPath

from v4t_setup.model.registry import DecodedValue, RegistryRoot, RegistryValueType
from v4t_setup.target.base import AssemblyIdentity, ShortcutSpec, SystemFacts, TargetError
from v4t_setup.util.digest import Digest, digest_file
from v4t_setup.util.fsops import atomic_write_bytes, ensure_dir, remove_path

IS_WINDOWS = sys.platform == "win32"

REGSVR32 = "regsvr32.exe"
PNPUTIL = "pnputil.exe"
COMMAND_TIMEOUT_SECONDS = 300

_HIVE_NAMES = {
    RegistryRoot.CLASSES_ROOT: "HKEY_CLASSES_ROOT",
    RegistryRoot.CURRENT_USER: "HKEY_CURRENT_USER",
    RegistryRoot.LOCAL_MACHINE: "HKEY_LOCAL_MACHINE",
    RegistryRoot.USERS: "HKEY_USERS",
}

_VALUE_TYPE_NAMES = {
    RegistryValueType.STRING: "REG_SZ",
    RegistryValueType.EXPAND_STRING: "REG_EXPAND_SZ",
    RegistryValueType.DWORD: "REG_DWORD",
    RegistryValueType.BINARY: "REG_BINARY",
    RegistryValueType.MULTI_STRING: "REG_MULTI_SZ",
}


def require_windows(operation: str) -> None:
    """Refuse an operation that only means something on Windows.

    :raises TargetError: on any other platform.
    """
    if not IS_WINDOWS:
        raise TargetError(
            f"{operation} needs a Windows terminal, this is {sys.platform}"
        )


def _winreg():  # type: ignore[no-untyped-def]
    require_windows("registry access")
    import winreg

    return winreg


def hive_handle(root: RegistryRoot):  # type: ignore[no-untyped-def]
    """The ``winreg`` handle for a root.

    :raises TargetError: for the ``HKMU`` pseudo root, which has to be resolved
        against ALLUSERS before it can be opened.
    """
    winreg = _winreg()
    if root is RegistryRoot.USER_OR_MACHINE:
        raise TargetError("HKMU must be resolved to a real hive before it can be opened")
    return getattr(winreg, _HIVE_NAMES[root])


class WindowsTarget:
    """A real Windows machine.

    :param view_64bit: read and write the 64 bit registry view. Set False for a
        component marked 32 bit, which belongs under Wow6432Node.
    :param dry_run_root: when set, file writes are redirected under this
        directory. Used by the capture tool to pull a terminal's layout without
        changing it.
    """

    def __init__(self, *, view_64bit: bool = True, dry_run_root: Path | None = None) -> None:
        self.view_64bit = view_64bit
        self.dry_run_root = dry_run_root

    # Paths

    def _local(self, path: str) -> Path:
        if self.dry_run_root is None:
            return Path(path)
        windows = PureWindowsPath(path)
        relative = Path(*(part for part in windows.parts if not part.endswith(":\\")))
        return self.dry_run_root / relative

    # Facts

    def facts(self) -> SystemFacts:
        require_windows("reading system facts")
        import ctypes
        import os
        import platform

        usage = shutil.disk_usage(os.environ.get("SYSTEMDRIVE", "C:") + "\\")
        try:
            elevated = bool(ctypes.windll.shell32.IsUserAnAdmin())  # type: ignore[attr-defined]
        except (AttributeError, OSError):
            elevated = False
        return SystemFacts(
            os_build=int(platform.version().split(".")[-1] or 0),
            architecture="x64" if platform.machine().endswith("64") else "x86",
            is_elevated=elevated,
            free_bytes=usage.free,
            computer_name=platform.node(),
            installed_fonts=frozenset(self._installed_fonts()),
        )

    def _installed_fonts(self) -> set[str]:
        winreg = _winreg()
        names: set[str] = set()
        key_path = r"Software\Microsoft\Windows NT\CurrentVersion\Fonts"
        try:
            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE, key_path, 0, winreg.KEY_READ | self._view_flag()
            ) as handle:
                index = 0
                while True:
                    try:
                        name, _, _ = winreg.EnumValue(handle, index)
                    except OSError:
                        break
                    names.add(name.split(" (")[0])
                    index += 1
        except OSError:
            return names
        return names

    def _view_flag(self) -> int:
        winreg = _winreg()
        return winreg.KEY_WOW64_64KEY if self.view_64bit else winreg.KEY_WOW64_32KEY

    # Filesystem

    def path_exists(self, path: str) -> bool:
        return self._local(path).exists()

    def is_directory(self, path: str) -> bool:
        return self._local(path).is_dir()

    def make_directory(self, path: str) -> bool:
        local = self._local(path)
        if local.is_dir():
            return False
        if local.exists():
            raise TargetError(f"a file already exists at {path}")
        ensure_dir(local)
        return True

    def remove_directory(self, path: str) -> bool:
        return remove_path(self._local(path))

    def read_file(self, path: str) -> bytes:
        local = self._local(path)
        try:
            return local.read_bytes()
        except OSError as exc:
            raise TargetError(f"cannot read {path}: {exc}") from exc

    def write_file(self, path: str, data: bytes) -> bool:
        local = self._local(path)
        if local.is_dir():
            raise TargetError(f"a directory already exists at {path}")
        if local.exists() and local.read_bytes() == data:
            return False
        atomic_write_bytes(local, data)
        return True

    def remove_file(self, path: str) -> bool:
        local = self._local(path)
        if not local.is_file():
            return False
        return remove_path(local)

    def file_digest(self, path: str) -> Digest | None:
        local = self._local(path)
        if not local.is_file():
            return None
        return digest_file(local)

    def list_directory(self, path: str) -> list[str]:
        local = self._local(path)
        if not local.is_dir():
            return []
        return sorted(entry.name for entry in local.iterdir())

    # Registry

    def key_exists(self, root: RegistryRoot, key: str) -> bool:
        winreg = _winreg()
        try:
            with winreg.OpenKey(
                hive_handle(root), key, 0, winreg.KEY_READ | self._view_flag()
            ):
                return True
        except FileNotFoundError:
            return False
        except OSError as exc:
            raise TargetError(f"cannot open {root.hive_name}\\{key}: {exc}") from exc

    def create_key(self, root: RegistryRoot, key: str) -> bool:
        winreg = _winreg()
        if self.key_exists(root, key):
            return False
        try:
            winreg.CreateKeyEx(
                hive_handle(root), key, 0, winreg.KEY_WRITE | self._view_flag()
            ).Close()
        except OSError as exc:
            raise TargetError(f"cannot create {root.hive_name}\\{key}: {exc}") from exc
        return True

    def remove_key(self, root: RegistryRoot, key: str) -> bool:
        winreg = _winreg()
        if not self.key_exists(root, key):
            return False
        for child in self._subkeys(root, key):
            self.remove_key(root, f"{key}\\{child}")
        try:
            winreg.DeleteKeyEx(hive_handle(root), key, self._view_flag(), 0)
        except OSError as exc:
            raise TargetError(f"cannot remove {root.hive_name}\\{key}: {exc}") from exc
        return True

    def _subkeys(self, root: RegistryRoot, key: str) -> list[str]:
        winreg = _winreg()
        names: list[str] = []
        try:
            with winreg.OpenKey(
                hive_handle(root), key, 0, winreg.KEY_READ | self._view_flag()
            ) as handle:
                index = 0
                while True:
                    try:
                        names.append(winreg.EnumKey(handle, index))
                    except OSError:
                        break
                    index += 1
        except FileNotFoundError:
            return []
        return names

    def read_value(self, root: RegistryRoot, key: str, name: str | None) -> DecodedValue | None:
        winreg = _winreg()
        try:
            with winreg.OpenKey(
                hive_handle(root), key, 0, winreg.KEY_READ | self._view_flag()
            ) as handle:
                raw, kind = winreg.QueryValueEx(handle, name or "")
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise TargetError(f"cannot read {root.hive_name}\\{key}: {exc}") from exc
        return _decode_registry(raw, kind)

    def write_value(
        self,
        root: RegistryRoot,
        key: str,
        name: str | None,
        value: DecodedValue,
    ) -> bool:
        winreg = _winreg()
        if self.read_value(root, key, name) == value:
            return False
        self.create_key(root, key)
        raw, kind = _encode_registry(value)
        try:
            with winreg.OpenKey(
                hive_handle(root), key, 0, winreg.KEY_WRITE | self._view_flag()
            ) as handle:
                winreg.SetValueEx(handle, name or "", 0, kind, raw)
        except OSError as exc:
            raise TargetError(f"cannot write {root.hive_name}\\{key}: {exc}") from exc
        return True

    def remove_value(self, root: RegistryRoot, key: str, name: str | None) -> bool:
        winreg = _winreg()
        if self.read_value(root, key, name) is None:
            return False
        try:
            with winreg.OpenKey(
                hive_handle(root), key, 0, winreg.KEY_WRITE | self._view_flag()
            ) as handle:
                winreg.DeleteValue(handle, name or "")
        except OSError as exc:
            raise TargetError(f"cannot remove value from {root.hive_name}\\{key}: {exc}") from exc
        return True

    def list_values(self, root: RegistryRoot, key: str) -> list[str]:
        winreg = _winreg()
        names: list[str] = []
        try:
            with winreg.OpenKey(
                hive_handle(root), key, 0, winreg.KEY_READ | self._view_flag()
            ) as handle:
                index = 0
                while True:
                    try:
                        name, _, _ = winreg.EnumValue(handle, index)
                    except OSError:
                        break
                    names.append(name)
                    index += 1
        except FileNotFoundError:
            return []
        return sorted(names)

    # COM

    def is_com_registered(self, path: str) -> bool:
        """Whether a server looks registered.

        There is no API that answers this for an arbitrary DLL, so the check is
        on the InprocServer32 path appearing under a CLSID. That is a heuristic
        and reconcile treats a negative as "unknown" rather than "missing".
        """
        winreg = _winreg()
        target = str(PureWindowsPath(path)).lower()
        try:
            with winreg.OpenKey(
                winreg.HKEY_CLASSES_ROOT, "CLSID", 0, winreg.KEY_READ | self._view_flag()
            ) as root:
                index = 0
                while True:
                    try:
                        clsid = winreg.EnumKey(root, index)
                    except OSError:
                        break
                    index += 1
                    server = self.read_value(
                        RegistryRoot.CLASSES_ROOT, f"CLSID\\{clsid}\\InprocServer32", None
                    )
                    if server is not None and str(server.data).lower() == target:
                        return True
        except OSError:
            return False
        return False

    def register_com(self, path: str) -> bool:
        require_windows("COM registration")
        if self.is_com_registered(path):
            return False
        self._run([REGSVR32, "/s", path], f"register {path}")
        return True

    def unregister_com(self, path: str) -> bool:
        require_windows("COM registration")
        if not self.is_com_registered(path):
            return False
        self._run([REGSVR32, "/s", "/u", path], f"unregister {path}")
        return True

    # Side by side store

    def _assembly_dir(self, identity: AssemblyIdentity, policy: bool) -> Path:
        import os

        root = Path(os.environ.get("SYSTEMROOT", "C:\\Windows")) / "WinSxS"
        prefix = "policy" if policy else "assembly"
        name = (
            f"{identity.processor_architecture}_{prefix}_{identity.name}_"
            f"{identity.public_key_token}_{identity.version}"
        )
        return root / name

    def has_assembly(self, identity: AssemblyIdentity) -> bool:
        return self._assembly_dir(identity, policy=False).is_dir()

    def install_assembly(self, identity: AssemblyIdentity, source: str) -> bool:
        if self.has_assembly(identity):
            return False
        destination = self._assembly_dir(identity, policy=False)
        shutil.copytree(source, destination)
        return True

    def remove_assembly(self, identity: AssemblyIdentity) -> bool:
        return remove_path(self._assembly_dir(identity, policy=False))

    def has_policy(self, identity: AssemblyIdentity) -> bool:
        return self._assembly_dir(identity, policy=True).is_dir()

    def install_policy(self, identity: AssemblyIdentity, source: str) -> bool:
        if not self.has_assembly(identity):
            raise TargetError(f"no assembly for policy {identity.name}")
        if self.has_policy(identity):
            return False
        shutil.copytree(source, self._assembly_dir(identity, policy=True))
        return True

    def remove_policy(self, identity: AssemblyIdentity) -> bool:
        return remove_path(self._assembly_dir(identity, policy=True))

    # Driver store

    def has_driver(self, inf_name: str) -> bool:
        require_windows("driver store access")
        result = self._run([PNPUTIL, "/enum-drivers"], "enumerate drivers", check=False)
        return inf_name.lower() in result.stdout.lower()

    def stage_driver(self, source: str, inf_name: str) -> bool:
        require_windows("driver staging")
        if self.has_driver(inf_name):
            return False
        self._run([PNPUTIL, "/add-driver", str(Path(source) / inf_name)], f"stage {inf_name}")
        return True

    def remove_driver(self, inf_name: str) -> bool:
        require_windows("driver removal")
        if not self.has_driver(inf_name):
            return False
        self._run([PNPUTIL, "/delete-driver", inf_name], f"remove {inf_name}")
        return True

    # Controller

    def controller_revision(self) -> str | None:
        value = self.read_value(
            RegistryRoot.LOCAL_MACHINE, r"Software\Matricode\V4T", "FpgaRevision"
        )
        return None if value is None else str(value.data)

    def program_controller(self, source: str, revision: str) -> bool:
        raise TargetError(
            "programming the controller needs the qu_imager bridge, which this "
            "build does not drive. Use the front end's service page."
        )

    # Fonts

    def _font_dir(self) -> Path:
        import os

        return Path(os.environ.get("SYSTEMROOT", "C:\\Windows")) / "Fonts"

    def has_font(self, name: str) -> bool:
        return name in self._installed_fonts()

    def install_font(self, source: str, name: str) -> bool:
        if self.has_font(name):
            return False
        destination = self._font_dir() / Path(source).name
        shutil.copyfile(source, destination)
        self.write_value(
            RegistryRoot.LOCAL_MACHINE,
            r"Software\Microsoft\Windows NT\CurrentVersion\Fonts",
            f"{name} (TrueType)",
            DecodedValue(RegistryValueType.STRING, destination.name),
        )
        return True

    def remove_font(self, name: str) -> bool:
        if not self.has_font(name):
            return False
        return self.remove_value(
            RegistryRoot.LOCAL_MACHINE,
            r"Software\Microsoft\Windows NT\CurrentVersion\Fonts",
            f"{name} (TrueType)",
        )

    # Shortcuts

    def _shortcut_path(self, spec: ShortcutSpec) -> Path:
        return self._local(spec.directory) / f"{spec.name}.lnk"

    def has_shortcut(self, spec: ShortcutSpec) -> bool:
        return self._shortcut_path(spec).is_file()

    def create_shortcut(self, spec: ShortcutSpec) -> bool:
        require_windows("shortcut creation")
        if self.has_shortcut(spec):
            return False
        path = self._shortcut_path(spec)
        ensure_dir(path.parent)
        workdir = spec.working_directory or str(PureWindowsPath(spec.target).parent)
        script = (
            f"$s = (New-Object -ComObject WScript.Shell).CreateShortcut('{path}');"
            f"$s.TargetPath = '{spec.target}';"
            f"$s.WorkingDirectory = '{workdir}';"
            f"$s.Description = '{spec.description}';"
            "$s.Save()"
        )
        self._run(["powershell.exe", "-NoProfile", "-Command", script], f"create {path}")
        return True

    def remove_shortcut(self, spec: ShortcutSpec) -> bool:
        return remove_path(self._shortcut_path(spec))

    # Processes

    def run_installer(self, path: str, arguments: list[str]) -> int:
        require_windows("running an installer")
        result = self._run([path, *arguments], f"run {path}", check=False)
        return result.returncode

    def _run(
        self,
        command: list[str],
        description: str,
        *,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=COMMAND_TIMEOUT_SECONDS,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise TargetError(f"cannot {description}: {exc}") from exc
        if check and result.returncode != 0:
            raise TargetError(
                f"cannot {description}: exit {result.returncode} {result.stderr.strip()}"
            )
        return result


def _decode_registry(raw: object, kind: int) -> DecodedValue:
    winreg = _winreg()
    if kind == winreg.REG_DWORD:
        return DecodedValue(RegistryValueType.DWORD, int(raw))  # type: ignore[arg-type]
    if kind == winreg.REG_BINARY:
        return DecodedValue(RegistryValueType.BINARY, bytes(raw))  # type: ignore[arg-type]
    if kind == winreg.REG_EXPAND_SZ:
        return DecodedValue(RegistryValueType.EXPAND_STRING, str(raw))
    if kind == winreg.REG_MULTI_SZ:
        return DecodedValue(RegistryValueType.MULTI_STRING, tuple(raw))  # type: ignore[arg-type]
    return DecodedValue(RegistryValueType.STRING, str(raw))


def _encode_registry(value: DecodedValue) -> tuple[object, int]:
    winreg = _winreg()
    mapping = {
        RegistryValueType.DWORD: winreg.REG_DWORD,
        RegistryValueType.BINARY: winreg.REG_BINARY,
        RegistryValueType.EXPAND_STRING: winreg.REG_EXPAND_SZ,
        RegistryValueType.MULTI_STRING: winreg.REG_MULTI_SZ,
        RegistryValueType.STRING: winreg.REG_SZ,
    }
    data = list(value.data) if value.type is RegistryValueType.MULTI_STRING else value.data
    return data, mapping[value.type]


def value_type_name(value: DecodedValue) -> str:
    """The ``REG_*`` name for a value, for reports that run off a bench."""
    return _VALUE_TYPE_NAMES[value.type]


__all__ = [
    "COMMAND_TIMEOUT_SECONDS",
    "IS_WINDOWS",
    "PNPUTIL",
    "REGSVR32",
    "WindowsTarget",
    "hive_handle",
    "require_windows",
    "value_type_name",
]
