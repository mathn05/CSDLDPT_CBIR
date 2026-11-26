"""Atomic publication of path-based gallery records, 4-group normalization, raw/normalized vectors, and provenance."""
import gzip
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from psycopg import sql
from psycopg.types.json import Jsonb

from flower_cbir.core.contract import file_sha256, verify_contract
from flower_cbir.core.schema import GROUP_SLICES, feature_columns, validate_features
from flower_cbir.services.database import (
    relative_image_path,
    require_grouped_normalization,
)
from flower_cbir.settings import PROJECT_ROOT, load_config, relative_project_path


def backup_database(repository):
    snapshot = {'format': 'cbir-postgres-snapshot-v1', 'schema': repository.schema, 'tables': {}}
    names = [
        'flower_images',
        'flower_features',
        'feature_normalization',
        'normalized_features',
        'retrieval_history',
        'cbir_index_versions',
        'cbir_index_entries',
    ]
    with repository.conn.transaction():
        repository.conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
        for name in names:
            exists = repository.conn.execute('SELECT to_regclass(%s)', (repository.schema + '.' + name,)).fetchone()[0]
            if exists:
                rows = repository.conn.execute(
                    sql.SQL('SELECT row_to_json(t) FROM {} t').format(sql.Identifier(name))
                ).fetchall()
                snapshot['tables'][name] = [row[0] for row in rows]
        snapshot['columns'] = repository.conn.execute('''
            SELECT table_name, column_name, data_type, is_identity, identity_generation, column_default
            FROM information_schema.columns
            WHERE table_schema = %s
            ORDER BY table_name, ordinal_position
        ''', (repository.schema,)).fetchall()
        snapshot['database'] = repository.conn.execute('SELECT current_database()').fetchone()[0]
    directory = PROJECT_ROOT / 'output/backups'
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '-postgres.json.gz')
    with gzip.open(destination, 'wt', encoding='utf-8') as stream:
        json.dump(snapshot, stream, ensure_ascii=False, separators=(',', ':'))
    return destination


