import hashlib
import tempfile
import unittest
from pathlib import Path
from threading import RLock
from unittest.mock import MagicMock, patch

import numpy as np

from flower_cbir.core.normalization import SelectiveNormalizer
from flower_cbir.core.schema import feature_columns
from flower_cbir.services.database import FlowerDatabase, resolve_dataset_path


class ExistingDatabaseTests(unittest.TestCase):
    def setUp(self):
        for target,value in [
            ('flower_cbir.services.database.verify_contract',{}),
            ('flower_cbir.services.database.file_sha256','source-hash'),
            ('flower_cbir.core.deeplab.get_segmenter',MagicMock(info={'runtime':'test'})),
        ]:
            patcher=patch(target,return_value=value)
            patcher.start();self.addCleanup(patcher.stop)

    def repository(self):
        database=FlowerDatabase.__new__(FlowerDatabase)
        database.lock=RLock();database.conn=MagicMock();database.schema='public'
        vector=np.zeros(163,np.float32);vector[[0,72,96,106]]=1
        self.normalizer=SelectiveNormalizer().fit(vector[None,:])
        self.normalization=self.normalizer.to_dict()
        self.statistics=(self.normalizer.mean.tolist(),self.normalizer.std.tolist())
        metadata={'image_id':1,'filename':'flower.png','image_path':'images/flower.png',
                  'mask_path':'masks/flower.png','image_sha256':'source-hash','mask_sha256':'source-hash',
                  'pixel_sha256':hashlib.sha256(np.zeros((384,384,3),np.uint8).tobytes()).hexdigest()}
        self.rows=[(1,'flower.png','images/flower.png','masks/flower.png',*feature_columns(vector),
                   vector.tolist(),vector.tolist(),vector.tolist(),metadata)]
        self.manifest={'model_runtime':{'runtime':'test'},'summary':{'total_images':1}}
        self.has_version=True
        def execute(statement,parameters=None):
            result=MagicMock()
            if 'to_regclass' in statement:result.fetchone.return_value=('public.cbir_index_versions' if self.has_version else None,)
            elif 'SELECT version_id FROM' in statement:result.fetchone.return_value=('test-v3',)
            elif 'SELECT version_id,normalization_id' in statement:
                result.fetchone.return_value=('test-v3',7,{},self.manifest,self.normalization)
            elif 'SELECT feature_means,feature_stds' in statement:result.fetchone.return_value=self.statistics
            elif 'FROM cbir_index_entries' in statement:result.fetchall.return_value=self.rows
            return result
        database.conn.execute.side_effect=execute
        return database

    def test_old_machine_path_resolves_without_database_update(self):
        with tempfile.TemporaryDirectory() as directory:
            image=Path(directory)/'flower.png';image.touch()
            with patch('flower_cbir.services.database.load_config',return_value={'dataset':{'image_dir':directory}}):
                self.assertEqual(resolve_dataset_path('missing/flower.png','flower.png','image_dir'),str(image.resolve()))

    def test_load_exact_raw_values_without_inverse_normalization_or_writes(self):
        database=self.repository()
        data=database.load_index()
        self.assertEqual(data['raw_features'].shape,(1,163))
        self.assertEqual(data['version_id'],'test-v3')
        np.testing.assert_array_equal(data['normalizer'].transform(data['raw_features']),data['normalized_features'])
        for call in database.conn.execute.call_args_list:
            self.assertTrue(call.args[0].strip().upper().startswith(('SELECT ','SET TRANSACTION')))

    def test_saved_pixel_identity_is_preserved(self):
        database=self.repository()
        data=database.load_index()
        self.assertEqual(data['metadata'][0]['pixel_sha256'],self.rows[0][-1]['pixel_sha256'])

    def test_unversioned_database_is_rejected(self):
        database=self.repository();self.has_version=False
        with self.assertRaisesRegex(ValueError,'no verified extraction contract'):database.load_index()

    def test_missing_normalized_vector_is_rejected(self):
        database=self.repository();row=list(self.rows[0]);row[16]=None;self.rows=[tuple(row)]
        with self.assertRaisesRegex(ValueError,'Every active gallery image'):database.load_index()

    def test_duplicate_image_vectors_are_rejected(self):
        database=self.repository();self.rows*=2;self.manifest['summary']['total_images']=2
        with self.assertRaisesRegex(ValueError,'Duplicate'):database.load_index()

    def test_normalization_snapshot_drift_is_rejected(self):
        database=self.repository();self.statistics=([1]*163,[1]*163)
        with self.assertRaisesRegex(ValueError,'Normalization table differs'):database.load_index()

    def test_histogram_zscore_is_rejected(self):
        database=self.repository()
        self.normalization['mean']=[1]*163;self.statistics=([1]*163,self.normalization['std'])
        with self.assertRaisesRegex(ValueError,'F1/F3'):database.load_index()

    def test_raw_table_drift_is_rejected(self):
        database=self.repository();row=list(self.rows[0]);row[9]=.5;self.rows=[tuple(row)]
        with self.assertRaisesRegex(ValueError,'immutable extraction version'):database.load_index()

    def test_changed_source_file_is_rejected(self):
        database=self.repository()
        with patch('flower_cbir.services.database.file_sha256',return_value='changed'):
            with self.assertRaisesRegex(ValueError,'changed after index publication'):database.load_index()

    def test_wrong_feature_dimensions_are_rejected(self):
        database=self.repository();row=list(self.rows[0]);row[15]=[0]*6;self.rows=[tuple(row)]
        with self.assertRaisesRegex(ValueError,'163-dimensional'):database.load_index()

    def test_history_preserves_version_weights_context_and_top5_limit(self):
        database=self.repository();database.conn.execute.side_effect=None
        database.log_retrieval('query.png',np.zeros(163),[{'rank':i} for i in range(8)],(.25,)*4,5,
                               version_id='v3',query_context={'method':'deeplabv3'})
        statement,parameters=database.conn.execute.call_args.args
        self.assertIn('index_version,weights,query_context',statement)
        self.assertEqual(len(parameters[2].obj),5)
        self.assertEqual(parameters[4],'v3')
        self.assertEqual(parameters[5],[.25]*4)
        self.assertEqual(parameters[6].obj,{'method':'deeplabv3'})

    def test_history_reads_native_jsonb_and_timestamp(self):
        database=self.repository();database.conn.execute.side_effect=None
        database.conn.execute.return_value.fetchall.return_value=[(2,'q.png',[],5,1700000000,'v3',[.25]*4,{'method':'deeplabv3'})]
        history=database.get_history()
        self.assertEqual(history[0]['version_id'],'v3')
        self.assertEqual(history[0]['weights'],[.25]*4)
        self.assertEqual(history[0]['timestamp'],1700000000.)
