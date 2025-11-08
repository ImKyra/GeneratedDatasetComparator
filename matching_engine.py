import difflib
from typing import List, Tuple, Dict, Set, Optional, Callable
from collections import defaultdict
from pathlib import Path

from items import OriginalItem, GeneratedItem
from dataset_scanner import DatasetScanner


class MatchingEngine:
    def __init__(self, scanner: DatasetScanner):
        self.scanner = scanner
        # Pre-computed indices for faster lookups - use Path as key instead of GeneratedItem
        self._filename_index: Dict[str, List[GeneratedItem]] = {}
        self._normalized_prompts: Dict[Path, str] = {}  # Use Path as key
        self._prompt_words: Dict[Path, Set[str]] = {}   # Use Path as key
        self._indexed_items: List[GeneratedItem] = []

    def _build_indices(self, generated_items: List[GeneratedItem],
                       progress_callback: Optional[Callable[[int, int, str], bool]] = None):
        """Pre-build indices for faster matching operations."""
        if self._indexed_items == generated_items:
            return True  # Already indexed

        self._filename_index.clear()
        self._normalized_prompts.clear()
        self._prompt_words.clear()

        total_items = len(generated_items)

        # Build filename index
        for i, item in enumerate(generated_items):
            if progress_callback and not progress_callback(i, total_items,
                                                           f"Building filename index... ({i}/{total_items})"):
                return False  # User cancelled

            stem = item.image_path.stem.lower()
            if stem not in self._filename_index:
                self._filename_index[stem] = []
            self._filename_index[stem].append(item)

        # Pre-normalize all prompts and extract word sets
        for i, item in enumerate(generated_items):
            if progress_callback and not progress_callback(i, total_items,
                                                           f"Pre-processing prompts... ({i}/{total_items})"):
                return False  # User cancelled

            normalized = self.scanner._normalize_prompt(item.prompt_text)
            if normalized:
                self._normalized_prompts[item.image_path] = normalized  # Use path as key
                self._prompt_words[item.image_path] = set(normalized.split())  # Use path as key

        self._indexed_items = generated_items[:]
        return True

    def match_all_items(self, original_items: List[OriginalItem], generated_items: List[GeneratedItem],
                        progress_callback: Optional[Callable[[int, int, str], bool]] = None) -> Optional[Dict]:
        """Match all original items to generated items with progress reporting."""

        # Build indices first
        if progress_callback and not progress_callback(0, len(original_items), "Building search indices..."):
            return None

        if not self._build_indices(generated_items, progress_callback):
            return None  # User cancelled during indexing

        matches = {}
        total_items = len(original_items)

        for i, original_item in enumerate(original_items):
            if progress_callback:
                progress_text = f"Matching item {i + 1}/{total_items}: {original_item.image_path.name}"
                if not progress_callback(i, total_items, progress_text):
                    return None  # User cancelled

            matched_items = self.match_single_item(original_item, generated_items)
            matches[original_item.image_path] = matched_items

        # Final progress update
        if progress_callback:
            progress_callback(total_items, total_items, f"Matching complete! Found matches for {len(matches)} items.")

        return matches

    def match_single_item(self, item: OriginalItem, generated_items: List[GeneratedItem]) -> List[GeneratedItem]:
        # Build indices if not already built or items changed
        self._build_indices(generated_items)

        o_stem = item.image_path.stem.lower()
        o_norm = self.scanner._normalize_prompt(item.prompt_text)

        # Fast filename lookup using index
        filename_matches = self._filename_index.get(o_stem, [])
        # Use set of paths for fast membership checking
        filename_match_paths = {match.image_path for match in filename_matches}

        prompt_exact_matches: List[GeneratedItem] = []
        fuzzy_candidates: List[GeneratedItem] = []

        if o_norm:
            o_words = set(o_norm.split())
            o_len = len(o_norm)

            # Pre-filter candidates to avoid expensive operations
            for item_g in generated_items:
                # Check if this item is already in filename matches by comparing paths
                if item_g.image_path in filename_match_paths:
                    continue

                g_norm = self._normalized_prompts.get(item_g.image_path)  # Use path as key
                if not g_norm:
                    continue

                # Quick length check first (cheapest operation)
                g_len = len(g_norm)
                len_diff = abs(o_len - g_len) / max(o_len, g_len, 1)
                if len_diff > 0.5:
                    continue

                # Exact match checks (cheap string operations)
                if o_norm == g_norm or o_norm in g_norm or g_norm in o_norm:
                    prompt_exact_matches.append(item_g)
                    continue

                # Word overlap check using pre-computed sets
                g_words = self._prompt_words.get(item_g.image_path, set())  # Use path as key
                if not o_words or not g_words:
                    continue

                word_overlap = len(o_words & g_words) / len(o_words | g_words)
                if word_overlap >= 0.3:
                    fuzzy_candidates.append(item_g)

        # Only run expensive fuzzy matching on pre-filtered candidates
        fuzzy_matches: List[Tuple[float, GeneratedItem]] = []
        for candidate in fuzzy_candidates:
            g_norm = self._normalized_prompts[candidate.image_path]  # Use path as key
            score = difflib.SequenceMatcher(None, o_norm, g_norm).ratio()
            if score >= 0.85:
                fuzzy_matches.append((score, candidate))

        # Combine results
        result = filename_matches[:]
        result.extend(prompt_exact_matches)
        fuzzy_matches.sort(key=lambda x: (-x[0], x[1].image_path.name.lower()))
        result.extend([g for _, g in fuzzy_matches])

        return result