def ensure_schema(connection):
    connection.execute('''
        CREATE TABLE IF NOT EXISTS cbir_index_versions (
            version_id TEXT PRIMARY KEY,
            normalization_id INTEGER NOT NULL UNIQUE REFERENCES feature_normalization(normalization_id),
            contract JSONB NOT NULL,
            manifest JSONB NOT NULL,
            normalizer JSONB NOT NULL,
            active BOOLEAN NOT NULL DEFAULT FALSE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    connection.execute('CREATE UNIQUE INDEX IF NOT EXISTS cbir_one_active_index ON cbir_index_versions ((active)) WHERE active')
    connection.execute('''
        CREATE TABLE IF NOT EXISTS cbir_index_entries (
            version_id TEXT NOT NULL REFERENCES cbir_index_versions(version_id),
            image_id INTEGER NOT NULL REFERENCES flower_images(image_id),
            raw_vector DOUBLE PRECISION[] NOT NULL CHECK(array_length(raw_vector, 1) = 163),
            normalized_vector DOUBLE PRECISION[] NOT NULL CHECK(array_length(normalized_vector, 1) = 163),
            metadata JSONB NOT NULL,
            PRIMARY KEY(version_id, image_id)
        )
    ''')
    connection.execute('ALTER TABLE retrieval_history ADD COLUMN IF NOT EXISTS index_version TEXT')
    connection.execute('ALTER TABLE retrieval_history ADD COLUMN IF NOT EXISTS weights DOUBLE PRECISION[]')
    connection.execute('ALTER TABLE retrieval_history ADD COLUMN IF NOT EXISTS query_context JSONB')


def publish_index(repository, data):
    contract = verify_contract(data['contract'])
    raw = np.asarray(data['raw_features'], dtype=np.float32)
    normalized = np.asarray(data['normalized_features'], dtype=np.float32)
    validate_features(raw)
    metadata = data['metadata']
    if raw.shape != (len(metadata), 163) or not np.array_equal(data['normalizer'].transform(raw), normalized):
        raise ValueError('Cannot publish inconsistent raw features and normalization.')
    if len({m['filename'] for m in metadata}) != len(metadata):
        raise ValueError('Duplicate filenames in the publication batch.')

    mask_dir = (PROJECT_ROOT / load_config()['dataset']['mask_dir']).resolve()
    for item in metadata:
        for kind in ('image', 'mask'):
            file_path = Path(item[kind + '_path'])
            if not file_path.is_file() or file_sha256(file_path) != item[kind + '_sha256']:
                raise ValueError(f'Source changed or missing since extraction: {item["filename"]}, {kind}')
        canonical_image = (PROJECT_ROOT / relative_image_path(item['filename'])).resolve()
        if not canonical_image.is_file() or file_sha256(canonical_image) != item['image_sha256']:
            raise ValueError(f'Configured image_dir does not contain the indexed image: {item["filename"]}')
        if Path(item['mask_path']).resolve() != mask_dir / item['filename']:
            raise ValueError(f'Gallery mask must use its image filename in {mask_dir}: {item["filename"]}')
        if item['mask_provenance'] not in ('final_segmentation_v2', 'deeplabv3plus_v2_postprocessed'):
            raise ValueError(f"Unknown mask source for {item['filename']}")

    if repository.active_version() == data['version_id']:
        existing = repository.load_index()
        by_name = {m['filename']: i for i, m in enumerate(existing['metadata'])}
        if len(by_name) != len(metadata) or any(
            not np.array_equal(raw[i], existing['raw_features'][by_name[m['filename']]])
            for i, m in enumerate(metadata)
        ):
            raise ValueError('The active immutable version differs from the publication batch.')
        return {'version_id': data['version_id'], 'already_active': True, 'rows': len(metadata)}

    backup = backup_database(repository)

    connection = repository.conn
    with repository.lock, connection.transaction():
        connection.execute("SELECT pg_advisory_xact_lock(hashtext('cbir_publish_index'))")
        ensure_schema(connection)
        columns = set(connection.execute(
            'SELECT table_name, column_name FROM information_schema.columns WHERE table_schema = %s',
            (repository.schema,)
        ).fetchall())
        require_grouped_normalization(columns)

        connection.execute(
            'LOCK TABLE flower_images, flower_features, feature_normalization, normalized_features IN SHARE ROW EXCLUSIVE MODE'
        )
        existing_names = {row[0] for row in connection.execute('SELECT filename FROM flower_images').fetchall()}
        if existing_names - {m['filename'] for m in metadata}:
            raise ValueError('Publication must include every existing gallery image; partial replacement is rejected.')

        existing = connection.execute(
            'SELECT normalization_id, manifest FROM cbir_index_versions WHERE version_id = %s',
            (data['version_id'],)
        ).fetchone()
        if existing:
            normalization_id, previous_manifest = existing
            if previous_manifest['fingerprint'] != data['manifest']['fingerprint']:
                raise ValueError('An immutable version ID was reused for different data.')
        else:
            normalization_id = connection.execute('''
                INSERT INTO feature_normalization
                    (method, f1_means, f1_stds, f2_means, f2_stds, f3_means, f3_stds, f4_means, f4_stds)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING normalization_id
            ''', (
                'report1_f2f4_zscore',
                *[
                    values[part].tolist()
                    for part in GROUP_SLICES
                    for values in (data['normalizer'].mean, data['normalizer'].std)
                ]
            )).fetchone()[0]
            connection.execute('''
                INSERT INTO cbir_index_versions (version_id, normalization_id, contract, manifest, normalizer)
                VALUES (%s, %s, %s, %s, %s)
            ''', (
                data['version_id'],
                normalization_id,
                Jsonb(contract),
                Jsonb(data['manifest']),
                Jsonb(data['normalizer'].to_dict()),
            ))

        for index, item in enumerate(metadata):
            stored_img_path = relative_image_path(item['filename'])
            stored_msk_path = relative_project_path(item['mask_path'])
            img_format = Path(item['filename']).suffix.lstrip('.').upper()

            identity = connection.execute('''
                INSERT INTO flower_images (filename, image_path, mask_path, width, height, image_format)
                VALUES (%s, %s, %s, 384, 384, %s)
                ON CONFLICT (filename) DO UPDATE SET
                    image_path = EXCLUDED.image_path,
                    mask_path = EXCLUDED.mask_path,
                    width = 384,
                    height = 384,
                    image_format = EXCLUDED.image_format
                RETURNING image_id
            ''', (item['filename'], stored_img_path, stored_msk_path, img_format)).fetchone()[0]

            connection.execute('''
                INSERT INTO flower_features (
                    image_id, hs_histogram, v_histogram, lab_mean, lab_std, lbp_histogram,
                    circularity, solidity, aspect_ratio, rotated_extent, radial_spectrum, iap, iep
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (image_id) DO UPDATE SET
                    hs_histogram = EXCLUDED.hs_histogram,
                    v_histogram = EXCLUDED.v_histogram,
                    lab_mean = EXCLUDED.lab_mean,
                    lab_std = EXCLUDED.lab_std,
                    lbp_histogram = EXCLUDED.lbp_histogram,
                    circularity = EXCLUDED.circularity,
                    solidity = EXCLUDED.solidity,
                    aspect_ratio = EXCLUDED.aspect_ratio,
                    rotated_extent = EXCLUDED.rotated_extent,
                    radial_spectrum = EXCLUDED.radial_spectrum,
                    iap = EXCLUDED.iap,
                    iep = EXCLUDED.iep,
                    extracted_at = CURRENT_TIMESTAMP
            ''', (identity, *feature_columns(raw[index])))

            f1_vec, f2_vec, f3_vec, f4_vec = [normalized[index, part].tolist() for part in GROUP_SLICES]
            connection.execute('''
                INSERT INTO normalized_features
                    (image_id, normalization_id, f1_vector, f2_vector, f3_vector, f4_vector)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (image_id) DO UPDATE SET
                    normalization_id = EXCLUDED.normalization_id,
                    f1_vector = EXCLUDED.f1_vector,
                    f2_vector = EXCLUDED.f2_vector,
                    f3_vector = EXCLUDED.f3_vector,
                    f4_vector = EXCLUDED.f4_vector,
                    created_at = CURRENT_TIMESTAMP
            ''', (identity, normalization_id, f1_vec, f2_vec, f3_vec, f4_vec))

            record = {
                **item,
                'image_id': identity,
                'image_path': stored_img_path,
                'mask_path': stored_msk_path,
            }
            connection.execute('''
                INSERT INTO cbir_index_entries (version_id, image_id, raw_vector, normalized_vector, metadata)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (version_id, image_id) DO NOTHING
            ''', (data['version_id'], identity, raw[index].tolist(), normalized[index].tolist(), Jsonb(record)))

        count = connection.execute(
            'SELECT count(*) FROM cbir_index_entries WHERE version_id = %s',
            (data['version_id'],)
        ).fetchone()[0]
        if count != len(metadata):
            raise ValueError('Incomplete immutable version; refusing activation.')

        actual = connection.execute('''
            SELECT i.filename,
                   e.raw_vector,
                   e.normalized_vector,
                   n.f1_vector || n.f2_vector || n.f3_vector || n.f4_vector
            FROM cbir_index_entries e
            JOIN flower_images i ON i.image_id = e.image_id
            JOIN normalized_features n ON n.image_id = i.image_id AND n.normalization_id = %s
            WHERE e.version_id = %s
        ''', (normalization_id, data['version_id'])).fetchall()
        positions = {m['filename']: i for i, m in enumerate(metadata)}
        if len(actual) != len(metadata) or any(
            not np.array_equal(raw[positions[name]], np.asarray(rv, np.float32)) or
            not np.array_equal(normalized[positions[name]], np.asarray(nv, np.float32)) or
            not np.array_equal(normalized[positions[name]], np.asarray(table_nv, np.float32))
            for name, rv, nv, table_nv in actual
        ):
            raise ValueError('Database round-trip differs from the staged batch; activation refused.')

        verify_contract(contract)
        connection.execute('UPDATE cbir_index_versions SET active = FALSE WHERE active')
        connection.execute('UPDATE cbir_index_versions SET active = TRUE WHERE version_id = %s', (data['version_id'],))

    return {
        'version_id': data['version_id'],
        'normalization_id': normalization_id,
        'rows': len(metadata),
        'backup': str(backup),
        'already_active': False,
    }
