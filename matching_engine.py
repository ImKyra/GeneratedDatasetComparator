import difflib
from typing import List, Tuple

from items import OriginalItem, GeneratedItem
from dataset_scanner import DatasetScanner


class MatchingEngine:
    def __init__(self, scanner: DatasetScanner):
        self.scanner = scanner

    def match_single_item(self, item: OriginalItem, generated_items: List[GeneratedItem]) -> List[GeneratedItem]:
        o_stem = item.image_path.stem.lower()

        filename_matches: List[GeneratedItem] = []
        for g in generated_items:
            if g.image_path.stem.lower() == o_stem:
                filename_matches.append(g)

        gen_norm: List[Tuple[GeneratedItem, str]] = []
        for g in generated_items:
            norm = self.scanner._normalize_prompt(g.prompt_text)
            if norm:
                gen_norm.append((g, norm))

        o_norm = self.scanner._normalize_prompt(item.prompt_text)
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

        return result
