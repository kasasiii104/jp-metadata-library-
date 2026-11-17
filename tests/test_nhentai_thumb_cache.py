import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, Mock, patch

from PIL import Image
from tools import cache_nhentai_thumbs as cache

URL = 'https://t.nhentai.net/galleries/123/thumb.webp'


class ThumbnailTests(unittest.TestCase):
    def test_url_validation(self):
        self.assertTrue(cache.valid_url(URL))
        session=Mock()
        for url in ['http://t.nhentai.net/galleries/123/thumb.webp', URL+'?x=1',
                    URL+'/../x', URL.replace('t.nhentai.net','evil.test'),
                    'https://t.nhentai.net@evil.test/galleries/123/thumb.webp']:
            with self.subTest(url=url):
                self.assertFalse(cache.valid_url(url))
                self.assertIsNone(cache.download(session,url))
        session.get.assert_not_called()

    def test_confined_destination(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(cache,'OUT',Path(directory)/'cache'):
            cache.OUT.mkdir()
            self.assertEqual(cache.cache_destination('123'), cache.OUT/'123.webp')
            for sid in ['../escape','a/b','abc','/tmp/escape','１２','1.2']:
                with self.assertRaises(ValueError):
                    cache.cache_destination(sid)
            (cache.OUT/'456.webp').symlink_to(Path(directory)/'outside.webp')
            with self.assertRaises(ValueError):
                cache.cache_destination('456')

    def run_main(self, data, limit=60):
        with tempfile.TemporaryDirectory() as directory, patch.object(cache,'OUT',Path(directory)/'cache'), \
             patch.object(cache,'LIMIT',limit), patch.object(cache,'time') as time, \
             patch.object(cache,'load',side_effect=[data,{}]), patch.object(cache,'save') as save, \
             patch.object(cache,'download',return_value=b'x'*600) as download:
            time.monotonic.return_value = 0
            cache.main()
            stats=save.call_args_list[-1].args[1]['nhentai']['thumbnail_cache']
            return stats,download,time

    def test_deadline_keeps_reusing(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory)
            (out/'3.webp').write_bytes(b'x'*600)
            data = {'items':[{'source':'nhentai','source_id':str(i),'thumbnail':URL} for i in (1,2,3)]}
            with patch.object(cache,'OUT',out), patch.object(cache,'load',side_effect=[data,{}]), \
                 patch.object(cache,'save') as save, patch.object(cache,'download',return_value=b'x'*600) as download, \
                 patch.object(cache.time,'monotonic',side_effect=[0,0,181]), patch.object(cache.time,'sleep'):
                cache.main()
            stats = save.call_args.args[1]['nhentai']['thumbnail_cache']
            self.assertTrue(stats['deadline_reached'])
            self.assertEqual(stats['attempted'],1)
            self.assertEqual(stats['reused'],1)
            self.assertEqual(download.call_count,1)

    def test_missing_items(self):
        stats,download,_=self.run_main({})
        self.assertEqual(stats['attempted'],0)
        download.assert_not_called()

    def test_missing_items_real_storage(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'data.json').write_text('{}')
            with patch.object(cache, 'DATA', root / 'data.json'), \
                 patch.object(cache, 'STATUS', root / 'status.json'), \
                 patch.object(cache, 'OUT', root / 'thumbs'), \
                 patch.object(cache, 'download') as download:
                cache.main()
                download.assert_not_called()

    def test_limit_and_invalid_rows(self):
        data={'items':[{'source':'nhentai','source_id':str(i),'thumbnail':URL} for i in range(4)]}
        data['items'] += [{'source':'nhentai','source_id':'../escape','thumbnail':URL},
                          {'source':'nhentai','source_id':'99','thumbnail':'https://evil.test/image'}]
        stats,download,time=self.run_main(data,2)
        self.assertEqual(stats['attempted'],2)
        self.assertEqual(stats['cached'],2)
        self.assertEqual(stats['failed'],2)
        self.assertEqual(download.call_count,2)
        self.assertEqual(time.sleep.call_count,2)
        self.assertEqual(data['items'][0]['thumbnail'],'thumbs/nhentai/0.webp')

    def test_reuse(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(cache,'OUT',Path(directory)), \
             patch.object(cache,'load',side_effect=[{'items':[{'source':'nhentai','source_id':'123'}]},{}]), \
             patch.object(cache,'save') as save, patch.object(cache,'download') as download:
            (cache.OUT/'123.webp').write_bytes(b'x'*600)
            cache.main()
            self.assertEqual(save.call_args.args[1]['nhentai']['thumbnail_cache']['reused'],1)
            download.assert_not_called()

    def test_redirect_and_conversion(self):
        session=MagicMock()
        response=session.get.return_value.__enter__.return_value
        response.status_code=302
        self.assertIsNone(cache.download(session,URL))
        self.assertFalse(session.get.call_args.kwargs['allow_redirects'])
        self.assertEqual(session.get.call_args.kwargs['headers']['Referer'],'https://nhentai.net/')
        response.raw.read.assert_not_called()
        response.status_code=200
        buffer=io.BytesIO()
        Image.new('RGBA',(800,1000),(255,0,0,128)).save(buffer,'PNG')
        response.raw.read.return_value=buffer.getvalue()
        result=cache.download(session,URL)
        with Image.open(io.BytesIO(result)) as image:
            self.assertEqual(image.format,'WEBP')
            self.assertLessEqual(image.width,520)
            self.assertLessEqual(image.height,760)
        response.raw.read.return_value=b'x'*(cache.MAX_BYTES+1)
        self.assertIsNone(cache.download(session,URL))
