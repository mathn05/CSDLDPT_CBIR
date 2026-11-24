import time
import hashlib
from io import BytesIO
from PIL import Image
import numpy as np

from flower_cbir.core.preprocess import load_image
from flower_cbir.core.distance import compute_overall_distance
from flower_cbir.settings import default_weights, validate_weights, load_config
from flower_cbir.core.deeplab import run_deeplab
from flower_cbir.core.features import extract_all_features
from flower_cbir.core.contract import verify_contract
from flower_cbir.core.segmentation import clean_mask


def gallery_image(record):
    """Use PostgreSQL image bytes, or a file for a disk-built offline index."""
    return record.get('image_data') or record.get('image_path')


def gallery_mask(record):
    """Decode a binary mask in the same coordinates as the prepared RGB image."""
    source = BytesIO(record['mask_data']) if record.get('mask_data') else record['mask_path']
    with Image.open(source) as image:
        return clean_mask(np.asarray(image.convert('L')))


class FlowerSearchEngine:
    """
    CBIR Search Engine for 163-dimensional flower image retrieval.
    Performs full vector matching against dataset index and returns Top 5 matches.
    """
    TOP_K = 5

    def __init__(self, index_data: dict = None):
        if index_data is None:
            from flower_cbir.services.database import FlowerDatabase
            database = FlowerDatabase()
            try:
                index_data = database.load_index()
            finally:
                database.close()

        self.metadata = index_data["metadata"]
        self.raw_matrix = index_data["raw_features"]
        self.normalized_matrix = index_data["normalized_features"]
        self.normalizer = index_data["normalizer"]
        self.num_images = len(self.metadata)
        self.version_id = index_data.get('version_id', 'unversioned')
        self.contract = index_data.get('contract')
        self.manifest = index_data.get('manifest', {})
        if self.contract is not None:
            verify_contract(self.contract)
        if self.raw_matrix.shape != (self.num_images, 163) or self.normalized_matrix.shape != self.raw_matrix.shape or self.num_images == 0:
            raise ValueError('Index shape does not match metadata.')
        if not np.isfinite(self.raw_matrix).all() or not np.isfinite(self.normalized_matrix).all():
            raise ValueError('Index contains nonfinite values.')
        self.image_ids = np.array([item['image_id'] for item in self.metadata])
        self.metadata_by_id = {item['image_id']: item for item in self.metadata}
        self.pixel_lookup = {item['pixel_sha256']: item for item in self.metadata if 'pixel_sha256' in item}

    def match_source(self, source):
        image = load_image(source)
        if image.shape != (384, 384, 3):
            return None
        return self.pixel_lookup.get(hashlib.sha256(image.tobytes()).hexdigest())

    def search_by_vector(self, query_norm_vector: np.ndarray,
                         weights=None,
                         exclude_filename: str = None):
        """
        Search directly given a normalized 163-d query vector.
        Vectorized across all dataset images via NumPy and returns up to 5 best matches.
        """
        t0 = time.perf_counter()
        weights = default_weights() if weights is None else validate_weights(weights)
        D_all, S_all, details = compute_overall_distance(
            query_norm_vector, self.normalized_matrix, weights=weights
        )

        # Sort order: D ascending. Tie-break: image_id ascending
        image_ids = self.image_ids
        # lexsort sorts by last key primary, first key secondary -> (image_ids, D_all)
        sorted_indices = np.lexsort((image_ids, D_all))

        limit = min(self.TOP_K, self.num_images)
        results = []
        rank = 1
        for idx in sorted_indices:
            meta = self.metadata[idx]
            if exclude_filename and meta["filename"] == exclude_filename:
                continue

            w1, w2, w3, w4 = weights
            d1_val = float(details["D1"][idx])
            d2_val = float(details["D2"][idx])
            d3_val = float(details["D3"][idx])
            d4_val = float(details["D4"][idx])

            item = {
                "rank": rank,
                "image_id": meta["image_id"],
                "filename": meta["filename"],
                "image_path": meta.get("image_path"),
                "mask_path": meta.get("mask_path"),
                "distance": float(D_all[idx]),
                "similarity": float(S_all[idx]),
                "D1": d1_val,
                "D2": d2_val,
                "D3": d3_val,
                "D4": d4_val,
                "contributions": {
                    "w1_D1": float(w1 * d1_val),
                    "w2_D2": float(w2 * d2_val),
                    "w3_D3": float(w3 * d3_val),
                    "w4_D4": float(w4 * d4_val),
                },
                "f4_details": {
                    "scalars": float(details["d4_scalars"][idx]),
                    "fourier": float(details["d4_fourier"][idx]),
                    "iap": float(details["d4_iap"][idx]),
                    "iep": float(details["d4_iep"][idx]),
                }
            }
            results.append(item)
            rank += 1
            if rank > limit:
                break

        return results, (time.perf_counter() - t0) * 1000.0

    def rerank(self, previous_result, weights=None):
        if self.contract is not None:
            verify_contract(self.contract)
        if previous_result.get('version_id') != self.version_id:
            raise ValueError('Query belongs to another index version. Run a new query.')
        weights = default_weights() if weights is None else validate_weights(weights)
        results, elapsed = self.search_by_vector(previous_result['query_info']['norm_vector'], weights=weights)
        return {
            **previous_result,
            'results': results,
            'weights': list(weights),
            'timings': {
                'preprocess_ms': 0.0,
                'segmentation_ms': 0.0,
                'extraction_ms': 0.0,
                'normalization_ms': 0.0,
                'search_ms': elapsed,
                'total_backend_ms': elapsed,
            },
            'reused_query_features': True,
        }

    def search(self, query_source, weights=None):
        """Retrieve top 5 similar flowers for a new, centered 384 x 384 image."""
        if self.contract is not None:
            verify_contract(self.contract)
            from flower_cbir.core.deeplab import get_segmenter
            if get_segmenter().info != self.manifest['model_runtime']:
                raise ValueError('DeepLabV3+ runtime differs from this index. Synchronize before querying.')
        weights = default_weights() if weights is None else validate_weights(weights)
        total_start = time.perf_counter()
        rgb = load_image(query_source)
        if rgb.shape[:2] != (384, 384):
            raise ValueError('Ảnh truy vấn phải có kích thước 384×384 pixel.')
        timings = {'preprocess_ms': (time.perf_counter() - total_start) * 1000}
        t0 = time.perf_counter()
        mask, overlay, model_info = run_deeplab(rgb)
        timings['segmentation_ms'] = (time.perf_counter() - t0) * 1000
        t0 = time.perf_counter()
        raw_vector, explanation = extract_all_features(rgb, mask)
        timings['extraction_ms'] = (time.perf_counter() - t0) * 1000
        query_info = {
            'image_rgb': rgb, 'mask_binary': mask, 'overlay': overlay,
            'raw_vector': raw_vector, 'explanation': explanation,
            'preparation': {'source_size': [384, 384], 'crop_bounds': None},
            'segmentation': {'method': load_config()['segmentation']['method'], **model_info},
        }
        t0 = time.perf_counter()
        norm_vector = self.normalizer.transform(query_info['raw_vector'])
        timings["normalization_ms"] = (time.perf_counter() - t0) * 1000.0

        # Step 5: Search Top 5
        results, t_search_ms = self.search_by_vector(norm_vector, weights=weights)
        timings["search_ms"] = t_search_ms
        timings["total_backend_ms"] = (time.perf_counter() - total_start) * 1000.0

        query_info['norm_vector'] = norm_vector

        return {
            "query_info": query_info,
            "results": results,
            "timings": timings,
            "version_id": self.version_id,
            "weights": list(weights),
            "reused_query_features": False,
        }
