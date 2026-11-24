import hashlib
import json
import os
from pathlib import Path
from threading import RLock

import numpy as np
import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

from flower_cbir.core.normalization import SelectiveNormalizer
from flower_cbir.core.contract import verify_contract
from flower_cbir.core.schema import validate_features, vector_from_columns
from flower_cbir.settings import PROJECT_ROOT, load_config


def load_database_config():
    path = PROJECT_ROOT / 'database.local.json'
    if path.exists():
        config = json.loads(path.read_text(encoding='utf-8-sig'))
    else:
        env_path = Path(os.environ.get('CBIR_DATABASE_ENV',
            r'D:\CSDLDPT\flower_features\cau3_postgres\.env'))
        if not env_path.is_file():
            raise ValueError('Missing database.local.json and CBIR PostgreSQL .env file.')
        settings = {}
        for line in env_path.read_text(encoding='utf-8-sig').splitlines():
            key, separator, value = line.partition('=')
            if separator and not key.strip().startswith('#'):
                settings[key.strip()] = value.strip()
        config = {'host': settings.get('DB_HOST', 'localhost'),
                  'port': int(settings.get('DB_PORT', '5432')),
                  'dbname': settings.get('DB_NAME'), 'user': settings.get('DB_USER'),
                  'password': settings.get('DB_PASSWORD'), 'schema': 'public'}
    for name, variable in [('host', 'PGHOST'), ('port', 'PGPORT'), ('dbname', 'PGDATABASE'), ('user', 'PGUSER'), ('password', 'PGPASSWORD')]:
        if variable in os.environ:
            config[name] = os.environ[variable]
    if not config.get('dbname') or not config.get('user'):
        raise ValueError('Fill in dbname and user in database.local.json.')
    return config


def connect_database(config=None):
    config = dict(load_database_config() if config is None else config)
    schema = config.pop('schema', 'public')
    if not isinstance(schema, str) or not schema or schema.startswith('pg_') or schema == 'information_schema':
        raise ValueError('Specify the schema containing the existing tables, normally public.')
    config.setdefault('connect_timeout', 10)
    try:
        connection = psycopg.connect(**config, autocommit=True)
    except psycopg.OperationalError:
        raise RuntimeError('Cannot connect to PostgreSQL. Check host, port, dbname, user and password in database.local.json, and ensure the PostgreSQL service is running.') from None
    return connection, schema


