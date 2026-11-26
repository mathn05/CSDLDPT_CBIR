import hashlib
import json
import os
import re
import time
from pathlib import Path
from threading import RLock

import numpy as np
import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

from flower_cbir.core.contract import file_sha256, verify_contract
from flower_cbir.core.normalization import SelectiveNormalizer
from flower_cbir.core.schema import validate_features, vector_from_columns
from flower_cbir.settings import PROJECT_ROOT, load_config, relative_project_path

QUERY_UPLOAD_DIR = Path('flower_cbir/data/query_uploads')


def load_database_config():
    path = PROJECT_ROOT / 'database/connection.local.json'
    if not path.is_file():
        path = PROJECT_ROOT / 'database.local.json'  # Existing installations
    if path.exists():
        config = json.loads(path.read_text(encoding='utf-8-sig'))
    elif 'CBIR_DATABASE_ENV' in os.environ and Path(os.environ['CBIR_DATABASE_ENV']).is_file():
        env_path = Path(os.environ['CBIR_DATABASE_ENV'])
        settings = {}
        for line in env_path.read_text(encoding='utf-8-sig').splitlines():
            key, separator, value = line.partition('=')
            if separator and not key.strip().startswith('#'):
                settings[key.strip()] = value.strip()
        config = {
            'host': settings.get('DB_HOST', 'localhost'),
            'port': int(settings.get('DB_PORT', '5432')),
            'dbname': settings.get('DB_NAME'),
            'user': settings.get('DB_USER'),
            'password': settings.get('DB_PASSWORD'),
            'schema': 'public',
        }
    else:
        raise ValueError('Copy database/connection.example.json to database/connection.local.json and set your PostgreSQL connection details.')

    for name, variable in [
        ('host', 'PGHOST'),
        ('port', 'PGPORT'),
        ('dbname', 'PGDATABASE'),
        ('user', 'PGUSER'),
        ('password', 'PGPASSWORD'),
    ]:
        if variable in os.environ:
            config[name] = os.environ[variable]
    if not config.get('dbname') or not config.get('user'):
        raise ValueError('PostgreSQL configuration requires dbname and user.')
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
        raise RuntimeError(
            'Cannot connect to PostgreSQL. Check host, port, dbname, user and password in your database configuration, '
            'and ensure the PostgreSQL service is running.'
        ) from None
    return connection, schema


def relative_image_path(filename: str) -> str:
    """Path to a gallery image stored in PostgreSQL, relative to PROJECT_ROOT."""
    name = Path(filename).name
    if name != filename:
        raise ValueError('Gallery filenames must not include directory components.')
    image_dir = (PROJECT_ROOT / load_config()['dataset']['image_dir']).resolve()
    return relative_project_path(image_dir / name)


def resolve_dataset_path(stored_path: str, filename: str, directory_key: str) -> str:
    if stored_path:
        candidate_stored = Path(stored_path)
        path = candidate_stored.resolve() if candidate_stored.is_absolute() else (PROJECT_ROOT / candidate_stored).resolve()
        if path.is_file():
            return str(path)
    directory = (PROJECT_ROOT / load_config()['dataset'][directory_key]).resolve()
    candidate = (directory / Path(filename).name).resolve()
    return str(candidate) if candidate.is_file() else str((PROJECT_ROOT / Path(stored_path or filename)).resolve())


def _schema_columns(connection, schema):
    return set(connection.execute(
        'SELECT table_name, column_name FROM information_schema.columns WHERE table_schema=%s',
        (schema,)
    ).fetchall())


def require_grouped_normalization(columns):
    expected = {
        ('feature_normalization', f'f{group}_{stat}')
        for group in range(1, 5) for stat in ('means', 'stds')
    } | {
        ('normalized_features', f'f{group}_vector')
        for group in range(1, 5)
    } | {
        ('flower_images', 'image_path'),
        ('flower_images', 'mask_path'),
    }
    missing = sorted(expected - columns)
    if missing:
        names = ', '.join(f'{table}.{column}' for table, column in missing)
        raise ValueError(f'Path-based & grouped F1-F4 database columns are required: {names}')


def _persist_query_upload(query_filename: str, query_source) -> str:
    safe_name = re.sub(r'[^A-Za-z0-9._-]+', '_', Path(query_filename or 'query.png').name).strip('_') or 'query.png'
    if Path(safe_name).suffix.lower() not in {'.png', '.jpg', '.jpeg', '.webp'}:
        safe_name += '.png'

    if isinstance(query_source, (str, Path)):
        src_path = Path(query_source).resolve()
        if src_path.is_file():
            if src_path.is_relative_to(PROJECT_ROOT):
                return relative_project_path(src_path)
            query_source = src_path.read_bytes()

    if isinstance(query_source, (bytes, bytearray)) and len(query_source) > 0:
        payload = bytes(query_source)
        digest = hashlib.sha256(payload).hexdigest()[:12]
        upload_dir = PROJECT_ROOT / QUERY_UPLOAD_DIR
        upload_dir.mkdir(parents=True, exist_ok=True)
        dest = upload_dir / f'{int(time.time() * 1000)}_{digest}_{safe_name}'
        dest.write_bytes(payload)
        return relative_project_path(dest)

    return safe_name


