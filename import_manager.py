"""Manages importing generated images from external sources."""

import difflib
import shutil
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from PySide6.QtWidgets import QApplication, QMessageBox, QStatusBar

from items import OriginalItem, GeneratedItem
from dataset_scanner import DatasetScanner, GENERATED_IMG_EXTS


class ImportManager:
    """Handles importing generated images and matching them to original dataset items."""

    def __init__(self, scanner: DatasetScanner, status_bar: QStatusBar):
        """
        Initialize the import manager.

        Args:
            scanner: DatasetScanner instance for loading metadata
            status_bar: Status bar for displaying progress messages
        """
        self.scanner = scanner
        self.status_bar = status_bar

    def import_generated_files(
        self,
        source_dir: Path,
        target_dir: Path,
        original_items: List[OriginalItem]
    ) -> tuple[int, int, int]:
        """
        Import generated files from source to target directory, matching them to originals.

        Args:
            source_dir: Source directory containing generated images
            target_dir: Target directory for imported images
            original_items: List of original dataset items to match against

        Returns:
            Tuple of (imported_count, skipped_count, unmatched_count)
        """
        self.status_bar.showMessage("Importing generated files...")
        QApplication.processEvents()

        # Scan source directory for generated images
        source_items = self._scan_source_directory(source_dir)

        if not source_items:
            return 0, 0, 0

        # Build lookup structures
        orig_by_stem = self._build_stem_lookup(original_items)
        orig_norm_map = self._build_normalized_prompt_map(original_items)

        # Process each generated item
        imported_count = 0
        skipped_count = 0
        unmatched_count = 0

        for gen_item in source_items:
            result = self._import_single_file(
                gen_item,
                target_dir,
                orig_by_stem,
                orig_norm_map,
                original_items
            )

            if result == "imported":
                imported_count += 1
            elif result == "skipped":
                skipped_count += 1
            elif result == "unmatched":
                unmatched_count += 1

        self.status_bar.showMessage(
            f"Import complete: {imported_count} imported, {skipped_count} skipped, {unmatched_count} unmatched",
            5000
        )

        return imported_count, skipped_count, unmatched_count

    def _scan_source_directory(self, source_dir: Path) -> List[GeneratedItem]:
        """Scan source directory and return list of generated items."""
        import os

        source_items: List[GeneratedItem] = []
        for dirpath, _, filenames in os.walk(source_dir):
            for fname in filenames:
                ext = Path(fname).suffix.lower()
                if ext in GENERATED_IMG_EXTS:
                    p = Path(dirpath) / fname
                    prompt = self.scanner.load_image_metadata_prompt_png(p)
                    source_items.append(GeneratedItem(image_path=p, prompt_text=prompt))

        return source_items

    def _build_stem_lookup(self, original_items: List[OriginalItem]) -> Dict[str, OriginalItem]:
        """Build lookup dictionary mapping file stems to original items."""
        orig_by_stem: Dict[str, OriginalItem] = {}
        for orig in original_items:
            stem = orig.image_path.stem.lower()
            orig_by_stem[stem] = orig
        return orig_by_stem

    def _build_normalized_prompt_map(self, original_items: List[OriginalItem]) -> Dict[Path, str]:
        """Build mapping of original image paths to normalized prompts."""
        orig_norm_map: Dict[Path, str] = {}
        for orig in original_items:
            norm = self.scanner._normalize_prompt(orig.prompt_text)
            if norm:
                orig_norm_map[orig.image_path] = norm
        return orig_norm_map

    def _import_single_file(
        self,
        gen_item: GeneratedItem,
        target_dir: Path,
        orig_by_stem: Dict[str, OriginalItem],
        orig_norm_map: Dict[Path, str],
        original_items: List[OriginalItem]
    ) -> str:
        """
        Import a single generated file.

        Returns:
            "imported", "skipped", or "unmatched"
        """
        gen_stem = gen_item.image_path.stem.lower()
        matched_orig = None
        target_name = None

        # Try to match by filename stem first
        if gen_stem in orig_by_stem:
            matched_orig = orig_by_stem[gen_stem]
            target_name = matched_orig.image_path.stem + gen_item.image_path.suffix

        # If no filename match, try to match by prompt metadata
        if not matched_orig and gen_item.prompt_text:
            matched_orig = self._match_by_prompt(
                gen_item,
                original_items,
                orig_norm_map
            )
            if matched_orig:
                target_name = matched_orig.image_path.stem + gen_item.image_path.suffix

        # Skip if no match found
        if not matched_orig or not target_name:
            return "unmatched"

        target_path = target_dir / target_name

        # Check if file already exists
        if target_path.exists():
            if target_path.resolve() == gen_item.image_path.resolve():
                return "skipped"

            # Generate unique filename
            target_name = self._generate_unique_filename(target_dir, target_name)
            target_path = target_dir / target_name

        # Copy the file
        try:
            shutil.copy2(gen_item.image_path, target_path)
            return "imported"
        except Exception as e:
            error_msg = f"Failed to copy {gen_item.image_path.name}: {e}"
            print(error_msg)
            self.status_bar.showMessage(error_msg, 5000)
            return "unmatched"

    def _match_by_prompt(
        self,
        gen_item: GeneratedItem,
        original_items: List[OriginalItem],
        orig_norm_map: Dict[Path, str]
    ) -> Optional[OriginalItem]:
        """Match generated item to original by prompt similarity."""
        gen_norm = self.scanner._normalize_prompt(gen_item.prompt_text)
        if not gen_norm:
            return None

        best_match: Optional[OriginalItem] = None
        best_score = 0.0

        for orig in original_items:
            orig_norm = orig_norm_map.get(orig.image_path)
            if not orig_norm:
                continue

            # Check for exact or substring match
            if gen_norm == orig_norm or gen_norm in orig_norm or orig_norm in gen_norm:
                return orig

            # Check length difference threshold
            len_diff = abs(len(orig_norm) - len(gen_norm)) / max(len(orig_norm), len(gen_norm), 1)
            if len_diff > 0.5:
                continue

            # Check word overlap
            o_words = set(orig_norm.split())
            g_words = set(gen_norm.split())
            if not o_words or not g_words:
                continue

            word_overlap = len(o_words & g_words) / len(o_words | g_words)
            if word_overlap < 0.3:
                continue

            # Calculate similarity score
            score = difflib.SequenceMatcher(None, orig_norm, gen_norm).ratio()
            if score >= 0.85 and score > best_score:
                best_score = score
                best_match = orig

        return best_match

    @staticmethod
    def _generate_unique_filename(target_dir: Path, target_name: str) -> str:
        """Generate a unique filename by appending a counter."""
        base_stem = Path(target_name).stem
        ext = Path(target_name).suffix
        counter = 1

        target_path = target_dir / target_name
        while target_path.exists():
            target_name = f"{base_stem}_{counter}{ext}"
            target_path = target_dir / target_name
            counter += 1

        return target_name

    def create_timestamped_directory(self, dest_folder: Path) -> Optional[Path]:
        """
        Create a timestamped directory if the destination is not empty.

        Args:
            dest_folder: Destination folder path

        Returns:
            Target directory path, or None if creation failed
        """
        if any(dest_folder.iterdir()):
            timestamp = datetime.now().strftime("%y%m%d%H%M%S")
            target_dir = dest_folder / f"generated.{timestamp}"
            try:
                target_dir.mkdir(parents=True, exist_ok=False)
                self.status_bar.showMessage(f"Destination not empty. Created: {target_dir.name}", 3000)
                return target_dir
            except Exception:
                return None
        else:
            return dest_folder
