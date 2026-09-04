from __future__ import annotations

import sys


def select_main() -> int:
    if "--gui" in sys.argv or len(sys.argv) == 1:
        if "--gui" in sys.argv:
            sys.argv.remove("--gui")
        return run_gui()

    if "--cli" in sys.argv:
        sys.argv.remove("--cli")

    from translator_app.cli import main as cli_main

    return cli_main()


def run_gui() -> int:
    try:
        from translator_app.gui import main as gui_main
    except ModuleNotFoundError as exc:
        if exc.name == "tkinter":
            print(
                "tkinter is not installed. On Windows it is usually included "
                "with Python. On Ubuntu, install it with `sudo apt install python3-tk`."
            )
            return 1
        raise

    return gui_main()


if __name__ == "__main__":
    raise SystemExit(select_main())
