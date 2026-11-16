import unittest
import tempfile
import contextlib
import io
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from sources import hentai3 as h
from tools import cache_3hentai_thumbs as thumbs


FIXTURES = Path(__file__).parent / 'fixtures' / '3hentai'


def gallery(inner):
    return '<div id="main-info"><div class="tag-container field-name">' + inner + '</div></div>'


class GalleryScopeTests(unittest.TestCase):
    def test_real_gallery_metadata_and_own_images(self):
        for number, tags, evidence, content, images in [
            (605753, 9, 14, 7, 17), (605739, 11, 15, 9, 22), (708438, 9, 13, 7, 5)
        ]:
            with self.subTest(number=number):
                html = (FIXTURES / f'html_gallery_{number}.html').read_text()
                url = f'https://3hentai.net/d/{number}'
                meta = h._gallery_metadata_from_html(html, url)
                self.assertEqual((meta['language'], meta['category'], len(meta['tags']),
                                  meta['evidence_count'], meta['content_tag_count']),
                                 ('japanese', 'doujinshi', tags, evidence, content))
                self.assertTrue(meta['tag_section_found'])
                item = {}
                h._merge_gallery_metadata(item, meta)
                self.assertEqual(item['filter_metadata_checked'], '3hentai-gallery-v1')
                soup = BeautifulSoup(html, 'html.parser')
                own = {urljoin(url, img.get('data-src') or img.get('src') or '') for img in soup.select(
                    '#main-info a.main-cover img, #thumbnail-gallery img')}
                related = {urljoin(url, img.get('data-src') or img.get('src') or '') for img in soup.select('#similar-content img')}
                candidates = thumbs.page_image_candidates(html, url)
                self.assertEqual(set(candidates), own)
                self.assertEqual(len(candidates), images)
                self.assertFalse(set(candidates) & related)

    def test_outside_links_labels_and_badges_fail_closed(self):
        html = '''<nav><a href="/language/japanese">japanese</a></nav>
        <div id="similar-content"><div>Categories: <a href="/category/doujinshi">doujinshi</a></div>
        <div>Tags: <a href="/tags/sample">sample</a></div><span class="badge">tag:bad</span></div>'''
        for prefix, found in [('', False), (gallery('Pages: 4'), True)]:
            with self.subTest(found=found):
                meta = h._gallery_metadata_from_html(prefix + html, '')
                self.assertEqual(meta['tag_section_found'], found)
                self.assertEqual(meta['evidence_count'], 0)
                self.assertEqual(meta['content_tag_count'], 0)
                item = {'tags': ['previous']}
                h._merge_gallery_metadata(item, meta)
                self.assertNotIn('filter_metadata_checked', item)
                self.assertEqual(h._gallery_filter_reason(meta),
                                 'gallery_no_tags' if found else 'gallery_no_tag_section')

    def test_language_and_category_only_remain_unchecked(self):
        html = gallery('''<span>Tags:</span><a href="/language/japanese">japanese</a>''')
        html += gallery('<span>Categories:</span><a href="/category/doujinshi">doujinshi</a>')
        meta = h._gallery_metadata_from_html(html, '')
        self.assertEqual(meta['content_tag_count'], 0)
        self.assertEqual(meta['tags'], ['language:japanese', 'japanese'])
        item = {}
        h._merge_gallery_metadata(item, meta)
        self.assertNotIn('filter_metadata_checked', item)
        with patch.object(h.requests, 'Session'), patch.object(h, '_fetch_gallery_metadata',
                return_value=(meta, {'http_status': 200})):
            item.update(uid='3hentai:1', source='3hentai', source_url='https://3hentai.net/d/1')
            result = h.enrich_existing_for_filter([item], limit=1)
        self.assertEqual(result['existing_filter_audit_failed'], 1)
        self.assertEqual(result['existing_filter_audit_reason_counts'], {'gallery_no_tags': 1})

    def test_unrelated_images_produce_no_candidates(self):
        html = '<div id="similar-content"><a class="cover"><img class="lazy" data-src="/other.jpg"></a></div><img src="/logo.png">'
        self.assertEqual(thumbs.page_image_candidates(html, 'https://3hentai.net/d/1'), [])
        session = Mock()
        session.get.return_value = Mock(status_code=200, text=html, url='https://3hentai.net/d/1')
        self.assertEqual(thumbs.fetch_page_candidates(session, session.get.return_value.url),
                         ([], 'gallery_no_image_candidates', False))

    def test_meta_candidates_remain_supported(self):
        html = '<meta property="og:image" content="/cover.jpg"><meta name="twitter:image" content="/cover.jpg">'
        self.assertEqual(thumbs.page_image_candidates(html, 'https://3hentai.net/d/1'),
                         ['https://3hentai.net/cover.jpg'])

    def test_no_candidates_count_as_thumbnail_failure(self):
        with tempfile.TemporaryDirectory(dir=FIXTURES.parent.parent) as directory:
            root = Path(directory)
            item = dict(source='3hentai', source_id='1', source_url_kind='direct',
                        source_url='https://3hentai.net/d/1')
            data = root / 'data.json'
            data.write_text('{"items": []}')
            status = {}
            session = Mock()
            session.get.return_value = Mock(status_code=200, text='<img src="/logo.png">',
                                             url=item['source_url'])
            with patch.object(thumbs, 'DATA', data), patch.object(thumbs, 'OUTDIR', root / 'thumbs'), \
                 patch.object(thumbs, 'ATTEMPTS', root / 'attempts.json'), \
                 patch.object(thumbs, 'load', side_effect=[{'items': [item]}, status]), \
                 patch.object(thumbs, 'save'), patch.object(thumbs.requests, 'Session', return_value=session), \
                 patch.object(thumbs.time, 'sleep'), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(thumbs.main(), 0)
            stats = status['3hentai']['thumbnail_cache']
            self.assertEqual((stats['attempted'], stats['failed'], stats['cached']), (1, 1, 0))
            self.assertIn('1: gallery_no_image_candidates', stats['failures'])
            self.assertEqual(session.get.call_count, 1)
