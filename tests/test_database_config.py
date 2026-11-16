import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flower_cbir.services.database import connect_database, load_database_config


class DatabaseConfigTests(unittest.TestCase):
    def test_missing_database_rejected_before_connecting(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / 'database.local.json'
            config.write_text(json.dumps({'dbname': '', 'user': 'postgres'}), encoding='utf-8')
            with patch('flower_cbir.services.database.PROJECT_ROOT', Path(directory)), patch.dict('os.environ', {}, clear=True):
                with self.assertRaisesRegex(ValueError, 'dbname'):
                    load_database_config()

    def test_environment_overrides_local_config(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / 'database.local.json'
            config.write_text(json.dumps({'dbname': 'local', 'user': 'postgres', 'port': 5433}), encoding='utf-8')
            with patch('flower_cbir.services.database.PROJECT_ROOT', Path(directory)), patch.dict('os.environ', {'PGDATABASE': 'remote', 'PGPORT': '5434'}, clear=True):
                result = load_database_config()
            self.assertEqual(result['dbname'], 'remote')
            self.assertEqual(result['port'], '5434')

    def test_shared_schema_rejected_before_connecting(self):
        with patch('flower_cbir.services.database.psycopg.connect') as connect:
            for schema in ['information_schema', 'pg_catalog', '']:
                with self.subTest(schema=schema), self.assertRaises(ValueError):
                    connect_database({'schema': schema})
            connect.assert_not_called()
