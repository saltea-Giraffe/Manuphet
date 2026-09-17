"""
scripts/make_icons.py  –  アプリアイコンの生成

assets/Manuphet_icon.png（原画）から次のファイルを作る:
  assets/manuphet.ico     Windows 用（exe・ショートカット・ウィンドウ・favicon）
  assets/manuphet.png     256px（Web のアイコン）
  assets/manuphet_64.png  64px（Web 画面ヘッダーのロゴ）

使い方（project/ で実行、Pillow が必要）:
  python scripts/make_icons.py [原画のパス]
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
SOURCE = ASSETS / "Manuphet_icon.png"
ICO_SIZES = [16, 24, 32, 48, 64, 128, 256]


def main() -> int:
    ASSETS.mkdir(parents=True, exist_ok=True)
    if len(sys.argv) > 1:
        src = Path(sys.argv[1])
        if src.resolve() != SOURCE.resolve():
            shutil.copyfile(src, SOURCE)

    img = Image.open(SOURCE).convert("RGBA")
    side = min(img.size)
    left = (img.width - side) // 2
    top = (img.height - side) // 2
    img = img.crop((left, top, left + side, top + side))

    base = img.resize((256, 256), Image.Resampling.LANCZOS)
    base.save(ASSETS / "manuphet.png", optimize=True)
    img.resize((64, 64), Image.Resampling.LANCZOS).save(ASSETS / "manuphet_64.png", optimize=True)
    base.save(ASSETS / "manuphet.ico", sizes=[(s, s) for s in ICO_SIZES])

    for name in ("manuphet.ico", "manuphet.png", "manuphet_64.png"):
        print(f"{name}: {(ASSETS / name).stat().st_size:,} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
