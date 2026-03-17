import difflib
from typing import List, Tuple, Dict, Set, Optional, Callable
from collections import defaultdict
from pathlib import Path

from items import OriginalItem, GeneratedItem
from dataset_scanner import DatasetScanner

LENGTH_DIFF_THRESHOLD = 0.5
WORD_OVERLAP_THRESHOLD = 0.3
FUZZY_SCORE_THRESHOLD = 0.85


class MatchingEngine:
    def __init__(self, scanner: DatasetScanner):
        self.scanner = scanner
        self._filename_index: Dict[str, List[GeneratedItem]] = {}
        self._normalized_prompts: Dict[Path, str] = {}
        self._prompt_words: Dict[Path, Set[str]] = {}
        self._indexed_items: List[GeneratedItem] = []

    def _build_indices(self, generated_items: List[GeneratedItem],
                       progress_callback: Optional[Callable[[int, int, str], bool]] = None):
        if self._indexed_items == generated_items:
            return True

        self._filename_index.clear()
        self._normalized_prompts.clear()
        self._prompt_words.clear()

        total_items = len(generated_items)

        for i, item in enumerate(generated_items):
            if progress_callback and not progress_callback(i, total_items,
                                                           f"Building filename index... ({i}/{total_items})"):
                return False

            stem = item.image_path.stem.lower()
            if stem not in self._filename_index:
                self._filename_index[stem] = []
            self._filename_index[stem].append(item)

        for i, item in enumerate(generated_items):
            if progress_callback and not progress_callback(i, total_items,
                                                           f"Pre-processing prompts... ({i}/{total_items})"):
                return False

            normalized = self.scanner._normalize_prompt(item.prompt_text)
            if normalized:
                self._normalized_prompts[item.image_path] = normalized
                self._prompt_words[item.image_path] = set(normalized.split())

        self._indexed_items = generated_items[:]
        return True

    def match_all_items(self, original_items: List[OriginalItem], generated_items: List[GeneratedItem],
                        progress_callback: Optional[Callable[[int, int, str], bool]] = None) -> Optional[Dict]:
        if progress_callback and not progress_callback(0, len(original_items), "Building search indices..."):
            return None

        if not self._build_indices(generated_items, progress_callback):
            return None

        matches = {}
        total_items = len(original_items)

        for i, original_item in enumerate(original_items):
            if progress_callback:
                progress_text = f"Matching item {i + 1}/{total_items}: {original_item.image_path.name}"
                if not progress_callback(i, total_items, progress_text):
                    return None

            matched_items = self.match_single_item(original_item, generated_items)
            matches[original_item.image_path] = matched_items

        if progress_callback:
            progress_callback(total_items, total_items, f"Matching complete! Found matches for {len(matches)} items.")

        return matches

    def match_single_item(self, item: OriginalItem, generated_items: List[GeneratedItem]) -> List[GeneratedItem]:
        self._build_indices(generated_items)

        o_stem = item.image_path.stem.lower()
        o_norm = self.scanner._normalize_prompt(item.prompt_text)

        filename_matches = self._filename_index.get(o_stem, [])
        filename_match_paths = {match.image_path for match in filename_matches}

        prompt_exact_matches: List[GeneratedItem] = []
        fuzzy_candidates: List[GeneratedItem] = []

        if o_norm:
            o_words = set(o_norm.split())
            o_len = len(o_norm)

            for item_g in generated_items:
                if item_g.image_path in filename_match_paths:
                    continue

                g_norm = self._normalized_prompts.get(item_g.image_path)
                if not g_norm:
                    continue

                g_len = len(g_norm)
                len_diff = abs(o_len - g_len) / max(o_len, g_len, 1)
                if len_diff > LENGTH_DIFF_THRESHOLD:
                    continue

                if o_norm == g_norm or o_norm in g_norm or g_norm in o_norm:
                    prompt_exact_matches.append(item_g)
                    continue

                g_words = self._prompt_words.get(item_g.image_path, set())
                if not o_words or not g_words:
                    continue

                word_overlap = len(o_words & g_words) / len(o_words | g_words)
                if word_overlap >= WORD_OVERLAP_THRESHOLD:
                    fuzzy_candidates.append(item_g)

        fuzzy_matches: List[Tuple[float, GeneratedItem]] = []
        for candidate in fuzzy_candidates:
            g_norm = self._normalized_prompts.get(candidate.image_path)
            if not g_norm:
                continue
            score = difflib.SequenceMatcher(None, o_norm, g_norm).ratio()
            if score >= FUZZY_SCORE_THRESHOLD:
                fuzzy_matches.append((score, candidate))

        result = filename_matches[:]
        result.extend(prompt_exact_matches)
        fuzzy_matches.sort(key=lambda x: (-x[0], x[1].image_path.name.lower()))
        result.extend([g for _, g in fuzzy_matches])

        return result