class FlowerDatabase:
    """Read a verified active snapshot; publish all gallery tables atomically."""

    def __init__(self, config=None):
        self.lock = RLock()
        self.conn, self.schema = connect_database(config)
        try:
            self.conn.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(self.schema)))
        except Exception:
            self.conn.close()
            raise

    def active_version(self):
        with self.lock:
            exists = self.conn.execute('SELECT to_regclass(%s)',(self.schema+'.cbir_index_versions',)).fetchone()[0]
            if not exists:
                return None
            row = self.conn.execute('SELECT version_id FROM cbir_index_versions WHERE active').fetchone()
            return row[0] if row else None

    def load_index(self):
        if self.active_version() is None:
            raise ValueError('PostgreSQL has no verified extraction contract. Run python main.py --index to synchronize it.')
        with self.lock, self.conn.transaction():
            self.conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
            version_id, normalization_id, contract, manifest, normalization = self.conn.execute('''
                SELECT version_id,normalization_id,contract,manifest,normalizer
                FROM cbir_index_versions WHERE active
            ''').fetchone()
            saved_stats = self.conn.execute('''
                SELECT f1_means || f2_means || f3_means || f4_means,
                       f1_stds || f2_stds || f3_stds || f4_stds
                FROM feature_normalization WHERE normalization_id=%s
            ''',(normalization_id,)).fetchone()
            rows = self.conn.execute('''
                SELECT i.image_id,i.filename,i.image_data,m.mask_data,
                       f.hs_histogram,f.v_histogram,f.lab_mean,f.lab_std,f.lbp_histogram,
                       f.circularity,f.solidity,f.aspect_ratio,f.rotated_extent,f.radial_spectrum,f.iap,f.iep,
                       n.f1_vector || n.f2_vector || n.f3_vector || n.f4_vector,
                       e.raw_vector,e.normalized_vector,e.metadata
                FROM cbir_index_entries e JOIN flower_images i ON i.image_id=e.image_id
                LEFT JOIN flower_masks m ON m.image_id=i.image_id
                LEFT JOIN flower_features f ON f.image_id=i.image_id
                LEFT JOIN normalized_features n ON n.image_id=i.image_id AND n.normalization_id=%s
                WHERE e.version_id=%s ORDER BY i.filename
            ''',(normalization_id,version_id)).fetchall()
        verify_contract(contract)
        from flower_cbir.core.deeplab import get_segmenter
        if get_segmenter().info != manifest['model_runtime']:
            raise ValueError('DeepLabV3 runtime differs from the published index. Rebuild the index.')
        if not rows or len(rows) != manifest['summary']['total_images'] or any(
                any(value is None for value in row[:19]) for row in rows):
            raise ValueError('Every active gallery image must have raw and normalized features.')
        if len({row[0] for row in rows}) != len(rows):
            raise ValueError('Duplicate feature vectors for the same image.')
        normalizer = SelectiveNormalizer.from_dict(normalization)
        if saved_stats is None or not np.array_equal(np.asarray(saved_stats[0],np.float32),normalizer.mean) or not np.array_equal(
                np.asarray(saved_stats[1],np.float32),normalizer.std):
            raise ValueError('Normalization table differs from the immutable version.')
        histogram_dims = np.r_[0:78,96:124]
        if not np.array_equal(normalizer.mean[histogram_dims],np.zeros(106,np.float32)) or not np.array_equal(
                normalizer.std[histogram_dims],np.ones(106,np.float32)):
            raise ValueError('Stored normalization is incompatible: F1/F3 histograms must remain unchanged.')
        raw = np.asarray([vector_from_columns(row[4:16]) for row in rows],dtype=np.float32)
        normalized = np.asarray([row[16] for row in rows],dtype=np.float32)
        validate_features(raw)
        if not np.array_equal(raw,np.asarray([row[17] for row in rows],np.float32)) or not np.array_equal(
                normalized,np.asarray([row[18] for row in rows],np.float32)):
            raise ValueError('Gallery tables differ from their immutable extraction version.')
        if not np.array_equal(normalizer.transform(raw),normalized):
            raise ValueError('Normalizer does not reproduce PostgreSQL vectors exactly.')
        metadata = []
        for row in rows:
            identity, filename, image_data, mask_data = row[:4]
            if row[19]['filename'] != filename:
                raise ValueError('Image metadata differs from the immutable extraction version.')
            record = {**row[19], 'image_id': identity, 'filename': filename,
                      'image_data': bytes(image_data), 'mask_data': bytes(mask_data)}
            for kind in ['image', 'mask']:
                payload = record[kind + '_data']
                if not payload or hashlib.sha256(payload).hexdigest() != record[kind + '_sha256']:
                    raise ValueError(f'{filename}: PostgreSQL {kind} bytes differ from the published index.')
            metadata.append(record)
        return {'metadata':metadata,'raw_features':raw,'normalized_features':normalized,
                'normalizer':normalizer,'version_id':version_id,'contract':contract,
                'manifest':manifest,'normalization_id':normalization_id}

    def publish_index(self, data, *, replace_all=False, backup_artifact=None):
        from flower_cbir.services.index_store import publish_index
        return publish_index(self,data,replace_all=replace_all,backup_artifact=backup_artifact)

    def log_retrieval(self, query_filename, query_norm_vec, top5_results, weights, execution_ms,
                      version_id=None, query_context=None, query_image_data=None):
        vector = query_norm_vec.tolist() if query_norm_vec is not None else []
        with self.lock, self.conn.transaction():
            self.conn.execute('''INSERT INTO retrieval_history
                (query_image_path, query_features, top5_results, execution_ms, created_at,
                 index_version, weights, query_context, query_image_data)
                VALUES (%s,%s,%s,%s,CURRENT_TIMESTAMP,%s,%s,%s,%s)''',
                (query_filename, vector, Jsonb(top5_results[:5]), execution_ms, version_id,
                 list(weights), Jsonb(query_context or {}), query_image_data))

    def get_history(self, limit=10):
        with self.lock:
            rows = self.conn.execute('''
                SELECT query_id,query_image_path,top5_results,execution_ms,
                       EXTRACT(EPOCH FROM created_at),index_version,weights,query_context,query_image_data
                FROM retrieval_history ORDER BY query_id DESC LIMIT %s
            ''', (limit,)).fetchall()
            image_ids = {int(item['image_id']) for row in rows for item in (row[2] or [])
                         if item.get('image_id') is not None}
            media = {}
            if image_ids:
                media = {identity: bytes(payload) for identity, payload in self.conn.execute(
                    'SELECT image_id,image_data FROM flower_images WHERE image_id=ANY(%s)',
                    (list(image_ids),)).fetchall() if payload is not None}
        return [{
            'query_id': row[0], 'query_filename': row[1],
            'top5': [{**item, 'image_data': media.get(int(item['image_id']))}
                     if item.get('image_id') is not None else item for item in (row[2] or [])],
            'weights': row[6], 'execution_ms': row[3], 'timestamp': float(row[4]), 'version_id': row[5],
            'query_context': row[7],
            'query_image_data': bytes(row[8]) if row[8] is not None else None,
        } for row in rows]

    def close(self):
        with self.lock:
            self.conn.close()
