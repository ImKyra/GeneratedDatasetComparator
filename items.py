from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class OriginalItem:
    image_path: Path
    prompt_path: Path
    prompt_text: str


@dataclass
class GeneratedItem:
    image_path: Path
    prompt_text: Optional[str]
