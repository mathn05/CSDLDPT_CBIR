"""Atomic publication of raw features, normalization, vectors and provenance."""
import gzip
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from psycopg import sql
from psycopg.types.json import Jsonb

from flower_cbir.core.contract import fingerprint, verify_contract
from flower_cbir.core.schema import feature_columns, validate_features, GROUP_SLICES
from flower_cbir.settings import PROJECT_ROOT
from flower_cbir.services.database import require_grouped_normalization, relative_image_path


def backup_database(repository):
    snapshot = {'format': 'cbir-postgres-snapshot-v1', 'schema': repository.schema, 'tables': {}}
    names = ['flower_images', 'flower_masks', 'flower_features', 'feature_normalization', 'normalized_features',
             'retrieval_history', 'cbir_index_versions', 'cbir_index_entries']
    with repository.conn.transaction():
        repository.conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
        for name in names:
            exists = repository.conn.execute('SELECT to_regclass(%s)', (repository.schema + '.' + name,)).fetchone()[0]
            if exists:
                rows = repository.conn.execute(sql.SQL('SELECT row_to_json(t) FROM {} t').format(sql.Identifier(name))).fetchall()
                snapshot['tables'][name] = [row[0] for row in rows]
        snapshot['columns'] = repository.conn.execute('''
            SELECT table_name,column_name,data_type,is_identity,identity_generation,column_default
            FROM information_schema.columns WHERE table_schema=%s ORDER BY table_name,ordinal_position
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
            contract JSONB NOT NULL, manifest JSONB NOT NULL, normalizer JSONB NOT NULL,
            active BOOLEAN NOT NULL DEFAULT FALSE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    connection.execute('CREATE UNIQUE INDEX IF NOT EXISTS cbir_one_active_index ON cbir_index_versions ((active)) WHERE active')
    connection.execute('''
        CREATE TABLE IF NOT EXISTS cbir_index_entries (
            version_id TEXT NOT NULL REFERENCES cbir_index_versions(version_id),
            image_id INTEGER NOT NULL REFERENCES flower_images(image_id),
            raw_vector DOUBLE PRECISION[] NOT NULL CHECK(array_length(raw_vector,1)=163),
            normalized_vector DOUBLE PRECISION[] NOT NULL CHECK(array_length(normalized_vector,1)=163),
            metadata JSONB NOT NULL,
            PRIMARY KEY(version_id,image_id)
        )
    ''')
    connection.execute('ALTER TABLE retrieval_history ADD COLUMN IF NOT EXISTS index_version TEXT')
    connection.execute('ALTER TABLE retrieval_history ADD COLUMN IF NOT EXISTS weights DOUBLE PRECISION[]')
    connection.execute('ALTER TABLE retrieval_history ADD COLUMN IF NOT EXISTS query_context JSONB')
    connection.execute('ALTER TABLE retrieval_history ADD COLUMN IF NOT EXISTS query_image_data BYTEA')


def _relative(path):
    import os
    return Path(os.path.relpath(Path(path).resolve(), PROJECT_ROOT)).as_posix()


def publish_index(repository, data, *, replace_all=False, backup_artifact=None):
    contract = verify_contract(data['contract'])
    raw = np.asarray(data['raw_features'], dtype=np.float32)
    normalized = np.asarray(data['normalized_features'], dtype=np.float32)
    validate_features(raw)
    metadata = data['metadata']
    if raw.shape != (len(metadata), 163) or not np.array_equal(data['normalizer'].transform(raw), normalized):
        raise ValueError('Cannot publish inconsistent raw features and normalization.')
    if len({m['filename'] for m in metadata}) != len(metadata):
        raise ValueError('Duplicate filenames in the publication batch.')
    media_payloads = []
    for item in metadata:
        payloads = {}
        for kind in ['image', 'mask']:
            payload = item.get(kind + '_data')
            if payload is None:
                payload = Path(item[kind + '_path']).read_bytes()
            payload = bytes(payload)
            if not payload or hashlib.sha256(payload).hexdigest() != item[kind + '_sha256']:
                raise ValueError(f'Source changed since extraction: {item["filename"]}, {kind}')
            payloads[kind] = payload
        canonical_image = PROJECT_ROOT / relative_image_path(item['filename'])
        if not canonical_image.is_file() or hashlib.sha256(canonical_image.read_bytes()).hexdigest() != item['image_sha256']:
            raise ValueError(f'image_384 does not contain the indexed image: {item["filename"]}')
        media_payloads.append(payloads)
        if item.get('mask_provenance') not in ('deeplabv3', 'reviewed_final_segmentation_v2',
                                                'deeplabv3plus_v2_postprocessed'):
            raise ValueError('Only verified model masks or reviewed v2 ground truth may be published.')
    if not replace_all and repository.active_version() == data['version_id']:
        existing = repository.load_index()
        by_name = {m['filename']: i for i, m in enumerate(existing['metadata'])}
        if len(by_name) != len(metadata) or any(
            not np.array_equal(raw[i], existing['raw_features'][by_name[m['filename']]])
            for i, m in enumerate(metadata)):
            raise ValueError('The active immutable version differs from the publication batch.')
        return {'version_id': data['version_id'], 'already_active': True, 'rows': len(metadata)}
    if replace_all:
        backup = Path(backup_artifact).resolve() if backup_artifact is not None else None
        if backup is None or not backup.is_file() or backup.stat().st_size == 0:
            raise ValueError('A validated pg_dump backup is required before replacing all tables.')
    else:
        backup = backup_database(repository)
    connection = repository.conn
    with repository.lock, connection.transaction():
        connection.execute("SELECT pg_advisory_xact_lock(hashtext('cbir_publish_index'))")
        ensure_schema(connection)
        columns = set(connection.execute(
            "SELECT table_name, column_name FROM information_schema.columns WHERE table_schema=%s",
            (repository.schema,)
        ).fetchall())
        require_grouped_normalization(columns)
        has_image_paths = ('flower_images', 'image_path') in columns and ('flower_images', 'mask_path') in columns
        store_images = ('flower_images', 'image_data') in columns
        store_masks = ('flower_masks', 'mask_data') in columns
        store_embeddings = ('normalized_features', 'pgvector_embedding') in columns

        if replace_all:
            tables_to_truncate = [
                'retrieval_history', 'cbir_index_entries', 'cbir_index_versions',
                'normalized_features', 'flower_features', 'flower_images', 'feature_normalization'
            ]
            if store_masks:
                tables_to_truncate.insert(5, 'flower_masks')
            connection.execute(f"TRUNCATE TABLE {','.join(tables_to_truncate)} RESTART IDENTITY CASCADE")
        connection.execute('LOCK TABLE flower_images,flower_features,feature_normalization,normalized_features IN SHARE ROW EXCLUSIVE MODE')
        existing_names = {row[0] for row in connection.execute('SELECT filename FROM flower_images').fetchall()}
        if existing_names - {m['filename'] for m in metadata}:
            raise ValueError('Publication must include every existing gallery image; partial replacement is rejected.')
        existing = connection.execute('SELECT normalization_id,manifest FROM cbir_index_versions WHERE version_id=%s',
                                      (data['version_id'],)).fetchone()
        if existing:
            normalization_id, previous_manifest = existing
            if previous_manifest['fingerprint'] != data['manifest']['fingerprint']:
                raise ValueError('An immutable version ID was reused for different data.')
        else:
            normalization_id = connection.execute('''
                INSERT INTO feature_normalization
                    (method,f1_means,f1_stds,f2_means,f2_stds,f3_means,f3_stds,f4_means,f4_stds)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING normalization_id
            ''', ('report1_f2f4_zscore', *[
                values[part].tolist() for part in GROUP_SLICES
                for values in (data['normalizer'].mean, data['normalizer'].std)
            ])).fetchone()[0]
            connection.execute('''
                INSERT INTO cbir_index_versions (version_id,normalization_id,contract,manifest,normalizer)
                VALUES (%s,%s,%s,%s,%s)
            ''', (data['version_id'], normalization_id, Jsonb(contract), Jsonb(data['manifest']), Jsonb(data['normalizer'].to_dict())))

        for index, item in enumerate(metadata):
            img_cols = ['filename', 'width', 'height', 'image_format']
            img_vals = ['%s', '384', '384', '%s']
            img_updates = ['width=384', 'height=384', 'image_format=EXCLUDED.image_format']
            img_params = [item['filename'], Path(item['filename']).suffix.lstrip('.').upper()]
            if has_image_paths:
                img_cols.extend(['image_path', 'mask_path'])
                img_vals.extend(['%s', '%s'])
                img_updates.extend(['image_path=EXCLUDED.image_path', 'mask_path=EXCLUDED.mask_path'])
                img_params.extend([
                    relative_image_path(item['filename']),
                    _relative(item['mask_path']) if item.get('mask_path') else (Path(item['filename']).stem + '.png'),
                ])
            if store_images:
                img_cols.append('image_data')
                img_vals.append('%s')
                img_updates.append('image_data=EXCLUDED.image_data')
                img_params.append(media_payloads[index]['image'])
            identity = connection.execute(f'''
                INSERT INTO flower_images ({",".join(img_cols)})
                VALUES ({",".join(img_vals)})
                ON CONFLICT (filename) DO UPDATE SET {",".join(img_updates)}
                RETURNING image_id
            ''', img_params).fetchone()[0]

            connection.execute('''
                INSERT INTO flower_features (image_id,hs_histogram,v_histogram,lab_mean,lab_std,lbp_histogram,
                    circularity,solidity,aspect_ratio,rotated_extent,radial_spectrum,iap,iep)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (image_id) DO UPDATE SET hs_histogram=EXCLUDED.hs_histogram,
                    v_histogram=EXCLUDED.v_histogram,lab_mean=EXCLUDED.lab_mean,lab_std=EXCLUDED.lab_std,
                    lbp_histogram=EXCLUDED.lbp_histogram,circularity=EXCLUDED.circularity,solidity=EXCLUDED.solidity,
                    aspect_ratio=EXCLUDED.aspect_ratio,rotated_extent=EXCLUDED.rotated_extent,
                    radial_spectrum=EXCLUDED.radial_spectrum,iap=EXCLUDED.iap,iep=EXCLUDED.iep,extracted_at=CURRENT_TIMESTAMP
            ''', (identity, *feature_columns(raw[index])))

            if store_masks:
                connection.execute('''
                    INSERT INTO flower_masks (image_id,mask_data,mask_format,mask_sha256,source_version)
                    VALUES (%s,%s,'PNG',%s,%s)
                    ON CONFLICT (image_id) DO UPDATE SET mask_data=EXCLUDED.mask_data,
                        mask_format=EXCLUDED.mask_format,mask_sha256=EXCLUDED.mask_sha256,
                        source_version=EXCLUDED.source_version,updated_at=CURRENT_TIMESTAMP
                ''', (identity, media_payloads[index]['mask'], item['mask_sha256'],
                      item['mask_provenance']))

            norm_cols = ['image_id', 'normalization_id']
            norm_vals = ['%s', '%s']
            norm_updates = ['normalization_id=EXCLUDED.normalization_id', 'created_at=CURRENT_TIMESTAMP']
            norm_params = [identity, normalization_id]
            norm_cols.extend(['f1_vector', 'f2_vector', 'f3_vector', 'f4_vector'])
            norm_vals.extend(['%s', '%s', '%s', '%s'])
            norm_updates.extend([
                'f1_vector=EXCLUDED.f1_vector', 'f2_vector=EXCLUDED.f2_vector',
                'f3_vector=EXCLUDED.f3_vector', 'f4_vector=EXCLUDED.f4_vector',
            ])
            norm_params.extend([normalized[index, part].tolist() for part in GROUP_SLICES])
            if store_embeddings:
                norm_cols.append('pgvector_embedding')
                norm_vals.append('%s::vector(163)')
                norm_updates.append('pgvector_embedding=EXCLUDED.pgvector_embedding')
                norm_params.append(normalized[index].tolist())
            connection.execute(f'''
                INSERT INTO normalized_features ({",".join(norm_cols)})
                VALUES ({",".join(norm_vals)})
                ON CONFLICT (image_id) DO UPDATE SET {",".join(norm_updates)}
            ''', norm_params)

            record = {key: value for key, value in item.items()
                      if key not in ('image_data', 'mask_data')}
            record['image_id'] = identity
            record['image_path'] = relative_image_path(item['filename'])
            if record.get('mask_path'):
                record['mask_path'] = _relative(record['mask_path'])
            connection.execute('''
                INSERT INTO cbir_index_entries (version_id,image_id,raw_vector,normalized_vector,metadata)
                VALUES (%s,%s,%s,%s,%s) ON CONFLICT (version_id,image_id) DO NOTHING
            ''', (data['version_id'], identity, raw[index].tolist(), normalized[index].tolist(), Jsonb(record)))

        count = connection.execute('SELECT count(*) FROM cbir_index_entries WHERE version_id=%s',
                                   (data['version_id'],)).fetchone()[0]
        if count != len(metadata):
            raise ValueError('Incomplete immutable version; refusing activation.')
        check_norm_expr = 'n.f1_vector || n.f2_vector || n.f3_vector || n.f4_vector'
        actual = connection.execute(f'''
            SELECT i.filename,e.raw_vector,e.normalized_vector,{check_norm_expr}
                   {',n.pgvector_embedding::real[]' if store_embeddings else ''}
            FROM cbir_index_entries e JOIN flower_images i ON i.image_id=e.image_id
            JOIN normalized_features n ON n.image_id=i.image_id AND n.normalization_id=%s
            WHERE e.version_id=%s
        ''', (normalization_id, data['version_id'])).fetchall()
        positions = {m['filename']: i for i, m in enumerate(metadata)}
        if len(actual) != len(metadata) or any(
            not np.array_equal(raw[positions[name]], np.asarray(rv, np.float32)) or
            not np.array_equal(normalized[positions[name]], np.asarray(nv, np.float32)) or
            not np.array_equal(normalized[positions[name]], np.asarray(table_nv, np.float32)) or
            any(not np.array_equal(normalized[positions[name]], np.asarray(v, np.float32)) for v in embeddings)
            for name, rv, nv, table_nv, *embeddings in actual):
            raise ValueError('Database round-trip differs from the staged batch; activation refused.')
        # Check the contract again before committing a potentially long import.
        verify_contract(contract)
        connection.execute('UPDATE cbir_index_versions SET active=FALSE WHERE active')
        connection.execute('UPDATE cbir_index_versions SET active=TRUE WHERE version_id=%s', (data['version_id'],))
    return {'version_id': data['version_id'], 'normalization_id': normalization_id,
            'rows': len(metadata), 'backup': str(backup), 'already_active': False}
