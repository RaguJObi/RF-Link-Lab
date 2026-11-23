"""Separate read-only application resources from writable user sessions."""
from __future__ import annotations
import os
from pathlib import Path
import sys


def resource_root() -> Path:
    explicit=os.environ.get('RF_LINK_LAB_RESOURCES')
    if explicit:return Path(explicit).expanduser().resolve()
    if getattr(sys,'frozen',False):return Path(sys._MEIPASS)/'resources'
    return Path(__file__).resolve().parents[2]


def state_root() -> Path:
    explicit=os.environ.get('RF_LINK_LAB_HOME')
    if explicit:return Path(explicit).expanduser().resolve()
    source=Path(__file__).resolve().parents[2]
    if not getattr(sys,'frozen',False) and (source/'pyproject.toml').is_file():return source
    if sys.platform=='win32':return Path(os.environ.get('LOCALAPPDATA',str(Path.home()/'AppData/Local')))/'RFLinkLab'
    return Path(os.environ.get('XDG_DATA_HOME',str(Path.home()/'.local/share')))/'rf-link-lab'
