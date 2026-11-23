"""Frozen Windows entry with a persistent, noninteractive startup error log."""
import os
from pathlib import Path
import sys
import traceback

if __name__=='__main__':
    try:
        from rf_link_lab.cli import main
        raise SystemExit(main())
    except Exception:
        folder=Path(os.environ.get('RF_LINK_LAB_HOME',str(Path.home()/'AppData/Local/RFLinkLab')))
        folder.mkdir(parents=True,exist_ok=True)
        details=traceback.format_exc()
        (folder/'startup-error.log').write_text(details,encoding='utf-8')
        if sys.stderr is not None:sys.stderr.write(details)
        raise SystemExit(1)
