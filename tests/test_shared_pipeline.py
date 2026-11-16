import csv
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch,MagicMock

import cv2
import numpy as np

from flower_cbir.core.features import extract_all_features
from flower_cbir.core.pipeline import extract_image,prepare_image
from flower_cbir.core.preprocess import square_crop_bounds,crop_with_padding
from flower_cbir.core.schema import FEATURE_NAMES,feature_columns,vector_from_columns
from flower_cbir.services.csv_tools import META_NAMES,validate_csv
from flower_cbir.core.normalization import SelectiveNormalizer
from flower_cbir.services.search import FlowerSearchEngine
from trich_rut_minh import full_163


class SharedPipelineTests(unittest.TestCase):
    def image_and_mask(self):
        rgb=np.random.default_rng(17).integers(0,256,(384,384,3),np.uint8)
        mask=np.zeros((384,384),np.uint8)
        cv2.circle(mask,(192,192),140,1,-1)
        return rgb,mask

    def test_exporter_imports_the_identical_feature_function(self):
        self.assertIs(full_163.extract_all_features,extract_all_features)

    def test_exporter_matches_query_on_disconnected_mask_and_both_encodings(self):
        rgb,mask=self.image_and_mask();mask[5:10,5:10]=1
        expected=extract_image(rgb,supplied_mask=mask)['raw_vector']
        for encoding in [mask.astype(bool),mask,mask*255]:
            actual=np.concatenate(full_163.extract_one(rgb,encoding)).astype(np.float32)
            np.testing.assert_array_equal(expected,actual)

    def test_postgresql_column_mapping_round_trip_is_bitwise_exact(self):
        rgb,mask=self.image_and_mask()
        vector,_=extract_all_features(rgb,mask)
        np.testing.assert_array_equal(vector,vector_from_columns(feature_columns(vector)))

    def test_prepared_images_are_not_cropped_twice(self):
        rgb,mask=self.image_and_mask()
        actual,actual_mask,info=prepare_image(rgb,supplied_mask=mask)
        np.testing.assert_array_equal(rgb,actual)
        np.testing.assert_array_equal(mask*255,actual_mask)
        self.assertIsNone(info['crop_bounds'])

    def test_report_crop_margin_and_gray_padding(self):
        # A 100px flower box touching the image corner expands to 110px,
        # including 5px of neutral padding; it must not be clamped into a rectangle.
        bounds=square_crop_bounds((0,0,100,60))
        self.assertEqual(bounds,[-5,-25,110,110])
        source=np.zeros((100,100,3),np.uint8)
        cropped=crop_with_padding(source,bounds,128)
        self.assertEqual(cropped.shape,(110,110,3))
        np.testing.assert_array_equal(cropped[0],128)
        np.testing.assert_array_equal(cropped[25,5],0)

    def test_new_image_uses_deeplab_without_grabcut_fallback(self):
        rgb,mask=self.image_and_mask()
        with patch('flower_cbir.core.pipeline.run_deeplab',return_value=(mask*255,rgb,{'device':'test'})) as model:
            result=extract_image(rgb)
        model.assert_called_once()
        self.assertEqual(result['segmentation']['method'],'deeplabv3')
        expected,_=extract_all_features(rgb,mask)
        np.testing.assert_array_equal(expected,result['raw_vector'])

    def test_empty_prediction_is_not_replaced_with_another_algorithm(self):
        rgb,_=self.image_and_mask()
        with patch('flower_cbir.core.pipeline.run_deeplab',side_effect=ValueError('empty model mask')):
            with self.assertRaisesRegex(ValueError,'empty model mask'):extract_image(rgb)

    def production_engine_fixture(self,rgb,mask):
        vector,_=extract_all_features(rgb,mask)
        raw=vector[None,:]
        normalizer=SelectiveNormalizer().fit(raw)
        return FlowerSearchEngine({'metadata':[{'image_id':1,'filename':'sample.png','image_path':'','mask_path':''}],
            'raw_features':raw,'normalized_features':normalizer.transform(raw),'normalizer':normalizer,
            'contract':{'test_fixture':True},'manifest':{'model_runtime':{'runtime':'expected'}}})

    def test_production_query_rejects_an_alternate_supplied_mask(self):
        rgb,mask=self.image_and_mask()
        other=np.zeros_like(mask);cv2.circle(other,(192,192),100,1,-1)
        model=MagicMock(info={'runtime':'expected'})
        model.predict.return_value=mask*255
        with patch('flower_cbir.services.search.verify_contract'),patch('flower_cbir.core.deeplab.get_segmenter',return_value=model):
            engine=self.production_engine_fixture(rgb,mask)
            with self.assertRaisesRegex(ValueError,'supplied query mask differs'):
                engine.search(rgb,query_mask=other,top_k=1)

    def test_production_query_rejects_a_changed_model_runtime(self):
        rgb,mask=self.image_and_mask()
        with patch('flower_cbir.services.search.verify_contract'),patch('flower_cbir.core.deeplab.get_segmenter',return_value=MagicMock(info={'runtime':'changed'})):
            engine=self.production_engine_fixture(rgb,mask)
            with self.assertRaisesRegex(ValueError,'runtime differs'):
                engine.search(rgb,top_k=1)

    def test_f2_empty_ring_is_zero_as_report_requires(self):
        rgb,_=self.image_and_mask();mask=np.zeros((384,384),np.uint8)
        cv2.circle(mask,(192,192),100,1,-1);cv2.circle(mask,(192,192),60,0,-1)
        vector,explanation=extract_all_features(rgb,mask)
        self.assertEqual(explanation['f2']['ring_stats'][0]['count'],0)
        np.testing.assert_array_equal(vector[78:84],0)
        self.assertTrue(np.isfinite(vector).all())

    def test_csv_round_trip_is_exact_and_legacy_precision_is_rejected(self):
        rgb,mask=self.image_and_mask();vector,_=extract_all_features(rgb,mask)
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'features.csv'
            def write(values):
                with path.open('w',newline='',encoding='utf-8') as stream:
                    writer=csv.writer(stream);writer.writerow(META_NAMES+FEATURE_NAMES)
                    writer.writerow(['flower.png','ihash','mhash',int(mask.sum()),*values])
            values=[format(float(x),'.17g') for x in vector];write(values)
            _,matrix=validate_csv(path,1)
            np.testing.assert_array_equal(matrix[0],vector)
            values[0]='0.123456789123456789';write(values)
            with self.assertRaisesRegex(ValueError,'lossless float32'):validate_csv(path,1)
