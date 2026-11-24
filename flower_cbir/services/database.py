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
from flower_cbir.core.contract import verify_contract, file_sha256
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


def resolve_dataset_path(stored_path, filename, directory_key):
    path = (PROJECT_ROOT / Path(stored_path)).resolve()
    if path.is_file():
        return str(path)
    directory = PROJECT_ROOT / load_config()['dataset'][directory_key]
    candidate = (directory / Path(filename).name).resolve()
    return str(candidate) if candidate.is_file() else str(path)


def _schema_columns(connection, schema):
    return set(connection.execute(
        'SELECT table_name, column_name FROM information_schema.columns WHERE table_schema=%s',
        (schema,)
    ).fetchall())


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
            exists = self.conn.execute('SELECT to_regclass(%s)', (self.schema + '.cbir_index_versions',)).fetchone()[0]
            if not exists:
                return None
            row = self.conn.execute('SELECT version_id FROM cbir_index_versions WHERE active').fetchone()
            return row[0] if row else None

    def load_index(self):
        if self.active_version() is None:
            raise ValueError('PostgreSQL has no verified extraction contract. Run python main.py --index to synchronize it.')
        with self.lock, self.conn.transaction():
            self.conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
            columns = _schema_columns(self.conn, self.schema)
            grouped_norm_stats = ('feature_normalization', 'f1_means') in columns
            grouped_norm_vec = ('normalized_features', 'f1_vector') in columns
            has_bytea_media = ('flower_images', 'image_data') in columns and ('flower_masks', 'mask_data') in columns
            has_paths = ('flower_images', 'image_path') in columns and ('flower_images', 'mask_path') in columns

            version_id, normalization_id, contract, manifest, normalization = self.conn.execute('''
                SELECT version_id,normalization_id,contract,manifest,normalizer
                FROM cbir_index_versions WHERE active
            ''').fetchone()
            if grouped_norm_stats:
                saved_stats = self.conn.execute('''
                    SELECT f1_means || f2_means || f3_means || f4_means,
                           f1_stds || f2_stds || f3_stds || f4_stds
                    FROM feature_normalization WHERE normalization_id=%s
                ''', (normalization_id,)).fetchone()
            else:
                saved_stats = self.conn.execute('''
                    SELECT feature_means,feature_stds FROM feature_normalization WHERE normalization_id=%s
                ''', (normalization_id,)).fetchone()

            img_data_expr = 'i.image_data' if has_bytea_media else 'NULL::bytea'
            mask_data_expr = 'm.mask_data' if has_bytea_media else 'NULL::bytea'
            img_path_expr = 'i.image_path' if has_paths else 'NULL::text'
            mask_path_expr = 'i.mask_path' if has_paths else 'NULL::text'
            masks_join = 'LEFT JOIN flower_masks m ON m.image_id=i.image_id' if has_bytea_media else ''
            norm_vec_expr = (
                'n.f1_vector || n.f2_vector || n.f3_vector || n.f4_vector'
                if grouped_norm_vec else 'n.normalized_vector'
            )
            rows = self.conn.execute(f'''
                SELECT i.image_id,i.filename,{img_data_expr},{mask_data_expr},{img_path_expr},{mask_path_expr},
                       f.hs_histogram,f.v_histogram,f.lab_mean,f.lab_std,f.lbp_histogram,
                       f.circularity,f.solidity,f.aspect_ratio,f.rotated_extent,f.radial_spectrum,f.iap,f.iep,
                       {norm_vec_expr},
                       e.raw_vector,e.normalized_vector,e.metadata
                FROM cbir_index_entries e JOIN flower_images i ON i.image_id=e.image_id
                {masks_join}
                LEFT JOIN flower_features f ON f.image_id=i.image_id
                LEFT JOIN normalized_features n ON n.image_id=i.image_id AND n.normalization_id=%s
                WHERE e.version_id=%s ORDER BY i.filename
            ''', (normalization_id, version_id)).fetchall()
        verify_contract(contract)
        from flower_cbir.core.deeplab import get_segmenter
        if get_segmenter().info != manifest['model_runtime']:
            raise ValueError('DeepLabV3 runtime differs from the published index. Rebuild the index.')
        if not rows or len(rows) != manifest['summary']['total_images'] or any(
                any(value is None for value in row[6:21]) for row in rows):
            raise ValueError('Every active gallery image must have raw and normalized features.')
        if len({row[0] for row in rows}) != len(rows):
            raise ValueError('Duplicate feature vectors for the same image.')
        normalizer = SelectiveNormalizer.from_dict(normalization)
        if saved_stats is None or not np.array_equal(np.asarray(saved_stats[0], np.float32), normalizer.mean) or not np.array_equal(
                np.asarray(saved_stats[1], np.float32), normalizer.std):
            raise ValueError('Normalization table differs from the immutable version.')
        histogram_dims = np.r_[0:78, 96:124]
        if not np.array_equal(normalizer.mean[histogram_dims], np.zeros(106, np.float32)) or not np.array_equal(
                normalizer.std[histogram_dims], np.ones(106, np.float32)):
            raise ValueError('Stored normalization is incompatible: F1/F3 histograms must remain unchanged.')
        raw = np.asarray([vector_from_columns(row[6:18]) for row in rows], dtype=np.float32)
        normalized = np.asarray([row[18] for row in rows], dtype=np.float32)
        validate_features(raw)
        if not np.array_equal(raw, np.asarray([row[19] for row in rows], np.float32)) or not np.array_equal(
                normalized, np.asarray([row[20] for row in rows], np.float32)):
            raise ValueError('Gallery tables differ from their immutable extraction version.')
        if not np.array_equal(normalizer.transform(raw), normalized):
            raise ValueError('Normalizer does not reproduce PostgreSQL vectors exactly.')
        metadata = []
        for row in rows:
            identity, filename, image_data, mask_data, image_path, mask_path = row[:6]
            entry_meta = row[21]
            if entry_meta['filename'] != filename:
                raise ValueError('Image metadata differs from the immutable extraction version.')
            record = {**entry_meta, 'image_id': identity, 'filename': filename}
            if image_data is not None and mask_data is not None:
                record['image_data'] = bytes(image_data)
                record['mask_data'] = bytes(mask_data)
                for kind in ['image', 'mask']:
                    payload = record[kind + '_data']
                    if not payload or hashlib.sha256(payload).hexdigest() != record[kind + '_sha256']:
                        raise ValueError(f'{filename}: PostgreSQL {kind} bytes differ from the published index.')
                if image_path or record.get('image_path'):
                    resolved_img = resolve_dataset_path(image_path or record['image_path'], filename, 'image_dir')
                    record['image_path'] = resolved_img if Path(resolved_img).is_file() else None
                if mask_path or record.get('mask_path'):
                    resolved_msk = resolve_dataset_path(mask_path or record['mask_path'], Path(filename).stem + '.png', 'mask_dir')
                    record['mask_path'] = resolved_msk if Path(resolved_msk).is_file() else None
            else:
                src_img = image_path or record.get('image_path')
                src_msk = mask_path or record.get('mask_path')
                if not src_img or not src_msk:
                    raise ValueError(f'{filename}: missing both BYTEA media and dataset file paths.')
                record['image_path'] = resolve_dataset_path(src_img, filename, 'image_dir')
                record['mask_path'] = resolve_dataset_path(src_msk, Path(filename).stem + '.png', 'mask_dir')
                for kind in ['image', 'mask']:
                    if file_sha256(record[kind + '_path']) != record[kind + '_sha256']:
                        raise ValueError(f'{filename}: source {kind} changed after index publication.')
            metadata.append(record)
        return {'metadata': metadata, 'raw_features': raw, 'normalized_features': normalized,
                'normalizer': normalizer, 'version_id': version_id, 'contract': contract,
                'manifest': manifest, 'normalization_id': normalization_id}

    def publish_index(self, data, *, replace_all=False, backup_artifact=None):
        from flower_cbir.services.index_store import publish_index
        return publish_index(self, data, replace_all=replace_all, backup_artifact=backup_artifact)

    def log_retrieval(self, query_filename, query_norm_vec, top5_results, weights, execution_ms,
                      version_id=None, query_context=None, query_image_data=None):
        vector = query_norm_vec.tolist() if query_norm_vec is not None else []
        if isinstance(query_image_data, (str, Path)):
            p = Path(query_image_data)
            payload = p.read_bytes() if p.is_file() else None
        elif isinstance(query_image_data, (bytes, bytearray)):
            payload = bytes(query_image_data)
        else:
            payload = None
        with self.lock, self.conn.transaction():
            self.conn.execute('ALTER TABLE retrieval_history ADD COLUMN IF NOT EXISTS query_image_data BYTEA')
            self.conn.execute('''INSERT INTO retrieval_history
                (query_image_path, query_features, top5_results, execution_ms, created_at,
                 index_version, weights, query_context, query_image_data)
                VALUES (%s,%s,%s,%s,CURRENT_TIMESTAMP,%s,%s,%s,%s)''',
                (query_filename, vector, Jsonb(top5_results[:5]), execution_ms, version_id,
                 list(weights), Jsonb(query_context or {}), payload))

    def get_history(self, limit=10):
        with self.lock:
            columns = _schema_columns(self.conn, self.schema)
            has_query_img = ('retrieval_history', 'query_image_data') in columns
            has_img_data = ('flower_images', 'image_data') in columns
            has_img_path = ('flower_images', 'image_path') in columns
            q_img_expr = 'query_image_data' if has_query_img else 'NULL::bytea'
            rows = self.conn.execute(f'''
                SELECT query_id,query_image_path,top5_results,execution_ms,
                       EXTRACT(EPOCH FROM created_at),index_version,weights,query_context,{q_img_expr}
                FROM retrieval_history ORDER BY query_id DESC LIMIT %s
            ''', (limit,)).fetchall()
            image_ids = {int(item['image_id']) for row in rows for item in (row[2] or [])
                         if item.get('image_id') is not None}
            media_bytes = {}
            media_paths = {}
            if image_ids:
                if has_img_data:
                    media_bytes = {
                        identity: bytes(payload)
                        for identity, payload in self.conn.execute(
                            'SELECT image_id,image_data FROM flower_images WHERE image_id=ANY(%s)',
                            (list(image_ids),)
                        ).fetchall()
                        if payload is not None
                    }
                if has_img_path:
                    for identity, fname, ipath in self.conn.execute(
                        'SELECT image_id,filename,image_path FROM flower_images WHERE image_id=ANY(%s)',
                        (list(image_ids),)
                    ).fetchall():
                        if ipath:
                            resolved = resolve_dataset_path(ipath, fname, 'image_dir')
                            if Path(resolved).is_file():
                                media_paths[identity] = resolved
        history = []
        for row in rows:
            enriched_top5 = []
            for item in (row[2] or []):
                if item.get('image_id') is not None:
                    iid = int(item['image_id'])
                    img_payload = media_bytes.get(iid)
                    if img_payload is None and iid in media_paths:
                        img_payload = Path(media_paths[iid]).read_bytes()
                    enriched_top5.append({**item, 'image_data': img_payload})
                else:
                    enriched_top5.append(item)
            history.append({
                'query_id': row[0],
                'query_filename': row[1],
                'top5': enriched_top5,
                'weights': row[6],
                'execution_ms': row[3],
                'timestamp': float(row[4]),
                'version_id': row[5],
                'query_context': row[7],
                'query_image_data': bytes(row[8]) if row[8] is not None else None,
            })
        return history

    def close(self):
        with self.lock:
            self.conn.close()
