"""Panel registry backed by a YAML file that both humans and the UI edit.

Panels are keyed by IP. Writes are atomic and preserve comments.
"""
import ipaddress
import os
import tempfile
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq

_yaml = YAML()
_yaml.indent(mapping=2, sequence=4, offset=2)

DEFAULT_DOC = """\
# SensorIQ panels to monitor. Edit by hand or from the /panels page.
#   name:         display name
#   ip:           panel IP address (read-only HTTP GETs only)
#   room:         where the panel physically is
#   grist_panel:  EIS panel number in Grist (Panel_Schedule_Export.Panel), e.g. 12 -> circuits "1201".."1248"
#   slot_offset:  circuit number = slot + slot_offset (normally 0)
#   enabled:      false to stop polling without deleting
panels: []
"""


class RegistryError(ValueError):
    pass


@dataclass
class Panel:
    name: str
    ip: str
    room: str = ""
    grist_panel: int | None = None
    slot_offset: int = 0
    enabled: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def validate(data: dict[str, Any]) -> Panel:
    name = str(data.get("name") or "").strip()
    ip = str(data.get("ip") or "").strip()
    if not name:
        raise RegistryError("name is required")
    try:
        ipaddress.ip_address(ip)
    except ValueError:
        raise RegistryError(f"invalid IP address: {ip!r}") from None
    gp = data.get("grist_panel")
    if gp in ("", None):
        gp = None
    else:
        try:
            gp = int(gp)
        except (TypeError, ValueError):
            raise RegistryError(f"grist_panel must be a number: {gp!r}") from None
        if not 1 <= gp <= 99:
            raise RegistryError("grist_panel must be 1-99")
    try:
        offset = int(data.get("slot_offset") or 0)
    except (TypeError, ValueError):
        raise RegistryError("slot_offset must be a number") from None
    enabled = data.get("enabled", True)
    if isinstance(enabled, str):
        enabled = enabled.strip().lower() not in ("false", "0", "no", "off")
    return Panel(name=name, ip=ip, room=str(data.get("room") or "").strip(),
                 grist_panel=gp, slot_offset=offset, enabled=bool(enabled))


class Registry:
    def __init__(self, path: str | os.PathLike):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._doc: CommentedMap | None = None
        self._mtime: float | None = None
        self.panels: list[Panel] = []
        self.load_error: str | None = None
        if not self.path.exists():
            self.path.write_text(DEFAULT_DOC, encoding="utf-8")
        self.reload()

    # ---- reading -------------------------------------------------------
    def reload(self) -> bool:
        """(Re)load the file. Returns True if the panel list changed."""
        with self._lock:
            try:
                doc = _yaml.load(self.path.read_text(encoding="utf-8")) or CommentedMap()
                if not isinstance(doc, dict):
                    raise RegistryError("top level must be a mapping with a 'panels' list")
                items = doc.get("panels") or []
                panels = [validate(dict(p)) for p in items]
                self._check_unique(panels)
            except Exception as e:  # keep last good config on a bad hand edit
                self.load_error = f"{self.path}: {e}"
                self._mtime = self.path.stat().st_mtime
                return False
            self.load_error = None
            if "panels" not in doc or doc["panels"] is None:
                doc["panels"] = CommentedSeq()
            self._doc = doc
            self._mtime = self.path.stat().st_mtime
            changed = panels != self.panels
            self.panels = panels
            return changed

    def reload_if_changed(self) -> bool:
        try:
            mtime = self.path.stat().st_mtime
        except FileNotFoundError:
            return False
        if mtime != self._mtime:
            return self.reload()
        return False

    def get(self, ip: str) -> Panel | None:
        return next((p for p in self.panels if p.ip == ip), None)

    @staticmethod
    def _check_unique(panels: list[Panel]) -> None:
        ips, gps = set(), set()
        for p in panels:
            if p.ip in ips:
                raise RegistryError(f"duplicate ip {p.ip}")
            ips.add(p.ip)
            if p.grist_panel is not None:
                if p.grist_panel in gps:
                    raise RegistryError(f"duplicate grist_panel {p.grist_panel}")
                gps.add(p.grist_panel)

    # ---- writing -------------------------------------------------------
    def add(self, data: dict[str, Any]) -> Panel:
        panel = validate(data)
        with self._lock:
            self._check_unique([*self.panels, panel])
            self._doc["panels"].append(self._to_map(panel))
            self._save()
            self.panels.append(panel)
        return panel

    def update(self, ip: str, data: dict[str, Any]) -> Panel:
        with self._lock:
            idx = next((i for i, p in enumerate(self.panels) if p.ip == ip), None)
            if idx is None:
                raise KeyError(ip)
            merged = {**self.panels[idx].to_dict(), **data}
            panel = validate(merged)
            others = [p for i, p in enumerate(self.panels) if i != idx]
            self._check_unique([*others, panel])
            node = self._doc["panels"][idx]
            for k, v in self._to_map(panel).items():
                node[k] = v
            self._save()
            self.panels[idx] = panel
        return panel

    def remove(self, ip: str) -> None:
        with self._lock:
            idx = next((i for i, p in enumerate(self.panels) if p.ip == ip), None)
            if idx is None:
                raise KeyError(ip)
            del self._doc["panels"][idx]
            self._save()
            del self.panels[idx]

    @staticmethod
    def _to_map(p: Panel) -> CommentedMap:
        m = CommentedMap()
        for k, v in p.to_dict().items():
            m[k] = v
        return m

    def _save(self) -> None:
        if self.load_error:
            raise RegistryError(f"fix the config file before editing from the UI: {self.load_error}")
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".panels-", suffix=".yaml")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                _yaml.dump(self._doc, f)
            os.replace(tmp, self.path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
        self._mtime = self.path.stat().st_mtime
