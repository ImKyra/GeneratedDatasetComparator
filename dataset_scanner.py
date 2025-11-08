import os
import re
import difflib
from pathlib import Path
from typing import Dict, List, Optional, Tuple

try:
    from PIL import Image
except Exception:
    raise SystemExit("Pillow is required. Please install with: pip install Pillow")

from items import OriginalItem, GeneratedItem

SUPPORTED_IMG_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
GENERATED_IMG_EXTS = {".png"}


class DatasetScanner:
    def __init__(self) -> None:
        pass

    @staticmethod
    def _normalize_prompt(text: Optional[str]) -> str:
        if not text:
            return ""
        s = text.strip()
        if s.lower().startswith("parameters "):
            s = s[len("parameters "):]
        s = re.sub(r"<[^>]+>", " ", s)
        s = s.replace("\r", " \n ").replace("\n", " ")
        s = re.sub(r"\s+", " ", s)
        s = s.lower().strip()
        return s

    @staticmethod
    def read_text_file(path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8").strip()
        except UnicodeDecodeError:
            return path.read_text(errors="ignore").strip()
        except FileNotFoundError:
            return ""

    @staticmethod
    def load_image_metadata_prompt_png(path: Path) -> Optional[str]:
        try:
            with Image.open(path) as im:
                info = getattr(im, "info", {}) or {}
        except Exception:
            return None

        for key in ("prompt", "Prompt"):
            if key in info and isinstance(info[key], str) and info[key].strip():
                return info[key].strip()

        params = info.get("parameters") or info.get("Parameters")
        if isinstance(params, str) and params.strip():
            text = params.strip()
            neg_idx = text.find("\nNegative prompt:")
            if neg_idx != -1:
                text = text[:neg_idx]
            for marker in ["\nSteps:", "\nSampler:", "\nCFG scale:", "\nSeed:", "\nModel:"]:
                m = text.find(marker)
                if m != -1:
                    text = text[:m]
            return text.strip() if text.strip() else None
        return None

    def scan_original_dataset(self, root: Path) -> List[OriginalItem]:
        items: List[OriginalItem] = []
        if not root.exists():
            return items
        for dirpath, _, filenames in os.walk(root):
            images_by_stem: Dict[str, Path] = {}
            for fname in filenames:
                ext = Path(fname).suffix.lower()
                if ext in SUPPORTED_IMG_EXTS:
                    stem = Path(fname).stem
                    priority = {".png": 0, ".jpg": 1, ".jpeg": 2, ".webp": 3}.get(ext, 9)
                    prev = images_by_stem.get(stem)
                    if prev is None or {".png": 0, ".jpg": 1, ".jpeg": 2, ".webp": 3}.get(prev.suffix.lower(), 9) > priority:
                        images_by_stem[stem] = Path(dirpath) / fname
            for stem, img_path in images_by_stem.items():
                txt_path = Path(dirpath) / f"{stem}.txt"
                prompt = self.read_text_file(txt_path)
                items.append(OriginalItem(image_path=img_path, prompt_path=txt_path, prompt_text=prompt))
        items.sort(key=lambda it: str(it.image_path).lower())
        return items

    def scan_generated_dataset(self, root: Path) -> List[GeneratedItem]:
        items: List[GeneratedItem] = []
        if not root.exists():
            return items
        for dirpath, _, filenames in os.walk(root):
            for fname in filenames:
                ext = Path(fname).suffix.lower()
                if ext in GENERATED_IMG_EXTS:
                    p = Path(dirpath) / fname
                    prompt = self.load_image_metadata_prompt_png(p)
                    items.append(GeneratedItem(image_path=p, prompt_text=prompt))
        items.sort(key=lambda it: str(it.image_path).lower())
        return items

    def match_generated_to_original(self, originals: List[OriginalItem], generated: List[GeneratedItem]) -> Dict[
        Path, List[GeneratedItem]]:
        gen_by_stem: Dict[str, List[GeneratedItem]] = {}
        for g in generated:
            stem = g.image_path.stem.lower()
            if stem not in gen_by_stem:
                gen_by_stem[stem] = []
            gen_by_stem[stem].append(g)

        gen_norm: List[Tuple[GeneratedItem, str]] = []
        for g in generated:
            norm = self._normalize_prompt(g.prompt_text)
            if norm:
                gen_norm.append((g, norm))

        mapping: Dict[Path, List[GeneratedItem]] = {}
        for o in originals:
            o_stem = o.image_path.stem.lower()
            filename_matches = gen_by_stem.get(o_stem, [])

            o_norm = self._normalize_prompt(o.prompt_text)
            prompt_exact_matches: List[GeneratedItem] = []
            fuzzy_matches: List[Tuple[float, GeneratedItem]] = []

            if o_norm:
                for g, g_norm in gen_norm:
                    if g in filename_matches:
                        continue

                    if o_norm == g_norm or o_norm in g_norm or g_norm in o_norm:
                        prompt_exact_matches.append(g)
                        continue

                    len_diff = abs(len(o_norm) - len(g_norm)) / max(len(o_norm), len(g_norm), 1)
                    if len_diff > 0.5:
                        continue

                    o_words = set(o_norm.split())
                    g_words = set(g_norm.split())
                    if not o_words or not g_words:
                        continue

                    word_overlap = len(o_words & g_words) / len(o_words | g_words)
                    if word_overlap < 0.3:
                        continue

                    score = difflib.SequenceMatcher(None, o_norm, g_norm).ratio()
                    if score >= 0.85:
                        fuzzy_matches.append((score, g))

            result = filename_matches[:]
            result.extend(prompt_exact_matches)
            fuzzy_matches.sort(key=lambda x: (-x[0], x[1].image_path.name.lower()))
            result.extend([g for _, g in fuzzy_matches])

            mapping[o.image_path] = result

        return mapping
