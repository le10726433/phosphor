"""Start phosphor: python app.py opens the Qt editor."""
from phosphor.storage import crash_log

crash_log.start()  # Source runs log only with PHOSPHOR_CRASH_LOG=1; before Qt loads.
from phosphor.editor.qt_app import main  # noqa: E402

if __name__ == '__main__':
    raise SystemExit(main())
