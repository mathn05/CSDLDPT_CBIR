import hashlib
import json
import os
import unittest
import uuid

import psycopg
from psycopg import sql

import cv2
import numpy as np

from flower_cbir.core.distance import compute_overall_distance
from flower_cbir.core.features import extract_all_features
from flower_cbir.core.normalization import SelectiveNormalizer
from flower_cbir.core.preprocess import preprocess_mask, preprocess_query_image
from flower_cbir.core.segmentation import clean_mask, run_grabcut
from flower_cbir.evaluation.experiment import split_dataset
from flower_cbir.services.database import FlowerDatabase, load_database_config
from flower_cbir.services.search import FlowerSearchEngine


def synthetic_index():
    raw = np.zeros((3, 163), dtype=np.float32)
    raw[:, [0, 72, 96, 106]] = 1
    normalizer = SelectiveNormalizer().fit(raw)
    metadata = [{'image_id': identity, 'filename': f'{identity}.png', 'image_path': '', 'mask_path': ''} for identity in [30, 10, 20]]
    return {'raw_features': raw, 'normalized_features': normalizer.transform(raw), 'metadata': metadata, 'normalizer': normalizer, 'version_id': 'test-v1'}


class PipelineTests(unittest.TestCase):
    def test_radial_stays_inside_image(self):
        vector, explanation = extract_all_features(np.zeros((384, 384, 3), np.uint8), np.full((384, 384), 255, np.uint8))
        signal = explanation['f4']['r_signal']
        self.assertEqual(vector.shape, (163,))
        self.assertLess(abs(signal[0] - 192), 1)
        self.assertLess(abs(signal[16] - np.sqrt(2) * 192), 1)
        self.assertGreater(signal.max() - signal.min(), 70)

    def test_largest_component_shared_by_features(self):
        mask = np.zeros((40, 40), np.uint8)
        mask[10:30, 10:30] = 1
        mask[1, 1] = 1
        vector, explanation = extract_all_features(np.full((40, 40, 3), 255, np.uint8), mask)
        self.assertEqual(explanation['geometry']['A'], 400)
        self.assertEqual(int((clean_mask(mask) > 0).sum()), 400)
        for start, end in [(0, 72), (72, 78), (96, 106), (106, 124)]:
            self.assertAlmostEqual(float(vector[start:end].sum()), 1)

    def test_degenerate_masks_rejected(self):
        for mask in [np.zeros((20, 20), np.uint8), np.eye(20, dtype=np.uint8), np.full((20, 20), 7)]:
            with self.subTest(mask=int(mask.sum())), self.assertRaises(ValueError):
                extract_all_features(np.zeros((20, 20, 3), np.uint8), mask)

    def test_crop_and_mask_align(self):
        image = np.zeros((60, 100, 3), np.uint8)
        image[10:50, 30:70] = 255
        mask = (image[:, :, 0] > 0).astype(np.uint8)
        for crop in [None, (20, 5, 60, 50)]:
            processed = preprocess_query_image(image, crop_box=crop)
            processed_mask = preprocess_mask(mask, image.shape[:2], crop_box=crop)
            self.assertLess(np.mean((processed[:, :, 0] > 127) != (processed_mask > 0)), 0.01)

    def test_grabcut_reproducible(self):
        image = np.full((80, 80, 3), 20, np.uint8)
        cv2.circle(image, (40, 40), 24, (220, 40, 80), -1)
        first = run_grabcut(image, rect=(4, 4, 72, 72))[0]
        second = run_grabcut(image, rect=(4, 4, 72, 72))[0]
        np.testing.assert_array_equal(first, second)

    def test_distance_known_hellinger(self):
        data = synthetic_index()
        query = data['raw_features'][0].copy()
        target = query.copy()
        target[0], target[1] = 0, 1
        distance, similarity, _ = compute_overall_distance(query, target, weights=(1, 0, 0, 0))
        self.assertAlmostEqual(float(distance), 0.5)
        self.assertAlmostEqual(float(similarity), 0.5)

    def test_invalid_inputs_rejected(self):
        engine = FlowerSearchEngine(synthetic_index())
        for kwargs in [{'top_k': 0}, {'top_k': True}, {'weights': (-1, 1, 1, 1)}, {'weights': (0, 0, 0, 0)}, {'weights': (1, 2)}]:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                engine.search_by_vector(engine.normalized_matrix[0], **{'top_k': 3, **kwargs})
        with self.assertRaises(ValueError):
            engine.search_by_vector(np.full(163, np.nan), top_k=3)

    def test_tie_break_and_exclusion(self):
        engine = FlowerSearchEngine(synthetic_index())
        hits, _ = engine.search_by_vector(engine.normalized_matrix[0], top_k=3, exclude_filename='10.png')
        self.assertEqual([item['image_id'] for item in hits], [20, 30])

    def test_rerank_reuses_features_and_checks_version(self):
        engine = FlowerSearchEngine(synthetic_index())
        previous = {'version_id': 'test-v1', 'query_info': {'norm_vector': engine.normalized_matrix[0]}, 'matched_filename': '10.png'}
        result = engine.rerank(previous, weights=(1, 0, 0, 0), top_k=2, leave_one_out=True)
        self.assertTrue(result['reused_query_features'])
        self.assertEqual(result['timings']['extraction_ms'], 0)
        with self.assertRaises(ValueError):
            engine.rerank({**previous, 'version_id': 'old'})

    def test_content_match_ignores_filename(self):
        image = np.zeros((384, 384, 3), np.uint8)
        data = synthetic_index()
        data['metadata'][0]['pixel_sha256'] = hashlib.sha256(image.tobytes()).hexdigest()
        engine = FlowerSearchEngine(data)
        self.assertEqual(engine.match_source(image)['image_id'], 30)
        self.assertIsNone(engine.match_source(np.full_like(image, 255)))

    def test_split_has_no_overlap(self):
        metadata = [{'label': label} for label in [1] * 10 + [2] * 3 + [3] * 2 + [4] + [None]]
        gallery, validation, test = split_dataset(metadata)
        self.assertFalse(set(gallery) & set(validation) or set(gallery) & set(test) or set(validation) & set(test))
        self.assertEqual(len(gallery) + len(validation) + len(test), len(metadata))
        for index in list(validation) + list(test):
            self.assertIn(metadata[index]['label'], [metadata[position]['label'] for position in gallery])


if __name__ == '__main__':
    unittest.main()
