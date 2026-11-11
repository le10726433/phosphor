"""Start phosphor: python app.py for the Qt editor, python app.py --cpu for the Tk fallback."""
import sys

if __name__ == '__main__':
    if '--cpu' in sys.argv:
        sys.argv.remove('--cpu')
        from phosphor.legacy_tk.tk_app import main
    else:
        from phosphor.editor.qt_app import main
    raise SystemExit(main())
