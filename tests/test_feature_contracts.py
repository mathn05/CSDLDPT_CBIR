import unittest

import cv2
import numpy as np

from flower_cbir.core.distance import compute_overall_distance
from flower_cbir.core.features import extract_all_features


class FeatureContractTests(unittest.TestCase):
    def circle_mask(self):
        mask = np.zeros((128, 128), dtype=np.uint8)
        cv2.circle(mask, (64, 64), 45, 255, -1)
        return mask

    def test_constant_red_known_color_and_texture(self):
        image = np.zeros((128, 128, 3), dtype=np.uint8)
        image[:, :, 0] = 255
        vector, explanation = extract_all_features(image, self.circle_mask())
        self.assertAlmostEqual(float(vector[5]), 1)
        self.assertAlmostEqual(float(vector[77]), 1)
        expected_lab = [53.24, 80.09, 67.20]
        for ring in explanation['f2']['ring_stats']:
            np.testing.assert_allclose([ring['mu_L'], ring['mu_a'], ring['mu_b']], expected_lab, atol=.15)
            np.testing.assert_allclose([ring['std_L'], ring['std_a'], ring['std_b']], 0, atol=1e-3)
        self.assertAlmostEqual(float(vector[96 + 8]), 1)
        self.assertAlmostEqual(float(vector[106 + 16]), 1)
        np.testing.assert_array_equal(vector[140:163], 0)

    def test_extraction_is_exactly_repeatable(self):
        image = np.random.default_rng(7).integers(0, 256, (128, 128, 3), dtype=np.uint8)
        first, _ = extract_all_features(image, self.circle_mask())
        second, _ = extract_all_features(image, self.circle_mask())
        np.testing.assert_array_equal(first, second)

    def test_self_distance_zero_and_symmetry(self):
        generator = np.random.default_rng(42)
        first = generator.normal(size=163).astype(np.float32)
        second = generator.normal(size=163).astype(np.float32)
        for start, stop in [(0, 72), (72, 78), (96, 106), (106, 124)]:
            for vector in [first, second]:
                vector[start:stop] = generator.dirichlet(np.ones(stop - start))
        distance, similarity, _ = compute_overall_distance(first, first)
        self.assertEqual(float(distance), 0)
        self.assertEqual(float(similarity), 1)
        forward = compute_overall_distance(first, second)[0]
        reverse = compute_overall_distance(second, first)[0]
        self.assertAlmostEqual(float(forward), float(reverse))
        self.assertGreaterEqual(float(forward), 0)
        self.assertLessEqual(float(forward), 1)
