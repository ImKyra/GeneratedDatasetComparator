from pathlib import Path
from typing import Dict, Optional, Tuple

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap, QImage

try:
    from PIL import Image
except Exception:
    raise SystemExit("Pillow is required. Please install with: pip install Pillow")

DEFAULT_MAX_SIZE = (1200, 1200)
MAX_CACHE_SIZE = 15


class ImageLoader:
    def __init__(self, max_size: Tuple[int, int] = DEFAULT_MAX_SIZE):
        self.max_size = max_size
        self.cache: Dict[Tuple[Path, Tuple[int, int]], QPixmap] = {}
        self.max_cache_size = MAX_CACHE_SIZE

    def clear_cache(self) -> None:
        self.cache.clear()

    def load_for_display(self, path: Path, target_size: Tuple[int, int]) -> Optional[QPixmap]:
        key = (path, target_size)
        if key in self.cache:
            return self.cache[key]

        if len(self.cache) >= self.max_cache_size:
            self.cache.clear()

        try:
            with Image.open(path) as pil_image:
                if pil_image.mode in ('RGBA', 'LA'):
                    background = Image.new('RGB', pil_image.size, (255, 255, 255))
                    background.paste(pil_image, mask=pil_image.split()[-1])
                    pil_image = background
                elif pil_image.mode != 'RGB':
                    pil_image = pil_image.convert('RGB')

                pil_image = pil_image.copy()
                pil_image.thumbnail(target_size, Image.Resampling.LANCZOS)

                width, height = pil_image.size
                qimg = QImage(
                    pil_image.tobytes(),
                    width,
                    height,
                    width * 3,
                    QImage.Format_RGB888
                )
                pix = QPixmap.fromImage(qimg.copy())

                self.cache[key] = pix
                return pix

        except Exception as e:
            print(f"Error loading image {path}: {e}")
            placeholder = QPixmap(100, 100)
            placeholder.fill(Qt.lightGray)
            return placeholder
