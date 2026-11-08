from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import config
from sources import asmhentai, hentai3

# The retired nHentai collector no longer has runtime settings in config.
with patch.multiple(config, NH_BACKFILL_PAGES=1, NH_LATEST_PAGES=1,
                    NH_MAX_DETAILS_PER_RUN=1, create=True):
    from sources import nhentai
from sources.common import is_valid_source_id
from tools import cache_3hentai_thumbs, cache_asmhentai_thumbs


class SourceIdSafetyTests(unittest.TestCase):
    def test_safe_ids_are_preserved(self):
        for sid in ('123456', '9000001', 'AbC123', 'api_123-ABC'):
            with self.subTest(sid=sid):
                self.assertTrue(is_valid_source_id(sid))
                raw = {'id': sid, 'title': 'sample', 'language': 'japanese'}
                self.assertEqual(asmhentai.normalize(raw)['source_id'], sid)
                self.assertEqual(hentai3._normalize(raw)['source_id'], sid)
                self.assertEqual(
                    nhentai._normalize(dict(raw, title={'english': 'sample'}))['source_id'],
                    sid,
                )

    def test_invalid_ids_are_logged_and_skipped(self):
        for sid in ('../escape', 'abc/../../escape', '/absolute', r'..\escape',
                    'a%2fb', '..', '', 'a\x00b', '日本語', 'a:b'):
            with self.subTest(sid=sid):
                self.assertFalse(is_valid_source_id(sid))
                raw = {'id': sid, 'title': 'sample', 'language': 'japanese'}
                with self.assertLogs(level='WARNING') as logs:
                    self.assertIsNone(asmhentai.normalize(raw))
                    self.assertIsNone(hentai3._normalize(raw))
                    self.assertIsNone(nhentai._normalize(raw))
                self.assertIn('skipping invalid source_id', '\n'.join(logs.output))

    def test_cache_resolves_and_confines_destination(self):
        for module, setting in ((cache_3hentai_thumbs, 'OUTDIR'),
                                (cache_asmhentai_thumbs, 'OUT')):
            with self.subTest(module=module.__name__):
                with tempfile.TemporaryDirectory() as directory:
                    tmp_path = Path(directory)
                    root = tmp_path / 'cache'
                    root.mkdir()
                    with patch.object(module, setting, root):
                        self.assertEqual(
                            module.cache_destination('api_123-ABC'),
                            root / 'api_123-ABC.webp',
                        )
                        for sid in ('../escape', 'nested/../../escape',
                                    str(tmp_path / 'absolute')):
                            with self.subTest(sid=sid):
                                with self.assertRaises(ValueError):
                                    module.cache_destination(sid)
                        outside = tmp_path / 'outside.webp'
                        (root / 'link.webp').symlink_to(outside)
                        with self.assertRaises(ValueError):
                            module.cache_destination('link')
                        self.assertFalse(outside.exists())

    def test_cache_main_skips_traversal_before_download(self):
        for module, setting, source in (
            (cache_3hentai_thumbs, 'OUTDIR', '3hentai'),
            (cache_asmhentai_thumbs, 'OUT', 'asmhentai'),
        ):
            with self.subTest(module=module.__name__):
                with tempfile.TemporaryDirectory() as directory:
                    tmp_path = Path(directory)
                    item = {'source': source, 'source_id': '../escape',
                            'thumbnail': 'https://example.test/image'}
                    download = Mock(side_effect=AssertionError('must skip before download'))
                    download_name = 'download_thumbnail' if source == '3hentai' else 'download'
                    with patch.object(module, setting, tmp_path / 'cache'), \
                         patch.object(module, 'load', side_effect=lambda path, default, validator:
                                      {'items': [item]} if path == module.DATA else {}), \
                         patch.object(module, 'save', Mock()), \
                         patch.object(module, download_name, download):
                        module.main()
                    download.assert_not_called()
                    self.assertFalse((tmp_path / 'escape.webp').exists())
