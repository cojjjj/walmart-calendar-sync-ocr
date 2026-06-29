from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if "--gui" in args:
        args.remove("--gui")
        from walmart_calendar_sync.gui import main as gui_main

        return gui_main()

    from walmart_calendar_sync.cli import main as cli_main

    return cli_main(args)


if __name__ == "__main__":
    raise SystemExit(main())
