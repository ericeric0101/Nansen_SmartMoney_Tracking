from __future__ import annotations

from .cli.app import app


def main() -> None:
    """CLI 入口點。"""

    app()


if __name__ == "__main__":
    main()