class FlowerDatabase:
    """Read a verified active snapshot and publish all path-based gallery tables atomically."""

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
            require_grouped_normalization(columns)

            version_id, normalization_id, contract, manifest, normalization = self.conn.execute('''
                SELECT version_id, normalization_id, contract, manifest, normalizer
                FROM cbir_index_versions WHERE active
            ''').fetchone()
            saved_stats = self.conn.execute('''
                SELECT f1_means || f2_means || f3_means || f4_means,
                       f1_stds || f2_stds || f3_stds || f4_stds
                FROM feature_normalization WHERE normalization_id=%s
            ''', (normalization_id,)).fetchone()

            rows = self.conn.execute('''
                SELECT i.image_id, i.filename, i.image_path, i.mask_path,
                       f.hs_histogram, f.v_histogram, f.lab_mean, f.lab_std, f.lbp_histogram,
                       f.circularity, f.solidity, f.aspect_ratio, f.rotated_extent, f.radial_spectrum, f.iap, f.iep,
                       n.f1_vector || n.f2_vector || n.f3_vector || n.f4_vector,
                       e.raw_vector, e.normalized_vector, e.metadata
                FROM cbir_index_entries e
                JOIN flower_images i ON i.image_id = e.image_id
                LEFT JOIN flower_features f ON f.image_id = i.image_id
                LEFT JOIN normalized_features n ON n.image_id = i.image_id AND n.normalization_id = %s
                WHERE e.version_id = %s
                ORDER BY i.filename
            ''', (normalization_id, version_id)).fetchall()

        verify_contract(contract)
        if not rows or len(rows) != manifest['summary']['total_images'] or any(
                any(value is None for value in row[:19]) for row in rows):
            raise ValueError('Every active gallery image must have file paths, raw features, and normalized features.')
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

        raw = np.asarray([vector_from_columns(row[4:16]) for row in rows], dtype=np.float32)
        normalized = np.asarray([row[16] for row in rows], dtype=np.float32)
        validate_features(raw)
        if not np.array_equal(raw, np.asarray([row[17] for row in rows], np.float32)) or not np.array_equal(
                normalized, np.asarray([row[18] for row in rows], np.float32)):
            raise ValueError('Gallery tables differ from their immutable extraction version.')
        if not np.array_equal(normalizer.transform(raw), normalized):
            raise ValueError('Normalizer does not reproduce PostgreSQL vectors exactly.')

        metadata = []
        for row in rows:
            identity, filename, image_path, mask_path = row[:4]
            entry_meta = row[19]
            if entry_meta['filename'] != filename:
                raise ValueError('Image metadata differs from the immutable extraction version.')
            resolved_img = resolve_dataset_path(image_path, filename, 'image_dir')
            resolved_msk = resolve_dataset_path(mask_path, Path(filename).stem + '.png', 'mask_dir')
            record = {
                **entry_meta,
                'image_id': identity,
                'filename': filename,
                'image_path': resolved_img,
                'mask_path': resolved_msk,
            }
            for kind in ('image', 'mask'):
                if file_sha256(record[kind + '_path']) != record[kind + '_sha256']:
                    raise ValueError(f'{filename}: source {kind} file changed after index publication.')
            metadata.append(record)

        return {
            'metadata': metadata,
            'raw_features': raw,
            'normalized_features': normalized,
            'normalizer': normalizer,
            'version_id': version_id,
            'contract': contract,
            'manifest': manifest,
            'normalization_id': normalization_id,
        }

    def publish_index(self, data):
        from flower_cbir.services.index_store import publish_index
        return publish_index(self, data)

    def log_retrieval(self, query_filename, query_norm_vec, top5_results, weights, execution_ms,
                      version_id=None, query_context=None, query_source=None):
        vector = query_norm_vec.tolist() if query_norm_vec is not None else []
        stored_query_path = _persist_query_upload(query_filename, query_source)
        context = {'original_filename': query_filename, **(query_context or {})}
        stored_top5 = []
        for item in top5_results[:5]:
            entry = dict(item)
            if entry.get('image_path'):
                entry['image_path'] = relative_project_path(entry['image_path'])
            if entry.get('mask_path'):
                entry['mask_path'] = relative_project_path(entry['mask_path'])
            stored_top5.append(entry)

        with self.lock, self.conn.transaction():
            self.conn.execute('''
                INSERT INTO retrieval_history
                    (query_image_path, query_features, top5_results, execution_ms, created_at,
                     index_version, weights, query_context)
                VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP, %s, %s, %s)
            ''', (
                stored_query_path,
                vector,
                Jsonb(stored_top5),
                execution_ms,
                version_id,
                list(weights),
                Jsonb(context),
            ))

    def close(self):
        with self.lock:
            self.conn.close()
